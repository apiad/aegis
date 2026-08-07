---
title: Session titles — slice 1 (storage + manual set)
date: 2026-08-07
status: ready
spec: docs/superpowers/specs/2026-07-30-aegis-session-titles-design.md
slice: 1 of 4
---

# Session titles — slice 1 implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A session carries a human- or agent-settable `title` beside its handle, persisted in the transcript, surviving a restart, with `human > agent > auto` precedence enforced — and **not one LLM call anywhere in this slice**.

**Architecture:** The title rides the existing `SessionMeta` append-only mutation record (the same mechanism a rename already uses), so persistence costs one new encode/decode pair and no new store. Precedence is a pure ranking function; the live value lives on `AgentSession` because both `AppBridge` implementations (`AegisApp` panes and `SessionManager` sessions) hold one. Surfaces are `/title` (human), `aegis_title` (agent), and an optional `title=` on `aegis_rename`.

**Tech Stack:** Python 3.13+, `uv`, pytest, Textual 8.x, FastMCP.

## Global Constraints

- Python 3.13+. Package management is `uv` — `uv run python -m pytest`, never bare `pip`/`pytest`.
- TDD: failing test first, minimal implementation, **commit per task**.
- Test selection uses `-m "not live"`. **Never `-k "not live"`** — it matches `live` as a substring and silently eats unrelated names (e.g. anything containing `deliver`).
- A failing test is a real failure, not a flake to re-roll (fixed in 0.25.0; see AGENTS.md § Tests).
- **Never rewrite an existing session log** — it is the only copy of that conversation. All writes are appends via `append_meta`.
- **No LLM call in this slice.** `generate()`, `text_generation:`, and auto-titling are slices 2–4 and must not appear here.
- Code, comments, identifiers and commit messages in English.
- Commit from inside the repo: `git -C /home/apiad/Workspace/repos/aegis ...`, and stage explicit paths — never `git add -A`.

## Ground truth (verified against `main` @ `8df5f94`, 2026-08-07)

Do not trust the spec's file attributions; these are the real ones.

| thing | real location |
|---|---|
| `SessionMeta` dataclass | `src/aegis/events.py:203-214` |
| encode | `src/aegis/state/event_codec.py:140-145` (hand-written dict) |
| decode | `src/aegis/state/event_codec.py:245-250` (`d.get("preview", "")` is the legacy pattern to copy) |
| append helper | `src/aegis/state/session_log.py:230` `append_meta(state_dir_path, log_id, meta)` |
| history fold | `src/aegis/state/history.py:_fold_file:62`, `_Fold:50`, `SessionHistoryRow:33` |
| fold disk cache | `src/aegis/state/history.py:INDEX_VERSION = 1` (line 130), `_fold_to_entry:158`, `_entry_to_fold:174` |
| rename (TUI) | `src/aegis/tui/app.py:1848 rename_handle`, `:1892 _record_rename` |
| rename (manager) | `src/aegis/core/manager.py:461 rename_handle` |
| `AppBridge` Protocol | `src/aegis/mcp/bridge.py:143` |
| `SessionInfo` | `src/aegis/mcp/bridge.py:10-29` |
| `/rename` etc. | `src/aegis/commands/builtins/session_ctl.py` |
| `Ctrl+R` row render | `src/aegis/tui/history.py:_row_label:66`, `_matches:130` |
| live session object | `src/aegis/core/session.py:AgentSession.__init__:55` |

**Two traps this plan is built around:**

1. **A rename must not wipe the title.** `_record_rename` (`tui/app.py:1892`) appends a `SessionMeta` with `preview=""` and every other field re-derived. If the fold takes "last `SessionMeta` wins" for title the way it does for handle, every `/rename` silently clears the title. The fold therefore takes the **last non-empty** title (Task 3), *and* `_record_rename` carries the current title forward (Task 4). Both, because either alone leaves a hole.
2. **The fold cache is on disk and versioned.** `history.py` persists folds to `history_index.json`; a cached entry lacking the new fields would render every pre-change session titleless forever. Bump `INDEX_VERSION` to `2` (Task 3) — that costs one cold re-read (~60s on a 615MB corpus, once) and is the honest option.

## File structure

| file | change | responsibility |
|---|---|---|
| `src/aegis/events.py` | modify | `SessionMeta` gains `title`, `title_source` |
| `src/aegis/state/event_codec.py` | modify | round-trip the two new fields, legacy-tolerant decode |
| `src/aegis/state/titles.py` | **create** | pure: `TITLE_SOURCES`, `outranks()`, `sanitize_title()` |
| `src/aegis/state/history.py` | modify | fold last-non-empty title into `SessionHistoryRow.title`; `INDEX_VERSION = 2` |
| `src/aegis/core/session.py` | modify | `AgentSession.title` / `.title_source` live state |
| `src/aegis/mcp/bridge.py` | modify | `AppBridge.set_title(...)`; `SessionInfo.title` |
| `src/aegis/core/manager.py` | modify | `SessionManager.set_title`; `title=` on `rename_handle`; `SessionInfo.title` |
| `src/aegis/tui/app.py` | modify | `AegisApp.set_title` + `_record_title`; title-preserving `_record_rename`; `title=` on `rename_handle` |
| `src/aegis/tui/remote_manager.py` | modify | `set_title` RPC passthrough |
| `src/aegis/web/wssession.py` | modify | `set_title` RPC dispatch; `title` on `rename_handle` |
| `src/aegis/commands/builtins/session_ctl.py` | modify | `/title` |
| `src/aegis/mcp/server.py` | modify | `aegis_title` tool; `title=` on `aegis_rename`; BRIEFING line |
| `src/aegis/tui/history.py` | modify | `Ctrl+R` shows and searches the title |
| `tests/test_session_titles.py` | **create** | precedence + sanitizer + `set_title` |
| `tests/test_session_meta_event.py` | modify | codec round-trip + legacy decode |
| `tests/test_history_reader.py` | modify | fold behaviour incl. the rename trap |
| `tests/test_history_index.py` | modify | title survives the fold cache |
| `tests/test_history_modal.py` | modify | `Ctrl+R` label + filter |
| `tests/test_slash_commands.py` | modify | `/title` |
| `tests/test_mcp_server.py` | modify | `aegis_title` precedence refusals |
| `tests/test_tab_reorder.py` | modify | tab-cell decision from Task 8 |

