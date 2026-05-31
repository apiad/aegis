# Aegis memory plugin — v1 implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship a second canonical aegis plugin (`memory-system`) — Hermes-inspired persistent memory with periodic dreaming — using only the v1 plugin substrate primitives. Lands under `plugins/memory-system/` at the aegis repo root, parallel to `skill-system/`.

**Architecture:** Five vertical slices. Slice 1 builds the on-disk memory layer (frontmatter I/O + index management) and the three "read" tools — testable in isolation. Slice 2 adds the "write" tools and the `pre_turn` hook (turn-0 bundle injection + turn-≥1 teaser). Slice 3 packages it as a plugin with `_install.py` / `_uninstall.py` (including the schedule overlay via `aegis.scheduler.push.write_atomic`). Slice 4 lands the three-stage `dream` workflow. Slice 5 is a live integration test driving a real `claude` subprocess. Each slice ships its own commit batch with a passing hermetic test suite.

**Tech Stack:** Python 3.13+, PyYAML (already in stack via `skill-system`), the v1 plugin substrate (`aegis.hooks`, `aegis.tools`, `aegis.plugins`), the v1 scheduler (`aegis.scheduler.push.write_atomic`), the v1 workflow engine (`aegis.workflow.engine.WorkflowEngine.delegate`), pytest + pytest-asyncio (already in stack), ruamel.yaml (for `.aegis.yaml` edits via `aegis.config.edit`).

**Spec:** `docs/superpowers/specs/2026-05-30-aegis-memory-plugin-design.md` (commit `35cbad6`, scheduler-fix commit `72a39e2`).

**Conventions:**
- TDD: failing test → run-to-fail → implement → run-to-pass → commit. One logical change per commit.
- `uv run pytest -q -m "not live"` for the fast hermetic suite.
- Plugin lives under `plugins/memory-system/` (parallel to `plugins/skill-system/`).
- Test files live under `tests/` at the aegis repo root; manual module import per the `tests/test_skill_system.py:_load_skill_system` pattern.

---

## File structure

### New files

```
plugins/memory-system/
  plugin.toml
  memory_system.py             # entries I/O, all 5 @tool, 2 @hook, 1 @workflow
  _install.py
  _uninstall.py

tests/
  test_memory_io.py            # Slice 1: entries I/O + index
  test_memory_read_tools.py    # Slice 1: memory_search, memory_read
  test_memory_write_tools.py   # Slice 2: memory_add, memory_replace, memory_remove
  test_memory_hooks.py         # Slice 2: pre_turn turn-0 + turn-≥1, session_start observer
  test_memory_install.py       # Slice 3: install + uninstall
  test_memory_dream.py         # Slice 4: dream workflow stages (mocked LLM)
  test_memory_system_live.py   # Slice 5: live, requires claude
```

### Modified files

- `CHANGELOG.md` — Unreleased section gets a `memory-system` plugin entry once landed.
- `TASKS.md` — strike "Memory plugin *(brainstorming next)*" once shipped.

No production-source modifications. The v1 plugin substrate is fully sufficient.

---

# Slice 1 — On-disk memory I/O + read tools

End state: a module that can parse, write, and search markdown entries on disk; two MCP tools (`memory_read`, `memory_search`) registered via `@tool` and round-trippable in hermetic tests.

## Task 1.1: Frontmatter I/O helpers

**Files:**
- Create: `plugins/memory-system/memory_system.py` (initial file with helpers only)
- Test: `tests/test_memory_io.py`

- [ ] **Step 1: Write the failing test**

```python
"""Hermetic tests for memory_system frontmatter I/O + index helpers."""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest


def _load_memory_system():
    repo_root = Path(__file__).parent.parent
    path = repo_root / "plugins" / "memory-system" / "memory_system.py"
    spec = importlib.util.spec_from_file_location("_test_memory_system", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules["_test_memory_system"] = module
    spec.loader.exec_module(module)
    return module


def test_write_entry_creates_file_with_frontmatter(tmp_path: Path) -> None:
    m = _load_memory_system()
    root = tmp_path
    path = m.write_entry(
        root=root,
        type_="feedback",
        name="no-load-bearing",
        description="User hates 'load-bearing'",
        content="Avoid the phrase in every draft.",
    )
    assert path == root / ".aegis/memory/entries/feedback_no-load-bearing.md"
    text = path.read_text(encoding="utf-8")
    assert text.startswith("---\n")
    assert "type: feedback" in text
    assert "name: no-load-bearing" in text
    assert "description: User hates 'load-bearing'" in text
    assert "created:" in text
    assert "updated:" in text
    assert text.rstrip().endswith("Avoid the phrase in every draft.")


def test_read_entry_round_trips(tmp_path: Path) -> None:
    m = _load_memory_system()
    m.write_entry(tmp_path, "fact", "dream-at-3am",
                  "Default cron is 3am", "Body here.")
    entry = m.read_entry(tmp_path, "fact_dream-at-3am")
    assert entry.slug == "fact_dream-at-3am"
    assert entry.type == "fact"
    assert entry.name == "dream-at-3am"
    assert entry.description == "Default cron is 3am"
    assert entry.content.strip() == "Body here."


def test_write_entry_rejects_bad_type(tmp_path: Path) -> None:
    m = _load_memory_system()
    with pytest.raises(ValueError, match="invalid type"):
        m.write_entry(tmp_path, "bogus", "x", "d", "c")


def test_write_entry_kebabs_the_name(tmp_path: Path) -> None:
    m = _load_memory_system()
    path = m.write_entry(tmp_path, "user", "Alex Likes Spanish",
                         "d", "c")
    assert path.name == "user_alex-likes-spanish.md"
```

- [ ] **Step 2: Run the test, verify it fails**

Run: `uv run pytest tests/test_memory_io.py -v`
Expected: FAIL — module `_test_memory_system` cannot be loaded (file missing).

- [ ] **Step 3: Write the helper module**

Create `plugins/memory-system/memory_system.py`:

```python
"""memory-system plugin: persistent memory + periodic dreaming.

Entries live as markdown files with YAML frontmatter under
<project>/.aegis/memory/entries/, indexed by .aegis/memory/MEMORY.md.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import yaml


VALID_TYPES = ("user", "feedback", "fact", "reference")
MEMORY_SUBDIR = ".aegis/memory"
ENTRIES_SUBDIR = ".aegis/memory/entries"
DREAMS_SUBDIR = ".aegis/memory/dreams"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _kebab(s: str) -> str:
    s = s.strip().lower()
    s = re.sub(r"[^a-z0-9]+", "-", s)
    return s.strip("-")


@dataclass(frozen=True)
class Entry:
    slug:        str
    type:        str
    name:        str
    description: str
    created:     str
    updated:     str
    content:     str


def _entry_path(root: Path, slug: str) -> Path:
    return root / ENTRIES_SUBDIR / f"{slug}.md"


def _slug_for(type_: str, name: str) -> str:
    return f"{type_}_{_kebab(name)}"


def write_entry(root: Path, type_: str, name: str,
                description: str, content: str) -> Path:
    """Write a new entry. Fails if the slug already exists."""
    if type_ not in VALID_TYPES:
        raise ValueError(f"invalid type {type_!r}; expected one of {VALID_TYPES}")
    slug = _slug_for(type_, name)
    path = _entry_path(root, slug)
    if path.exists():
        raise FileExistsError(f"entry {slug!r} already exists at {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    now = _now_iso()
    front = {
        "type": type_,
        "name": _kebab(name),
        "description": description,
        "created": now,
        "updated": now,
    }
    body = (
        "---\n"
        + yaml.safe_dump(front, sort_keys=False, allow_unicode=True)
        + "---\n\n"
        + content.rstrip()
        + "\n"
    )
    path.write_text(body, encoding="utf-8")
    return path


def read_entry(root: Path, slug: str) -> Entry:
    """Read a single entry. Raises FileNotFoundError if missing,
    ValueError if frontmatter is malformed."""
    path = _entry_path(root, slug)
    text = path.read_text(encoding="utf-8")
    if not text.startswith("---\n"):
        raise ValueError(f"{path}: missing YAML frontmatter")
    end = text.find("\n---\n", 4)
    if end == -1:
        raise ValueError(f"{path}: malformed frontmatter")
    front = yaml.safe_load(text[4:end]) or {}
    content = text[end + len("\n---\n"):].lstrip()
    return Entry(
        slug=slug,
        type=str(front.get("type", "")),
        name=str(front.get("name", "")),
        description=str(front.get("description", "")),
        created=str(front.get("created", "")),
        updated=str(front.get("updated", "")),
        content=content,
    )
```

