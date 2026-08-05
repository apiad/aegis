# Conversational Corpus VS1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make aegis's own session ledger queryable — `aegis_recall("did we discuss X")` returns ranked exchanges from months of history.

**Architecture:** A pure extractor turns raw event logs (plus backfill sidecars) into *exchanges*; an incremental indexer writes them into a beaver document collection; a recall layer narrows with FTS5 and re-ranks in Python. Two MCP tools and one CLI command sit on top.

**Tech Stack:** Python 3.11+, `beaver-db` 2.3 (async API), `fastmcp` tool registration, `typer` CLI, `pytest`.

Spec: `docs/superpowers/specs/2026-08-04-aegis-conversational-corpus-design.md`

## Global Constraints

- Package manager is `uv`. Never `pip`. Tests: `uv run python -m pytest -q -m "not live"`.
- TDD: failing test first, minimal implementation, commit per logical unit.
- **Never write into `state/sessions/`.** `repair.py:61`, `session_log.py:160` and `history.py:203` each glob `sessions/*.jsonl`; anything else placed there is read back as a session log. Sidecars live in `state/backfill/`, the index in `state/corpus.db`.
- **Never modify an existing session log.** They are the only copy of a conversation. Read-only, always.
- Use `AsyncBeaverDB`, not the sync `BeaverDB`. The sync `.search()` convenience returns `-0.0` for every hit; only `query().fts(...).execute()` yields real BM25.
- **BM25 scores are negative; more negative is a better match.** Sort ascending.
- beaver's FTS5 rejects punctuation (`fts5: syntax error near "."`). Every query string must be tokenized before it reaches beaver.
- **`beaver.Document` has exactly two fields, `id` and `body`. There is no `metadata=`.** Structured fields go in a pydantic model passed as `db.docs(name, model=ExchangeDoc)`, with `Document(id=..., body=ExchangeDoc(...))`. Verified 2026-08-05: a model body also yields far better-separated scores than a plain string body (-1.4186 vs -0.8596, against a flat -1e-06 for strings).
- **Do not use beaver's `.where()`.** `Ex.field == value` raises `AttributeError` on pydantic v2 — models have no class-level field descriptors. All filtering and ranking happens in Python, on the candidate set FTS returns.
- Tests inherit the autouse `isolated_project_dir` fixture from `tests/conftest.py`; anything resolving state from `Path.cwd()` is isolated automatically.
- English for all code, comments, identifiers, log messages, and commit messages.

---

## File Structure

| File | Responsibility |
|---|---|
| `src/aegis/corpus/__init__.py` | package marker, re-exports `Exchange` |
| `src/aegis/corpus/extract.py` | **pure**: events → exchanges. No I/O. |
| `src/aegis/corpus/source.py` | reads a session log + its sidecar, merges by timestamp |
| `src/aegis/corpus/index.py` | beaver collection, watermark, incremental indexing |
| `src/aegis/corpus/recall.py` | query sanitization, FTS candidate fetch, re-ranking |
| `tests/test_corpus_extract.py` | extractor unit tests |
| `tests/test_corpus_source.py` | merge + provenance tests |
| `tests/test_corpus_index.py` | watermark / incremental tests |
| `tests/test_corpus_recall.py` | ranking, sanitization, scoping tests |

---

### Task 1: The Exchange dataclass and the pure extractor

**Files:**
- Create: `src/aegis/corpus/__init__.py`
- Create: `src/aegis/corpus/extract.py`
- Test: `tests/test_corpus_extract.py`

**Interfaces:**
- Consumes: `aegis.events` (`UserMessage`, `AssistantText`, `ToolUse`, `SessionMeta`)
- Produces: `Exchange` dataclass; `extract_exchanges(events: list, meta: SessionMeta | None) -> list[Exchange]`; module constant `BOUNDARY_SOURCES: frozenset[str]`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_corpus_extract.py
from aegis.corpus.extract import extract_exchanges, BOUNDARY_SOURCES


def test_operator_turn_starts_an_exchange():
    evs = [
        ({"t": "UserMessage", "text": "fix the geocoder", "source": "operator"}, "2026-07-01T10:00:00Z"),
        ({"t": "AssistantText", "text": "on it"}, "2026-07-01T10:00:05Z"),
    ]
    out = extract_exchanges(evs, meta={"handle": "h1", "cwd": "/w"})
    assert len(out) == 1
    assert out[0].operator_text == "fix the geocoder"
    assert out[0].assistant_text == "on it"
    assert out[0].handle == "h1"


def test_monitor_wake_attaches_instead_of_splitting():
    evs = [
        ({"t": "UserMessage", "text": "build it", "source": "operator"}, "2026-07-01T10:00:00Z"),
        ({"t": "AssistantText", "text": "building"}, "2026-07-01T10:00:01Z"),
        ({"t": "UserMessage", "text": "> from monitor:X done", "source": "monitor"}, "2026-07-01T10:05:00Z"),
        ({"t": "AssistantText", "text": "green"}, "2026-07-01T10:05:01Z"),
    ]
    out = extract_exchanges(evs, meta={"handle": "h1", "cwd": "/w"})
    assert len(out) == 1, "a monitor wake is a continuation, not a new question"
    assert "green" in out[0].assistant_text