---

### Task 1: `SessionMeta` carries a title

**Files:**
- Modify: `src/aegis/events.py:203-214`
- Modify: `src/aegis/state/event_codec.py:140-145`, `:245-250`
- Test: `tests/test_session_meta_event.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `SessionMeta(handle, profile, provider, cwd, created_at, origin, preview="", title="", title_source="")` — both new fields are `str`, default `""`. `title_source` is one of `"" | "auto" | "agent" | "human"`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_session_meta_event.py`:

```python
from aegis.events import SessionMeta
from aegis.state.event_codec import decode_event, encode_event


def _meta(**kw):
    base = dict(handle="lucid-knuth", profile="opus", provider="claude-code",
                cwd="/tmp", created_at="2026-08-07T10:00:00Z", origin="tui")
    return SessionMeta(**{**base, **kw})


def test_session_meta_defaults_have_no_title():
    m = _meta()
    assert m.title == ""
    assert m.title_source == ""


def test_session_meta_title_round_trips_through_the_codec():
    m = _meta(title="fix the eviction race", title_source="human")
    back = decode_event(encode_event(m))
    assert back == m


def test_legacy_session_meta_without_title_still_decodes():
    # A record written before titles existed: no title keys at all.
    legacy = {"t": "SessionMeta", "handle": "lucid-knuth", "profile": "opus",
              "provider": "claude-code", "cwd": "/tmp",
              "created_at": "2026-08-07T10:00:00Z", "origin": "tui",
              "preview": "hello"}
    back = decode_event(legacy)
    assert back.preview == "hello"
    assert back.title == ""
    assert back.title_source == ""
```

- [ ] **Step 2: Run the tests to verify they fail**

```bash
cd /home/apiad/Workspace/repos/aegis
uv run python -m pytest tests/test_session_meta_event.py -q -m "not live"
```

Expected: FAIL — `TypeError: SessionMeta.__init__() got an unexpected keyword argument 'title'`.

- [ ] **Step 3: Add the fields**

In `src/aegis/events.py`, extend the dataclass (keep the existing docstring):

```python
@dataclass(frozen=True)
class SessionMeta:
    """First record of a user-initiated session log — the gating header that
    makes a log show up in the Ctrl+H history. Substrate ephemera (queue
    workers, workflow spawns) skip this write."""
    handle: str
    profile: str
    provider: str
    cwd: str
    created_at: str
    origin: str
    preview: str = ""
    # A label, never an identity. The handle keeps doing routing and log-id
    # duty; this is what a human reads on a tab. `title_source` records who
    # set it so a late write can't clobber a more authoritative one — see
    # aegis.state.titles.outranks.
    title: str = ""
    title_source: str = ""   # "" | "auto" | "agent" | "human"
```

- [ ] **Step 4: Round-trip them in the codec**

In `src/aegis/state/event_codec.py`, extend the encode branch:

```python
    if isinstance(ev, SessionMeta):
        return {"t": "SessionMeta",
                "handle": ev.handle, "profile": ev.profile,
                "provider": ev.provider, "cwd": ev.cwd,
                "created_at": ev.created_at, "origin": ev.origin,
                "preview": ev.preview,
                "title": ev.title, "title_source": ev.title_source}
```

and the decode branch — `.get(..., "")` is the established legacy-tolerant pattern already used for `preview`:

```python
    if t == "SessionMeta":
        return SessionMeta(
            handle=d["handle"], profile=d["profile"],
            provider=d["provider"], cwd=d["cwd"],
            created_at=d["created_at"], origin=d["origin"],
            preview=d.get("preview", ""),
            title=d.get("title", ""),
            title_source=d.get("title_source", ""))
```

- [ ] **Step 5: Run the tests to verify they pass**

```bash
uv run python -m pytest tests/test_session_meta_event.py tests/test_state_event_codec.py -q -m "not live"
```

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git -C /home/apiad/Workspace/repos/aegis add src/aegis/events.py src/aegis/state/event_codec.py tests/test_session_meta_event.py
git -C /home/apiad/Workspace/repos/aegis commit -m "feat(titles): SessionMeta carries a title and its source"
```

---

### Task 2: precedence and sanitizing, as pure functions

**Files:**
- Create: `src/aegis/state/titles.py`
- Test: `tests/test_session_titles.py` (create)

**Interfaces:**
- Consumes: nothing.
- Produces:
  - `TITLE_RANK: dict[str, int]` — `{"": 0, "auto": 1, "agent": 2, "human": 3}`
  - `outranks(new_source: str, current_source: str) -> bool` — True when a write from `new_source` may overwrite a title currently set by `current_source`. Equal ranks are allowed (a human may retype their own title). Unknown sources rank 0.
  - `sanitize_title(text: str, *, cap: int = 32) -> str` — returns `""` when nothing usable survives.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_session_titles.py`:

```python
import pytest

from aegis.state.titles import TITLE_RANK, outranks, sanitize_title


@pytest.mark.parametrize("new,current,allowed", [
    # auto beats only "unset"
    ("auto", "", True),
    ("auto", "auto", True),
    ("auto", "agent", False),
    ("auto", "human", False),
    # agent beats auto and unset, not human
    ("agent", "", True),
    ("agent", "auto", True),
    ("agent", "agent", True),
    ("agent", "human", False),
    # human beats everything
    ("human", "", True),
    ("human", "auto", True),
    ("human", "agent", True),
    ("human", "human", True),
])
def test_precedence_is_human_over_agent_over_auto(new, current, allowed):
    assert outranks(new, current) is allowed


def test_unknown_sources_rank_lowest():
    assert TITLE_RANK.get("nonsense", 0) == 0
    assert outranks("nonsense", "auto") is False
    assert outranks("auto", "nonsense") is True


@pytest.mark.parametrize("raw,expected", [
    ("fix the eviction race", "fix the eviction race"),
    ("  padded  ", "padded"),
    ("first line\nsecond line", "first line"),
    ('"quoted"', "quoted"),
    ("`backticked`", "backticked"),
    ("'single'", "single"),
    ("collapse    inner   space", "collapse inner space"),
    ("trailing punctuation.", "trailing punctuation"),
    ("", ""),
    ("   ", ""),
    ("\n\n", ""),
])
def test_sanitizer_table(raw, expected):
    assert sanitize_title(raw) == expected


def test_sanitizer_truncates_on_a_word_boundary_with_an_ellipsis():
    out = sanitize_title("alpha beta gamma delta epsilon zeta", cap=20)
    assert len(out) <= 20
    assert out.endswith("…")
    # cut at a space, so no half-word before the ellipsis
    assert out == "alpha beta gamma…"


def test_sanitizer_truncates_a_single_long_word_hard():
    out = sanitize_title("x" * 100, cap=10)
    assert out == "x" * 9 + "…"
    assert len(out) == 10
```