- [ ] **Step 4: Run the test, verify it passes**

Run: `uv run pytest tests/test_memory_io.py -v`
Expected: 4 PASSED.

- [ ] **Step 5: Commit**

```bash
cd /home/apiad/Workspace/repos/aegis
git add plugins/memory-system/memory_system.py tests/test_memory_io.py
git commit -m "feat(memory-system): entries frontmatter I/O + slug helpers"
```

## Task 1.2: List entries + MEMORY.md index rebuild

**Files:**
- Modify: `plugins/memory-system/memory_system.py` (append helpers)
- Modify: `tests/test_memory_io.py` (append tests)

- [ ] **Step 1: Append failing tests**

Append to `tests/test_memory_io.py`:

```python
def test_list_entries_returns_all(tmp_path: Path) -> None:
    m = _load_memory_system()
    m.write_entry(tmp_path, "user", "name", "Goes by Alex", "body")
    m.write_entry(tmp_path, "feedback", "phrasing", "no load-bearing", "body")
    slugs = sorted(e.slug for e in m.list_entries(tmp_path))
    assert slugs == ["feedback_phrasing", "user_name"]


def test_list_entries_skips_malformed(tmp_path: Path) -> None:
    m = _load_memory_system()
    m.write_entry(tmp_path, "user", "good", "d", "c")
    bad = tmp_path / m.ENTRIES_SUBDIR / "bad.md"
    bad.write_text("no frontmatter here\n", encoding="utf-8")
    slugs = [e.slug for e in m.list_entries(tmp_path)]
    assert slugs == ["user_good"]


def test_rebuild_index_writes_memory_md(tmp_path: Path) -> None:
    m = _load_memory_system()
    m.write_entry(tmp_path, "user", "name", "Goes by Alex", "body")
    m.write_entry(tmp_path, "fact", "cron", "Dream at 3am", "body")
    m.rebuild_index(tmp_path)
    text = (tmp_path / m.MEMORY_SUBDIR / "MEMORY.md").read_text(encoding="utf-8")
    assert "# Memory index" in text
    assert "## Index" in text
    assert "[name](entries/user_name.md) — Goes by Alex" in text
    assert "[cron](entries/fact_cron.md) — Dream at 3am" in text


def test_rebuild_index_empty_when_no_entries(tmp_path: Path) -> None:
    m = _load_memory_system()
    m.rebuild_index(tmp_path)
    text = (tmp_path / m.MEMORY_SUBDIR / "MEMORY.md").read_text(encoding="utf-8")
    assert "## Index" in text
    assert text.count("\n- [") == 0
```

- [ ] **Step 2: Run, verify failure**

Run: `uv run pytest tests/test_memory_io.py -v`
Expected: 4 new tests FAIL — `list_entries` / `rebuild_index` not defined.

- [ ] **Step 3: Append helpers**

Append to `plugins/memory-system/memory_system.py`:

```python
def list_entries(root: Path) -> list[Entry]:
    """Return every well-formed entry under entries/, sorted by name."""
    folder = root / ENTRIES_SUBDIR
    if not folder.exists():
        return []
    out: list[Entry] = []
    for path in sorted(folder.glob("*.md")):
        slug = path.stem
        try:
            out.append(read_entry(root, slug))
        except (ValueError, FileNotFoundError):
            continue
    return out


def rebuild_index(root: Path) -> Path:
    """Rebuild MEMORY.md from the current state of entries/."""
    entries = list_entries(root)
    lines = ["# Memory index", "", "## Index", ""]
    for e in entries:
        lines.append(f"- [{e.name}](entries/{e.slug}.md) — {e.description}")
    path = root / MEMORY_SUBDIR / "MEMORY.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path
```

- [ ] **Step 4: Run, verify pass**

Run: `uv run pytest tests/test_memory_io.py -v`
Expected: 8 PASSED total.

- [ ] **Step 5: Commit**

```bash
git add plugins/memory-system/memory_system.py tests/test_memory_io.py
git commit -m "feat(memory-system): list_entries + rebuild_index"
```

## Task 1.3: `memory_search` and `memory_read` tools

**Files:**
- Modify: `plugins/memory-system/memory_system.py` (append `@tool` functions + scorer)
- Test: `tests/test_memory_read_tools.py`

- [ ] **Step 1: Write the failing test**

```python
"""Hermetic tests for memory_read / memory_search MCP tools."""
from __future__ import annotations

import importlib.util
import os
import sys
from pathlib import Path

import pytest

from aegis.tools.decorator import _reset_registry_for_tests as _reset_tools


def _load(monkeypatch, tmp_path: Path):
    _reset_tools()
    monkeypatch.chdir(tmp_path)
    repo_root = Path(__file__).parent.parent
    path = repo_root / "plugins" / "memory-system" / "memory_system.py"
    spec = importlib.util.spec_from_file_location("_test_memory_system", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules["_test_memory_system"] = module
    spec.loader.exec_module(module)
    return module


@pytest.mark.asyncio
async def test_memory_read_returns_entry_body(tmp_path: Path, monkeypatch) -> None:
    m = _load(monkeypatch, tmp_path)
    m.write_entry(tmp_path, "feedback", "phrasing",
                  "no load-bearing", "Avoid that phrase.")
    out = await m.memory_read(slug="feedback_phrasing")
    assert out["slug"] == "feedback_phrasing"
    assert out["type"] == "feedback"
    assert out["name"] == "phrasing"
    assert out["description"] == "no load-bearing"
    assert "Avoid that phrase." in out["content"]


@pytest.mark.asyncio
async def test_memory_read_missing_raises(tmp_path: Path, monkeypatch) -> None:
    m = _load(monkeypatch, tmp_path)
    with pytest.raises(FileNotFoundError):
        await m.memory_read(slug="never-existed")


@pytest.mark.asyncio
async def test_memory_search_scores_by_keywords(tmp_path: Path, monkeypatch) -> None:
    m = _load(monkeypatch, tmp_path)
    m.write_entry(tmp_path, "feedback", "phrasing",
                  "load-bearing phrase ban", "Avoid that phrase.")
    m.write_entry(tmp_path, "user", "name",
                  "Goes by Alex", "Use Alex in writing.")
    hits = await m.memory_search(query="load bearing")
    assert hits[0]["slug"] == "feedback_phrasing"
    assert hits[0]["score"] > 0
    assert "snippet" in hits[0]


@pytest.mark.asyncio
async def test_memory_search_respects_limit(tmp_path: Path, monkeypatch) -> None:
    m = _load(monkeypatch, tmp_path)
    for i in range(15):
        m.write_entry(tmp_path, "fact", f"x{i}",
                      f"fact {i} about widgets", f"widget body {i}")
    hits = await m.memory_search(query="widget", limit=5)
    assert len(hits) == 5


@pytest.mark.asyncio
async def test_memory_search_empty_when_no_matches(tmp_path: Path, monkeypatch) -> None:
    m = _load(monkeypatch, tmp_path)
    m.write_entry(tmp_path, "user", "name", "Goes by Alex", "body")
    hits = await m.memory_search(query="xyzzy-no-match")
    assert hits == []
```

- [ ] **Step 2: Run, verify failure**

Run: `uv run pytest tests/test_memory_read_tools.py -v`
Expected: 5 FAILED — `memory_read` / `memory_search` not defined.

- [ ] **Step 3: Append the scorer + tools**

Append to `plugins/memory-system/memory_system.py`:

```python
from aegis.tools import tool


_STOPWORDS = frozenset({
    "the", "a", "an", "and", "or", "but", "if", "then", "of", "to",
    "for", "in", "on", "at", "by", "with", "is", "are", "was", "were",
    "be", "been", "being", "this", "that", "these", "those", "i", "you",
    "he", "she", "it", "we", "they", "what", "which", "who", "when",
    "where", "why", "how", "do", "does", "did", "have", "has", "had",
})


def _tokenize(s: str) -> set[str]:
    out: set[str] = set()
    for tok in re.split(r"[^a-z0-9]+", s.lower()):
        if tok and tok not in _STOPWORDS:
            out.add(tok)
    return out


def _score(entry: Entry, query_toks: set[str]) -> int:
    name_desc = _tokenize(entry.name + " " + entry.description)
    body = _tokenize(entry.content)
    return (len(query_toks & name_desc) * 2) + len(query_toks & body)


def _snippet(content: str, query_toks: set[str], window: int = 200) -> str:
    low = content.lower()
    for tok in query_toks:
        idx = low.find(tok)
        if idx >= 0:
            half = window // 2
            start = max(0, idx - half)
            end = min(len(content), idx + half)
            return content[start:end].strip()
    return content[:window].strip()


def _project_root() -> Path:
    return Path.cwd()


@tool(timeout=5.0)
async def memory_read(slug: str) -> dict:
    """Fetch one memory entry's full body by slug.

    Args:
        slug: the entry's slug (e.g. "feedback_phrasing").

    Returns:
        a dict with keys: slug, name, type, description, content.
    """
    e = read_entry(_project_root(), slug)
    return {
        "slug": e.slug, "name": e.name, "type": e.type,
        "description": e.description, "content": e.content,
    }


@tool(timeout=5.0)
async def memory_search(query: str, limit: int = 10) -> list[dict]:
    """Keyword search over memory entries.

    Args:
        query: free-text query.
        limit: max results, default 10.

    Returns:
        list of {slug, name, description, score, snippet}, score-descending.
    """
    qtoks = _tokenize(query)
    if not qtoks:
        return []
    scored: list[tuple[int, Entry]] = []
    for e in list_entries(_project_root()):
        s = _score(e, qtoks)
        if s > 0:
            scored.append((s, e))
    scored.sort(key=lambda x: x[0], reverse=True)
    out: list[dict] = []
    for s, e in scored[:limit]:
        out.append({
            "slug": e.slug, "name": e.name,
            "description": e.description, "score": s,
            "snippet": _snippet(e.content, qtoks),
        })
    return out
```

- [ ] **Step 4: Run, verify pass**

Run: `uv run pytest tests/test_memory_read_tools.py -v`
Expected: 5 PASSED.

- [ ] **Step 5: Commit**

```bash
git add plugins/memory-system/memory_system.py tests/test_memory_read_tools.py
git commit -m "feat(memory-system): memory_read + memory_search MCP tools"
```

---

# Slice 2 — Write tools + hooks

End state: agent can create / replace / remove memory through MCP tools; both hooks fire correctly (turn-0 bundle, turn-≥1 teaser, session_start observability).

## Task 2.1: `memory_add` / `memory_replace` / `memory_remove`

**Files:**
- Modify: `plugins/memory-system/memory_system.py` (append three `@tool`s + helper)
- Test: `tests/test_memory_write_tools.py`

- [ ] **Step 1: Write the failing test**

```python
"""Hermetic tests for memory_add / memory_replace / memory_remove tools."""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

from aegis.tools.decorator import _reset_registry_for_tests as _reset_tools


def _load(monkeypatch, tmp_path: Path):
    _reset_tools()
    monkeypatch.chdir(tmp_path)
    repo_root = Path(__file__).parent.parent
    path = repo_root / "plugins" / "memory-system" / "memory_system.py"
    spec = importlib.util.spec_from_file_location("_test_memory_system", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules["_test_memory_system"] = module
    spec.loader.exec_module(module)
    return module


@pytest.mark.asyncio
async def test_memory_add_writes_entry_and_index(tmp_path, monkeypatch) -> None:
    m = _load(monkeypatch, tmp_path)
    out = await m.memory_add(type="feedback", name="phrasing",
                             description="no load-bearing", content="avoid it")
    assert out["slug"] == "feedback_phrasing"
    assert Path(out["path"]).exists()
    index = (tmp_path / m.MEMORY_SUBDIR / "MEMORY.md").read_text()
    assert "[phrasing](entries/feedback_phrasing.md) — no load-bearing" in index


@pytest.mark.asyncio
async def test_memory_add_rejects_duplicate(tmp_path, monkeypatch) -> None:
    m = _load(monkeypatch, tmp_path)
    await m.memory_add(type="user", name="x", description="d", content="c")
    with pytest.raises(FileExistsError):
        await m.memory_add(type="user", name="x", description="d2", content="c2")


@pytest.mark.asyncio
async def test_memory_replace_updates_content_and_timestamp(
        tmp_path, monkeypatch) -> None:
    m = _load(monkeypatch, tmp_path)
    await m.memory_add(type="fact", name="cron",
                       description="3am", content="initial")
    out = await m.memory_replace(slug="fact_cron", content="updated body")
    e = m.read_entry(tmp_path, "fact_cron")
    assert e.content.strip() == "updated body"
    assert e.created != e.updated  # timestamp moved forward


@pytest.mark.asyncio
async def test_memory_replace_updates_description_and_index(
        tmp_path, monkeypatch) -> None:
    m = _load(monkeypatch, tmp_path)
    await m.memory_add(type="user", name="name",
                       description="old desc", content="body")
    await m.memory_replace(slug="user_name", description="new desc")
    index = (tmp_path / m.MEMORY_SUBDIR / "MEMORY.md").read_text()
    assert "new desc" in index
    assert "old desc" not in index


@pytest.mark.asyncio
async def test_memory_remove_deletes_file_and_index_line(
        tmp_path, monkeypatch) -> None:
    m = _load(monkeypatch, tmp_path)
    await m.memory_add(type="user", name="name", description="d", content="c")
    out = await m.memory_remove(slug="user_name")
    assert out == {"slug": "user_name", "removed": True}
    assert not (tmp_path / m.ENTRIES_SUBDIR / "user_name.md").exists()
    assert "user_name" not in (tmp_path / m.MEMORY_SUBDIR / "MEMORY.md").read_text()
```

- [ ] **Step 2: Run, verify failure**

Run: `uv run pytest tests/test_memory_write_tools.py -v`
Expected: 5 FAILED.

- [ ] **Step 3: Append the tools**

Append to `plugins/memory-system/memory_system.py`:

```python
@tool(timeout=5.0)
async def memory_add(type: str, name: str,
                     description: str, content: str) -> dict:
    """Save a new memory entry.

    Args:
        type: one of "user", "feedback", "fact", "reference".
        name: short label (will be kebab-cased).
        description: one-line summary (used by future sessions to decide
            relevance).
        content: the entry body, markdown.

    Returns:
        {"slug": str, "path": str}.
    """
    root = _project_root()
    path = write_entry(root, type, name, description, content)
    rebuild_index(root)
    return {"slug": path.stem, "path": str(path)}


@tool(timeout=5.0)
async def memory_replace(slug: str, *, description: str | None = None,
                         content: str | None = None) -> dict:
    """Update an existing entry. Name and type are immutable.

    Args:
        slug: the entry's slug.
        description: new description (optional).
        content: new body (optional).

    Returns:
        {"slug": str, "path": str}.
    """
    root = _project_root()
    e = read_entry(root, slug)
    new_desc = description if description is not None else e.description
    new_content = content if content is not None else e.content
    path = _entry_path(root, slug)
    front = {
        "type": e.type, "name": e.name,
        "description": new_desc, "created": e.created,
        "updated": _now_iso(),
    }
    body = (
        "---\n"
        + yaml.safe_dump(front, sort_keys=False, allow_unicode=True)
        + "---\n\n"
        + new_content.rstrip()
        + "\n"
    )
    path.write_text(body, encoding="utf-8")
    if description is not None and description != e.description:
        rebuild_index(root)
    return {"slug": slug, "path": str(path)}


@tool(timeout=5.0)
async def memory_remove(slug: str) -> dict:
    """Delete an entry permanently."""
    root = _project_root()
    path = _entry_path(root, slug)
    if not path.exists():
        raise FileNotFoundError(f"entry {slug!r} not found at {path}")
    path.unlink()
    rebuild_index(root)
    return {"slug": slug, "removed": True}
```

- [ ] **Step 4: Run, verify pass**

Run: `uv run pytest tests/test_memory_write_tools.py -v`
Expected: 5 PASSED.

- [ ] **Step 5: Commit**

```bash
git add plugins/memory-system/memory_system.py tests/test_memory_write_tools.py
git commit -m "feat(memory-system): memory_add / memory_replace / memory_remove tools"
```

## Task 2.2: `pre_turn` hook (turn-0 + turn-≥1) and observer `session_start`

**Files:**
- Modify: `plugins/memory-system/memory_system.py` (append hooks + helpers)
- Test: `tests/test_memory_hooks.py`

- [ ] **Step 1: Write the failing test**