def test_agent_handoff_starts_a_new_exchange():
    evs = [
        ({"t": "UserMessage", "text": "build it", "source": "operator"}, "2026-07-01T10:00:00Z"),
        ({"t": "UserMessage", "text": "> from agent:peer take over", "source": "agent"}, "2026-07-01T10:01:00Z"),
    ]
    out = extract_exchanges(evs, meta={"handle": "h1", "cwd": "/w"})
    assert len(out) == 2, "a handoff carries new intent"
    assert BOUNDARY_SOURCES == frozenset({"operator", "agent"})


def test_file_and_tool_facets_are_collected():
    evs = [
        ({"t": "UserMessage", "text": "edit it", "source": "operator"}, "2026-07-01T10:00:00Z"),
        ({"t": "ToolUse", "name": "Edit", "input": {"file_path": "/w/a.py"}}, "2026-07-01T10:00:01Z"),
        ({"t": "ToolUse", "name": "Bash", "input": {"command": "ls"}}, "2026-07-01T10:00:02Z"),
    ]
    out = extract_exchanges(evs, meta={"handle": "h1", "cwd": "/w"})
    assert out[0].files_touched == ("/w/a.py",)
    assert set(out[0].tools_used) == {"Edit", "Bash"}


def test_tool_results_are_excluded_from_text():
    evs = [
        ({"t": "UserMessage", "text": "read it", "source": "operator"}, "2026-07-01T10:00:00Z"),
        ({"t": "ToolResult", "content": "a" * 5000}, "2026-07-01T10:00:01Z"),
    ]
    out = extract_exchanges(evs, meta={"handle": "h1", "cwd": "/w"})
    assert "aaaa" not in out[0].assistant_text
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run python -m pytest tests/test_corpus_extract.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'aegis.corpus'`

- [ ] **Step 3: Write minimal implementation**

```python
# src/aegis/corpus/__init__.py
from aegis.corpus.extract import Exchange, extract_exchanges

__all__ = ["Exchange", "extract_exchanges"]
```

```python
# src/aegis/corpus/extract.py
"""Events in, exchanges out. Pure — no disk, no db, no clock.

Pure for the same reason `btw/window.py` is: it is the piece every other
part of the corpus depends on, so it is the piece worth testing hard.
"""
from __future__ import annotations

from dataclasses import dataclass, field

# A turn from one of these sources carries genuine new intent and opens a new
# exchange. Everything else (monitor wakes, queue callbacks, canvas notices)
# is a continuation of the task already in flight — letting those split would
# shatter one piece of work into a dozen fake exchanges.
BOUNDARY_SOURCES = frozenset({"operator", "agent"})

# Tool inputs whose file_path is worth indexing as a facet.
FILE_TOOLS = frozenset({"Read", "Edit", "Write", "NotebookEdit"})

_MAX_TEXT = 8000


@dataclass(frozen=True)
class Exchange:
    operator_text: str
    assistant_text: str
    source: str
    handle: str | None
    cwd: str | None
    ts_start: str
    ts_end: str
    files_touched: tuple[str, ...] = ()
    tools_used: tuple[str, ...] = ()
    friction: tuple[str, ...] = ()

    @property
    def key(self) -> str:
        """Stable id: handle + start timestamp uniquely locate an exchange."""
        return f"{self.handle or '?'}@{self.ts_start}"


def _text_of(ev: dict) -> str:
    return (ev.get("text") or "").strip()