- [ ] **Step 2: Run the tests to verify they fail**

```bash
uv run python -m pytest tests/test_session_titles.py -q -m "not live"
```

Expected: FAIL — `ModuleNotFoundError: No module named 'aegis.state.titles'`.

- [ ] **Step 3: Write the implementation**

Create `src/aegis/state/titles.py`:

```python
"""Title precedence and sanitizing — pure, no I/O.

A title is a label; the handle is identity. These two functions are the
whole concurrency story for titles: a write is applied only when its
source outranks (or equals) the current one, so a slow auto-generation
landing after the operator typed ``/title`` is simply discarded on
arrival. No request ids, no in-flight bookkeeping.
"""
from __future__ import annotations

TITLE_RANK: dict[str, int] = {"": 0, "auto": 1, "agent": 2, "human": 3}

# The tab cell already carries a state dot, an index, the handle, the slug
# and a muted suffix, and the bar scrolls sideways — so this is deliberately
# far below t3code's 50.
DEFAULT_CAP = 32

_STRIP_CHARS = "\"'`“”‘’ \t"


def outranks(new_source: str, current_source: str) -> bool:
    """May a write from ``new_source`` overwrite a title set by
    ``current_source``? Equal ranks may (a human retyping their own title
    is not a conflict). Unknown sources rank lowest."""
    return TITLE_RANK.get(new_source, 0) >= TITLE_RANK.get(current_source, 0)


def sanitize_title(text: str, *, cap: int = DEFAULT_CAP) -> str:
    """First line only, unwrapped, collapsed, capped. Returns "" when
    nothing usable survives — callers treat that as "leave it unset"
    rather than storing an empty label."""
    if not text:
        return ""
    line = text.strip().splitlines()[0] if text.strip() else ""
    line = line.strip(_STRIP_CHARS)
    line = " ".join(line.split())
    line = line.rstrip(".,;:!-–—")
    if not line:
        return ""
    if len(line) <= cap:
        return line
    head = line[:cap - 1]
    cut = head.rsplit(" ", 1)[0] if " " in head else head
    return f"{cut}…"
```

- [ ] **Step 4: Run the tests to verify they pass**

```bash
uv run python -m pytest tests/test_session_titles.py -q -m "not live"
```

Expected: PASS (23 tests).

- [ ] **Step 5: Mutation-check the sanitizer gate**

A table test that cannot fail is worth less than none. Temporarily break it and confirm red:

```bash
# make sanitize_title return `text` unchanged, then:
uv run python -m pytest tests/test_session_titles.py -q -m "not live"
```

Expected: FAIL on the multi-line, quoted and truncation cases. **Revert the break** before committing.

- [ ] **Step 6: Commit**

```bash
git -C /home/apiad/Workspace/repos/aegis add src/aegis/state/titles.py tests/test_session_titles.py
git -C /home/apiad/Workspace/repos/aegis commit -m "feat(titles): precedence ranking and title sanitizer"
```

---

### Task 3: the history fold reads titles back

**Files:**
- Modify: `src/aegis/state/history.py` — `_Fold:50`, `_fold_file:62`, `SessionHistoryRow:33`, `INDEX_VERSION:130`, `_fold_to_entry:158`, `_entry_to_fold:174`, row build `:239`
- Test: `tests/test_history_reader.py`, `tests/test_history_index.py`

**Interfaces:**
- Consumes: `SessionMeta.title` / `.title_source` (Task 1).
- Produces: `SessionHistoryRow.title: str = ""` and `SessionHistoryRow.title_source: str = ""`, populated from the **last non-empty** title across the log's `SessionMeta` records.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_history_reader.py`. That file already has `_meta(handle, ...)` and `_put(sd, handle, **kw)` helpers (lines 10-24) — extend `_meta`'s signature with `title: str = ""` / `title_source: str = ""` and pass them through, rather than adding a second helper:

```python
def test_history_row_carries_the_last_non_empty_title(tmp_path: Path):
    sd = tmp_path / "state"
    # Three headers: spawn (no title), a /title, then a plain rename.
    _put(sd, "lucid-knuth")
    _put(sd, "lucid-knuth", title="eviction race", title_source="human")
    append_meta(sd, "lucid-knuth", _meta("fix-eviction"))
    row, = list_history(sd, live_handles=set())
    # The rename wins the handle...
    assert row.handle == "fix-eviction"
    # ...but must NOT wipe the title.
    assert row.title == "eviction race"
    assert row.title_source == "human"


def test_history_row_has_no_title_when_none_was_ever_set(tmp_path: Path):
    sd = tmp_path / "state"
    _put(sd, "deep-dijkstra")
    row, = list_history(sd, live_handles=set())
    assert row.title == ""
    assert row.title_source == ""


def test_a_later_title_replaces_an_earlier_one(tmp_path: Path):
    sd = tmp_path / "state"
    _put(sd, "lucid-knuth", title="first", title_source="agent")
    _put(sd, "lucid-knuth", title="second", title_source="human")
    row, = list_history(sd, live_handles=set())
    assert row.title == "second"
    assert row.title_source == "human"
```

And in `tests/test_history_index.py`, which has its own `_meta(handle, preview)` and `_log(state_dir, log_id, handle, n)` helpers (lines 18-26) — extend `_meta` there the same way:

```python
def test_title_survives_the_fold_cache_round_trip(tmp_path: Path):
    sd = tmp_path / "state"
    append_event(sd, "20260807T100000000000Z-lucid-knuth",
                 _meta("lucid-knuth", title="eviction race",
                       title_source="human"))
    append_event(sd, "20260807T100000000000Z-lucid-knuth",
                 AssistantText(text="hi"))
    first, = list_history(sd, live_handles=set())   # cold: folds + saves
    second, = list_history(sd, live_handles=set())  # warm: from cache
    assert second.title == first.title == "eviction race"
    assert second.title_source == "human"


def test_a_stale_index_version_is_discarded(tmp_path: Path):
    from aegis.state.history import INDEX_NAME, INDEX_VERSION, _load_index
    (tmp_path / INDEX_NAME).write_text(
        '{"version": 1, "entries": {"x.jsonl": {"stamp": [0, 0]}}}',
        encoding="utf-8")
    assert INDEX_VERSION > 1
    assert _load_index(tmp_path) == {}
```

- [ ] **Step 2: Run the tests to verify they fail**

```bash
uv run python -m pytest tests/test_history_reader.py tests/test_history_index.py -q -m "not live"
```

Expected: FAIL — `AttributeError: 'SessionHistoryRow' object has no attribute 'title'`.

- [ ] **Step 3: Extend the row and the fold**

In `src/aegis/state/history.py`, add to `SessionHistoryRow` (after `inferred`, so existing positional construction is unaffected):

```python
    title: str = ""
    title_source: str = ""
```

Add to `_Fold`:

```python
    title: str = ""
    title_source: str = ""
```

In `_fold_file`, initialise beside the other accumulators:

```python
    title = ""
    title_source = ""
```

and inside the `isinstance(ev, SessionMeta)` branch, **after** the existing
`last_handle` / `preview` handling:

```python
                    # Last *non-empty* wins, not simply last: a rename
                    # appends a header with title="" and must not wipe a
                    # title the operator set (tui/app.py:_record_rename).
                    if ev.title:
                        title = ev.title
                        title_source = ev.title_source
```

Thread both into the `_Fold(...)` construction at the end of `_fold_file`.

- [ ] **Step 4: Carry them through the disk cache**

Bump the version at line 130 — a cached fold from before this change knows
nothing about titles, and silently serving it would leave every existing
session permanently titleless:

```python
INDEX_VERSION = 2
```

In `_fold_to_entry`, add to the returned dict:

```python
        "title": fold.title, "title_source": fold.title_source,
```

In `_entry_to_fold`, read them tolerantly (the surrounding `try` already
returns `None` on a malformed entry, which forces a re-fold):

```python
            title=entry.get("title", ""),
            title_source=entry.get("title_source", ""),
```

In the `SessionHistoryRow(...)` construction (~line 239), add:

```python
            title=fold.title,
            title_source=fold.title_source,
```

- [ ] **Step 5: Run the tests to verify they pass**

```bash
uv run python -m pytest tests/test_history_reader.py tests/test_history_index.py tests/test_history_modal.py tests/test_history_offthread.py tests/test_web_history.py -q -m "not live"
```

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git -C /home/apiad/Workspace/repos/aegis add src/aegis/state/history.py tests/test_history_reader.py tests/test_history_index.py
git -C /home/apiad/Workspace/repos/aegis commit -m "feat(titles): fold the last non-empty title into history rows"
```

---

### Task 4: live title state and the write path

**Files:**
- Modify: `src/aegis/core/session.py:55-80` (`AgentSession.__init__`)
- Modify: `src/aegis/mcp/bridge.py:10-29` (`SessionInfo`), `:143` (`AppBridge`)
- Modify: `src/aegis/core/manager.py:444`, `:461`
- Modify: `src/aegis/tui/app.py:1140,1514,1796` (`SessionInfo` sites), `:1848`, `:1892`
- Modify: `src/aegis/tui/remote_manager.py:286,326`
- Modify: `src/aegis/web/wssession.py:305`
- Test: `tests/test_session_titles.py` (extend)

**Interfaces:**
- Consumes: `outranks` (Task 2), `SessionMeta.title` (Task 1).
- Produces:
  - `AgentSession.title: str` and `AgentSession.title_source: str`, both `""` at spawn.
  - `AppBridge.set_title(handle: str, title: str, *, source: str) -> dict` — returns `{"ok": True, "handle":…, "title":…, "source":…}`, or `{"error": "..."}` when the write is outranked or the handle is unknown. Passing `title=""` **clears** the title (resetting `title_source` to `""`), which is how `/title` with no argument undoes a bad one.
  - `rename_handle(old, new, title=None)` on both implementations — `title=None` means "leave the title alone", which is exactly today's behaviour.
  - `SessionInfo.title: str = ""`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_session_titles.py`:

```python
import pytest

from aegis.state.titles import sanitize_title


@pytest.mark.asyncio
async def test_manager_set_title_records_source(manager_with_session):
    mgr, handle = manager_with_session
    res = await mgr.set_title(handle, "eviction race", source="human")
    assert res["ok"] is True
    assert mgr.get(handle).title == "eviction race"
    assert mgr.get(handle).title_source == "human"


@pytest.mark.asyncio
async def test_an_agent_cannot_overwrite_a_human_title(manager_with_session):
    mgr, handle = manager_with_session
    await mgr.set_title(handle, "operator wrote this", source="human")
    res = await mgr.set_title(handle, "agent wrote this", source="agent")
    assert "error" in res
    # The refusal says why, rather than failing silently.
    assert "human" in res["error"]
    assert mgr.get(handle).title == "operator wrote this"


@pytest.mark.asyncio
async def test_a_human_overwrites_an_agent_title(manager_with_session):
    mgr, handle = manager_with_session
    await mgr.set_title(handle, "agent wrote this", source="agent")
    res = await mgr.set_title(handle, "operator wrote this", source="human")
    assert res["ok"] is True
    assert mgr.get(handle).title == "operator wrote this"


@pytest.mark.asyncio
async def test_set_title_sanitizes_before_storing(manager_with_session):
    mgr, handle = manager_with_session
    await mgr.set_title(handle, '  "wrapped\nand long"  ', source="human")
    assert mgr.get(handle).title == sanitize_title('  "wrapped\nand long"  ')
    assert "\n" not in mgr.get(handle).title


@pytest.mark.asyncio
async def test_empty_title_clears_and_resets_the_source(manager_with_session):
    mgr, handle = manager_with_session
    await mgr.set_title(handle, "something", source="human")
    res = await mgr.set_title(handle, "", source="human")
    assert res["ok"] is True
    assert mgr.get(handle).title == ""
    assert mgr.get(handle).title_source == ""


@pytest.mark.asyncio
async def test_set_title_on_an_unknown_handle_errors(manager_with_session):
    mgr, _ = manager_with_session
    res = await mgr.set_title("no-such-agent", "x", source="human")
    assert "error" in res


@pytest.mark.asyncio
async def test_rename_preserves_the_title(manager_with_session):
    mgr, handle = manager_with_session
    await mgr.set_title(handle, "eviction race", source="human")
    await mgr.rename_handle(handle, "fix-eviction")
    assert mgr.get("fix-eviction").title == "eviction race"
    assert mgr.get("fix-eviction").title_source == "human"


@pytest.mark.asyncio
async def test_rename_can_set_a_title_in_one_call(manager_with_session):
    mgr, handle = manager_with_session
    res = await mgr.rename_handle(handle, "fix-eviction",
                                  title="eviction race")
    assert res["ok"] is True
    assert mgr.get("fix-eviction").title == "eviction race"
    assert mgr.get("fix-eviction").title_source == "agent"


@pytest.mark.asyncio
async def test_rename_title_does_not_override_a_human_one(
        manager_with_session):
    mgr, handle = manager_with_session
    await mgr.set_title(handle, "operator wrote this", source="human")
    res = await mgr.rename_handle(handle, "fix-eviction",
                                  title="agent wrote this")
    # The rename still succeeds; only the title write is declined.
    assert res["ok"] is True
    assert mgr.get("fix-eviction").title == "operator wrote this"
```

Add the fixture at the top of the file, copying the `FakeHarness` + `_mgr()`
idiom that `tests/test_rename_handle.py:13-29` already uses (a `SessionManager`
over a no-op harness, spawned synchronously with `_sync_spawn`):

```python
class FakeHarness:
    async def start(self): ...
    async def send(self, t): ...
    async def close(self): ...

    async def events(self):
        if False:
            yield


@pytest.fixture
def manager_with_session():
    """A SessionManager with one live session; yields (manager, handle)."""
    mgr = SessionManager(
        {"default": object()}, "default",
        make_session=lambda profile, url, handle: FakeHarness(),
        mcp=None, inbox=None)
    session = mgr._sync_spawn("default")
    return mgr, session.handle
```

- [ ] **Step 2: Run the tests to verify they fail**

```bash
uv run python -m pytest tests/test_session_titles.py -q -m "not live"
```

Expected: FAIL — `AttributeError: 'SessionManager' object has no attribute 'set_title'`.

- [ ] **Step 3: Add the live state**

In `src/aegis/core/session.py`, after `self.handle = handle` (line 66):

```python
        # A label, not an identity — see aegis.state.titles. The handle is
        # what routes; this is what a human reads. Empty until something
        # sets it.
        self.title: str = ""
        self.title_source: str = ""
```

- [ ] **Step 4: Add `set_title` to `SessionManager`**

In `src/aegis/core/manager.py`, beside `rename_handle`:

```python
    async def set_title(self, handle: str, title: str, *,
                        source: str) -> dict:
        """Set a session's display title, subject to source precedence.

        An empty ``title`` clears it (and the source with it), which is how
        ``/title`` with no argument undoes a bad manual one.
        """
        session = self.get(handle)
        if session is None:
            return {"error": f"no session {handle!r} (use aegis_list_sessions)"}
        if not outranks(source, session.title_source):
            return {"error":
                    f"title is set by {session.title_source!r} and "
                    f"{source!r} cannot overwrite it"}
        clean = sanitize_title(title)
        session.title = clean
        session.title_source = source if clean else ""
        return {"ok": True, "handle": handle, "title": clean,
                "source": session.title_source}
```

with `from aegis.state.titles import outranks, sanitize_title` at the top.

Extend `rename_handle`'s signature to `(self, old: str, new: str, title: str | None = None)`. After the successful `session.handle = new` assignment, apply the title as a *separate, non-fatal* step so a declined title never fails the rename:

```python
        if title is not None:
            await self.set_title(new, title, source="agent")
```

Add `title=s.title` to the `SessionInfo(...)` construction at line 444.

- [ ] **Step 5: Mirror it on the other bridges**

`src/aegis/mcp/bridge.py` — add to `SessionInfo` (after `plan`, so existing keyword construction is unaffected):

```python
    # Human-readable label beside the handle. "" when unset.
    title: str = ""
```

and to the `AppBridge` Protocol beside `rename_handle`:

```python
    async def rename_handle(self, old: str, new: str,
                            title: str | None = None) -> dict: ...
    async def set_title(self, handle: str, title: str, *,
                        source: str) -> dict: ...
```

`src/aegis/tui/app.py`:
- Extend `rename_handle` (`:1848`) with the same `title: str | None = None` parameter, applying it via `set_title` after `_record_rename`.
- Add `AegisApp.set_title`, mirroring the manager's logic but reading/writing `pane._core.title` / `.title_source` and appending a `SessionMeta` via a new `_record_title(pane)` helper (shape it on `_record_rename` at `:1892`).
- **Fix `_record_rename` to carry the title forward** — this is the trap:

```python
            append_meta(self._state_dir, pane.log_id, SessionMeta(
                handle=new_handle, profile=pane.agent_slug,
                provider=_provider_slug(pane), cwd=self._cwd,
                created_at=now_iso, origin="tui", preview="",
                # Re-state the current title. The fold takes the last
                # non-empty one, so omitting it here would be harmless —
                # but a header that contradicts live state is a trap for
                # the next reader, and doctor --repair reads these too.
                title=pane._core.title,
                title_source=pane._core.title_source))
```

- Add `title=...` to the three `SessionInfo(...)` sites (`:1140`, `:1514`, `:1796`), sourcing from the pane's `_core` where one exists and `""` otherwise.

`src/aegis/tui/remote_manager.py` — passthrough beside `rename_handle` (`:286`):