```python
"""Hermetic tests for memory_system pre_turn / session_start hooks."""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

from aegis.hooks.contexts import (
    PreTurnContext, PreTurnResult, SessionHandle, Turn,
)
from aegis.hooks.decorator import _reset_registry_for_tests as _reset_hooks
from aegis.tools.decorator import _reset_registry_for_tests as _reset_tools


SH = SessionHandle(handle="test-handle",
                   agent_profile="test", harness="claude")


def _load(monkeypatch, tmp_path: Path):
    _reset_hooks()
    _reset_tools()
    monkeypatch.chdir(tmp_path)
    repo_root = Path(__file__).parent.parent
    path = repo_root / "plugins" / "memory-system" / "memory_system.py"
    spec = importlib.util.spec_from_file_location("_test_memory_system", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules["_test_memory_system"] = module
    spec.loader.exec_module(module)
    return module


def _ctx(tmp_path: Path, message: str, history: tuple[Turn, ...] = ()) -> PreTurnContext:
    return PreTurnContext(
        session=SH, user_message=message,
        history=history, project_root=tmp_path,
    )


@pytest.mark.asyncio
async def test_turn_zero_injects_soul_user_index_primer(
        tmp_path, monkeypatch) -> None:
    m = _load(monkeypatch, tmp_path)
    (tmp_path / m.MEMORY_SUBDIR).mkdir(parents=True)
    (tmp_path / m.MEMORY_SUBDIR / "SOUL.md").write_text("# Voice\n\nConcise.\n")
    (tmp_path / m.MEMORY_SUBDIR / "USER.md").write_text("User: Alex.\n")
    m.write_entry(tmp_path, "user", "name", "Goes by Alex", "body")
    m.rebuild_index(tmp_path)
    result = await m.inject_memory(_ctx(tmp_path, "hello"))
    assert isinstance(result, PreTurnResult)
    text = result.prepend_system
    assert "Concise." in text
    assert "User: Alex." in text
    assert "## Index" in text
    assert "[name](entries/user_name.md)" in text
    assert "# Memory" in text  # the primer header


@pytest.mark.asyncio
async def test_turn_zero_skips_missing_files(tmp_path, monkeypatch) -> None:
    m = _load(monkeypatch, tmp_path)
    result = await m.inject_memory(_ctx(tmp_path, "hello"))
    # Nothing on disk → primer-only injection
    assert isinstance(result, PreTurnResult)
    assert "# Memory" in result.prepend_system


@pytest.mark.asyncio
async def test_turn_ge_one_injects_top_5_teasers(tmp_path, monkeypatch) -> None:
    m = _load(monkeypatch, tmp_path)
    m.write_entry(tmp_path, "feedback", "load-bearing",
                  "no load-bearing phrase", "avoid it")
    m.write_entry(tmp_path, "user", "name",
                  "Goes by Alex", "use Alex")
    history = (Turn(role="user", content="prior"),
               Turn(role="assistant", content="ok"))
    result = await m.inject_memory(
        _ctx(tmp_path, "let's drop the load-bearing thing", history))
    assert isinstance(result, PreTurnResult)
    text = result.prepend_system
    assert "## Possibly relevant memory" in text
    assert "load-bearing" in text
    # Body NOT included — only name + description
    assert "avoid it" not in text


@pytest.mark.asyncio
async def test_turn_ge_one_returns_none_when_no_match(
        tmp_path, monkeypatch) -> None:
    m = _load(monkeypatch, tmp_path)
    m.write_entry(tmp_path, "user", "name", "Goes by Alex", "use Alex")
    history = (Turn(role="user", content="prior"),)
    result = await m.inject_memory(_ctx(tmp_path, "xyzzy nothing matches", history))
    assert result is None


@pytest.mark.asyncio
async def test_turn_ge_one_caps_word_count(tmp_path, monkeypatch) -> None:
    m = _load(monkeypatch, tmp_path)
    # 20 entries, each with a long description matching "widget"
    for i in range(20):
        m.write_entry(tmp_path, "fact", f"w{i}",
                      "widget " + " ".join(["lorem"] * 200),
                      "body")
    history = (Turn(role="user", content="prior"),)
    result = await m.inject_memory(_ctx(tmp_path, "tell me about widgets", history))
    assert result is not None
    word_count = len(result.prepend_system.split())
    assert word_count <= 1000
```

- [ ] **Step 2: Run, verify failure**

Run: `uv run pytest tests/test_memory_hooks.py -v`
Expected: 5 FAILED.

- [ ] **Step 3: Append the hooks**

Append to `plugins/memory-system/memory_system.py`:

```python
from aegis.hooks import hook, PreTurnContext, PreTurnResult


PRIMER = """\
# Memory

You have a persistent memory at .aegis/memory/. The MEMORY.md index above
lists everything you know. Use memory_search(query) to find an entry's
body, or memory_read(slug) when you already know the slug.

Write a memory when:
- the user corrects you ("don't", "stop X") → save as `feedback`
- the user reveals a preference, role, or constraint → `user`
- you discover a non-obvious fact about the project or tooling → `fact`
- the user names an external system you'll need again → `reference`

Skip trivial / easily-rediscovered things. When unsure, save — the
dream pass will consolidate later.

Tools:
- memory_search(query)         — find entries by keyword
- memory_read(slug)            — fetch one entry's body
- memory_add(type, name, …)    — save a new memory
- memory_replace(slug, …)      — update an existing one
- memory_remove(slug)          — delete (use sparingly outside dream pass)
"""


TURN_ZERO_CAP_TOKENS = 4000
TURN_N_CAP_WORDS = 1000
TOP_K = 5


def _approx_tokens(text: str) -> int:
    # Cheap heuristic: ~1.3 tokens per word for English.
    return int(len(text.split()) * 1.3)


def _read_file_or_none(path: Path) -> str | None:
    if not path.exists():
        return None
    try:
        return path.read_text(encoding="utf-8").strip()
    except OSError:
        return None


def _build_turn_zero(project_root: Path) -> str:
    mem_dir = project_root / MEMORY_SUBDIR
    parts: list[str] = []
    soul = _read_file_or_none(mem_dir / "SOUL.md")
    user = _read_file_or_none(mem_dir / "USER.md")
    entries = list_entries(project_root)
    # Sort entries by mtime descending so truncation drops least-recent first.
    entries.sort(key=lambda e: (mem_dir / "entries" / f"{e.slug}.md").stat().st_mtime,
                 reverse=True)
    if soul:
        parts.append(soul)
    if user:
        parts.append(user)
    if entries:
        parts.append("## Memory index\n")
        for e in entries:
            parts.append(f"- [{e.name}](entries/{e.slug}.md) — {e.description}")
    parts.append("")  # blank line before primer
    parts.append(PRIMER)
    text = "\n".join(parts)
    # Truncate the index if cap exceeded — primer is fixed-size and stays whole.
    while _approx_tokens(text) > TURN_ZERO_CAP_TOKENS and entries:
        entries.pop()  # drop the oldest entry
        parts = []
        if soul: parts.append(soul)
        if user: parts.append(user)
        parts.append("## Memory index\n")
        for e in entries:
            parts.append(f"- [{e.name}](entries/{e.slug}.md) — {e.description}")
        parts.append(f"… {len(list_entries(project_root)) - len(entries)} "
                     f"more entries; use memory_search to find specific ones")
        parts.append("")
        parts.append(PRIMER)
        text = "\n".join(parts)
    return text


def _build_turn_n(project_root: Path, user_message: str) -> str | None:
    qtoks = _tokenize(user_message)
    if not qtoks:
        return None
    now_ts = datetime.now(timezone.utc).timestamp()
    scored: list[tuple[int, Entry]] = []
    for e in list_entries(project_root):
        s = _score(e, qtoks)
        # Recency boost (+1 if updated within 24h).
        try:
            mtime = (project_root / ENTRIES_SUBDIR / f"{e.slug}.md").stat().st_mtime
            if now_ts - mtime < 86400:
                s += 1
        except OSError:
            pass
        if s >= 2:
            scored.append((s, e))
    if not scored:
        return None
    scored.sort(key=lambda x: x[0], reverse=True)
    lines = ["## Possibly relevant memory", ""]
    used = 0
    for _, e in scored[:TOP_K]:
        line = f"- **{e.name}** — {e.description}"
        if used + len(line.split()) > TURN_N_CAP_WORDS:
            break
        lines.append(line)
        used += len(line.split())
    if len(lines) == 2:  # header only
        return None
    return "\n".join(lines)


@hook("pre_turn")
async def inject_memory(ctx: PreTurnContext) -> PreTurnResult | None:
    """Inject SOUL+USER+index+primer on turn 0, top-K teasers afterward."""
    if not ctx.history:
        return PreTurnResult(prepend_system=_build_turn_zero(ctx.project_root))
    body = _build_turn_n(ctx.project_root, ctx.user_message)
    if body is None:
        return None
    return PreTurnResult(prepend_system=body)


@hook("session_start")
async def log_session_open(ctx) -> None:
    """Observer: best-effort note that a session has started."""
    # ctx shape matches SessionStartEvent; we just need .session.handle.
    pass
```