def extract_exchanges(events, meta=None) -> list[Exchange]:
    """`events` is an iterable of (event_dict, aegis_ts) in log order."""
    meta = meta or {}
    handle, cwd = meta.get("handle"), meta.get("cwd")
    out: list[Exchange] = []

    cur: dict | None = None

    def flush():
        if cur is None:
            return
        out.append(Exchange(
            operator_text=cur["operator"][:_MAX_TEXT],
            assistant_text=" ".join(cur["assistant"])[:_MAX_TEXT],
            source=cur["source"],
            handle=handle, cwd=cwd,
            ts_start=cur["ts_start"], ts_end=cur["ts_end"],
            files_touched=tuple(dict.fromkeys(cur["files"])),
            tools_used=tuple(dict.fromkeys(cur["tools"])),
            friction=tuple(cur["friction"]),
        ))

    for ev, ts in events:
        t = ev.get("t")
        if t == "UserMessage":
            src = ev.get("source") or "unknown"
            if src in BOUNDARY_SOURCES:
                flush()
                cur = {"operator": _text_of(ev), "assistant": [], "source": src,
                       "ts_start": ts, "ts_end": ts, "files": [], "tools": [],
                       "friction": ["interrupted"] if ev.get("interrupted") else []}
            elif cur is not None:
                cur["assistant"].append(_text_of(ev))
                cur["ts_end"] = ts
            continue
        if cur is None:
            continue
        cur["ts_end"] = ts
        if t == "AssistantText":
            cur["assistant"].append(_text_of(ev))
        elif t == "ToolUse":
            name = ev.get("name") or ""
            if name:
                cur["tools"].append(name)
            inp = ev.get("input")
            if isinstance(inp, dict):
                fp = inp.get("file_path")
                if name in FILE_TOOLS and fp:
                    cur["files"].append(fp)
        elif t in ("Interrupted", "TurnAborted"):
            cur["friction"].append(t.lower())
        # ToolResult deliberately ignored: ~60% of corpus bytes, no signal.

    flush()
    return out
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run python -m pytest tests/test_corpus_extract.py -q`
Expected: 5 passed

- [ ] **Step 5: Commit**

```bash
git add src/aegis/corpus/__init__.py src/aegis/corpus/extract.py tests/test_corpus_extract.py
git commit -m "feat(corpus): pure exchange extractor"
```

---

### Task 2: Reading a session log merged with its backfill sidecar

**Files:**
- Create: `src/aegis/corpus/source.py`
- Test: `tests/test_corpus_source.py`

**Interfaces:**
- Consumes: `extract_exchanges` from Task 1
- Produces: `read_log(log_path: Path, backfill_dir: Path | None) -> tuple[list[tuple[dict, str]], dict]`; `derive_source(text: str) -> tuple[str, str | None]`; `exchanges_for_log(log_path, backfill_dir) -> list[Exchange]`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_corpus_source.py
import json
from pathlib import Path
from aegis.corpus.source import derive_source, read_log, exchanges_for_log


def _write(p: Path, records):
    p.write_text("".join(json.dumps(r) + "\n" for r in records))


def test_derive_source_classifies_substrate_headers():
    assert derive_source("> from monitor:ABC · ok") == ("monitor", "ABC")
    assert derive_source("> from agent:peer-x · hi") == ("agent", "peer-x")
    assert derive_source("> from queue:build · done") == ("queue", "build")
    assert derive_source("> from loop · iteration 3/20") == ("loop", None)
    assert derive_source("<task-notification>x</task-notification>") == ("harness", None)
    assert derive_source("fix the geocoder") == ("operator", None)


def test_derive_source_mutation_guard():
    """A classifier that returned 'operator' unconditionally must fail here."""
    header = "> from monitor:ABC · ok"
    assert derive_source(header)[0] == "monitor"
    assert derive_source(header.replace("> from ", "")) [0] == "operator"


def test_sidecar_records_are_merged_in_timestamp_order(tmp_path):
    sessions = tmp_path / "sessions"; sessions.mkdir()
    backfill = tmp_path / "backfill"; backfill.mkdir()
    log = sessions / "s1.jsonl"
    _write(log, [
        {"v": 1, "aegis_ts": "2026-07-01T10:00:00Z",
         "event": {"t": "SessionMeta", "handle": "h1", "cwd": "/w"}},
        {"v": 1, "aegis_ts": "2026-07-01T10:00:10Z",
         "event": {"t": "AssistantText", "text": "answer"}},
    ])
    _write(backfill / "s1.jsonl", [
        {"v": 1, "aegis_ts": "2026-07-01T10:00:05Z",
         "event": {"t": "UserMessage", "text": "question", "source": "operator"}},
    ])
    events, meta = read_log(log, backfill)
    kinds = [e["t"] for e, _ in events]
    assert kinds == ["SessionMeta", "UserMessage", "AssistantText"]
    assert meta["handle"] == "h1"


def test_exchanges_for_log_pairs_sidecar_question_with_log_answer(tmp_path):
    sessions = tmp_path / "sessions"; sessions.mkdir()
    backfill = tmp_path / "backfill"; backfill.mkdir()
    log = sessions / "s1.jsonl"
    _write(log, [
        {"v": 1, "aegis_ts": "2026-07-01T10:00:00Z",
         "event": {"t": "SessionMeta", "handle": "h1", "cwd": "/w"}},
        {"v": 1, "aegis_ts": "2026-07-01T10:00:10Z",
         "event": {"t": "AssistantText", "text": "answer"}},
    ])
    _write(backfill / "s1.jsonl", [
        {"v": 1, "aegis_ts": "2026-07-01T10:00:05Z",
         "event": {"t": "UserMessage", "text": "question", "source": "operator"}},
    ])
    ex = exchanges_for_log(log, backfill)
    assert len(ex) == 1
    assert ex[0].operator_text == "question"
    assert ex[0].assistant_text == "answer"


def test_missing_sidecar_is_not_an_error(tmp_path):
    sessions = tmp_path / "sessions"; sessions.mkdir()
    log = sessions / "s2.jsonl"
    _write(log, [{"v": 1, "aegis_ts": "2026-07-01T10:00:00Z",
                  "event": {"t": "UserMessage", "text": "hi", "source": "operator"}}])
    assert len(exchanges_for_log(log, tmp_path / "nope")) == 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run python -m pytest tests/test_corpus_source.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'aegis.corpus.source'`

- [ ] **Step 3: Write minimal implementation**