```python
    async def set_title(self, handle: str, title: str, *,
                        source: str) -> dict:
        return await self._ws.rpc("set_title", {"handle": handle,
                                                "title": title,
                                                "source": source})
```

and thread `title` through its `rename_handle`. Add `title=payload.get("title", "")` to the `SessionInfo(...)` at `:326`.

`src/aegis/web/wssession.py` — beside the `rename_handle` dispatch at `:305`:

```python
        if method == "set_title":
            return await self._m.set_title(
                params["handle"], params["title"],
                source=params.get("source", "human"))
```

and add `params.get("title")` to the existing `rename_handle` dispatch.

- [ ] **Step 6: Run the tests to verify they pass**

```bash
uv run python -m pytest tests/test_session_titles.py tests/test_rename_handle.py tests/test_core_manager.py tests/test_wssession_handoff_rename.py -q -m "not live"
```

Expected: PASS. `tests/test_rename_handle.py` and `tests/test_wssession_handoff_rename.py` are the existing regression guards for the rename path — they must stay green, since `rename_handle` gained a parameter.

- [ ] **Step 7: Commit**

```bash
git -C /home/apiad/Workspace/repos/aegis add src/aegis/core/session.py src/aegis/core/manager.py src/aegis/mcp/bridge.py src/aegis/tui/app.py src/aegis/tui/remote_manager.py src/aegis/web/wssession.py tests/test_session_titles.py
git -C /home/apiad/Workspace/repos/aegis commit -m "feat(titles): live title state, precedence-guarded writes, rename preserves it"
```

---

### Task 5: `/title` — the operator surface

**Files:**
- Modify: `src/aegis/commands/builtins/session_ctl.py`
- Test: `tests/test_slash_commands.py`

**Interfaces:**
- Consumes: `AppBridge.set_title` (Task 4).
- Produces: `/title [text]` — greedy-verbatim positional, so `/title fix the eviction race` needs no quoting. Bare `/title` clears (slice 3 will make it *regenerate* instead; until then, clearing is the honest behaviour and the docstring says so).

- [ ] **Step 1: Write the failing test**

In `tests/test_slash_commands.py`, first extend the existing `FakeBridge` (line 32) — add `self.titles = []` and `self.set_title_result = None` to `__init__`, and a method beside `rename_handle` (line 72):

```python
    async def set_title(self, handle, title, *, source):
        self.titles.append((handle, title, source))
        if self.set_title_result is not None:
            return self.set_title_result
        return {"ok": True, "handle": handle, "title": title,
                "source": source if title else ""}
```

Then append the tests, matching the file's existing `dispatch` style:

```python
async def test_title_sets_a_human_title():
    bridge = FakeBridge()
    res = await dispatch("/title fix the eviction race",
                         CommandContext(bridge=bridge, handle="me"))
    assert res.ok is True
    assert bridge.titles == [("me", "fix the eviction race", "human")]


async def test_bare_title_clears():
    bridge = FakeBridge()
    res = await dispatch("/title", CommandContext(bridge=bridge, handle="me"))
    assert res.ok is True
    assert bridge.titles == [("me", "", "human")]
    assert "cleared" in res.summary


async def test_title_surfaces_a_refusal():
    bridge = FakeBridge()
    bridge.set_title_result = {"error": "title is set by 'human' and "
                                        "'agent' cannot overwrite it"}
    res = await dispatch("/title nope",
                         CommandContext(bridge=bridge, handle="me"))
    assert res.ok is False
    assert "cannot overwrite" in (res.body or "")
```

> `CommandResult`'s two text fields are positional — check `commands/__init__.py`
> for whether the second is `body` or `detail`, and use the real name in both
> the test and the implementation.

- [ ] **Step 2: Run the test to verify it fails**

```bash
uv run python -m pytest tests/test_slash_commands.py -q -m "not live" -k title
```

Expected: FAIL — unknown command `/title`.

- [ ] **Step 3: Write the command**

In `src/aegis/commands/builtins/session_ctl.py`, beside `_rename`:

```python
async def _title(ctx: CommandContext, args) -> CommandResult:
    """Set the session's display title. Bare ``/title`` clears it.

    (Slice 3 turns the bare form into "regenerate"; until generation
    exists, clearing is what it honestly does.)
    """
    text = args.get("text") or ""
    res = await ctx.bridge.set_title(ctx.handle, text, source="human")
    if isinstance(res, dict) and res.get("error"):
        return CommandResult(False, "title rejected", res["error"])
    return (CommandResult(True, f"title → {res['title']}") if res.get("title")
            else CommandResult(True, "title cleared"))
```

and register it in the tuple at the bottom, beside `/rename`:

```python
    SlashCommand("title", "set the session's display title (bare: clear)",
                 "/title [text]", _title,
                 spec=ArgSpec(positionals=(
                     Arg("text", required=False, greedy=True),))),
```

> Check `commands/args.py` for the real name of the greedy-verbatim flag —
> 2A shipped it as part of `ArgSpec`; use whatever `Arg` actually exposes
> rather than inventing `greedy=True` if it differs.

- [ ] **Step 4: Run the tests to verify they pass**

```bash
uv run python -m pytest tests/test_slash_commands.py tests/test_command_complete.py -q -m "not live"
```

Expected: PASS. `/title` also appears in the drop-up palette for free (2D introspects the registry).

- [ ] **Step 5: Commit**

```bash
git -C /home/apiad/Workspace/repos/aegis add src/aegis/commands/builtins/session_ctl.py tests/test_slash_commands.py
git -C /home/apiad/Workspace/repos/aegis commit -m "feat(titles): /title sets the operator title"
```

---

### Task 6: `aegis_title` and `title=` on `aegis_rename`

**Files:**
- Modify: `src/aegis/mcp/server.py` — `_aegis_rename_impl:112`, the `aegis_rename` tool `:932`, BRIEFING `:151`
- Test: `tests/test_mcp_server.py`

**Interfaces:**
- Consumes: `AppBridge.set_title`, `rename_handle(…, title=…)` (Task 4).
- Produces:
  - `aegis_title(from_handle: str, title: str) -> dict` — an agent titles itself at `source="agent"`. Refused against a `human` title, with the reason in `{"error": …}`.
  - `aegis_rename(old_handle: str, new_handle: str, title: str | None = None) -> dict` — unchanged when `title` is omitted.