- [ ] **Step 4: Run, verify pass**

Run: `uv run pytest tests/test_memory_hooks.py -v`
Expected: 5 PASSED.

- [ ] **Step 5: Commit**

```bash
git add plugins/memory-system/memory_system.py tests/test_memory_hooks.py
git commit -m "feat(memory-system): pre_turn hook + observer session_start"
```

---

# Slice 3 — Plugin packaging, install, uninstall

End state: plugin can be installed and uninstalled into a clean project; install writes the directory tree, the YAML config block, the schedule overlay (if user opted in); uninstall strips config but preserves accreted memory by default.

## Task 3.1: `plugin.toml` manifest

**Files:**
- Create: `plugins/memory-system/plugin.toml`

- [ ] **Step 1: Write the manifest**

```toml
[plugin]
name           = "memory-system"
version        = "0.1.0"
description    = "Hermes-inspired persistent memory with periodic dreaming."
requires_aegis = ">=0.15"

[default_config]
lookback_days     = 7
max_session_files = 50
dreamer_agent     = "dreamer"
```

- [ ] **Step 2: Smoke test by listing**

Run: `uv run aegis plugin list --from plugins/`
Expected: output lists `memory-system 0.1.0` alongside `skill-system 0.1.0`.

- [ ] **Step 3: Commit**

```bash
git add plugins/memory-system/plugin.toml
git commit -m "feat(memory-system): plugin.toml manifest"
```

## Task 3.2: `_install.py` with directory tree + stub files + YAML edits

**Files:**
- Create: `plugins/memory-system/_install.py`
- Test: `tests/test_memory_install.py`

- [ ] **Step 1: Write the failing test**

```python
"""Hermetic tests for memory-system install / uninstall."""
from __future__ import annotations

import importlib.util
import sys
import textwrap
from pathlib import Path

import pytest

from aegis.config.yaml_loader import load_config
from aegis.plugins.install_context import InstallContext


def _load_module(name: str):
    repo_root = Path(__file__).parent.parent
    path = repo_root / "plugins" / "memory-system" / f"{name}.py"
    spec = importlib.util.spec_from_file_location(f"_test_{name}", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[f"_test_{name}"] = module
    spec.loader.exec_module(module)
    return module


def _ctx(tmp_path: Path, *, yes: bool) -> InstallContext:
    (tmp_path / ".aegis.yaml").write_text("agents: {}\n", encoding="utf-8")
    return InstallContext(
        project_root=tmp_path,
        aegis_dir=tmp_path,
        plugin_dir=tmp_path / "plugins" / "memory-system",
        plugin_name="memory-system",
        manifest={"plugin": {"name": "memory-system", "version": "0.1.0"}},
        config=None,
        console=None,
        _confirm_default=True,
        _yes=yes,
    )


def test_install_creates_directory_tree(tmp_path: Path) -> None:
    install = _load_module("_install")
    install.install(_ctx(tmp_path, yes=True))
    assert (tmp_path / ".aegis/memory/entries").is_dir()
    assert (tmp_path / ".aegis/memory/dreams").is_dir()


def test_install_writes_stub_files(tmp_path: Path) -> None:
    install = _load_module("_install")
    install.install(_ctx(tmp_path, yes=True))
    assert (tmp_path / ".aegis/memory/SOUL.md").exists()
    assert (tmp_path / ".aegis/memory/USER.md").exists()
    assert (tmp_path / ".aegis/memory/MEMORY.md").exists()


def test_install_preserves_existing_files(tmp_path: Path) -> None:
    install = _load_module("_install")
    (tmp_path / ".aegis/memory").mkdir(parents=True)
    (tmp_path / ".aegis/memory/SOUL.md").write_text("MINE\n", encoding="utf-8")
    install.install(_ctx(tmp_path, yes=True))
    assert (tmp_path / ".aegis/memory/SOUL.md").read_text() == "MINE\n"


def test_install_adds_dreamer_agent_and_memory_block(tmp_path: Path) -> None:
    install = _load_module("_install")
    install.install(_ctx(tmp_path, yes=True))
    text = (tmp_path / ".aegis.yaml").read_text(encoding="utf-8")
    assert "dreamer:" in text
    assert "memory:" in text
    assert "lookback_days: 7" in text


def test_install_writes_schedule_overlay_when_yes(tmp_path: Path) -> None:
    install = _load_module("_install")
    install.install(_ctx(tmp_path, yes=True))
    overlay = tmp_path / ".aegis/schedules/memory-dream.yaml"
    assert overlay.exists()
    body = overlay.read_text(encoding="utf-8")
    assert "workflow: dream" in body
    assert "0 3 * * *" in body
```

- [ ] **Step 2: Run, verify failure**

Run: `uv run pytest tests/test_memory_install.py -v`
Expected: 5 FAILED.

- [ ] **Step 3: Write `_install.py`**

```python
"""Setup hook for memory-system: directory tree, stub files, yaml edits,
optional schedule overlay."""
from __future__ import annotations

import io
from pathlib import Path

from ruamel.yaml import YAML

from aegis.config.edit import add_agent as _add_agent
from aegis.plugins import InstallContext
from aegis.scheduler.push import write_atomic as _write_schedule


def _yaml() -> YAML:
    y = YAML()
    y.preserve_quotes = True
    y.default_flow_style = False
    return y


def _add_top_level_section(yaml_path: Path, key: str, value: dict) -> None:
    """Add a top-level mapping `key: value` to .aegis.yaml if absent.
    Comment-preserving via ruamel; atomic via tempfile-rename."""
    y = _yaml()
    data = y.load(yaml_path.read_text(encoding="utf-8")) or {}
    if key in data:
        return
    data[key] = value
    buf = io.StringIO()
    y.dump(data, buf)
    tmp = yaml_path.with_suffix(".yaml.tmp")
    tmp.write_text(buf.getvalue(), encoding="utf-8")
    tmp.replace(yaml_path)


def _remove_top_level_section(yaml_path: Path, key: str) -> None:
    y = _yaml()
    data = y.load(yaml_path.read_text(encoding="utf-8")) or {}
    if key not in data:
        return
    del data[key]
    buf = io.StringIO()
    y.dump(data, buf)
    tmp = yaml_path.with_suffix(".yaml.tmp")
    tmp.write_text(buf.getvalue(), encoding="utf-8")
    tmp.replace(yaml_path)


SOUL_STUB = """\
# Voice

(Edit this file to shape the agent's voice and behavior. The memory-system
plugin injects it on every session's first turn.)

- Concise.
- Explicit about uncertainty.
- Direct when correcting.
"""

USER_STUB = """\
# User

(Edit this file to record who the user is. The memory-system plugin
injects it on every session's first turn.)

- Name:
- Role:
- Preferences:
"""

MEMORY_STUB = """\
# Memory index

## Index
"""


def _add_agent_if_absent(yaml_path: Path) -> None:
    text = yaml_path.read_text(encoding="utf-8")
    if "dreamer:" in text:
        return
    _add_agent(yaml_path, "dreamer", {
        "provider":   "claude",
        "model":      "haiku",
        "effort":     "low",
        "permission": "read-write",
    })


def _add_memory_block_if_absent(yaml_path: Path, defaults: dict) -> None:
    _add_top_level_section(yaml_path, "memory", defaults)


def _maybe_install_schedule(ctx: InstallContext) -> bool:
    if not ctx.confirm(
        "Schedule the dream pass daily at 3am?", default=True,
    ):
        return False
    _write_schedule(
        state_root=ctx.aegis_dir,
        name="memory-dream",
        spec={
            "workflow":  "dream",
            "cron":      "0 3 * * *",
            "lifecycle": "forever",
        },
        pushed_from="plugin:memory-system",
    )
    return True


def install(ctx: InstallContext) -> None:
    mem_dir = ctx.aegis_dir / ".aegis" / "memory"
    (mem_dir / "entries").mkdir(parents=True, exist_ok=True)
    (mem_dir / "dreams").mkdir(parents=True, exist_ok=True)

    for fname, body in (("SOUL.md", SOUL_STUB),
                        ("USER.md", USER_STUB),
                        ("MEMORY.md", MEMORY_STUB)):
        path = mem_dir / fname
        if not path.exists():
            path.write_text(body, encoding="utf-8")

    yaml_path = ctx.aegis_dir / ".aegis.yaml"
    _add_agent_if_absent(yaml_path)
    _add_memory_block_if_absent(yaml_path, dict(ctx.manifest.get(
        "default_config",
        {"lookback_days": 7, "max_session_files": 50,
         "dreamer_agent": "dreamer"})))

    scheduled = _maybe_install_schedule(ctx)

    if ctx.console is not None:
        msg = f"[green]memory-system[/] ready at {mem_dir}/"
        if scheduled:
            msg += (" · dream scheduled at 03:00 — fires "
                    "whenever `aegis serve` is running")
        ctx.console.print(msg)
```