```python
# src/aegis/corpus/source.py
"""Read a session log, merged with its backfill sidecar, as (event, ts) pairs.

Sidecars live in `state/backfill/`, never in `state/sessions/` — three
separate call sites glob `sessions/*.jsonl` and would read a sidecar back as
a session log. The merge happens here, at read time, so an existing log is
never rewritten.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

from aegis.corpus.extract import Exchange, extract_exchanges

_FROM_RE = re.compile(r"^>\s*from\s+([a-z]+)(?::([^\s·]+))?", re.I)
_KNOWN = {"queue", "agent", "monitor", "canvas", "term",
          "workflow", "loop", "reminder"}
_HARNESS_PREFIXES = (
    "<task-notification>", "<system-reminder>", "<command-name>",
    "<local-command-stdout>", "<user-prompt-submit-hook>",
    "Base directory for this skill:",
)


def derive_source(text: str) -> tuple[str, str | None]:
    """Provenance from the substrate's own header. Import path only —
    the live path uses the pending-send table (VS2), because an operator
    can legitimately type a line starting with '> from'."""
    s = (text or "").lstrip()
    m = _FROM_RE.match(s)
    if m:
        kind = m.group(1).lower()
        return (kind if kind in _KNOWN else "substrate"), m.group(2)
    if s.startswith(_HARNESS_PREFIXES):
        return "harness", None
    return "operator", None


def _iter_records(path: Path):
    if not path.exists():
        return
    with path.open(errors="replace") as fh:
        for line in fh:
            try:
                yield json.loads(line)
            except Exception:
                continue


def read_log(log_path: Path, backfill_dir: Path | None):
    """-> ([(event_dict, aegis_ts), ...] in timestamp order, meta dict)"""
    records = list(_iter_records(log_path))
    if backfill_dir is not None:
        records += list(_iter_records(Path(backfill_dir) / log_path.name))

    records.sort(key=lambda r: r.get("aegis_ts") or "")

    meta: dict = {}
    events: list[tuple[dict, str]] = []
    for r in records:
        ev = r.get("event") or {}
        ts = r.get("aegis_ts") or ""
        if ev.get("t") == "SessionMeta":
            meta = {"handle": ev.get("handle"), "cwd": ev.get("cwd"),
                    "profile": ev.get("profile"), "host": ev.get("host")}
        if ev.get("t") == "UserMessage" and not ev.get("source"):
            src, sender = derive_source(ev.get("text", ""))
            ev = {**ev, "source": src, "sender": sender}
        events.append((ev, ts))
    return events, meta


def exchanges_for_log(log_path: Path, backfill_dir: Path | None) -> list[Exchange]:
    events, meta = read_log(Path(log_path), backfill_dir)
    return extract_exchanges(events, meta)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run python -m pytest tests/test_corpus_source.py -q`
Expected: 5 passed

- [ ] **Step 5: Commit**

```bash
git add src/aegis/corpus/source.py tests/test_corpus_source.py
git commit -m "feat(corpus): merge session logs with backfill sidecars at read time"
```

---

### Task 3: Incremental index into beaver

**Files:**
- Modify: `pyproject.toml` (add `beaver-db>=2.3` to `dependencies`)
- Create: `src/aegis/corpus/index.py`
- Test: `tests/test_corpus_index.py`

**Interfaces:**
- Consumes: `exchanges_for_log` from Task 2
- Produces: `ExchangeDoc` (pydantic model); `async def index_state_dir(state_dir: Path, *, rebuild: bool = False) -> dict`; `async def open_corpus(state_dir: Path)`; `corpus_db_path(state_dir) -> Path`; collection name constant `COLLECTION = "exchanges"`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_corpus_index.py
import json
import pytest
from pathlib import Path
from aegis.corpus.index import index_state_dir, open_corpus, COLLECTION


def _session(state: Path, name: str, records):
    d = state / "sessions"; d.mkdir(parents=True, exist_ok=True)
    p = d / name
    with p.open("a") as fh:
        for r in records:
            fh.write(json.dumps(r) + "\n")
    return p


def _um(ts, text):
    return {"v": 1, "aegis_ts": ts,
            "event": {"t": "UserMessage", "text": text, "source": "operator"}}


@pytest.mark.asyncio
async def test_indexes_exchanges_and_reports_counts(tmp_path):
    _session(tmp_path, "s1.jsonl", [_um("2026-07-01T10:00:00Z", "the geocoder tarball")])
    stats = await index_state_dir(tmp_path)
    assert stats["logs_read"] == 1
    assert stats["exchanges_indexed"] == 1


@pytest.mark.asyncio
async def test_second_run_reads_nothing_when_no_log_grew(tmp_path):
    _session(tmp_path, "s1.jsonl", [_um("2026-07-01T10:00:00Z", "first")])
    await index_state_dir(tmp_path)
    stats = await index_state_dir(tmp_path)
    assert stats["logs_read"] == 0, "watermark must skip unchanged logs"


@pytest.mark.asyncio
async def test_appended_log_is_reindexed(tmp_path):
    p = _session(tmp_path, "s1.jsonl", [_um("2026-07-01T10:00:00Z", "first")])
    await index_state_dir(tmp_path)
    _session(tmp_path, "s1.jsonl", [_um("2026-07-01T11:00:00Z", "second")])
    stats = await index_state_dir(tmp_path)
    assert stats["logs_read"] == 1
    db, col = await open_corpus(tmp_path)
    assert await col.count() == 2
    await db.close()


@pytest.mark.asyncio
async def test_rebuild_clears_and_reindexes(tmp_path):
    _session(tmp_path, "s1.jsonl", [_um("2026-07-01T10:00:00Z", "first")])
    await index_state_dir(tmp_path)
    stats = await index_state_dir(tmp_path, rebuild=True)
    assert stats["logs_read"] == 1
    db, col = await open_corpus(tmp_path)
    assert await col.count() == 1, "rebuild must not duplicate"
    await db.close()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run python -m pytest tests/test_corpus_index.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'aegis.corpus.index'`

- [ ] **Step 3: Add the dependency**

In `pyproject.toml`, add to the `dependencies` list (keep it alphabetical):

```toml
    "beaver-db>=2.3",
```

Then: `uv lock && uv pip install -e .`

- [ ] **Step 4: Write minimal implementation**

```python
# src/aegis/corpus/index.py
"""Incremental index of exchanges into a beaver document collection.

The watermark is exact rather than heuristic: session logs are append-only,
so a file whose size is unchanged cannot contain new events.
"""
from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path

from beaver import AsyncBeaverDB, Document
from pydantic import BaseModel

from aegis.corpus.source import exchanges_for_log

COLLECTION = "exchanges"
_DB_NAME = "corpus.db"
_WATERMARK = "corpus_watermarks"


class ExchangeDoc(BaseModel):
    """The indexed shape. A pydantic body (rather than a plain string) is what
    makes beaver return usable BM25 — a string body scores every hit at
    ~-1e-06, a model body separates them properly."""
    handle: str = ""
    cwd: str = ""
    ts_start: str = ""
    ts_end: str = ""
    source: str = ""
    operator_text: str = ""
    assistant_text: str = ""
    files: str = ""      # space-joined: FTS-matchable alongside the prose
    tools: str = ""
    friction: str = ""


def corpus_db_path(state_dir: Path) -> Path:
    return Path(state_dir) / _DB_NAME


async def open_corpus(state_dir: Path):
    """-> (db, collection). Caller must `await db.close()`."""
    db = AsyncBeaverDB(str(corpus_db_path(state_dir)))
    await db.connect()
    return db, db.docs(COLLECTION, model=ExchangeDoc)


def _to_doc(ex) -> ExchangeDoc:
    return ExchangeDoc(
        handle=ex.handle or "", cwd=ex.cwd or "",
        ts_start=ex.ts_start, ts_end=ex.ts_end, source=ex.source,
        operator_text=ex.operator_text, assistant_text=ex.assistant_text,
        files=" ".join(ex.files_touched), tools=" ".join(ex.tools_used),
        friction=" ".join(ex.friction),
    )


async def index_state_dir(state_dir: Path, *, rebuild: bool = False) -> dict:
    state_dir = Path(state_dir)
    sessions = state_dir / "sessions"
    backfill = state_dir / "backfill"

    db, col = await open_corpus(state_dir)
    marks = db.dict(_WATERMARK)
    try:
        if rebuild:
            await col.clear()
            marks.clear()

        stats = {"logs_read": 0, "exchanges_indexed": 0, "logs_skipped": 0}
        if not sessions.is_dir():
            return stats

        for p in sorted(sessions.glob("*.jsonl")):
            try:
                size = p.stat().st_size
            except OSError:
                continue
            if marks.get(p.name) == size:
                stats["logs_skipped"] += 1
                continue

            stats["logs_read"] += 1
            for ex in exchanges_for_log(p, backfill):
                await col.index(document=Document(id=ex.key, body=_to_doc(ex)))
                stats["exchanges_indexed"] += 1
            marks[p.name] = size
        return stats
    finally:
        await db.close()
```

Drop the now-unused `json` / `asdict` imports if your editor added them —
the payload rides in the pydantic body, not a JSON blob.

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run python -m pytest tests/test_corpus_index.py -q`
Expected: 4 passed

- [ ] **Step 6: Commit**

```bash
git add pyproject.toml uv.lock src/aegis/corpus/index.py tests/test_corpus_index.py
git commit -m "feat(corpus): incremental beaver index with exact append-only watermark"
```

---

### Task 4: Recall — sanitize, narrow, re-rank

**Files:**
- Create: `src/aegis/corpus/recall.py`
- Test: `tests/test_corpus_recall.py`

**Interfaces:**
- Consumes: `open_corpus`, `COLLECTION` from Task 3
- Produces: `sanitize_query(q: str) -> str`; `async def recall(state_dir, query, *, since=None, until=None, cwd=None, all_projects=False, exclude_handle=None, limit=5) -> list[dict]`; `async def expand(state_dir, exchange_id, before=1, after=1) -> dict`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_corpus_recall.py
import json
import pytest
from pathlib import Path
from aegis.corpus.index import index_state_dir
from aegis.corpus.recall import sanitize_query, recall, expand


def _session(state: Path, name: str, records):
    d = state / "sessions"; d.mkdir(parents=True, exist_ok=True)
    with (d / name).open("a") as fh:
        for r in records:
            fh.write(json.dumps(r) + "\n")


def _log(state, name, handle, cwd, turns):
    recs = [{"v": 1, "aegis_ts": "2026-07-01T09:00:00Z",
             "event": {"t": "SessionMeta", "handle": handle, "cwd": cwd}}]
    for ts, text in turns:
        recs.append({"v": 1, "aegis_ts": ts,
                     "event": {"t": "UserMessage", "text": text, "source": "operator"}})
    _session(state, name, recs)


def test_sanitize_strips_punctuation_into_or_terms():
    out = sanitize_query("how is ainbox pushed to registry.syalia.dev?")
    assert "." not in out and "?" not in out
    assert "registry" in out and "syalia" in out


@pytest.mark.asyncio
async def test_punctuation_query_does_not_raise(tmp_path):
    """beaver's FTS5 throws 'syntax error near .' on a raw query."""
    _log(tmp_path, "s1.jsonl", "h1", "/w",
         [("2026-07-01T10:00:00Z", "we push images to registry syalia dev")])
    await index_state_dir(tmp_path)
    hits = await recall(tmp_path, "how is ainbox pushed to registry.syalia.dev?",
                        all_projects=True)
    assert hits, "sanitized query must still match"


@pytest.mark.asyncio
async def test_stronger_match_ranks_first(tmp_path):
    _log(tmp_path, "s1.jsonl", "strong", "/w",
         [("2026-07-01T10:00:00Z", "registry registry registry push image registry")])
    _log(tmp_path, "s2.jsonl", "weak", "/w",
         [("2026-07-01T10:00:00Z", "a passing mention of the registry once")])
    await index_state_dir(tmp_path)
    hits = await recall(tmp_path, "registry", all_projects=True, limit=5)
    assert hits[0]["handle"] == "strong", "BM25 is negative — sort ascending"


@pytest.mark.asyncio
async def test_current_session_is_excluded(tmp_path):
    _log(tmp_path, "s1.jsonl", "me", "/w", [("2026-07-01T10:00:00Z", "registry talk")])
    _log(tmp_path, "s2.jsonl", "other", "/w", [("2026-07-01T10:00:00Z", "registry talk")])
    await index_state_dir(tmp_path)
    hits = await recall(tmp_path, "registry", all_projects=True, exclude_handle="me")
    assert all(h["handle"] != "me" for h in hits)


@pytest.mark.asyncio
async def test_cwd_scoping_and_escape_hatch(tmp_path):
    _log(tmp_path, "s1.jsonl", "a", "/w/one", [("2026-07-01T10:00:00Z", "registry talk")])
    _log(tmp_path, "s2.jsonl", "b", "/w/two", [("2026-07-01T10:00:00Z", "registry talk")])
    await index_state_dir(tmp_path)
    scoped = await recall(tmp_path, "registry", cwd="/w/one")
    assert {h["handle"] for h in scoped} == {"a"}
    every = await recall(tmp_path, "registry", cwd="/w/one", all_projects=True)
    assert {h["handle"] for h in every} == {"a", "b"}


@pytest.mark.asyncio
async def test_since_filter_excludes_older(tmp_path):
    _log(tmp_path, "s1.jsonl", "old", "/w", [("2026-06-01T10:00:00Z", "registry talk")])
    _log(tmp_path, "s2.jsonl", "new", "/w", [("2026-07-20T10:00:00Z", "registry talk")])
    await index_state_dir(tmp_path)
    hits = await recall(tmp_path, "registry", since="2026-07-01", all_projects=True)
    assert {h["handle"] for h in hits} == {"new"}


@pytest.mark.asyncio
async def test_expand_returns_full_text(tmp_path):
    _log(tmp_path, "s1.jsonl", "h", "/w", [("2026-07-01T10:00:00Z", "registry " + "x" * 900)])
    await index_state_dir(tmp_path)
    hits = await recall(tmp_path, "registry", all_projects=True)
    full = await expand(tmp_path, hits[0]["exchange_id"])
    assert len(full["operator_text"]) > len(hits[0]["operator_snippet"])
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run python -m pytest tests/test_corpus_recall.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'aegis.corpus.recall'`

- [ ] **Step 3: Write minimal implementation**

```python
# src/aegis/corpus/recall.py
"""Query the corpus: sanitize, narrow with FTS5, re-rank in Python.

beaver's FTS is used as a *candidate filter*, not as the ranking function.
Its sync `.search()` returns -0.0 for every hit, and even the async
`query().fts()` BM25 is a single flat number that ignores our facets — so we
take a generous candidate set and score it ourselves.
"""
from __future__ import annotations

import math
import re
from collections import Counter
from pathlib import Path

from aegis.corpus.index import open_corpus

_SNIPPET = 300
_CANDIDATES = 200
_STOP = {"how", "is", "are", "the", "to", "a", "of", "we", "do", "does",
         "did", "was", "were", "that", "this", "it", "in", "on", "for"}


def sanitize_query(q: str) -> str:
    """FTS5 rejects punctuation — 'registry.syalia.dev?' is a syntax error.
    Reduce to alphanumeric terms joined by OR so partial matches still land."""
    toks = [t for t in re.findall(r"[A-Za-z0-9_]+", q or "")]
    return " OR ".join(toks)


def _terms(q: str) -> list[str]:
    return [t.lower() for t in re.findall(r"[A-Za-z0-9_]+", q or "")
            if t.lower() not in _STOP and len(t) > 1]


def _score(blob: str, terms: list[str], df: Counter, n: int) -> tuple[float, int]:
    blob = blob.lower()
    total, hit = 0.0, 0
    for t in terms:
        c = blob.count(t)
        if c:
            hit += 1
            total += (1 + math.log(c)) * math.log(n / (1 + df[t]))
    return total * (1 + 0.35 * hit), hit


async def recall(state_dir, query, *, since=None, until=None, cwd=None,
                 all_projects=False, exclude_handle=None, limit=5) -> list[dict]:
    fts = sanitize_query(query)
    if not fts:
        return []
    terms = _terms(query)

    db, col = await open_corpus(Path(state_dir))
    try:
        rows = await col.query().fts(fts).limit(_CANDIDATES).execute()

        # Filtering happens here, not in beaver: `.where(Model.field == x)`
        # raises AttributeError on pydantic v2.
        kept = []
        for r in rows:
            b = r.document.body
            if exclude_handle and b.handle == exclude_handle:
                continue
            if not all_projects and cwd and b.cwd != cwd:
                continue
            if since and b.ts_start < since:
                continue
            if until and b.ts_start > until:
                continue
            kept.append((r.document.id, b))

        n = max(len(kept), 1)
        df = Counter()
        blobs = []
        for _id, b in kept:
            blob = " ".join([b.operator_text, b.assistant_text, b.files])
            blobs.append(blob)
            low = blob.lower()
            for t in set(terms):
                if t in low:
                    df[t] += 1

        scored = []
        for (doc_id, b), blob in zip(kept, blobs):
            s, hit = _score(blob, terms, df, n)
            if hit:
                scored.append((s, doc_id, b))
        scored.sort(key=lambda x: -x[0])

        return [{
            "exchange_id": doc_id,
            "handle": b.handle,
            "ts": b.ts_start,
            "cwd": b.cwd,
            "score": round(s, 3),
            "operator_snippet": b.operator_text[:_SNIPPET],
            "assistant_snippet": b.assistant_text[:_SNIPPET],
            "files": b.files.split()[:5],
        } for s, doc_id, b in scored[:limit]]
    finally:
        await db.close()


async def expand(state_dir, exchange_id, before=1, after=1) -> dict:
    """Full text of one exchange. `before`/`after` are accepted now and wired
    to real neighbour lookup in VS3, when the index carries session ordering."""
    db, col = await open_corpus(Path(state_dir))
    try:
        doc = await col.get(exchange_id)
        return doc.body.model_dump()
    finally:
        await db.close()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run python -m pytest tests/test_corpus_recall.py -q`
Expected: 7 passed

- [ ] **Step 5: Commit**

```bash
git add src/aegis/corpus/recall.py tests/test_corpus_recall.py
git commit -m "feat(corpus): recall with FTS candidate narrowing and python re-ranking"
```

---

### Task 5: The `aegis history index` CLI command

**Files:**
- Modify: `src/aegis/cli.py`
- Test: `tests/test_corpus_index.py` (append)

**Interfaces:**
- Consumes: `index_state_dir` from Task 3
- Produces: `aegis history index [--rebuild]` on the typer app

- [ ] **Step 1: Write the failing test**

```python
# append to tests/test_corpus_index.py
from typer.testing import CliRunner
from aegis.cli import app


def test_history_index_command_reports_counts(tmp_path, monkeypatch):
    d = tmp_path / "sessions"; d.mkdir(parents=True)
    (d / "s1.jsonl").write_text(json.dumps({
        "v": 1, "aegis_ts": "2026-07-01T10:00:00Z",
        "event": {"t": "UserMessage", "text": "hello corpus", "source": "operator"},
    }) + "\n")
    monkeypatch.setenv("AEGIS_STATE_DIR", str(tmp_path))
    res = CliRunner().invoke(app, ["history", "index"])
    assert res.exit_code == 0, res.output
    assert "exchanges" in res.output.lower()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run python -m pytest tests/test_corpus_index.py::test_history_index_command_reports_counts -q`
Expected: FAIL — no such command `history`

- [ ] **Step 3: Write minimal implementation**

Read `src/aegis/cli.py` first to match how the existing sub-apps (`aegis schedule`, `aegis models`, `aegis plugin`) are registered — mirror that pattern exactly rather than inventing a new one. Then add:

```python
# src/aegis/cli.py  — near the other typer sub-apps
history_app = typer.Typer(help="Query and maintain the conversational corpus.")
app.add_typer(history_app, name="history")


@history_app.command("index")
def history_index(
    rebuild: bool = typer.Option(False, "--rebuild",
                                 help="Drop the index and rebuild from scratch."),
) -> None:
    """Index session logs (merged with backfill sidecars) for recall."""
    import asyncio
    import os

    from aegis.config import find_project_root
    from aegis.corpus.index import index_state_dir
    from aegis.state.workspace import state_dir

    override = os.environ.get("AEGIS_STATE_DIR")
    sd = Path(override) if override else state_dir(find_project_root() or Path.cwd())
    stats = asyncio.run(index_state_dir(sd, rebuild=rebuild))
    typer.echo(
        f"logs read {stats['logs_read']}, skipped {stats['logs_skipped']}, "
        f"exchanges indexed {stats['exchanges_indexed']}"
    )
```

`find_project_root` and `state_dir` are the existing helpers (`aegis/config.py`
and `aegis/state/workspace.py:67`, where `state_dir(cwd) -> cwd/".aegis"/"state"`).
`cli.py:159` already uses the `find_project_root() or Path.cwd()` idiom — match
it rather than adding a second resolution path.

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run python -m pytest tests/test_corpus_index.py -q`
Expected: 5 passed

- [ ] **Step 5: Commit**

```bash
git add src/aegis/cli.py tests/test_corpus_index.py
git commit -m "feat(cli): aegis history index"
```

---

### Task 6: The `aegis_recall` and `aegis_recall_expand` MCP tools

**Files:**
- Modify: `src/aegis/mcp/server.py`
- Test: `tests/test_corpus_recall.py` (append)

**Interfaces:**
- Consumes: `recall`, `expand` from Task 4
- Produces: two `@server.tool` coroutines registered on the MCP server

- [ ] **Step 1: Write the failing test**

```python
# append to tests/test_corpus_recall.py
def test_recall_tools_are_registered():
    """Guards against the tool existing but never being wired up."""
    import inspect
    from aegis.mcp import server as srv
    src = inspect.getsource(srv)
    assert "async def aegis_recall(" in src
    assert "async def aegis_recall_expand(" in src
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run python -m pytest tests/test_corpus_recall.py::test_recall_tools_are_registered -q`
Expected: FAIL — assertion error, `aegis_recall` not in source

- [ ] **Step 3: Write minimal implementation**

In `src/aegis/mcp/server.py`, alongside the other `@server.tool` definitions (see `aegis_monitors` around line 1327 for the house pattern):

```python
    @server.tool
    async def aegis_recall(
        query: str,
        from_handle: str | None = None,
        since: str | None = None,
        until: str | None = None,
        cwd: str | None = None,
        all_projects: bool = False,
        limit: int = 5,
    ) -> list[dict]:
        """Search past conversations. Returns ranked exchange snippets.

        Scoped to the calling session's cwd unless `all_projects=True`, and
        the calling session is excluded from its own results. Pass `since`
        as an ISO date ('2026-07-01') — translate "a few weeks ago" yourself.
        Follow up with aegis_recall_expand(exchange_id) to read one in full.
        """
        from pathlib import Path

        from aegis.config import find_project_root
        from aegis.corpus.recall import recall as _recall
        from aegis.state.workspace import state_dir

        root = find_project_root() or Path.cwd()
        return await _recall(
            state_dir(root), query,
            since=since, until=until,
            cwd=cwd or str(root),
            all_projects=all_projects,
            exclude_handle=from_handle,
            limit=limit,
        )

    @server.tool
    async def aegis_recall_expand(
        exchange_id: str, before: int = 1, after: int = 1,
    ) -> dict:
        """Read one exchange from aegis_recall in full, with its neighbours."""
        from pathlib import Path

        from aegis.config import find_project_root
        from aegis.corpus.recall import expand as _expand
        from aegis.state.workspace import state_dir

        root = find_project_root() or Path.cwd()
        return await _expand(state_dir(root), exchange_id,
                             before=before, after=after)
```

The bridge exposes no state-dir attribute — `server.py:565` and `:602` already
resolve the root inline with `find_project_root()` inside the tool body. Match
that pattern; do not add new attributes to `bridge`.

- [ ] **Step 4: Run the full corpus suite**

Run: `uv run python -m pytest tests/test_corpus_*.py -q`
Expected: all pass

- [ ] **Step 5: Run the whole hermetic suite for regressions**

Run: `uv run python -m pytest -q -m "not live"`
Expected: no new failures. A red run is a regression to investigate, not noise to re-roll.

- [ ] **Step 6: Commit**

```bash
git add src/aegis/mcp/server.py tests/test_corpus_recall.py
git commit -m "feat(mcp): aegis_recall and aegis_recall_expand"
```

---

### Task 7: End-to-end validation against the real corpus

**Files:**
- Create: `tests/test_corpus_live.py`

**Interfaces:**
- Consumes: everything above.

This is the only test that touches the real state dir. It is marked `live` and
skips when the corpus is absent, so CI and fresh clones stay green.

- [ ] **Step 1: Write the test**

```python
# tests/test_corpus_live.py
import pytest
from pathlib import Path
from aegis.corpus.index import index_state_dir
from aegis.corpus.recall import recall

STATE = Path("/home/apiad/Workspace/.aegis/state")

pytestmark = pytest.mark.live


@pytest.mark.asyncio
async def test_real_corpus_answers_the_registry_question():
    if not (STATE / "sessions").is_dir():
        pytest.skip("no local corpus")
    await index_state_dir(STATE)
    hits = await recall(STATE, "how is ainbox pushed to registry.syalia.dev?",
                        all_projects=True, limit=8)
    assert hits, "the corpus demonstrably contains this conversation"
    blob = " ".join((h["operator_snippet"] + h["assistant_snippet"]).lower()
                    for h in hits)
    assert "registry" in blob
```

- [ ] **Step 2: Run it**

Run: `uv run python -m pytest tests/test_corpus_live.py -q`
Expected: PASS (or skip on a machine without the corpus)

- [ ] **Step 3: Commit**

```bash
git add tests/test_corpus_live.py
git commit -m "test(corpus): live end-to-end recall against the real ledger"
```

---

## Out of scope for VS1

Deliberately deferred, each with its own slice in the spec:

- **VS2** — provenance at source (pending-send table), `Interrupted` / `TurnAborted` events, `SessionMeta.host`. VS1 derives provenance from headers at read time, which is sound for import and good enough to rank.
- **VS3** — `aegis history export` for the mining path.
- **VS4** — embeddings and RRF fusion.
- Automatic indexing on `SessionClosed`. VS1 indexes on demand via the CLI; wiring the hook is one line once the index is proven.