- [ ] **Step 1: Write the failing tests**

In `tests/test_mcp_server.py`, first give the existing `FakeBridge` (line 17) a title surface — it stores state rather than delegating, since precedence itself is unit-tested in Task 2 and Task 4:

```python
    async def set_title(self, handle, title, *, source):
        from aegis.state.titles import outranks, sanitize_title
        cur = self._titles.get(handle, ("", ""))
        if not outranks(source, cur[1]):
            return {"error": f"title is set by {cur[1]!r} and "
                             f"{source!r} cannot overwrite it"}
        clean = sanitize_title(title)
        self._titles[handle] = (clean, source if clean else "")
        return {"ok": True, "handle": handle, "title": clean,
                "source": self._titles[handle][1]}

    async def rename_handle(self, old, new, title=None):
        self.renamed = (old, new)
        self._titles[new] = self._titles.pop(old, ("", ""))
        if title is not None:
            await self.set_title(new, title, source="agent")
        return {"ok": True, "old": old, "new": new}
```

with `self._titles: dict[str, tuple[str, str]] = {}` in `__init__`. Then append,
using the file's existing `_call(server, name, **kwargs)` helper (line 70):

```python
async def test_aegis_title_sets_an_agent_title():
    br = FakeBridge()
    srv = build_server(br)
    out = await _call(srv, "aegis_title", from_handle="lucid-knuth",
                      title="eviction race")
    assert out["ok"] is True
    assert br._titles["lucid-knuth"] == ("eviction race", "agent")


async def test_aegis_title_is_refused_against_a_human_title():
    br = FakeBridge()
    await br.set_title("lucid-knuth", "operator wrote this", source="human")
    srv = build_server(br)
    out = await _call(srv, "aegis_title", from_handle="lucid-knuth",
                      title="agent wrote this")
    assert "error" in out
    assert "human" in out["error"]
    assert br._titles["lucid-knuth"][0] == "operator wrote this"


async def test_aegis_rename_without_a_title_is_unchanged():
    br = FakeBridge()
    srv = build_server(br)
    out = await _call(srv, "aegis_rename", old_handle="lucid-knuth",
                      new_handle="fix-eviction")
    assert out == {"ok": True, "old": "lucid-knuth", "new": "fix-eviction"}


async def test_aegis_rename_can_carry_a_title():
    br = FakeBridge()
    srv = build_server(br)
    out = await _call(srv, "aegis_rename", old_handle="lucid-knuth",
                      new_handle="fix-eviction", title="eviction race")
    assert out["ok"] is True
    assert br._titles["fix-eviction"] == ("eviction race", "agent")
```

Also extend `test_build_server_registers_all_aegis_tools` (line 104) with
`"aegis_title"` — that list is the registration guard.

- [ ] **Step 2: Run the tests to verify they fail**

```bash
uv run python -m pytest tests/test_mcp_server.py -q -m "not live" -k title
```

Expected: FAIL — no such tool `aegis_title`.

- [ ] **Step 3: Extend the impl and register the tool**

In `src/aegis/mcp/server.py`:

```python
async def _aegis_rename_impl(bridge, *, old_handle: str, new_handle: str,
                             title: str | None = None) -> dict:
    """Rename a live aegis session's handle in place."""
    return await bridge.rename_handle(old_handle, new_handle, title)


async def _aegis_title_impl(bridge, *, from_handle: str,
                            title: str) -> dict:
    """Set a session's display title at agent authority."""
    return await bridge.set_title(from_handle, title, source="agent")
```

Extend the `aegis_rename` tool signature with `title: str | None = None`, pass it through, and add its docstring line. Register the new tool beside it:

```python
    @server.tool
    async def aegis_title(from_handle: str, title: str) -> dict:
        """Give your session a human-readable title, beside your handle.

        The handle stays your identity — it is ``from_handle`` on every
        call, your inbox routing key, and half your log id. The title is
        only a label: it is what Alex reads on the tab and in the Ctrl+R
        history when ten sessions are open. Set it once the session's
        purpose has settled (3-8 words, e.g. "fix the eviction race").

        Refused when the operator has set a title by hand — theirs wins,
        and the refusal says so. Returns ``{"ok": True, ...}`` or
        ``{"error": "..."}``.
        """
        return await _aegis_title_impl(
            bridge, from_handle=from_handle, title=title)
```

Add one BRIEFING line after the `aegis_rename` entry (~`:151`):

```python
    "  - aegis_title(from_handle, title) : give your session a short "
    "human-readable title beside your handle (3-8 words, what you are "
    "actually doing). The handle stays your identity; the title is the "
    "label Alex reads on the tab. An operator-set title wins over yours.\n"
```

- [ ] **Step 4: Run the tests to verify they pass**

```bash
uv run python -m pytest tests/test_mcp_server.py tests/test_mcp_bridge.py -q -m "not live"
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git -C /home/apiad/Workspace/repos/aegis add src/aegis/mcp/server.py tests/test_mcp_server.py
git -C /home/apiad/Workspace/repos/aegis commit -m "feat(titles): aegis_title tool and title= on aegis_rename"
```

---

### Task 7: `Ctrl+R` shows and searches titles

**Files:**
- Modify: `src/aegis/tui/history.py:_row_label:66-77`, `_matches:130-135`
- Test: `tests/test_history_modal.py`

**Interfaces:**
- Consumes: `SessionHistoryRow.title` (Task 3).
- Produces: no new API — the row label prefers the title over the preview when present, and the filter matches on it.

- [ ] **Step 1: Write the failing tests**

`tests/test_history_modal.py` already has a `_row(handle, *, ...)` helper (line 10) that hardcodes `preview="hello"`. Give it `title: str = ""` and `preview: str = "hello"` keyword params and pass them through to `SessionHistoryRow`. Then append:

```python
from aegis.tui.history import HistoryModal, _row_label

_AGENTS = {"claude-sonnet"}
_RESUMABLE = {"claude-code"}


def test_row_label_prefers_the_title_over_the_preview():
    row = _row("lucid-knuth", title="eviction race",
               preview="hey can you look at the cache thing")
    label = _row_label(row, _AGENTS, _RESUMABLE)
    assert "eviction race" in label
    assert "hey can you look" not in label


def test_row_label_falls_back_to_the_preview_without_a_title():
    row = _row("lucid-knuth", title="",
               preview="hey can you look at the cache thing")
    label = _row_label(row, _AGENTS, _RESUMABLE)
    assert "hey can you look" in label


def test_the_filter_matches_on_the_title():
    # _matches touches no DOM, so the modal needs no running App here.
    modal = HistoryModal(
        [_row("lucid-knuth", title="eviction race",
              preview="unrelated words")],
        agents=_AGENTS, resume_capable_providers=_RESUMABLE)
    assert modal._matches(modal._rows[0], "eviction") is True
    assert modal._matches(modal._rows[0], "nonsense") is False
```

- [ ] **Step 2: Run the tests to verify they fail**

```bash
uv run python -m pytest tests/test_history_modal.py -q -m "not live" -k title
```

Expected: FAIL — the label carries the preview, not the title.

- [ ] **Step 3: Implement**

In `src/aegis/tui/history.py`, in `_row_label`, replace the preview line:

```python
    # The title is what the session is *about*; the preview is only the
    # first thing anyone said. Prefer the title when one exists.
    tail = (row.title or row.preview or "").replace("\n", " ")[:40]
```

and use `tail` in the returned f-string in place of `preview`.

In `_matches`, add the title to the haystack:

```python
        hay = " ".join(
            [row.handle, row.title, row.profile, row.cwd, row.preview]).lower()
```

- [ ] **Step 4: Run the tests to verify they pass**

```bash
uv run python -m pytest tests/test_history_modal.py -q -m "not live"
```

Expected: PASS.

- [ ] **Step 5: Run the whole hermetic suite**

This is the gate for the slice. Run it as its own tool call, and read the
exit code directly — **never pipe it** through `tail`/`grep`, which hands
`&&` the pipe's status and turns a red gate green:

```bash
cd /home/apiad/Workspace/repos/aegis && uv run python -m pytest -q -m "not live"
```

Expected: PASS, with no new failures against the pre-change baseline.

- [ ] **Step 6: Commit**

```bash
git -C /home/apiad/Workspace/repos/aegis add src/aegis/tui/history.py tests/test_history_modal.py
git -C /home/apiad/Workspace/repos/aegis commit -m "feat(titles): Ctrl+R shows and searches session titles"
```

---

### Task 8: drive it through a real TUI, then decide the tab bar

**Files:**
- Modify: `TASKS.md`, `CHANGELOG.md`, `docs/superpowers/specs/2026-07-30-aegis-session-titles-design.md` (status header + the stale TUI section)

**Interfaces:**
- Consumes: everything above.
- Produces: an answered open question and an honest status header.

**Why this task exists.** The live task list shipped two defects last week that
no unit test could see, both found by driving a real plan through a real pane.
Titles have exactly the same shape, and the spec's own open question —
*"Does the tab bar have room? A screenshot answers this, not a spec"* — is
still open. It is also **wrong about the mechanism**: the spec says
`worker_label` owns the suffix, but `_tab_suffix` (`tui/app.py:54-65`) now
composes plan roll-up + worker label + `@host`, so there is no free slot.

- [ ] **Step 1: Run a real aegis and exercise the surfaces**

```bash
cd /home/apiad/Workspace/repos/aegis && uv run aegis
```

In the TUI: `/title fix the eviction race`, then `Ctrl+R` (title visible and
searchable), then `/rename fix-eviction` (**title must survive**), then quit
and relaunch and `Ctrl+R` again (**title must survive the restart** — this is
the spec's own done-condition for slice 1).

- [ ] **Step 2: Confirm the precedence refusal end to end**

From a second aegis session, call `aegis_title` against the handle that has
the human title. Expected: a refusal naming `human`, and the title unchanged.

- [ ] **Step 3: Answer the tab-bar question**

Look at the tab bar with 4+ tabs open, at least one a queue worker with a plan.
Decide **one** and write the decision into the spec:

- title replaces the `·slug·` element in `_TabCell.render_tab`
  (`tui/widgets.py:184-192`) when set; or
- title goes into the suffix *only when* the suffix is otherwise empty; or
- title stays out of the tab bar entirely and lives in `Ctrl+R` + the status
  bar.

Implement whichever you picked, with a test in `tests/test_tab_reorder.py`
(the existing home for `TabBar` / `_TabCell` coverage) asserting the cell's
rendered element order and that a long title does not push the width past
its budget. Widths are measured in **cells, not `len()`** — one emoji is one
character and two columns; this is the same trap the plan dock paid for last
week.

- [ ] **Step 4: Update the docs**

- `docs/superpowers/specs/2026-07-30-aegis-session-titles-design.md`: flip
  `status: design` → `status: slice 1 implemented (<commit>)`; correct the
  TUI section's claim that `worker_label` owns the suffix; record the
  tab-bar decision from Step 3.
- `TASKS.md`: move *Session titles* from "specced, no plan" to slices 2–4
  outstanding, naming what slice 1 shipped.
- `CHANGELOG.md`: a `### Features` entry under the unreleased heading.

- [ ] **Step 5: Commit**

```bash
git -C /home/apiad/Workspace/repos/aegis add TASKS.md CHANGELOG.md docs/superpowers/specs/2026-07-30-aegis-session-titles-design.md src/aegis/tui/widgets.py tests/test_tui_widgets.py
git -C /home/apiad/Workspace/repos/aegis commit -m "docs(titles): slice 1 shipped; record the tab-bar decision"
git -C /home/apiad/Workspace/repos/aegis push origin main
```

---

## Out of scope (slices 2–4)

Named here so nobody builds them by accident:

- `HarnessDriver.supports_oneshot` / `generate(schema, *instructions)` — **slice 2**.
- The tolerant JSON parser for gemini/opencode, and the `lingo.Engine.create` reasoning-channel fallback — slice 2/4.
- `text_generation:` in `.aegis.yaml` — slice 2.
- First-turn auto-generation at `source="auto"`, and bare `/title` meaning *regenerate* rather than *clear* — **slice 3**.
- gemini / opencode / lovelaice `generate()` with live tests — slice 4.