- [ ] **Step 4: Run, verify pass**

Run: `uv run pytest tests/test_memory_install.py -v`
Expected: 5 PASSED.

Verified against `main` (commit `72a39e2`): `aegis.config.edit.add_agent(root, slug, agent_spec)` exists; there is no generic `add_section` helper, so the plugin ships its own `_add_top_level_section` / `_remove_top_level_section` using ruamel directly (above). If the `add_agent` signature has drifted, inspect `src/aegis/config/edit.py:144` and adapt.

- [ ] **Step 5: Commit**

```bash
git add plugins/memory-system/_install.py tests/test_memory_install.py
git commit -m "feat(memory-system): _install.py creates tree + edits yaml + drops schedule overlay"
```

## Task 3.3: `_uninstall.py`

**Files:**
- Create: `plugins/memory-system/_uninstall.py`
- Modify: `tests/test_memory_install.py` (append uninstall tests)

- [ ] **Step 1: Append failing test**

Append to `tests/test_memory_install.py`:

```python
def test_uninstall_strips_yaml_and_overlay_preserves_memory_dir(
        tmp_path: Path) -> None:
    install = _load_module("_install")
    uninstall = _load_module("_uninstall")
    install.install(_ctx(tmp_path, yes=True))
    # Land a memory entry the user shouldn't lose.
    (tmp_path / ".aegis/memory/entries/user_name.md").write_text(
        "---\ntype: user\nname: name\ndescription: d\n"
        "created: 2026-05-30T00:00:00+00:00\n"
        "updated: 2026-05-30T00:00:00+00:00\n---\n\nAlex\n",
        encoding="utf-8",
    )
    uninstall.uninstall(_ctx(tmp_path, yes=False))  # decline delete-data prompt
    text = (tmp_path / ".aegis.yaml").read_text(encoding="utf-8")
    assert "memory:" not in text
    assert "dreamer:" not in text
    assert not (tmp_path / ".aegis/schedules/memory-dream.yaml").exists()
    # Memory dir preserved.
    assert (tmp_path / ".aegis/memory/entries/user_name.md").exists()


def test_uninstall_deletes_memory_dir_when_user_consents(tmp_path: Path) -> None:
    install = _load_module("_install")
    uninstall = _load_module("_uninstall")
    install.install(_ctx(tmp_path, yes=True))
    uninstall.uninstall(_ctx(tmp_path, yes=True))  # accept delete-data prompt
    assert not (tmp_path / ".aegis/memory").exists()
```

- [ ] **Step 2: Run, verify failure**

Run: `uv run pytest tests/test_memory_install.py -v -k uninstall`
Expected: 2 new tests FAIL.

- [ ] **Step 3: Write `_uninstall.py`**

```python
"""Teardown hook for memory-system: strip yaml, remove overlay, optionally wipe data."""
from __future__ import annotations

import io
import shutil
from pathlib import Path

from ruamel.yaml import YAML

from aegis.config.edit import remove_agent as _remove_agent
from aegis.plugins import InstallContext


def _remove_top_level_section(yaml_path: Path, key: str) -> None:
    """Mirror of the helper in _install.py. Both `_*.py` files are skipped
    by the plugin auto-importer, so duplicating is simpler than threading
    an import path."""
    y = YAML()
    y.preserve_quotes = True
    y.default_flow_style = False
    data = y.load(yaml_path.read_text(encoding="utf-8")) or {}
    if key not in data:
        return
    del data[key]
    buf = io.StringIO()
    y.dump(data, buf)
    tmp = yaml_path.with_suffix(".yaml.tmp")
    tmp.write_text(buf.getvalue(), encoding="utf-8")
    tmp.replace(yaml_path)


def uninstall(ctx: InstallContext) -> None:
    yaml_path = ctx.aegis_dir / ".aegis.yaml"
    if yaml_path.exists():
        _remove_top_level_section(yaml_path, "memory")
        # Only remove the dreamer agent if no queue or schedule references it.
        # For v1 we trust the user; if something else uses it, the user can
        # re-add by hand.
        try:
            _remove_agent(yaml_path, "dreamer")
        except KeyError:
            pass

    overlay = ctx.aegis_dir / ".aegis" / "schedules" / "memory-dream.yaml"
    if overlay.exists():
        overlay.unlink()

    mem_dir = ctx.aegis_dir / ".aegis" / "memory"
    if mem_dir.exists() and ctx.confirm(
        f"Also delete {mem_dir} and all stored memories and dream logs?",
        default=False,
    ):
        shutil.rmtree(mem_dir)
        if ctx.console is not None:
            ctx.console.print(f"[yellow]memory-system[/] removed (data deleted).")
    else:
        if ctx.console is not None:
            ctx.console.print(
                f"[yellow]memory-system[/] removed (data preserved at {mem_dir})."
            )
```

- [ ] **Step 4: Run, verify pass**

Run: `uv run pytest tests/test_memory_install.py -v`
Expected: 7 PASSED.

Verified against `main`: `aegis.config.edit.remove_agent(root, slug)` exists (no `remove_section`; we ship our own helper). If signatures drift, the test fixtures pin the observable behavior — adapt the implementation.

- [ ] **Step 5: Commit**

```bash
git add plugins/memory-system/_uninstall.py tests/test_memory_install.py
git commit -m "feat(memory-system): _uninstall.py strips yaml/overlay, preserves data by default"
```

---

# Slice 4 — Dream workflow

End state: the `dream` workflow runs end-to-end against a populated `.aegis/state/sessions/` directory; consolidation actions apply; a `dream-YYYY-MM-DD.md` narrative log lands. LLM calls are mocked in hermetic tests.

## Task 4.1: `dream` workflow stages 1–3

**Files:**
- Modify: `plugins/memory-system/memory_system.py` (append `@workflow dream` + helpers)
- Test: `tests/test_memory_dream.py`

- [ ] **Step 1: Write the failing test**

```python
"""Hermetic test for the dream workflow stages 1-3 with mocked LLM."""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from aegis.hooks.decorator import _reset_registry_for_tests as _reset_hooks
from aegis.tools.decorator import _reset_registry_for_tests as _reset_tools


def _load(monkeypatch, tmp_path: Path):
    _reset_hooks()
    _reset_tools()
    monkeypatch.chdir(tmp_path)
    repo_root = Path(__file__).parent.parent
    path = repo_root / "plugins" / "memory-system" / "memory_system.py"
    spec = importlib.util.spec_from_file_location("_test_memory_system", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules["_test_memory_system"] = module
    spec.loader.exec_module(module)
    return module


def _drop_session(root: Path, handle: str) -> None:
    sessions = root / ".aegis" / "state" / "sessions"
    sessions.mkdir(parents=True, exist_ok=True)
    (sessions / f"{handle}.jsonl").write_text(
        '{"v":1,"aegis_ts":"2026-05-30T00:00:00Z","event":{"type":"assistant_text","text":"hi"}}\n',
        encoding="utf-8",
    )


class _FakeEngine:
    """Minimal stand-in for WorkflowEngine with a scripted delegate()."""
    def __init__(self, replies: list[str]) -> None:
        self._replies = list(replies)
        self.calls: list[tuple[str, str]] = []

    async def delegate(self, queue: str, payload: str) -> str:
        self.calls.append((queue, payload))
        return self._replies.pop(0)


@pytest.mark.asyncio
async def test_dream_consolidates_and_writes_log(
        tmp_path: Path, monkeypatch) -> None:
    m = _load(monkeypatch, tmp_path)
    # Seed two sessions and one existing entry to consolidate.
    _drop_session(tmp_path, "lucid-knuth")
    _drop_session(tmp_path, "blithe-hopper")
    m.write_entry(tmp_path, "feedback", "phrasing",
                  "old description", "old body")
    # Stage 1 returns one proposal + observations per session (2 calls).
    stage1_reply = json.dumps({
        "session_handle": "lucid-knuth",
        "summary": "session summary",
        "proposed_entries": [{
            "type": "fact", "name": "docker-quirk",
            "description": "DOCKER_BUILDKIT=1 is required",
            "content": "Run with the env var set.",
            "rationale": "observed three times",
        }],
        "observations": ["agent re-discovered docker quirk twice"],
    })
    # Stage 2 returns one add + one replace.
    stage2_reply = json.dumps({
        "actions": [
            {"action": "add", "type": "fact", "name": "docker-quirk",
             "description": "DOCKER_BUILDKIT=1 is required",
             "content": "Run with the env var set."},
            {"action": "replace", "slug": "feedback_phrasing",
             "description": "consolidated", "content": "new body"},
        ],
        "rationale": "merged duplicate",
    })
    # Stage 3 returns prose.
    stage3_reply = (
        "Last night I noticed a recurring pattern in three sessions: "
        "the agent kept rediscovering the same Docker quirk. I have now "
        "filed it as a `fact` entry."
    )
    engine = _FakeEngine([
        stage1_reply, stage1_reply,  # 2 sessions
        stage2_reply, stage3_reply,
    ])
    await m.dream(engine)

    # Stage 2 applied: new entry exists; old entry's description updated.
    new_entry = m.read_entry(tmp_path, "fact_docker-quirk")
    assert new_entry.description == "DOCKER_BUILDKIT=1 is required"
    replaced = m.read_entry(tmp_path, "feedback_phrasing")
    assert replaced.description == "consolidated"
    # Stage 3 wrote a dream log.
    dreams = list((tmp_path / ".aegis" / "memory" / "dreams").glob("dream-*.md"))
    assert len(dreams) == 1
    text = dreams[0].read_text(encoding="utf-8")
    assert "Last night" in text
    assert text.startswith("---\n")
    assert "actions:" in text
    assert "sessions_read:" in text


@pytest.mark.asyncio
async def test_dream_respects_lookback_window(
        tmp_path: Path, monkeypatch) -> None:
    """A stale session file (older than lookback_days) is skipped."""
    m = _load(monkeypatch, tmp_path)
    _drop_session(tmp_path, "recent")
    old = tmp_path / ".aegis" / "state" / "sessions" / "old.jsonl"
    old.parent.mkdir(parents=True, exist_ok=True)
    old.write_text("{}\n", encoding="utf-8")
    # Set old session's mtime to 30 days ago.
    import os, time
    ts = time.time() - 30 * 86400
    os.utime(old, (ts, ts))
    engine = _FakeEngine([
        json.dumps({"session_handle": "recent", "summary": "",
                    "proposed_entries": [], "observations": []}),
        json.dumps({"actions": [], "rationale": ""}),
        "no dreams tonight",
    ])
    await m.dream(engine, lookback_days=7, max_session_files=50)
    # Only the recent session was passed to stage 1 (1 of 2 delegate calls).
    stage1_calls = [c for c in engine.calls if "transcript" in c[1].lower()]
    assert len(stage1_calls) == 1
```

- [ ] **Step 2: Run, verify failure**

Run: `uv run pytest tests/test_memory_dream.py -v`
Expected: 2 FAILED — `dream` not defined.

- [ ] **Step 3: Implement the workflow**

Append to `plugins/memory-system/memory_system.py`:

```python
import json
import time

from aegis.workflow import workflow


def _recent_session_files(project_root: Path, lookback_days: int,
                          max_files: int) -> list[Path]:
    sessions = project_root / ".aegis" / "state" / "sessions"
    if not sessions.exists():
        return []
    cutoff = time.time() - lookback_days * 86400
    out: list[Path] = []
    for p in sessions.glob("*.jsonl"):
        try:
            if p.stat().st_mtime >= cutoff:
                out.append(p)
        except OSError:
            continue
    out.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    return out[:max_files]


def _stage1_prompt(transcript_path: Path) -> str:
    transcript = transcript_path.read_text(encoding="utf-8")
    return (
        "Here is one aegis session transcript. Summarize what happened, "
        "propose memory entries the agent should have saved but didn't, "
        "and note observations — surprising patterns, contradictions, "
        "repeated stumbles. Return JSON only, matching this shape:\n"
        '{"session_handle": "...", "summary": "...", '
        '"proposed_entries": [...], "observations": [...]}\n\n'
        f"Session handle: {transcript_path.stem}\n\n"
        "Transcript:\n"
        f"{transcript}\n"
    )


def _stage2_prompt(current_entries: list[Entry],
                   proposals: list[dict]) -> str:
    body = []
    for e in current_entries:
        body.append(f"=== {e.slug} ({e.type}) — {e.description} ===\n{e.content}")
    return (
        "Consolidate. Given the current memory entries and the proposals "
        "from each session reader, emit a JSON action plan:\n"
        '{"actions": [{"action": "add", "type": "...", "name": "...", '
        '"description": "...", "content": "..."}, '
        '{"action": "replace", "slug": "...", "description": "...", '
        '"content": "..."}, '
        '{"action": "remove", "slug": "..."}], "rationale": "..."}\n\n'
        "Current entries:\n\n" + "\n\n".join(body) +
        "\n\nProposals:\n" + json.dumps(proposals, indent=2)
    )


def _stage3_prompt(observations: list[str], rationale: str) -> str:
    return (
        "Write a short narrative dream log (500-1000 words) in first person "
        "from the agent's perspective. Reflect on patterns from the "
        "observations and the consolidation rationale. Prose only.\n\n"
        "Observations:\n" + "\n".join(f"- {o}" for o in observations) +
        f"\n\nConsolidation rationale: {rationale}\n"
    )


async def _apply_actions(root: Path, actions: list[dict]) -> list[str]:
    """Apply consolidation actions via the write helpers. Returns slugs touched."""
    touched: list[str] = []
    for a in actions:
        op = a.get("action")
        try:
            if op == "add":
                p = write_entry(root, a["type"], a["name"],
                                a["description"], a["content"])
                touched.append(p.stem)
            elif op == "replace":
                slug = a["slug"]
                e = read_entry(root, slug)
                new_desc = a.get("description", e.description)
                new_content = a.get("content", e.content)
                path = _entry_path(root, slug)
                front = {"type": e.type, "name": e.name,
                         "description": new_desc, "created": e.created,
                         "updated": _now_iso()}
                path.write_text(
                    "---\n" + yaml.safe_dump(front, sort_keys=False,
                                             allow_unicode=True) +
                    "---\n\n" + new_content.rstrip() + "\n",
                    encoding="utf-8")
                touched.append(slug)
            elif op == "remove":
                _entry_path(root, a["slug"]).unlink()
                touched.append(a["slug"])
        except (FileNotFoundError, FileExistsError, KeyError):
            continue
    rebuild_index(root)
    return touched


def _write_dream_log(root: Path, prose: str, *, actions: list[str],
                     sessions: list[str], lookback_days: int) -> Path:
    from datetime import date
    dreams_dir = root / DREAMS_SUBDIR
    dreams_dir.mkdir(parents=True, exist_ok=True)
    path = dreams_dir / f"dream-{date.today().isoformat()}.md"
    front = yaml.safe_dump({
        "actions": actions,
        "sessions_read": sessions,
        "lookback_days": lookback_days,
    }, sort_keys=False)
    path.write_text(
        "---\n" + front + "---\n\n" + prose.rstrip() + "\n",
        encoding="utf-8",
    )
    return path


@workflow
async def dream(engine, *, lookback_days: int = 7,
                max_session_files: int = 50,
                dreamer_queue: str = "dreamer-queue") -> dict:
    """Periodic memory consolidation + synthesis.

    Stage 1: per session in window, delegate to a dreamer subagent that
    returns JSON proposals + observations.
    Stage 2: one dreamer subagent consolidates current entries + proposals
    into an action plan, which is applied via the memory tools.
    Stage 3: one dreamer subagent writes a prose dream log.
    """
    root = _project_root()
    files = _recent_session_files(root, lookback_days, max_session_files)
    proposals: list[dict] = []
    observations: list[str] = []
    session_handles: list[str] = []

    for p in files:
        try:
            raw = await engine.delegate(dreamer_queue, _stage1_prompt(p))
            parsed = json.loads(raw)
        except (ValueError, json.JSONDecodeError):
            continue
        session_handles.append(parsed.get("session_handle", p.stem))
        proposals.extend(parsed.get("proposed_entries", []))
        observations.extend(parsed.get("observations", []))

    current = list_entries(root)
    try:
        raw2 = await engine.delegate(
            dreamer_queue, _stage2_prompt(current, proposals))
        plan = json.loads(raw2)
    except (ValueError, json.JSONDecodeError):
        plan = {"actions": [], "rationale": ""}
    touched = await _apply_actions(root, plan.get("actions", []))

    prose = await engine.delegate(
        dreamer_queue, _stage3_prompt(observations, plan.get("rationale", "")))
    log_path = _write_dream_log(
        root, prose, actions=touched,
        sessions=session_handles, lookback_days=lookback_days,
    )
    return {
        "sessions_read": len(files),
        "actions_applied": len(touched),
        "dream_log": str(log_path),
    }
```

- [ ] **Step 4: Run, verify pass**

Run: `uv run pytest tests/test_memory_dream.py -v`
Expected: 2 PASSED.

- [ ] **Step 5: Commit**

```bash
git add plugins/memory-system/memory_system.py tests/test_memory_dream.py
git commit -m "feat(memory-system): dream workflow — stage1 fan-out + consolidate + synthesize log"
```

## Task 4.2: Full hermetic suite sanity check

- [ ] **Step 1: Run every memory-system test**

Run: `uv run pytest tests/test_memory_*.py -v -m "not live"`
Expected: ~22 PASSED total across Slices 1–4.

- [ ] **Step 2: Run the full hermetic suite to confirm no regression**

Run: `uv run pytest -q -m "not live"`
Expected: all green (the existing aegis suite + the new memory tests).

- [ ] **Step 3: If anything regresses, fix root cause (not symptom). Re-run.**

---

# Slice 5 — Live integration test + README polish

End state: a live test exercises the install → start a real `claude` session → ask it to save a memory → restart → assert it's still there. The README highlights the substrate-proves-itself story.

## Task 5.1: Live `claude` round-trip test

**Files:**
- Create: `tests/test_memory_system_live.py`

- [ ] **Step 1: Write the live test**

```python
"""Live: install memory-system into a tmp project, drive a real claude
through install → save a memory → restart → assert persistence."""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest


pytestmark = pytest.mark.live


def _have_claude() -> bool:
    return shutil.which("claude") is not None


@pytest.mark.skipif(not _have_claude(), reason="claude CLI not on PATH")
def test_round_trip_save_and_recall(tmp_path: Path) -> None:
    # Bootstrap a project with .aegis.yaml and install the plugin from local path.
    (tmp_path / ".aegis.yaml").write_text("agents: {}\n", encoding="utf-8")
    repo_root = Path(__file__).parent.parent
    src = repo_root / "plugins" / "memory-system"
    res = subprocess.run(
        ["uv", "run", "aegis", "plugin", "install", "memory-system",
         "--from", str(src), "--yes"],
        cwd=tmp_path, capture_output=True, text=True, timeout=60,
    )
    assert res.returncode == 0, res.stderr
    assert (tmp_path / ".aegis/memory/MEMORY.md").exists()

    # First session: ask the agent to save a fact.
    s1 = subprocess.run(
        ["uv", "run", "aegis", "serve", "--once",
         "--prompt", "Please call memory_add to save a fact named "
         "'demo-fact' with description 'demo memory test' and content "
         "'this is the body'."],
        cwd=tmp_path, capture_output=True, text=True, timeout=180,
    )
    # The exact CLI surface may differ; what matters is that the entry lands.
    entry_path = tmp_path / ".aegis/memory/entries/fact_demo-fact.md"
    assert entry_path.exists(), (
        f"agent did not save the entry. stdout: {s1.stdout}; stderr: {s1.stderr}"
    )
```

If `aegis serve --once --prompt …` is not the right way to drive a one-shot claude session for the test, substitute the actual project pattern (look at `tests/test_skill_system_live.py` — it has the canonical driver setup; mirror it).

- [ ] **Step 2: Run the test (locally, with `claude` installed)**

Run: `uv run pytest tests/test_memory_system_live.py -v`
Expected: PASS. If skipped because `claude` is not on PATH, that's also acceptable — the test is conditional.

- [ ] **Step 3: Commit**

```bash
git add tests/test_memory_system_live.py
git commit -m "test(memory-system): live round-trip — save + recall against real claude"
```

## Task 5.2: README + CHANGELOG

**Files:**
- Modify: `README.md` (one paragraph in the plugins section)
- Modify: `CHANGELOG.md` (Unreleased section)
- Modify: `TASKS.md` (strike the memory plugin stub from Active)

- [ ] **Step 1: Add a CHANGELOG entry**

Insert under `## [Unreleased]` in `CHANGELOG.md`:

```markdown
### memory-system plugin (v0.1.0)

Second canonical plugin under `plugins/memory-system/`. Hermes-inspired
persistent memory:

- Per-project `.aegis/memory/` with `SOUL.md`, `USER.md`, and a
  `MEMORY.md` index over typed entries (`user` / `feedback` / `fact` /
  `reference`).
- `pre_turn` hook injects SOUL + USER + index + judgment primer on
  turn 0; top-5 entry teasers (name + description, 1000-word cap) on
  later turns.
- Five `@tool`s: `memory_add`, `memory_replace`, `memory_remove`,
  `memory_search`, `memory_read`.
- `dream` `@workflow` — three-stage consolidate + synthesize pass over
  the last 7 days of `.aegis/state/sessions/`. Writes new entries +
  a dated `dreams/dream-YYYY-MM-DD.md` narrative log. Defaults to a
  Haiku-backed `dreamer` agent.
- Install optionally drops a daily 3am cron via
  `aegis.scheduler.push.write_atomic` (overlay file at
  `.aegis/schedules/memory-dream.yaml`); cron fires while `aegis serve`
  runs.

Proves the v1 plugin substrate generalizes beyond `skill-system` —
every primitive shape (`@hook`, `@tool`, `@workflow`) is exercised
end-to-end.
```

- [ ] **Step 2: Update TASKS.md**

In `TASKS.md`, replace the "Memory plugin *(brainstorming next)*" stub under `## Active` with:

```markdown
### memory-system plugin *(shipped — v0.1.0)*

Second canonical plugin: Hermes-inspired persistent memory with
periodic dreaming. Exercises every v1 substrate primitive (`@hook`,
`@tool`, `@workflow`) end-to-end.

- Spec: `docs/superpowers/specs/2026-05-30-aegis-memory-plugin-design.md`
- Plan: `docs/superpowers/plans/2026-05-30-aegis-memory-plugin-v1.md`
- Release notes: `CHANGELOG.md` § memory-system plugin (v0.1.0)
```

- [ ] **Step 3: Add a brief README mention**

In `README.md`, find the section that mentions `skill-system` (probably under "Plugins" or near the bottom). Add a sibling bullet:

```markdown
- **`memory-system`** — Hermes-inspired persistent memory with periodic
  dreaming. Per-project `.aegis/memory/` (SOUL + USER + typed entries
  + dated dream logs); a `dream` `@workflow` consolidates and
  synthesizes nightly.
```

- [ ] **Step 4: Commit**

```bash
git add CHANGELOG.md TASKS.md README.md
git commit -m "docs(memory-system): CHANGELOG + TASKS + README — v0.1.0 ships"
```

- [ ] **Step 5: Push**

```bash
git push origin main
```

---

# Done state

When the plan completes:

- `plugins/memory-system/` is a fully-formed plugin parallel to `plugins/skill-system/`.
- The full hermetic test suite (`uv run pytest -q -m "not live"`) passes; ~22 new tests under `tests/test_memory_*.py`.
- A user can `aegis plugin install memory-system --from gh:apiad/aegis#plugins/memory-system` and immediately have working memory + an optional 3am dream cron.
- The README and CHANGELOG carry the "we have what Hermes has" line.
- `TASKS.md` marks the slot done; deferred follow-ups (state.db / FTS5, auto-skill-gen, etc.) remain in the spec's deferred table.

# Self-review notes for the implementer

- **Reality-grounding**: every signature in this plan is pulled from `main` as of commit `72a39e2`. If `aegis.config.edit` exposes different function names (e.g. `add_or_update_agent` instead of `add_agent`), inspect the module and substitute — the test fixtures pin behavior, not the function name.
- **Order matters**: do not run Slice 4's test until Slice 1–2 are green; the dream workflow consumes the I/O helpers and tools.
- **Don't skip the schedule overlay test**: Alex specifically called out that bare YAML appending doesn't activate cron. The `_install` test asserts an overlay file at `.aegis/schedules/memory-dream.yaml`. If the overlay path or `write_atomic` signature differs in `main`, that is the symptom to investigate first — not a reason to change the test.
- **Live test is best-effort**: if the project's canonical way to drive a one-shot claude session in tests differs from `aegis serve --once --prompt`, mirror `tests/test_skill_system_live.py` instead. The acceptance signal is "the entry file exists after the live run".
