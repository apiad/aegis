# `.aegis.yaml`: init, doctor, Settings page and hot reload. Implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** aegis creates (`aegis init`), checks (`aegis doctor`, `config.doctor`) and edits (a Settings page) `.aegis.yaml`, and re-reads the file whenever it changes on disk, so a Settings save only writes the file.

**Architecture:** A new `config.py` owns the file: a `Config` holder that stats it and re-parses on change, a `Snapshot` every reader uses, and the one round-trip writer. `doctor.py` holds detection of installed harnesses (through each harness's existing `probe()`), the checks, and the proposal `init` makes. The CLI, five registered operations and a `config` channel are thin front ends over those two modules; the client adds one view, `#settings`, in its own module.

**Tech Stack:** Python 3.13, ruamel.yaml (safe and round-trip modes), pydantic, Typer/click prompts, Starlette websocket channels, plain ES modules, Playwright for browser tests.

**Spec:** `docs/superpowers/specs/2026-10-08-aegis-config-init-doctor-settings-design.md` (issue #193). Read it before any task.

## Global Constraints

- Work in the worktree `/home/apiad/Workspace/repos/aegis/.claude/worktrees/config-doctor`, branch `feat/config-doctor`. Never commit to `main`; one PR at the end.
- Stage named paths only (`git commit -- <paths>`), conventional commits, English, never amend. Every commit message ends with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.
- Imports inside `src/aegis` are relative; nothing imports `legacy/` (`tests/test_imports.py`).
- Nothing below `cli.py` calls `Path.cwd()` (`tests/test_no_cwd.py`).
- The loader fills in no defaults: a missing field is an `error`, never a value (DESIGN.md).
- The doctor writes nothing: probe stderr goes to a temporary directory.
- A test that runs over 3 s without `@pytest.mark.slow` fails `make test` (`--max-unmarked-duration=3`). Mark it slow, don't speed it up artificially.
- Iterate with the one test file a step touches: `uv run pytest -q tests/<file>.py`. The full `make check` runs once, in Task 8.
- `WATCH_EVERY_S = 1.0`; `VERSION_TIMEOUT_S = 5.0`; `PROBE_TIMEOUT_S = 20.0`; `DETECT_TTL_S = 60.0`.
- `init` proposes: Claude agent `opus` (model `opus`, effort `high`, permission `full`); OpenCode agent named after its first catalog model's last path segment, effort = that model's first variant that is in `EFFORTS`, else `high`, permission `full`; `default_agent` = the Claude agent if any, else the first; queue `general` on the default, `max_parallel: 3`.
- The Settings key is **Alt+S** (Alt+, is already "focus the transcript").

## Review Focus

1. **An editor that truncates before writing.** For a moment the file is empty. Expected: the last good config stays in force (an empty file counts as "does not parse"), so pending queue tasks are not failed. Pinned in Task 1 (`test_an_empty_file_keeps_the_last_good_config`).
2. **Typing in the Settings form while sessions are working.** `render()` runs on every `sessions` patch. Expected: the form is drawn only when the view is entered, not on every render, so focus and half-typed text survive. Pinned in Task 7 (`test_typing_in_settings_survives_session_patches`).
3. **Saving over a file that does not parse.** Expected: the write is refused with `bad_config` and the broken file is left for the editor; the round-trip loader cannot keep its comments. Pinned in Task 2 (`test_a_write_over_a_broken_file_is_refused`).
4. **An agent name with a dot, space or quote.** Expected: its row is found and marked (rows are matched with `CSS.escape`, and `row` comes from Python). Pinned in Task 7 (`test_run_doctor_marks_the_row`, which uses the agent name `bad.one`).
5. **A multi-line priming saved from the form.** Expected: it is written as a `|` block and reads back byte-identical. Pinned in Task 2 (`test_a_multiline_priming_is_a_literal_block`).

---

### Task 1: The config holder (`config.py`) and the parse split

**Files:**
- Create: `src/aegis/config.py`
- Modify: `src/aegis/agents.py` (add `agents_from`; `load_agents` uses it)
- Modify: `src/aegis/queues.py:68-93` (split `queues_from` out of `load_queues`)
- Test: `tests/test_config.py`

**Interfaces:**
- Produces: `agents.agents_from(data: dict) -> list[Agent]`; `queues.queues_from(raw: Any) -> dict[str, dict]`; in `config.py`: `Stamp = tuple[int, int, int]`, `stamp_of(path) -> Stamp | None`, `load(path) -> tuple[dict | None, str | None]`, `TOP_KEYS`, `AGENT_KEYS`, `QUEUE_KEYS`, `WATCH_EVERY_S`, `Finding(level, where, message, row=None)` with `.wire()`, `AgentDoc`, `QueueDoc`, `ConfigDoc`, `doc_from(data) -> ConfigDoc`, `Snapshot` (fields `path, exists, stamp, agents, default_agent, queues, unknown_keys, doc, error`; `Snapshot.parse(path, stamp, data)`; `.wire()`), `Config(config_root, on_change)` with `.path`, `.current() -> Snapshot`, `async .watch(every=WATCH_EVERY_S)`.

- [ ] **Step 1: Write the failing tests**

`tests/test_config.py`:

```python
import asyncio
from pathlib import Path

from aegis.config import Config, ConfigDoc, QueueDoc, Snapshot, doc_from

from .conftest import until

A = "default_agent: a\nagents:\n  a: {harness: claude-code, model: opus, effort: high, permission: full}\n"
AB = A + "  b: {harness: claude-code, model: sonnet, effort: low, permission: read}\n"


def config(tmp_path: Path, text: str | None = None) -> tuple[Config, list]:
    if text is not None:
        (tmp_path / ".aegis.yaml").write_text(text)
    seen: list[Snapshot] = []
    return Config(tmp_path, seen.append), seen


def names(s: Snapshot) -> list[str]:
    return [a.name for a in s.agents]


def test_a_change_on_disk_is_seen_without_a_tick(tmp_path):
    c, seen = config(tmp_path, A)
    assert names(c.current()) == ["a"] and seen == []
    (tmp_path / ".aegis.yaml").write_text(AB)
    assert names(c.current()) == ["a", "b"]
    assert len(seen) == 1
    c.current()
    assert len(seen) == 1, "an unchanged file fires nothing"


def test_a_parse_error_keeps_the_last_good_config(tmp_path):
    c, seen = config(tmp_path, A)
    (tmp_path / ".aegis.yaml").write_text("agents: [1, 2\n")
    s = c.current()
    assert names(s) == ["a"] and s.default_agent == "a"
    assert s.error and ".aegis.yaml" in s.error
    c.current()
    assert len(seen) == 1, "the same broken file is parsed once"
    (tmp_path / ".aegis.yaml").write_text(AB)
    assert names(c.current()) == ["a", "b"] and c.current().error is None


def test_an_empty_file_keeps_the_last_good_config(tmp_path):
    c, _ = config(tmp_path, A)
    (tmp_path / ".aegis.yaml").write_text("")
    s = c.current()
    assert names(s) == ["a"] and s.queues == {} and "empty" in s.error


def test_a_broken_file_at_start_has_no_agents_and_says_why(tmp_path):
    c, _ = config(tmp_path, "agents: [1, 2\n")
    s = c.current()
    assert s.exists and s.agents == () and s.error


def test_deleting_the_file_empties_the_config(tmp_path):
    c, seen = config(tmp_path, A)
    (tmp_path / ".aegis.yaml").unlink()
    s = c.current()
    assert not s.exists and s.agents == () and s.default_agent is None
    assert len(seen) == 1


def test_a_file_created_later_is_picked_up(tmp_path):
    c, _ = config(tmp_path)
    assert not c.current().exists
    (tmp_path / ".aegis.yaml").write_text(A)
    assert names(c.current()) == ["a"]


def test_unknown_top_level_keys_are_listed(tmp_path):
    c, _ = config(tmp_path, "scheduler: {tick_seconds: 5}\nvoice: {preview: true}\n" + A)
    assert c.current().unknown_keys == ("scheduler", "voice")


def test_the_doc_keeps_a_broken_queue_editable(tmp_path):
    d = doc_from({"queues": {"broken": {"agent": "a"}, "ok": {"agent": "a", "max_parallel": 2}}})
    assert d.queues == [
        QueueDoc(name="broken", agent="a", max_parallel=None),
        QueueDoc(name="ok", agent="a", max_parallel=2),
    ]


def test_the_wire_carries_the_doc_the_stamp_and_the_vocabulary(tmp_path):
    c, _ = config(tmp_path, A)
    w = c.current().wire()
    assert w["exists"] and w["error"] is None and len(w["stamp"]) == 3
    assert w["root"] == str(tmp_path)
    assert ConfigDoc.model_validate(w["doc"]).agents[0].name == "a"
    assert w["vocab"]["efforts"] == ["low", "medium", "high", "xhigh", "max"]


async def test_the_watch_loop_fires_once_per_change(tmp_path):
    c, seen = config(tmp_path, A)
    task = asyncio.create_task(c.watch(every=0.01))
    try:
        (tmp_path / ".aegis.yaml").write_text(AB)
        await until(lambda: len(seen) == 1, what="the change")
        await asyncio.sleep(0.05)
        assert len(seen) == 1
    finally:
        task.cancel()
```

- [ ] **Step 2: Run them to see them fail**

Run: `uv run pytest -q tests/test_config.py`
Expected: FAIL at collection, `ModuleNotFoundError: No module named 'aegis.config'`.

- [ ] **Step 3: Split the parsers out of the loaders**

In `src/aegis/agents.py`, replace `load_agents` with:

```python
def agents_from(data: dict) -> list[Agent]:
    """Every agent in a parsed config's ``agents:`` map."""
    agents = data.get("agents")
    if not isinstance(agents, dict):
        return []
    return [_agent(name, raw) for name, raw in agents.items()]


def load_agents(config_root: Path) -> list[Agent]:
    return agents_from(read_config(config_root))
```

In `src/aegis/queues.py`, add `from typing import Any` (if `TYPE_CHECKING` is the only `typing` import, extend it) and replace `load_queues` with:

```python
def queues_from(raw: Any) -> dict[str, dict]:
    """Every queue in a parsed ``queues:`` map. A queue that does not name its
    agent and a positive ``max_parallel`` is kept with an ``error``, so
    enqueueing on it says what is wrong; nothing in .aegis.yaml is a default
    (agents.py)."""
    if not isinstance(raw, dict):
        return {}
    out: dict[str, dict] = {}
    for name, q in raw.items():
        q = q if isinstance(q, dict) else {}
        agent, limit = q.get("agent"), q.get("max_parallel")
        missing = [
            k for k, v in (("agent", agent), ("max_parallel", limit)) if v in (None, "")
        ]
        if missing:
            verb = "is" if len(missing) == 1 else "are"
            out[str(name)] = {"error": f"{', '.join(missing)} {verb} missing"}
        elif isinstance(limit, bool) or not isinstance(limit, int) or limit < 1:
            out[str(name)] = {
                "error": f"max_parallel {limit!r} is not a positive integer"
            }
        else:
            out[str(name)] = {"agent": str(agent), "max_parallel": limit}
    return out


def load_queues(config_root: Path) -> dict[str, dict]:
    try:
        raw = read_config(config_root).get("queues")
    except ConfigError:
        return {}
    return queues_from(raw)
```

- [ ] **Step 4: Write `src/aegis/config.py` (holder half; the writer comes in Task 2)**

```python
"""``.aegis.yaml``: the one place aegis's configuration lives.

The server holds a parsed copy (``Snapshot``) and nothing else. ``Config.current``
stats the file and re-reads it when its (mtime_ns, size, inode) changed, so every
reader sees an edit made anywhere, by the Settings page or a text editor, with no
restart; ``watch`` calls it once a second so a change is published even when
nobody reads. A file that does not parse, or is empty, leaves the last one that
did in force, with the error in the snapshot: a half-typed edit, or an editor
that truncates before it writes, must not stop every spawn or fail every queued
task. A deleted file is honoured: no agents, no queues.

``write`` is the one writer, for ``aegis init`` and the Settings page.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from dataclasses import asdict, dataclass, field, replace
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field
from ruamel.yaml import YAML, YAMLError

from .agents import EFFORTS, HARNESSES, PERMISSION_ORDER, Agent, agents_from
from .queues import queues_from
from .roots import CONFIG_FILE

log = logging.getLogger("aegis.config")

WATCH_EVERY_S = 1.0
TOP_KEYS = ("agents", "queues", "default_agent")
AGENT_KEYS = ("harness", "provider", "model", "effort", "permission", "priming")
QUEUE_KEYS = ("agent", "max_parallel")

Stamp = tuple[int, int, int]


def stamp_of(path: Path) -> Stamp | None:
    try:
        st = path.stat()
    except FileNotFoundError:
        return None
    return (st.st_mtime_ns, st.st_size, st.st_ino)


def load(path: Path) -> tuple[dict | None, str | None]:
    """The file's top-level mapping, or why it has none."""
    try:
        text = path.read_text()
        data = YAML(typ="safe").load(text)
    except (YAMLError, OSError, UnicodeDecodeError) as e:
        return None, f"{path}: {e}"
    if not text.strip():
        return None, f"{path}: the file is empty"
    if not isinstance(data, dict):
        return None, f"{path}: the top level is not a mapping"
    return data, None


@dataclass(frozen=True)
class Finding:
    level: Literal["ok", "warn", "error"]
    where: str  # "file", "agents.deepseek.model", "harness.opencode", "state"
    message: str
    # The Settings row it marks: "agents.<name>", "queues.<name>",
    # "default_agent", or None for the file, a harness and the state.
    row: str | None = None

    def wire(self) -> dict:
        return asdict(self)


class AgentDoc(BaseModel):
    model_config = {"extra": "forbid"}
    name: str
    harness: str = ""
    model: str = ""
    effort: str = ""
    permission: str = ""
    priming: str | None = None


class QueueDoc(BaseModel):
    model_config = {"extra": "forbid"}
    name: str
    agent: str = ""
    max_parallel: int | None = None


class ConfigDoc(BaseModel):
    """What the Settings form edits and ``aegis init`` proposes."""

    model_config = {"extra": "forbid"}
    agents: list[AgentDoc] = Field(default_factory=list)
    default_agent: str | None = None
    queues: list[QueueDoc] = Field(default_factory=list)


def doc_from(data: dict) -> ConfigDoc:
    agents = [
        AgentDoc(
            name=a.name,
            harness=a.harness,
            model=a.model,
            effort=a.effort,
            permission=a.permission,
            priming=a.priming,
        )
        for a in agents_from(data)
    ]
    queues = []
    raw = data.get("queues")
    for name, q in (raw if isinstance(raw, dict) else {}).items():
        q = q if isinstance(q, dict) else {}
        limit = q.get("max_parallel")
        ok = isinstance(limit, int) and not isinstance(limit, bool)
        queues.append(
            QueueDoc(
                name=str(name),
                agent=str(q.get("agent") or ""),
                max_parallel=limit if ok else None,
            )
        )
    d = data.get("default_agent")
    return ConfigDoc(agents=agents, default_agent=str(d) if d else None, queues=queues)


@dataclass(frozen=True)
class Snapshot:
    path: Path
    exists: bool = False
    stamp: Stamp | None = None
    agents: tuple[Agent, ...] = ()
    default_agent: str | None = None
    queues: dict[str, dict] = field(default_factory=dict)
    unknown_keys: tuple[str, ...] = ()
    doc: ConfigDoc = field(default_factory=ConfigDoc)
    # Why the file on disk is not the one in force, when it does not parse.
    error: str | None = None

    @classmethod
    def parse(cls, path: Path, stamp: Stamp | None, data: dict) -> Snapshot:
        d = data.get("default_agent")
        return cls(
            path=path,
            exists=True,
            stamp=stamp,
            agents=tuple(agents_from(data)),
            default_agent=str(d) if d else None,
            queues=queues_from(data.get("queues")),
            unknown_keys=tuple(str(k) for k in data if k not in TOP_KEYS),
            doc=doc_from(data),
        )

    def wire(self) -> dict:
        return {
            "path": str(self.path),
            "root": str(self.path.parent),
            "exists": self.exists,
            "stamp": list(self.stamp) if self.stamp else None,
            "error": self.error,
            "unknown_keys": list(self.unknown_keys),
            "doc": self.doc.model_dump(),
            "vocab": {
                "harnesses": list(HARNESSES),
                "efforts": list(EFFORTS),
                "permissions": list(PERMISSION_ORDER),
            },
        }


class Config:
    def __init__(
        self,
        config_root: Path,
        on_change: Callable[[Snapshot], None] = lambda s: None,
    ) -> None:
        self.path = config_root / CONFIG_FILE
        self._on_change = on_change
        self._snap = self._read(stamp_of(self.path), Snapshot(self.path))

    def _read(self, stamp: Stamp | None, last: Snapshot) -> Snapshot:
        if stamp is None:
            return Snapshot(self.path)
        data, error = load(self.path)
        if data is None:
            return replace(last, exists=True, stamp=stamp, error=error)
        return Snapshot.parse(self.path, stamp, data)

    def current(self) -> Snapshot:
        """The config in force: one ``stat``, and a re-read when it moved."""
        stamp = stamp_of(self.path)
        if stamp != self._snap.stamp:
            self._snap = self._read(stamp, self._snap)
            self._on_change(self._snap)
        return self._snap

    async def watch(self, every: float = WATCH_EVERY_S) -> None:
        while True:
            await asyncio.sleep(every)
            try:
                self.current()
            except Exception:
                log.exception("reading %s", self.path)
```

- [ ] **Step 5: Run the new and the old config tests**

Run: `uv run pytest -q tests/test_config.py tests/test_agent_config.py`
Expected: all PASS. `test_malformed_config_names_the_file` still passes because `load_agents` still goes through `read_config`.

- [ ] **Step 6: Commit**

```bash
git commit -m "feat(config): one holder for .aegis.yaml that re-reads it when it changes (#193)

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>" -- src/aegis/config.py src/aegis/agents.py src/aegis/queues.py tests/test_config.py
```

---

### Task 2: The writer: validate, refuse stale, edit in place

**Files:**
- Modify: `src/aegis/config.py` (append `validate`, `write` and helpers)
- Test: `tests/test_config_write.py`

**Interfaces:**
- Consumes: `ConfigDoc`, `AgentDoc`, `QueueDoc`, `Finding`, `stamp_of`, `Stamp`, `agents_from` (Task 1).
- Produces: `config.validate(doc: ConfigDoc) -> list[Finding]` (errors only, each with `row`); `config.write(path: Path, doc: ConfigDoc, expected: Stamp | None) -> list[Finding]`, which returns the problems that stopped it (empty list means written), raises `OpError("stale", …)` when the file's stamp is not `expected`, and raises `OpError("bad_config", …)` when the file on disk does not parse.

- [ ] **Step 1: Write the failing tests**

`tests/test_config_write.py`:

```python
from pathlib import Path

import pytest
from ruamel.yaml import YAML

from aegis.config import AgentDoc, ConfigDoc, QueueDoc, doc_from, stamp_of, validate, write
from aegis.ops import OpError

# The Workspace's own .aegis.yaml as of 2026-10-08: comments, the string
# `provider:` form, a pinned model.
WORKSPACE = """\
agents:
  opus:
    provider: claude-code
    model: opus
    effort: high
    permission: full
  # Cheap read-only profile for small one-shot sessions. Pinned rather than
  # the `haiku` alias so its cost does not drift with the CLI.
  haiku:
    provider: claude-code
    model: claude-haiku-4-5-20251001
    effort: low
    permission: read
  # Background research and bulk work on Alex's OpenCode Go allowance.
  deepseek:
    provider: opencode
    model: opencode-go/deepseek-v4-pro
    effort: high
    permission: full
default_agent: opus
queues:
  general:
    agent: opus
    max_parallel: 5
  deepseek:
    agent: deepseek
    max_parallel: 6
"""


def setup(tmp_path: Path, text: str) -> tuple[Path, ConfigDoc]:
    p = tmp_path / ".aegis.yaml"
    p.write_text(text)
    return p, doc_from(YAML(typ="safe").load(text))


def safe(p: Path) -> dict:
    return YAML(typ="safe").load(p.read_text())


def test_comments_forms_and_untouched_agents_survive_an_edit(tmp_path):
    p, doc = setup(tmp_path, WORKSPACE)
    doc.agents[0].effort = "max"
    assert write(p, doc, stamp_of(p)) == []
    text = p.read_text()
    assert "# Cheap read-only profile" in text and "# Background research" in text
    assert "harness:" not in text, "the provider: form is kept"
    data = safe(p)
    assert data["agents"]["opus"] == {"provider": "claude-code", "model": "opus", "effort": "max", "permission": "full"}
    assert data["agents"]["haiku"]["model"] == "claude-haiku-4-5-20251001"
    assert text.replace("effort: max", "effort: high", 1) == WORKSPACE


def test_the_nested_provider_form_is_edited_where_its_fields_are(tmp_path):
    p, doc = setup(
        tmp_path,
        "agents:\n  n:\n    provider: {name: claude-code, model: haiku, effort: low, permission: read}\n",
    )
    doc.agents[0].model = "opus"
    assert write(p, doc, stamp_of(p)) == []
    assert safe(p)["agents"]["n"] == {
        "provider": {"name": "claude-code", "model": "opus", "effort": "low", "permission": "read"}
    }


def test_unknown_keys_are_kept(tmp_path):
    p, doc = setup(tmp_path, "scheduler: {tick_seconds: 5}\n" + WORKSPACE)
    assert write(p, doc, stamp_of(p)) == []
    assert safe(p)["scheduler"] == {"tick_seconds": 5}


def test_added_removed_and_emptied_sections(tmp_path):
    p, doc = setup(tmp_path, WORKSPACE)
    doc.agents = [a for a in doc.agents if a.name != "haiku"]
    doc.agents.append(AgentDoc(name="new", harness="claude-code", model="sonnet", effort="low", permission="read"))
    doc.queues = []
    assert write(p, doc, stamp_of(p)) == []
    data = safe(p)
    assert list(data["agents"]) == ["opus", "deepseek", "new"]
    assert data["agents"]["new"] == {"harness": "claude-code", "model": "sonnet", "effort": "low", "permission": "read"}
    assert "queues" not in data


def test_a_multiline_priming_is_a_literal_block(tmp_path):
    p, doc = setup(tmp_path, WORKSPACE)
    doc.agents[0].priming = "You review.\nRank by severity.\n"
    assert write(p, doc, stamp_of(p)) == []
    assert "priming: |" in p.read_text()
    assert safe(p)["agents"]["opus"]["priming"] == "You review.\nRank by severity.\n"


def test_a_stale_stamp_is_refused_and_nothing_is_written(tmp_path):
    p, doc = setup(tmp_path, WORKSPACE)
    old = stamp_of(p)
    p.write_text(WORKSPACE + "# edited in an editor\n")
    before = p.read_bytes()
    with pytest.raises(OpError) as e:
        write(p, doc, old)
    assert e.value.code == "stale"
    assert p.read_bytes() == before


def test_expecting_no_file_when_one_exists_is_stale(tmp_path):
    p, doc = setup(tmp_path, WORKSPACE)
    with pytest.raises(OpError) as e:
        write(p, doc, None)
    assert e.value.code == "stale"


def test_a_write_over_a_broken_file_is_refused(tmp_path):
    p, _ = setup(tmp_path, "agents: [1, 2\n")
    before = p.read_bytes()
    with pytest.raises(OpError) as e:
        write(p, ConfigDoc(), stamp_of(p))
    assert e.value.code == "bad_config"
    assert p.read_bytes() == before


def test_an_invalid_document_writes_nothing_and_names_each_row(tmp_path):
    p, doc = setup(tmp_path, WORKSPACE)
    doc.agents[0].effort = "huge"
    doc.queues.append(QueueDoc(name="orphan", agent="nobody", max_parallel=0))
    doc.default_agent = "ghost"
    before = p.read_bytes()
    problems = write(p, doc, stamp_of(p))
    assert p.read_bytes() == before
    assert {f.row for f in problems} == {"agents.opus", "queues.orphan", "default_agent"}
    assert all(f.level == "error" for f in problems)


def test_validate_refuses_duplicates_and_blank_names(tmp_path):
    same = AgentDoc(name="a", harness="claude-code", model="opus", effort="high", permission="full")
    rows = [f.where for f in validate(ConfigDoc(agents=[same, same, same.model_copy(update={"name": " "})]))]
    assert "agents.a" in rows and "agents. " in rows


def test_a_new_file_is_created_from_nothing(tmp_path):
    p = tmp_path / ".aegis.yaml"
    doc = ConfigDoc(
        agents=[AgentDoc(name="opus", harness="claude-code", model="opus", effort="high", permission="full")],
        default_agent="opus",
        queues=[QueueDoc(name="general", agent="opus", max_parallel=3)],
    )
    assert write(p, doc, None) == []
    assert p.read_text().startswith("# aegis configuration.")
    data = safe(p)
    assert list(data) == ["default_agent", "agents", "queues"]
    assert doc_from(data) == doc
```

- [ ] **Step 2: Run them to see them fail**

Run: `uv run pytest -q tests/test_config_write.py`
Expected: FAIL at collection, `ImportError: cannot import name 'validate' from 'aegis.config'`.

- [ ] **Step 3: Append the writer to `src/aegis/config.py`**

Add to the imports: `import os`, `from io import StringIO`, `from ruamel.yaml.comments import CommentedMap`, `from ruamel.yaml.scalarstring import LiteralScalarString`, `from .ops import OpError`. Extend the module docstring's last paragraph with: "It refuses a stale write (the file changed since the caller read it), validates first and writes nothing on a problem, and edits the document in place in ruamel's round-trip mode, so comments, key order, each agent's form (`harness:`, `provider:` or a nested `provider:` map) and keys aegis does not read survive." Then append:

```python
def validate(doc: ConfigDoc) -> list[Finding]:
    """What stops ``doc`` being written: each a Finding on its Settings row."""
    out: list[Finding] = []

    def err(row: str, where: str, message: str) -> None:
        out.append(Finding("error", where, message, row))

    names = [a.name for a in doc.agents]
    for a in doc.agents:
        row = f"agents.{a.name}"
        if not a.name.strip():
            err(row, row, "an agent needs a name")
        elif names.count(a.name) > 1:
            err(row, row, f"two agents are named {a.name!r}")
        if a.harness and a.harness not in HARNESSES:
            err(row, f"{row}.harness", f"harness {a.harness!r} is not one of {', '.join(HARNESSES)}")
            continue
        (agent,) = agents_from({"agents": {a.name: a.model_dump(exclude={"name"})}})
        if agent.error:
            err(row, row, agent.error)
    queues = [q.name for q in doc.queues]
    for q in doc.queues:
        row = f"queues.{q.name}"
        if not q.name.strip():
            err(row, row, "a queue needs a name")
        elif queues.count(q.name) > 1:
            err(row, row, f"two queues are named {q.name!r}")
        if q.agent not in names:
            err(row, f"{row}.agent", f"no agent named {q.agent!r}" if q.agent else "agent is missing")
        if q.max_parallel is None or q.max_parallel < 1:
            err(row, f"{row}.max_parallel", "max_parallel is not a positive integer")
    if doc.default_agent and doc.default_agent not in names:
        err("default_agent", "default_agent", f"no agent named {doc.default_agent!r}")
    return out


def _put(node: dict, key: str, value: object) -> None:
    # Only a changed value is assigned, so an untouched one keeps its quotes.
    if node.get(key) != value:
        node[key] = value


def _section(data: CommentedMap, key: str) -> CommentedMap:
    node = data.get(key)
    if not isinstance(node, CommentedMap):
        node = data[key] = CommentedMap()
    return node


def _entries(data: CommentedMap, key: str, names: list[str]) -> CommentedMap:
    """The ``key`` map holding exactly ``names``: gone ones deleted, new ones
    appended at the end."""
    section = _section(data, key)
    for name in [k for k in section if k not in names]:
        del section[name]
    for name in names:
        if not isinstance(section.get(name), dict):
            section[name] = CommentedMap()
    return section


def _set_agent(node: CommentedMap, a: AgentDoc) -> None:
    provider = node.get("provider")
    if isinstance(provider, dict):
        _put(provider, "name", a.harness)
    elif isinstance(provider, str):
        _put(node, "provider", a.harness)
    else:
        _put(node, "harness", a.harness)
    for f in ("model", "effort", "permission"):
        holder = provider if isinstance(provider, dict) and f in provider else node
        _put(holder, f, getattr(a, f))
    if a.priming:
        if node.get("priming") != a.priming:
            node["priming"] = (
                LiteralScalarString(a.priming) if "\n" in a.priming else a.priming
            )
    else:
        node.pop("priming", None)


def _apply(data: CommentedMap, doc: ConfigDoc) -> None:
    if doc.default_agent:
        _put(data, "default_agent", doc.default_agent)
    else:
        data.pop("default_agent", None)
    if doc.agents:
        agents = _entries(data, "agents", [a.name for a in doc.agents])
        for a in doc.agents:
            _set_agent(agents[a.name], a)
    else:
        data.pop("agents", None)
    if doc.queues:
        queues = _entries(data, "queues", [q.name for q in doc.queues])
        for q in doc.queues:
            _put(queues[q.name], "agent", q.agent)
            _put(queues[q.name], "max_parallel", q.max_parallel)
    else:
        data.pop("queues", None)


def write(path: Path, doc: ConfigDoc, expected: Stamp | None) -> list[Finding]:
    """Write ``doc`` to ``path``. Returns the problems that stopped it; an
    empty list means it was written."""
    if stamp_of(path) != expected:
        raise OpError("stale", f"{path} changed on disk since it was read; reload it")
    if problems := validate(doc):
        return problems
    rt = YAML()
    rt.preserve_quotes = True
    data = None
    if expected is not None:
        try:
            data = rt.load(path.read_text())
        except YAMLError as e:
            raise OpError(
                "bad_config", f"{path} does not parse; fix it in an editor first: {e}"
            ) from e
    if not isinstance(data, CommentedMap):
        data = CommentedMap()
        data.yaml_set_start_comment("aegis configuration. `aegis doctor` checks it.")
    _apply(data, doc)
    buf = StringIO()
    rt.dump(data, buf)
    tmp = path.with_name(f".{path.name}.tmp")
    tmp.write_text(buf.getvalue())
    os.replace(tmp, path)
    return []
```

- [ ] **Step 4: Run the writer tests**

Run: `uv run pytest -q tests/test_config_write.py tests/test_config.py`
Expected: all PASS. If `test_comments_forms_and_untouched_agents_survive_an_edit` fails only on its last assertion (byte equality), print `p.read_text()` and find what ruamel re-emitted differently. Fix it in `write` (for example `rt.width = 4096` if it re-wrapped a line); do not loosen the test.

- [ ] **Step 5: Commit**

```bash
git commit -m "feat(config): one writer that keeps comments and refuses a stale or invalid write (#193)

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>" -- src/aegis/config.py tests/test_config_write.py
```

---

### Task 3: The server follows the file

**Files:**
- Modify: `src/aegis/app.py` (build `Config`, `_on_config`, watch task in `boot`/`shutdown`, `_agents`, `agents.list`, the `config` channel in `_resolve`)
- Modify: `src/aegis/queues.py` (`Queues` takes the `Config`; `queues` becomes a property; `_start` resolves from the snapshot)
- Modify: `src/aegis/agents.py` (delete `default_agent()`, now unused)
- Test: `tests/test_agents.py` (append two tests)

**Interfaces:**
- Consumes: `Config`, `Snapshot` (Task 1).
- Produces: `App.config: Config`; channel `config` (snapshot = `Snapshot.wire()`, patch op `{"set": <wire>}`); `agents.list` gains `"config_error": str | None`; `Queues(registry, monitors, path, config)`.

- [ ] **Step 1: Write the failing tests** (append to `tests/test_agents.py`)

```python
async def test_a_queue_added_on_disk_takes_tasks_without_a_restart(world, tmp_path):
    (tmp_path / ".aegis.yaml").write_text(CONFIG + "  late: {agent: opus, max_parallel: 1}\n")
    r = await world.app.registry.call("queue.enqueue", {"queue": "late", "payload": "say hi"})
    t = world.app.queues.tasks[r["task_id"]]
    await until(lambda: t.status == "completed", timeout=12, what="the late queue's task")


async def test_raising_max_parallel_on_disk_starts_a_waiting_task(world, tmp_path):
    flag = tmp_path / "hold"
    hold = mcp("monitor_start", description="hold", done=f"test -f {flag}", progress=None, interval_s=1)
    ids = [
        (await world.app.registry.call("queue.enqueue", {"queue": "solo", "payload": hold}))["task_id"]
        for _ in range(2)
    ]
    first, second = (world.app.queues.tasks[i] for i in ids)
    await until(lambda: first.status == "running", timeout=8, what="the first worker")
    assert second.status == "pending"
    (tmp_path / ".aegis.yaml").write_text(CONFIG.replace("solo: {agent: opus, max_parallel: 1}", "solo: {agent: opus, max_parallel: 2}"))
    await until(lambda: second.status == "running", timeout=5, what="the raised limit")
    flag.touch()


async def test_a_broken_file_keeps_spawning_with_the_last_good_agents(world, tmp_path):
    (tmp_path / ".aegis.yaml").write_text("agents: [1, 2\n")
    roster = await world.app.registry.call("agents.list", {})
    assert [a["name"] for a in roster["agents"]] == ["opus", "reviewer"]
    assert ".aegis.yaml" in roster["config_error"]
    await world.spawn()
```

`mcp` is already defined in `tests/test_agents.py` (used by `test_a_worker_with_a_live_monitor_is_not_finished`). Check its signature with `grep -n "^def mcp" tests/test_agents.py` before running.

- [ ] **Step 2: Run them to see them fail**

Run: `uv run pytest -q tests/test_agents.py -k "on_disk or broken_file"`
Expected: the first fails with `unknown_queue: no queue 'late'` and the second times out on `the raised limit`, because queues are read once at boot. The third fails on `agents.list` with `bad_config`.

- [ ] **Step 3: `Queues` reads the snapshot**

In `src/aegis/queues.py`: change the import to `from .agents import ConfigError, read_config, resolve`, and add `from .config import Config` under `if TYPE_CHECKING:`. Then:

```python
    def __init__(
        self, registry: Registry, monitors: Monitors, path: Path, config: Config
    ) -> None:
        self._registry = registry
        self._monitors = monitors
        self._path = path
        self._config = config
        self.tasks: dict[str, Task] = {}
        self._dispatching = False

    @property
    def queues(self) -> dict[str, dict]:
        """The queues in .aegis.yaml as it is now (config.py)."""
        return self._config.current().queues
```

In `_start`, replace the first `try` block with:

```python
        try:
            agents = list(self._config.current().agents)
            spec = resolve(agents, None, q["agent"], {}, Path(t.cwd))
        except OpError as e:
            self._fail(t, f"the queue's agent {q['agent']!r} cannot start: {e.message}")
            return
```

Add one sentence to the module docstring: "The queues are read from `.aegis.yaml` as it is at each dispatch (config.py), so a queue added or changed on disk takes tasks with no restart, and the app dispatches again on every change."

- [ ] **Step 4: The app holds the `Config` and publishes it**

In `src/aegis/app.py`:

- Imports: add `import asyncio` and `import contextlib`; in the `.agents` import drop `ConfigError`, `default_agent` and `load_agents`; add `from .config import Config, Snapshot`.
- In `App.__init__`, before `self.queues = Queues(...)`:

```python
        self.config = Config(roots.config_root, self._on_config)
        self._config_task: asyncio.Task | None = None
        self._background: set[asyncio.Task] = set()
```

  and pass it: `self.queues = Queues(self.sessions, self.monitors, roots.state_root / "tasks.jsonl", self.config)`.
- Add the method:

```python
    def _on_config(self, snap: Snapshot) -> None:
        """Every change to .aegis.yaml, however it was made: the page and the
        composer follow it, and the queues start what it now allows."""
        self.channels.publish("config", [{"set": snap.wire()}])
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        t = loop.create_task(self.queues.dispatch())
        self._background.add(t)
        t.add_done_callback(self._background.discard)
```

- `boot`: first line `self._config_task = asyncio.create_task(self.config.watch())`.
- `shutdown`: first lines

```python
        if self._config_task is not None:
            self._config_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._config_task
```

- `_resolve`: add `if name == "config": return lambda: self.config.current().wire()`.
- `_agents`:

```python
    def _agents(self):
        snap = self.config.current()
        return list(snap.agents), snap.default_agent
```

- `agents_list`: add `"config_error": self.config.current().error,` to the returned dict.
- Update the module docstring's channel list: add "``config`` (``.aegis.yaml`` as aegis holds it; patches ``set``)".

Run `grep -n "_agents()\|load_agents\|default_agent(" src/aegis/*.py` and make sure nothing still reads the file per call. Delete `default_agent()` from `agents.py`.

- [ ] **Step 5: Run the tests that build an App**

Run: `uv run pytest -q tests/test_agents.py tests/test_agent_config.py tests/test_web.py tests/test_session.py`
Expected: all PASS, including the three new tests.

- [ ] **Step 6: Commit**

```bash
git commit -m "feat(config): the server re-reads .aegis.yaml when it changes, queues included (#193)

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>" -- src/aegis/app.py src/aegis/queues.py src/aegis/agents.py tests/test_agents.py
```

---

### Task 4: Detection, the doctor and the proposal (`doctor.py`)

**Files:**
- Create: `src/aegis/doctor.py`
- Modify: `tests/fake_claude.py` (`main`: answer `--version`)
- Modify: `tests/fake_opencode.py` (`main`: answer `--version`)
- Test: `tests/test_doctor.py`

**Interfaces:**
- Consumes: `Finding`, `Snapshot.parse`, `load`, `TOP_KEYS`, `AGENT_KEYS`, `QUEUE_KEYS`, `AgentDoc`, `QueueDoc`, `ConfigDoc` (Tasks 1-2); `harness.harness_for(name, claude_bin, opencode_bin)`, `Harness.probe(spec, stderr_path) -> Catalog`; `claude.control.Model` (`value, resolved, label, efforts`, `.wire()`); `session.SpawnSpec`; `roots.Roots`, `roots.legacy_state`.
- Produces: `Found(harness, bin, version=None, models=(), error=None)` with `.wire()`; `LABELS`; `async detect(cwd: Path, bins: dict[str, str], only: Iterable[str] = HARNESSES) -> list[Found]`; `async doctor(roots: Roots, bins: dict[str, str], start: Path | None = None) -> list[Finding]`; `propose(found: list[Found]) -> ConfigDoc`. `bins` is always `{"claude-code": <bin>, "opencode": <bin>}`.

- [ ] **Step 1: Teach both fakes `--version`**

`tests/fake_claude.py`, first lines of `main()`:

```python
    if sys.argv[1:2] == ["--version"]:
        print("0.0-fake (Claude Code)")
        return
```

`tests/fake_opencode.py`, first lines of `main()` after `args = sys.argv[1:]`:

```python
    if args[:1] == ["--version"]:
        print("0.0-fake")
        return
```

- [ ] **Step 2: Write the failing tests**

`tests/test_doctor.py`:

```python
from pathlib import Path

import pytest

from aegis.claude.control import Model
from aegis.doctor import Found, detect, doctor, propose
from aegis.roots import make_roots

GOOD = """\
default_agent: opus
agents:
  opus: {harness: claude-code, model: opus, effort: high, permission: full}
  pro: {harness: opencode, model: opencode-go/fake-pro, effort: high, permission: full}
queues:
  general: {agent: opus, max_parallel: 3}
"""


def bins(claude: str, opencode: str) -> dict[str, str]:
    return {"claude-code": claude, "opencode": opencode}


async def run(tmp_path: Path, text: str | None, claude: str, opencode: str):
    if text is not None:
        (tmp_path / ".aegis.yaml").write_text(text)
    return await doctor(make_roots(tmp_path, tmp_path), bins(claude, opencode))


def at(findings, where):
    return [f for f in findings if f.where == where]


@pytest.mark.slow
async def test_a_healthy_config_has_no_errors_or_warnings(tmp_path, fake_claude, fake_opencode):
    found = await run(tmp_path, GOOD, fake_claude, fake_opencode)
    assert [f for f in found if f.level != "ok"] == []
    (claude,) = at(found, "harness.claude-code")
    assert "0.0-fake" in claude.message and "3 models" in claude.message
    assert {f.where for f in found} >= {"file", "harness.opencode", "agents.opus", "agents.pro", "default_agent", "queues.general", "state"}


async def test_no_file_says_run_init(tmp_path, fake_claude, fake_opencode):
    (f, *_) = await run(tmp_path, None, fake_claude, fake_opencode)
    assert (f.level, f.where) == ("error", "file") and "aegis init" in f.message


async def test_a_file_that_does_not_parse_is_an_error(tmp_path, fake_claude, fake_opencode):
    (f, *_) = await run(tmp_path, "agents: [1, 2\n", fake_claude, fake_opencode)
    assert (f.level, f.where) == ("error", "file") and "does not parse" in f.message


async def test_unknown_keys_warn_and_a_misspelt_field_is_named(tmp_path, fake_claude, fake_opencode):
    text = "scheduler: {tick_seconds: 5}\ndefault_agent: a\nagents:\n  a: {harness: claude-code, model: opus, efort: high, permission: full}\n"
    found = await run(tmp_path, text, fake_claude, fake_opencode)
    assert at(found, "scheduler")[0].level == "warn"
    assert at(found, "agents.a.efort")[0].level == "warn"
    assert at(found, "agents.a")[0].message == "effort is missing"
    assert at(found, "agents.a")[0].row == "agents.a"


async def test_a_missing_binary_is_an_error_on_the_harness_and_its_agents(tmp_path, fake_opencode):
    text = "default_agent: a\nagents:\n  a: {harness: claude-code, model: opus, effort: high, permission: full}\n"
    found = await run(tmp_path, text, str(tmp_path / "no-claude"), fake_opencode)
    (h,) = at(found, "harness.claude-code")
    assert h.level == "error" and "not on PATH" in h.message
    assert at(found, "agents.a.harness")[0].level == "error"


async def test_a_model_the_catalog_does_not_list_is_a_warning(tmp_path, fake_claude, fake_opencode):
    text = "default_agent: a\nagents:\n  a: {harness: claude-code, model: nope, effort: high, permission: full}\n"
    (f,) = at(await run(tmp_path, text, fake_claude, fake_opencode), "agents.a.model")
    assert f.level == "warn" and "opus" in f.message


@pytest.mark.slow
async def test_an_effort_the_model_does_not_take_is_a_warning(tmp_path, fake_claude, fake_opencode):
    text = "default_agent: p\nagents:\n  p: {harness: opencode, model: opencode-go/fake-pro, effort: low, permission: full}\n"
    (f,) = at(await run(tmp_path, text, fake_claude, fake_opencode), "agents.p.effort")
    assert f.level == "warn" and "high, max" in f.message


async def test_default_and_queue_problems_are_errors(tmp_path, fake_claude, fake_opencode):
    text = "agents:\n  a: {harness: claude-code, model: opus, effort: high, permission: full}\nqueues:\n  q: {agent: ghost, max_parallel: 1}\n"
    found = await run(tmp_path, text, fake_claude, fake_opencode)
    assert at(found, "default_agent")[0].level == "error"
    assert at(found, "queues.q.agent")[0].level == "error"


async def test_legacy_state_is_an_error_and_the_doctor_writes_nothing(tmp_path, fake_claude, fake_opencode):
    (tmp_path / ".aegis" / "state").mkdir(parents=True)
    (tmp_path / ".aegis" / "state" / "daemon.lock").touch()
    (tmp_path / ".aegis.yaml").write_text("default_agent: a\nagents:\n  a: {harness: claude-code, model: opus, effort: high, permission: full}\n")
    before = sorted(p for p in tmp_path.rglob("*") if "bin" not in p.parts and "fake-" not in str(p))
    found = await run(tmp_path, None, fake_claude, fake_opencode)
    assert at(found, "state")[0].level == "error"
    after = sorted(p for p in tmp_path.rglob("*") if "bin" not in p.parts and "fake-" not in str(p))
    assert before == after


@pytest.mark.slow
async def test_detect_finds_both_fakes_with_their_models(tmp_path, fake_claude, fake_opencode):
    claude, opencode = await detect(tmp_path, bins(fake_claude, fake_opencode))
    assert (claude.harness, claude.version, claude.error) == ("claude-code", "0.0-fake (Claude Code)", None)
    assert [m.value for m in claude.models] == ["opus", "sonnet", "haiku"]
    assert opencode.models[0].value == "opencode-go/fake-pro"


def m(value: str, efforts=()) -> Model:
    return Model(value=value, resolved=value, label=value, doc="", efforts=tuple(efforts))


def test_propose_claude_first_then_opencode():
    doc = propose([
        Found("claude-code", "/bin/claude", "2.1", (m("opus"),)),
        Found("opencode", "/bin/opencode", "1.18", (m("opencode-go/deepseek-v4-pro", ("minimal", "high", "max")),)),
    ])
    assert [(a.name, a.harness, a.model, a.effort, a.permission) for a in doc.agents] == [
        ("opus", "claude-code", "opus", "high", "full"),
        ("deepseek-v4-pro", "opencode", "opencode-go/deepseek-v4-pro", "high", "full"),
    ]
    assert doc.default_agent == "opus"
    assert [(q.name, q.agent, q.max_parallel) for q in doc.queues] == [("general", "opus", 3)]


def test_propose_without_claude_defaults_to_opencode_and_nothing_installed_proposes_nothing():
    doc = propose([Found("claude-code", None), Found("opencode", "/bin/opencode", "1.18", (m("opencode-go/opus"),))])
    assert doc.default_agent == "opus" and doc.agents[0].harness == "opencode"
    assert propose([Found("claude-code", None), Found("opencode", None)]).agents == []
```

- [ ] **Step 3: Run them to see them fail**

Run: `uv run pytest -q tests/test_doctor.py`
Expected: FAIL at collection, `ModuleNotFoundError: No module named 'aegis.doctor'`.

- [ ] **Step 4: Write `src/aegis/doctor.py`**

```python
"""What is installed here, and what is wrong with ``.aegis.yaml``. Reads only.

``detect`` looks for each harness aegis can run: its binary on PATH, its
``--version``, and its model catalog from the same ``probe`` the composer uses
(about 0.5 s for Claude and 3 s for OpenCode, no tokens). ``doctor`` checks the
file, the harnesses its agents name, every agent, the default, every queue and
the state directory, and returns findings a person can act on, each on the
Settings row it belongs to. A model the catalog does not list is a warning, not
an error: a CLI alias can resolve without being listed. ``propose`` is the
first config ``aegis init`` and the Settings page's Set up offer.

The doctor writes nothing: probe stderr goes to a temporary directory.
"""

from __future__ import annotations

import asyncio
import os
import shutil
import tempfile
from asyncio.subprocess import PIPE, STDOUT
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

from .agents import EFFORTS, HARNESSES, Agent
from .claude.control import Catalog, Model
from .config import (
    AGENT_KEYS,
    QUEUE_KEYS,
    TOP_KEYS,
    AgentDoc,
    ConfigDoc,
    Finding,
    QueueDoc,
    Snapshot,
    load,
)
from .harness import harness_for
from .roots import CONFIG_FILE, Roots, legacy_state
from .session import SpawnSpec

VERSION_TIMEOUT_S = 5.0
PROBE_TIMEOUT_S = 20.0
LABELS = {"claude-code": "Claude Code", "opencode": "OpenCode"}


@dataclass(frozen=True)
class Found:
    harness: str
    bin: str | None  # absolute, or None when not on PATH
    version: str | None = None
    models: tuple[Model, ...] = ()
    error: str | None = None

    def wire(self) -> dict:
        return {
            "harness": self.harness,
            "label": LABELS[self.harness],
            "bin": self.bin,
            "version": self.version,
            "models": [m.wire() for m in self.models],
            "error": self.error,
        }


async def _version(bin: str) -> str:
    proc = await asyncio.create_subprocess_exec(bin, "--version", stdout=PIPE, stderr=STDOUT)
    try:
        out, _ = await asyncio.wait_for(proc.communicate(), VERSION_TIMEOUT_S)
    except TimeoutError:
        proc.kill()
        await proc.wait()
        raise RuntimeError(f"no answer in {VERSION_TIMEOUT_S:.0f} s") from None
    text = out.decode(errors="replace").strip()
    if proc.returncode:
        raise RuntimeError(f"exited {proc.returncode}: {text[:200]}")
    return text.splitlines()[0] if text else ""


async def _catalog(name: str, bins: dict[str, str], cwd: Path) -> Catalog:
    h = harness_for(name, bins["claude-code"], bins["opencode"])
    spec = SpawnSpec(agent="doctor", model="", effort="", permission="read", cwd=cwd, harness=name)
    with tempfile.TemporaryDirectory() as d:
        stderr = Path(d) / "stderr.log"
        try:
            return await asyncio.wait_for(h.probe(spec, stderr), PROBE_TIMEOUT_S)
        except Exception as e:
            tail = stderr.read_text(errors="replace").strip().splitlines()[-3:] if stderr.exists() else []
            raise RuntimeError("; ".join([str(e) or type(e).__name__, *tail])) from e


async def _find(name: str, bins: dict[str, str], cwd: Path) -> Found:
    bin = shutil.which(bins[name])
    if bin is None:
        return Found(name, None, error=f"{bins[name]} is not on PATH")
    try:
        version = await _version(bin)
    except (OSError, RuntimeError) as e:
        return Found(name, bin, error=f"`{bin} --version` failed: {e}")
    try:
        cat = await _catalog(name, {**bins, name: bin}, cwd)
    except RuntimeError as e:
        return Found(name, bin, version, error=f"{LABELS[name]} gave no model list: {e}")
    return Found(name, bin, version, cat.models)


async def detect(cwd: Path, bins: dict[str, str], only: Iterable[str] = HARNESSES) -> list[Found]:
    wanted = set(only)
    return list(await asyncio.gather(*(_find(h, bins, cwd) for h in HARNESSES if h in wanted)))


def propose(found: list[Found]) -> ConfigDoc:
    have = {f.harness: f for f in found if f.bin}
    agents: list[AgentDoc] = []
    if "claude-code" in have:
        agents.append(AgentDoc(name="opus", harness="claude-code", model="opus", effort="high", permission="full"))
    oc = have.get("opencode")
    if oc is not None and oc.models:
        first = oc.models[0]
        name = first.value.rsplit("/", 1)[-1]
        if any(a.name == name for a in agents):
            name += "-opencode"
        effort = next((e for e in first.efforts if e in EFFORTS), "high")
        agents.append(AgentDoc(name=name, harness="opencode", model=first.value, effort=effort, permission="full"))
    if not agents:
        return ConfigDoc()
    default = agents[0].name
    return ConfigDoc(
        agents=agents,
        default_agent=default,
        queues=[QueueDoc(name="general", agent=default, max_parallel=3)],
    )


def _unknown_keys(data: dict) -> list[Finding]:
    out = [
        Finding("warn", str(k), f"aegis does not read {k!r}; it reads {', '.join(TOP_KEYS)}")
        for k in data
        if k not in TOP_KEYS
    ]
    for section, known in (("agents", AGENT_KEYS), ("queues", QUEUE_KEYS)):
        entries = data.get(section)
        for name, raw in (entries if isinstance(entries, dict) else {}).items():
            if not isinstance(raw, dict):
                continue
            keys = list(raw)
            if section == "agents" and isinstance(raw.get("provider"), dict):
                keys += [k for k in raw["provider"] if k != "name"]
            row = f"{section}.{name}"
            out += [
                Finding("warn", f"{row}.{k}", f"aegis does not read {k!r}; it reads {', '.join(known)}", row)
                for k in keys
                if k not in known
            ]
    return out


def _agent(a: Agent, f: Found | None) -> list[Finding]:
    row = f"agents.{a.name}"
    if a.error:
        return [Finding("error", row, a.error, row)]
    if a.harness not in HARNESSES:
        return [Finding("error", f"{row}.harness", f"aegis runs {', '.join(HARNESSES)}, not {a.harness!r}", row)]
    if f is not None and f.error:
        return [Finding("error", f"{row}.harness", f"{LABELS[a.harness]} cannot run here: {f.error}", row)]
    out: list[Finding] = []
    if f is not None and f.models:
        m = next((m for m in f.models if a.model in (m.value, m.resolved)), None)
        if m is None:
            listed = ", ".join(x.value for x in f.models[:8])
            out.append(Finding("warn", f"{row}.model", f"{LABELS[a.harness]} does not list {a.model!r}; it lists {listed}", row))
        elif m.efforts and a.effort not in m.efforts:
            out.append(Finding("warn", f"{row}.effort", f"{m.label} takes {', '.join(m.efforts)}, not {a.effort!r}", row))
    return out or [Finding("ok", row, f"{a.harness} {a.model}, effort {a.effort}, permission {a.permission}", row)]


def _default(name: str | None, by_name: dict[str, Agent]) -> Finding:
    row = "default_agent"
    if not name:
        return Finding("error", row, "not set; a spawn that names no agent fails", row)
    a = by_name.get(name)
    if a is None:
        return Finding("error", row, f"no agent named {name!r}", row)
    if a.error:
        return Finding("error", row, f"agent {name!r} cannot spawn: {a.error}", row)
    return Finding("ok", row, name, row)


def _queue(name: str, q: dict, by_name: dict[str, Agent]) -> Finding:
    row = f"queues.{name}"
    if "error" in q:
        return Finding("error", row, q["error"], row)
    a = by_name.get(q["agent"])
    if a is None:
        return Finding("error", f"{row}.agent", f"no agent named {q['agent']!r}", row)
    if a.error:
        return Finding("error", f"{row}.agent", f"agent {a.name!r} cannot spawn: {a.error}", row)
    return Finding("ok", row, f"{a.name}, {q['max_parallel']} at a time", row)


def _state(roots: Roots) -> Finding:
    sr = roots.state_root
    if found := legacy_state(sr):
        return Finding(
            "error",
            "state",
            f"{sr} holds aegis's pre-2.0 state ({', '.join(found)}); move it: mv {sr} {sr.parent / 'legacy-state'}",
        )
    probe = next(p for p in (sr, *sr.parents) if p.exists())
    if not os.access(probe, os.W_OK):
        return Finding("error", "state", f"{probe} is not writable; aegis keeps its state in {sr}")
    return Finding("ok", "state", str(sr))


async def doctor(roots: Roots, bins: dict[str, str], start: Path | None = None) -> list[Finding]:
    path = roots.config_root / CONFIG_FILE
    if not path.is_file():
        return [
            Finding("error", "file", f"no {CONFIG_FILE} at {roots.config_root}; run `aegis init` there"),
            _state(roots),
        ]
    data, error = load(path)
    if data is None:
        return [Finding("error", "file", f"does not parse: {error}"), _state(roots)]
    walked = start is not None and start.resolve() != roots.config_root
    out = [Finding("ok", "file", f"{path}, found from {start}" if walked else str(path))]
    out += _unknown_keys(data)
    snap = Snapshot.parse(path, None, data)
    used = {a.harness for a in snap.agents if a.harness in HARNESSES}
    found = {f.harness: f for f in await detect(roots.config_root, bins, used)}
    for name, f in found.items():
        where = f"harness.{name}"
        if f.error:
            out.append(Finding("error", where, f.error))
        else:
            out.append(Finding("ok", where, f"{f.bin}, {f.version}, {len(f.models)} models"))
    by_name = {a.name: a for a in snap.agents}
    for a in snap.agents:
        out += _agent(a, found.get(a.harness))
    out.append(_default(snap.default_agent, by_name))
    out += [_queue(name, q, by_name) for name, q in snap.queues.items()]
    out.append(_state(roots))
    return out
```

- [ ] **Step 5: Run the doctor tests, slow ones included, and time them**

Run: `uv run pytest -q tests/test_doctor.py --durations=0`
Expected: all PASS. Any test that probes OpenCode and is not yet `@pytest.mark.slow` and takes over 2.5 s gets the mark. Then run `uv run pytest -q -m "not slow" --max-unmarked-duration=3 tests/test_doctor.py` and expect PASS.

- [ ] **Step 6: Commit**

```bash
git commit -m "feat(doctor): detect installed harnesses and check .aegis.yaml against them (#193)

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>" -- src/aegis/doctor.py tests/test_doctor.py tests/fake_claude.py tests/fake_opencode.py
```

---

### Task 5: `aegis init` and `aegis doctor`

**Files:**
- Modify: `src/aegis/cli.py` (two commands and `_report`, `_ask`)
- Test: `tests/test_cli_config.py`

**Interfaces:**
- Consumes: `detect`, `doctor`, `propose`, `LABELS` (Task 4); `ConfigDoc`, `QueueDoc`, `write` (Tasks 1-2); `EFFORTS`, `PERMISSION_ORDER` (agents.py); `make_roots`, `find_config_root`, `CONFIG_FILE` (roots.py).
- Produces: `aegis init [--root DIR] [--yes|-y] [--claude BIN] [--opencode BIN]`, exit 1 when it writes nothing or the doctor finds an error; `aegis doctor [--root DIR] [--claude BIN] [--opencode BIN]`, exit 1 on any error finding.

- [ ] **Step 1: Write the failing tests**

`tests/test_cli_config.py`:

```python
from pathlib import Path

import pytest
from ruamel.yaml import YAML
from typer.testing import CliRunner

from aegis.cli import app

pytestmark = pytest.mark.slow  # every init probes both fakes


def invoke(*args: str, input: str | None = None):
    return CliRunner().invoke(app, list(args), input=input, env={"COLUMNS": "200"})


def fakes(claude: str, opencode: str) -> list[str]:
    return ["--claude", claude, "--opencode", opencode]


def test_init_yes_writes_a_config_the_doctor_passes(tmp_path, fake_claude, fake_opencode):
    root = tmp_path / "ws"
    root.mkdir()
    r = invoke("init", "--root", str(root), "--yes", *fakes(fake_claude, fake_opencode))
    assert r.exit_code == 0, r.output
    data = YAML(typ="safe").load((root / ".aegis.yaml").read_text())
    assert data["default_agent"] == "opus"
    assert data["agents"]["fake-pro"]["model"] == "opencode-go/fake-pro"
    assert data["queues"]["general"] == {"agent": "opus", "max_parallel": 3}
    assert "0 errors" in r.output


def test_init_asks_with_the_proposal_filled_in(tmp_path, fake_claude, fake_opencode):
    root = tmp_path / "ws"
    root.mkdir()
    # opus: add, model, effort, permission; fake-pro: add, model, effort,
    # permission; default; queue; workers.
    answers = "\n\n\nread\nn\nopus\ny\n5\n"
    r = invoke("init", "--root", str(root), *fakes(fake_claude, fake_opencode), input=answers)
    assert r.exit_code == 0, r.output
    data = YAML(typ="safe").load((root / ".aegis.yaml").read_text())
    assert list(data["agents"]) == ["opus"]
    assert data["agents"]["opus"]["permission"] == "read"
    assert data["queues"]["general"]["max_parallel"] == 5


def test_init_refuses_an_existing_file(tmp_path, fake_claude, fake_opencode):
    (tmp_path / ".aegis.yaml").write_text("agents: {}\n")
    r = invoke("init", "--root", str(tmp_path), "--yes", *fakes(fake_claude, fake_opencode))
    assert r.exit_code == 1 and "aegis doctor" in r.output
    assert (tmp_path / ".aegis.yaml").read_text() == "agents: {}\n"


def test_init_under_a_configured_parent_asks_first(tmp_path, fake_claude, fake_opencode):
    (tmp_path / ".aegis.yaml").write_text("agents: {}\n")
    child = tmp_path / "child"
    child.mkdir()
    r = invoke("init", "--root", str(child), *fakes(fake_claude, fake_opencode), input="n\n")
    assert r.exit_code == 1 and str(tmp_path / ".aegis.yaml") in r.output
    assert not (child / ".aegis.yaml").exists()


def test_init_with_no_harness_writes_nothing(tmp_path):
    r = invoke("init", "--root", str(tmp_path), "--yes", "--claude", str(tmp_path / "x"), "--opencode", str(tmp_path / "y"))
    assert r.exit_code == 1 and "No harness" in r.output
    assert not (tmp_path / ".aegis.yaml").exists()


def test_doctor_exits_1_on_an_error_and_0_when_healthy(tmp_path, fake_claude, fake_opencode):
    r = invoke("doctor", "--root", str(tmp_path), *fakes(fake_claude, fake_opencode))
    assert r.exit_code == 1 and "aegis init" in r.output
    (tmp_path / ".aegis.yaml").write_text(
        "default_agent: opus\nagents:\n  opus: {harness: claude-code, model: opus, effort: high, permission: full}\n"
    )
    r = invoke("doctor", "--root", str(tmp_path), *fakes(fake_claude, fake_opencode))
    assert r.exit_code == 0, r.output
    assert "0 errors, 0 warnings" in r.output
```

Check the interactive answers against the prompt order in Step 3 before running: opus `Add?` (Enter = yes), `model` (Enter), `effort` (Enter), `permission` (`read`), fake-pro `Add?` (`n`), `Default agent` (`opus`), queue `Add?` (`y`), `workers at a time` (`5`).

- [ ] **Step 2: Run them to see them fail**

Run: `uv run pytest -q tests/test_cli_config.py`
Expected: FAIL with exit code 2 and `No such command 'init'`.

- [ ] **Step 3: Add the commands to `src/aegis/cli.py`**

Update the module docstring's first line to "``aegis serve``, ``aegis init``, ``aegis doctor``, and ``aegis`` alone, which is ``serve --window``." Add `import asyncio` at the top. Append before `_open_running`:

```python
MARK = {"ok": "ok   ", "warn": "warn ", "error": "ERROR"}


def _bins(claude: str, opencode: str) -> dict[str, str]:
    return {"claude-code": claude, "opencode": opencode}


def _report(findings) -> int:
    """Print the findings; the number of errors."""
    for f in findings:
        typer.echo(f"{MARK[f.level]} {f.where:<26} {f.message}")
    errors = sum(f.level == "error" for f in findings)
    warns = sum(f.level == "warn" for f in findings)
    typer.echo(f"{errors} errors, {warns} warnings")
    return errors


@app.command()
def doctor(
    root: Path | None = typer.Option(
        None, help="Config root; default: the nearest ancestor holding .aegis.yaml."
    ),
    claude: str = typer.Option("claude", help="The claude executable to check."),
    opencode: str = typer.Option("opencode", help="The opencode executable to check."),
) -> None:
    """Check .aegis.yaml, the harnesses it names and the state directory."""
    from .doctor import doctor as run_doctor
    from .roots import make_roots

    start = Path.cwd()
    roots = make_roots(start=start, root=root)
    findings = asyncio.run(run_doctor(roots, _bins(claude, opencode), start=None if root else start))
    raise typer.Exit(1 if _report(findings) else 0)


def _ask(doc, found):
    """``doc`` as the person answers for it, each value offered as the default."""
    import click

    from .agents import EFFORTS, PERMISSION_ORDER
    from .config import ConfigDoc, QueueDoc

    agents = []
    for a in doc.agents:
        if not typer.confirm(f"Add agent {a.name!r} ({a.harness})?", default=True):
            continue
        models = next((f.models for f in found if f.harness == a.harness), ())
        if models:
            typer.echo("  models: " + ", ".join(m.value for m in models[:12]))
        agents.append(
            a.model_copy(
                update={
                    "model": typer.prompt("  model", default=a.model),
                    "effort": typer.prompt("  effort", default=a.effort, type=click.Choice(EFFORTS)),
                    "permission": typer.prompt(
                        "  permission", default=a.permission, type=click.Choice(PERMISSION_ORDER)
                    ),
                }
            )
        )
    if not agents:
        return ConfigDoc()
    names = [a.name for a in agents]
    first = doc.default_agent if doc.default_agent in names else names[0]
    default = typer.prompt("Default agent", default=first, type=click.Choice(names))
    queues = []
    if typer.confirm(f"Add a queue 'general' of workers running {default}?", default=True):
        n = typer.prompt("  workers at a time", default=3, type=click.IntRange(1))
        queues.append(QueueDoc(name="general", agent=default, max_parallel=n))
    return ConfigDoc(agents=agents, default_agent=default, queues=queues)


@app.command()
def init(
    root: Path | None = typer.Option(None, help="Where to write .aegis.yaml; default: here."),
    yes: bool = typer.Option(False, "--yes", "-y", help="Accept every proposal without asking."),
    claude: str = typer.Option("claude", help="The claude executable to look for."),
    opencode: str = typer.Option("opencode", help="The opencode executable to look for."),
) -> None:
    """Write a first .aegis.yaml from the harnesses installed here."""
    from .config import write
    from .doctor import LABELS, detect, propose
    from .doctor import doctor as run_doctor
    from .roots import CONFIG_FILE, find_config_root, make_roots

    target = (root or Path.cwd()).resolve()
    path = target / CONFIG_FILE
    if not target.is_dir():
        typer.echo(f"{target} is not a directory", err=True)
        raise typer.Exit(1)
    if path.exists():
        typer.echo(f"{path} already exists; run `aegis doctor` to check it", err=True)
        raise typer.Exit(1)
    parent = find_config_root(target.parent) / CONFIG_FILE
    if parent.is_file():
        typer.echo(f"{parent} governs {target} now.")
        if not yes and not typer.confirm(f"Create a new aegis root at {target}?", default=True):
            raise typer.Exit(1)
    bins = _bins(claude, opencode)
    found = asyncio.run(detect(target, bins))
    for f in found:
        if f.bin is None:
            typer.echo(f"{LABELS[f.harness]}: not found ({f.error})")
        elif f.error:
            typer.echo(f"{LABELS[f.harness]}: {f.bin}, {f.error}")
        else:
            typer.echo(f"{LABELS[f.harness]}: {f.bin}, {f.version}, {len(f.models)} models")
    if not any(f.bin for f in found):
        typer.echo("No harness found. Install Claude Code or OpenCode, then run `aegis init` again.", err=True)
        raise typer.Exit(1)
    doc = propose(found)
    if not yes:
        doc = _ask(doc, found)
    if not doc.agents:
        typer.echo("No agents chosen; nothing written.", err=True)
        raise typer.Exit(1)
    if problems := write(path, doc, None):
        _report(problems)
        raise typer.Exit(1)
    typer.echo(f"\nwrote {path}:\n")
    typer.echo(path.read_text())
    errors = _report(asyncio.run(run_doctor(make_roots(target, target), bins)))
    raise typer.Exit(1 if errors else 0)
```

The `doctor` command's `start` argument follows the spec: when `--root` is not given, the doctor reports which directory the file was walked up from.

- [ ] **Step 4: Run the CLI tests, plus the tests that exercise the CLI module**

Run: `uv run pytest -q tests/test_cli_config.py tests/test_no_cwd.py tests/test_imports.py`
Expected: all PASS. If the interactive test fails with `Aborted!`, print `r.output` and line the answers up with the prompts it shows.

- [ ] **Step 5: Try both commands by hand in a scratch directory with the real binaries**

Run: `d=$(mktemp -d) && uv run aegis init --root "$d" --yes; echo "rc=$?"; uv run aegis doctor --root "$d"; echo "rc=$?"`
Expected: init lists Claude Code 2.1.x and OpenCode 1.18.x with their model counts, writes a file, and both commands print `0 errors`. If the real OpenCode's first model is a poor default, note it for the PR body; don't change `propose` without asking.

- [ ] **Step 6: Commit**

```bash
git commit -m "feat(cli): aegis init writes a first .aegis.yaml, aegis doctor checks one (#193)

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>" -- src/aegis/cli.py tests/test_cli_config.py
```

---

### Task 6: The config operations

**Files:**
- Create: `src/aegis/config_ops.py`
- Modify: `src/aegis/app.py` (call `register_config_ops(self)` after `register_agent_ops(self)`)
- Test: `tests/test_config_ops.py`

**Interfaces:**
- Consumes: `App.config`, `App.registry`, `App.roots`, `App.claude_bin`, `App.opencode_bin` (Task 3); `write`, `ConfigDoc` (Tasks 1-2); `detect`, `doctor`, `propose` (Task 4).
- Produces: operations `config.read` → `Snapshot.wire()`; `config.write {doc, stamp}` → `{"saved": bool, "problems": [Finding.wire()], "config": Snapshot.wire()}` (raises `stale` or `bad_config`); `config.detect` → `[Found.wire()]`, cached `DETECT_TTL_S`; `config.doctor` (agent=True) → `[Finding.wire()]`; `config.propose` → `ConfigDoc.model_dump()`.

- [ ] **Step 1: Write the failing tests**

`tests/test_config_ops.py`:

```python
import pytest

from aegis.app import App
from aegis.ops import Caller, OpError
from aegis.roots import make_roots

CONFIG = "default_agent: opus\nagents:\n  opus: {harness: claude-code, model: opus, effort: high, permission: full}\n"


@pytest.fixture
def app(tmp_path, fake_claude, fake_opencode):
    (tmp_path / ".aegis.yaml").write_text(CONFIG)
    return App(make_roots(tmp_path, tmp_path), claude_bin=fake_claude, opencode_bin=fake_opencode)


async def test_read_then_write_round_trips_and_publishes(app, tmp_path):
    sent = []
    app.channels.subscribe("config", sent.append)
    w = await app.registry.call("config.read", {})
    w["doc"]["agents"][0]["effort"] = "max"
    r = await app.registry.call("config.write", {"doc": w["doc"], "stamp": w["stamp"]})
    assert r["saved"] and r["problems"] == []
    assert "effort: max" in (tmp_path / ".aegis.yaml").read_text()
    assert r["config"]["doc"]["agents"][0]["effort"] == "max"
    assert sent[-1]["t"] == "patch" and sent[-1]["ops"][0]["set"]["doc"]["agents"][0]["effort"] == "max"


async def test_a_stale_write_is_refused(app, tmp_path):
    w = await app.registry.call("config.read", {})
    (tmp_path / ".aegis.yaml").write_text(CONFIG + "# edited\n")
    with pytest.raises(OpError) as e:
        await app.registry.call("config.write", {"doc": w["doc"], "stamp": w["stamp"]})
    assert e.value.code == "stale"


async def test_an_invalid_write_returns_its_problems(app):
    w = await app.registry.call("config.read", {})
    w["doc"]["default_agent"] = "ghost"
    r = await app.registry.call("config.write", {"doc": w["doc"], "stamp": w["stamp"]})
    assert not r["saved"] and r["problems"][0]["row"] == "default_agent"


async def test_agents_may_run_the_doctor_but_not_write(app):
    agent = Caller("agent", log_id="x")
    with pytest.raises(OpError) as e:
        await app.registry.call("config.write", {"doc": {}, "stamp": None}, agent)
    assert e.value.code == "not_for_agents"
    assert "config.doctor" in [op.name for op in app.registry.agent_ops()]


@pytest.mark.slow
async def test_doctor_detect_and_propose(app, monkeypatch):
    import aegis.config_ops as config_ops

    calls = []
    real = config_ops.detect

    async def counted(*args, **kwargs):
        calls.append(args)
        return await real(*args, **kwargs)

    monkeypatch.setattr(config_ops, "detect", counted)
    findings = await app.registry.call("config.doctor", {})
    assert not [f for f in findings if f["level"] == "error"]
    found = await app.registry.call("config.detect", {})
    assert [f["harness"] for f in found] == ["claude-code", "opencode"]
    doc = await app.registry.call("config.propose", {})
    assert doc["default_agent"] == "opus"
    assert len(calls) == 1, "detect and propose share one probe within DETECT_TTL_S"
```

`config.doctor` calls `doctor()`, which calls `aegis.doctor.detect` directly, not the name the counter replaces, so only the `config.detect` and `config.propose` calls are counted.

- [ ] **Step 2: Run them to see them fail**

Run: `uv run pytest -q tests/test_config_ops.py`
Expected: FAIL with `unknown_op: no operation named 'config.read'`.

- [ ] **Step 3: Write `src/aegis/config_ops.py`**

```python
"""The configuration's operations: read, write, detect, doctor, propose.

The Settings page uses all five; ``config.doctor`` is open to agents too,
because it only reads. Writing is a person's: an agent that could write
.aegis.yaml could raise its own permission. A write goes to the file only; the
running server picks it up the way it picks up an editor's (config.py).
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING

from pydantic import BaseModel

from .config import ConfigDoc, write
from .doctor import Found, detect, doctor, propose

if TYPE_CHECKING:
    from .app import App

DETECT_TTL_S = 60.0


class WriteParams(BaseModel):
    model_config = {"extra": "forbid"}
    doc: ConfigDoc
    stamp: tuple[int, int, int] | None = None


def register_config_ops(app: App) -> None:
    r = app.registry
    cache: dict[str, tuple[float, list[Found]]] = {}

    def bins() -> dict[str, str]:
        return {"claude-code": app.claude_bin, "opencode": app.opencode_bin}

    async def detected() -> list[Found]:
        hit = cache.get("found")
        if hit is not None and time.monotonic() - hit[0] < DETECT_TTL_S:
            return hit[1]
        found = await detect(app.roots.config_root, bins())
        cache["found"] = (time.monotonic(), found)
        return found

    @r.op("config.read")
    async def config_read(_, caller):
        """.aegis.yaml as aegis holds it: the form's document, its stamp, and
        the error when the file on disk does not parse."""
        return app.config.current().wire()

    @r.op("config.write", WriteParams)
    async def config_write(p: WriteParams, caller):
        """Write the Settings form to .aegis.yaml. Refused when the file changed
        since `stamp`; nothing is written when the document has a problem."""
        problems = write(app.config.path, p.doc, p.stamp)
        return {
            "saved": not problems,
            "problems": [f.wire() for f in problems],
            "config": app.config.current().wire(),
        }

    @r.op("config.detect")
    async def config_detect(_, caller):
        """The harnesses installed on this machine: binary, version, models."""
        return [f.wire() for f in await detected()]

    @r.op("config.doctor", agent=True)
    async def config_doctor(_, caller):
        """Check .aegis.yaml, the harnesses it names and the state directory.
        Each finding has a level (ok, warn, error), where it is, and what is
        wrong."""
        return [f.wire() for f in await doctor(app.roots, bins())]

    @r.op("config.propose")
    async def config_propose(_, caller):
        """The first configuration `aegis init` would write here."""
        return propose(await detected()).model_dump()
```

In `app.py` add `from .config_ops import register_config_ops` and call `register_config_ops(self)` right after `register_agent_ops(self)`. Add the five operation names to the module docstring's operation list.

- [ ] **Step 4: Run the ops tests and the MCP tool listing tests**

Run: `uv run pytest -q tests/test_config_ops.py tests/test_registry.py tests/test_agents.py`
Expected: all PASS. If a test pins the exact list of agent tools, add `config_doctor` to it; don't loosen it.

- [ ] **Step 5: Commit**

```bash
git commit -m "feat(config): read, write, detect, doctor and propose as operations (#193)

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>" -- src/aegis/config_ops.py src/aegis/app.py tests/test_config_ops.py
```

(Add `tests/test_registry.py` to the paths only if Step 4 changed it.)

---

### Task 7: The Settings page

**Files:**
- Create: `src/aegis/client/js/settings.js`
- Modify: `src/aegis/client/index.html` (Settings button in the header, `v-settings` view)
- Modify: `src/aegis/client/js/app.js` (route, render branch, `config` subscription, key action, composer's empty message)
- Modify: `src/aegis/client/js/keys.js` (Alt+S)
- Modify: `src/aegis/client/css/base.css` (view and form styles)
- Test: `tests/test_browser.py` (append)

**Interfaces:**
- Consumes: channel `config`; operations `config.read`, `config.write`, `config.detect`, `config.doctor`, `config.propose` (Task 6); `agents.list`'s `config_error` (Task 3); `Connection.call`, `Connection.subscribe`, `OpError.code` (`js/protocol.js`).
- Produces: `export class Settings { constructor(conn, box); onConfig(wire); open() }`. DOM ids for tests: `#settings-btn`, `#settings`, `#set-path`, `#set-error`, `#set-stale`, `#set-reload`, `#set-setup`, `#set-agents` (rows `tr.set-agent[data-row]` with inputs named `name, harness, model, effort, permission, priming`), `#set-add-agent`, `#set-default` (`select`, wrapper `[data-row="default_agent"]`), `#set-queues` (rows `tr.set-queue[data-row]`, inputs `name, agent, max_parallel`), `#set-add-queue`, `#set-save`, `#set-doctor`, `#set-status`, `#set-findings`.

- [ ] **Step 1: Write the failing browser tests** (append to `tests/test_browser.py`)

```python
SETTINGS_CONFIG = (
    "# kept across a save\n"
    "default_agent: opus\nagents:\n  opus: {harness: claude-code, model: opus, effort: high, permission: full}\n"
    "  bad.one: {harness: claude-code, model: nope, effort: high, permission: full}\n"
)


@pytest.fixture
def settings_server(tmp_path: Path, fake_claude: str, fake_opencode: str):
    (tmp_path / ".aegis.yaml").write_text(SETTINGS_CONFIG)
    s = Server(tmp_path, fake_claude, fake_opencode).start()
    yield s
    s.stop()


def open_settings(pg, url: str) -> None:
    pg.goto(url)
    pg.wait_for_selector("#a2[data-view=fleet]")
    pg.keyboard.press("Alt+KeyS")
    pg.wait_for_selector("#a2[data-view=settings]")
    pg.wait_for_selector('tr.set-agent[data-row="agents.opus"]')


def test_settings_saves_an_edit_to_the_file_and_the_composer_follows(settings_server, page):
    open_settings(page, settings_server.url)
    page.select_option('tr.set-agent[data-row="agents.opus"] select[name=effort]', "max")
    page.click("#set-save")
    page.wait_for_function("document.querySelector('#set-status').textContent === 'Saved'")
    text = (settings_server.root / ".aegis.yaml").read_text()
    assert "# kept across a save" in text and "effort: max" in text
    page.click("#tab-add")
    page.wait_for_function("document.querySelector('#sp-effort').value === 'max'")
    assert page.errors == []


def test_an_edit_on_disk_reloads_the_open_settings_page(settings_server, page):
    open_settings(page, settings_server.url)
    (settings_server.root / ".aegis.yaml").write_text(
        SETTINGS_CONFIG + "  extra: {harness: claude-code, model: sonnet, effort: low, permission: read}\n"
    )
    page.wait_for_selector('tr.set-agent[data-row="agents.extra"]', timeout=5000)


def test_an_edit_on_disk_under_unsaved_edits_offers_reload(settings_server, page):
    open_settings(page, settings_server.url)
    page.fill('tr.set-agent[data-row="agents.opus"] input[name=model]', "sonnet")
    (settings_server.root / ".aegis.yaml").write_text(SETTINGS_CONFIG + "# changed\n")
    page.wait_for_selector("#set-stale", timeout=5000)
    assert page.input_value('tr.set-agent[data-row="agents.opus"] input[name=model]') == "sonnet"
    page.click("#set-reload")
    page.wait_for_function(
        "document.querySelector('tr.set-agent[data-row=\"agents.opus\"] input[name=model]').value === 'opus'"
    )


def test_run_doctor_marks_the_row(settings_server, page):
    open_settings(page, settings_server.url)
    page.click("#set-doctor")
    page.wait_for_selector('tr.set-agent[data-row="agents.bad.one"].warn', timeout=15000)
    assert "does not list 'nope'" in page.text_content("#set-findings")


def test_typing_in_settings_survives_session_patches(settings_server, page):
    open_settings(page, settings_server.url)
    box = 'tr.set-agent[data-row="agents.opus"] input[name=model]'
    page.click(box)
    page.keyboard.type("-x")
    page.evaluate("window.dispatchEvent(new HashChangeEvent('hashchange'))")  # a render() with no view change
    page.keyboard.type("y")
    assert page.input_value(box) == "opus-xy"
    assert page.evaluate("document.activeElement.name") == "model"


@pytest.fixture
def empty_server(tmp_path: Path, fake_claude: str, fake_opencode: str):
    s = Server(tmp_path, fake_claude, fake_opencode).start()
    yield s
    s.stop()


def test_an_empty_root_offers_set_up_and_saving_creates_the_file(empty_server, page):
    page.goto(empty_server.url + "#settings")
    page.wait_for_selector("#set-setup")
    page.click("#set-setup")
    page.wait_for_selector('tr.set-agent[data-row="agents.opus"]', timeout=15000)
    page.click("#set-save")
    page.wait_for_function("document.querySelector('#set-status').textContent === 'Saved'")
    assert "default_agent: opus" in (empty_server.root / ".aegis.yaml").read_text()
```

- [ ] **Step 2: Run them to see them fail**

Run: `uv run pytest -q -m browser tests/test_browser.py -k "settings or set_up or doctor_marks"`
Expected: FAIL, timing out on `#a2[data-view=settings]`.

- [ ] **Step 3: The markup and the key**

In `index.html`, before the `?` button:

```html
    <button class="keys-btn" id="settings-btn" title="Settings (Alt+S)">Settings</button>
```

and after the `v-spawn` `</main>`:

```html
  <main class="view v-settings"><section class="settings" id="settings"></section></main>
```

In `keys.js`, after the `Alt+N` row:

```js
  { scope: "global", label: "Alt+S", desc: "Settings: .aegis.yaml", action: "settings", match: alt("KeyS") },
```

- [ ] **Step 4: Write `src/aegis/client/js/settings.js`**

```js
// Settings: .aegis.yaml as a form. The server holds the file's parsed copy and
// sends it on the `config` channel; Save writes the whole form back through
// `config.write`, which refuses a stale write and validates first. The server
// re-reads the file after any write, so the page never patches server state.
// Nothing here decides what is valid: every finding names the row it marks.

const h = (tag, props = {}, ...kids) => {
  const el = Object.assign(document.createElement(tag), props);
  el.append(...kids.filter((k) => k != null));
  return el;
};

const button = (text, id, onclick) => {
  const b = h("button", { type: "button", textContent: text, onclick });
  if (id) b.id = id;
  return b;
};

function select(name, values, value, label = (v) => v) {
  const s = h("select", { name });
  for (const v of values) s.append(new Option(label(v), v));
  if (value && !values.includes(value)) s.append(new Option(`${value} (unknown)`, value));
  s.value = value || "";
  return s;
}

export class Settings {
  constructor(conn, box) {
    this.conn = conn;
    this.box = box;
    this.wire = null; // the config the server last sent
    this.doc = null; // what the form shows
    this.stamp = null; // the stamp of the file the form was loaded from
    this.dirty = false;
    this.saving = false;
    this.stale = false;
    this.found = []; // config.detect
    this.findings = []; // the last doctor run, or the last refused save
    this.status = "";
  }

  // A config from the channel. An untouched form follows the file; an edited
  // one keeps its edits and says the file moved.
  onConfig(wire) {
    this.wire = wire;
    if (this.saving) return;
    if (!this.dirty || this.doc === null) this.load(wire);
    else if (JSON.stringify(wire.stamp) !== JSON.stringify(this.stamp)) this.stale = true;
    if (this.box.isConnected && this.box.offsetParent !== null) this.draw();
  }

  load(wire) {
    this.doc = structuredClone(wire.doc);
    this.stamp = wire.stamp;
    this.dirty = false;
    this.stale = false;
  }

  // Entering the view. render() calls this only when the view changes, so a
  // sessions patch never redraws the form under the person's cursor.
  async open() {
    this.draw();
    if (this.found.length) return;
    try {
      this.found = await this.conn.call("config.detect");
    } catch (e) {
      this.status = e.message;
    }
    if (!this.dirty) this.draw();
  }

  touch() {
    this.dirty = true;
    this.status = "";
    const s = document.getElementById("set-status");
    if (s) s.textContent = "";
  }

  draw() {
    const w = this.wire;
    if (!w || !this.doc) return this.box.replaceChildren(h("p", { className: "notice", textContent: "Loading…" }));
    const parts = [h("div", { className: "set-head" }, h("h3", { textContent: "Settings" }), h("code", { id: "set-path", textContent: w.path }))];
    if (w.error)
      parts.push(
        h("p", {
          className: "set-band err",
          id: "set-error",
          textContent: `The file on disk does not parse; aegis is still using the last version that did. ${w.error}`,
        }),
      );
    if (this.stale)
      parts.push(
        h(
          "p",
          { className: "set-band", id: "set-stale", textContent: "The file changed on disk since you opened it. " },
          button("Reload from disk", "set-reload", () => {
            this.load(this.wire);
            this.findings = [];
            this.draw();
          }),
        ),
      );
    if (!w.exists && !this.dirty) {
      parts.push(
        h("div", { className: "set-empty" }, h("p", { textContent: `No .aegis.yaml at ${w.root}.` }), button("Set up", "set-setup", () => this.setup())),
      );
      return this.box.replaceChildren(...parts);
    }
    parts.push(this.findingsList(), this.agentsTable(), this.defaultPick(), this.queuesTable(), this.actions());
    this.box.replaceChildren(...parts);
    this.mark();
  }

  findingsList() {
    return h(
      "ul",
      { id: "set-findings" },
      ...this.findings.map((f) =>
        h("li", { className: f.level }, h("b", { textContent: f.level }), " ", h("code", { textContent: f.where }), " ", f.message),
      ),
    );
  }

  harnessLabel(x) {
    const f = this.found.find((f) => f.harness === x);
    return f && !f.bin ? `${x} (not installed)` : x;
  }

  modelOptions(harness) {
    const f = this.found.find((f) => f.harness === harness);
    return (f ? f.models : []).map((m) => new Option(m.label, m.value));
  }

  agentsTable() {
    const v = this.wire.vocab;
    const t = h("table", { className: "set-table", id: "set-agents" });
    t.append(h("tr", {}, ...["Agent", "Harness", "Model", "Effort", "Permission", ""].map((x) => h("th", { textContent: x }))));
    this.doc.agents.forEach((a, i) => {
      const tr = h("tr", { className: "set-agent" });
      tr.dataset.row = `agents.${a.name}`;
      const models = h("datalist", { id: `set-models-${i}` }, ...this.modelOptions(a.harness));
      const name = h("input", { name: "name", value: a.name, spellcheck: false });
      const harness = select("harness", v.harnesses, a.harness, (x) => this.harnessLabel(x));
      const model = h("input", { name: "model", value: a.model, spellcheck: false });
      model.setAttribute("list", models.id);
      const effort = select("effort", v.efforts, a.effort);
      const permission = select("permission", v.permissions, a.permission);
      const priming = h("textarea", { name: "priming", value: a.priming || "", rows: 3, placeholder: "The system prompt every session of this agent starts with" });
      for (const el of [name, harness, model, effort, permission, priming])
        el.addEventListener("input", () => {
          a[el.name] = el.name === "priming" ? el.value || null : el.value;
          if (el.name === "name") tr.dataset.row = `agents.${el.value}`;
          if (el.name === "harness") models.replaceChildren(...this.modelOptions(el.value));
          this.touch();
        });
      const fold = h("details", { className: "set-priming" }, h("summary", { textContent: a.priming ? "priming" : "no priming" }), priming);
      const del = button("Delete", null, () => {
        this.doc.agents.splice(i, 1);
        this.touch();
        this.draw();
      });
      tr.append(h("td", {}, name), h("td", {}, harness), h("td", {}, model, models), h("td", {}, effort), h("td", {}, permission), h("td", {}, fold, del));
      t.append(tr);
    });
    const add = button("Add agent", "set-add-agent", () => {
      this.doc.agents.push({ name: "", harness: v.harnesses[0], model: "", effort: "", permission: "", priming: null });
      this.touch();
      this.draw();
    });
    return h("section", {}, h("h4", { textContent: "Agents" }), t, add);
  }

  defaultPick() {
    const names = this.doc.agents.map((a) => a.name);
    const s = select("default_agent", ["", ...names], this.doc.default_agent || "", (x) => x || "(none)");
    s.id = "set-default";
    s.addEventListener("input", () => {
      this.doc.default_agent = s.value || null;
      this.touch();
    });
    const wrap = h("section", { className: "set-default" }, h("h4", { textContent: "Default agent" }), s);
    wrap.dataset.row = "default_agent";
    return wrap;
  }

  queuesTable() {
    const names = this.doc.agents.map((a) => a.name);
    const t = h("table", { className: "set-table", id: "set-queues" });
    t.append(h("tr", {}, ...["Queue", "Agent", "Workers at a time", ""].map((x) => h("th", { textContent: x }))));
    this.doc.queues.forEach((q, i) => {
      const tr = h("tr", { className: "set-queue" });
      tr.dataset.row = `queues.${q.name}`;
      const name = h("input", { name: "name", value: q.name, spellcheck: false });
      const agent = select("agent", names, q.agent);
      const limit = h("input", { name: "max_parallel", type: "number", min: 1, value: q.max_parallel ?? "" });
      for (const el of [name, agent, limit])
        el.addEventListener("input", () => {
          q[el.name] = el.name === "max_parallel" ? (el.value === "" ? null : Number(el.value)) : el.value;
          if (el.name === "name") tr.dataset.row = `queues.${el.value}`;
          this.touch();
        });
      const del = button("Delete", null, () => {
        this.doc.queues.splice(i, 1);
        this.touch();
        this.draw();
      });
      tr.append(h("td", {}, name), h("td", {}, agent), h("td", {}, limit), h("td", {}, del));
      t.append(tr);
    });
    const add = button("Add queue", "set-add-queue", () => {
      this.doc.queues.push({ name: "", agent: this.doc.default_agent || names[0] || "", max_parallel: null });
      this.touch();
      this.draw();
    });
    return h("section", {}, h("h4", { textContent: "Queues" }), t, add);
  }

  actions() {
    return h(
      "div",
      { className: "set-actions" },
      button("Save", "set-save", () => this.save()),
      button("Run doctor", "set-doctor", () => this.runDoctor()),
      h("span", { id: "set-status", textContent: this.status }),
    );
  }

  // Each finding marks the row Python named; the rest stay in the list.
  mark() {
    for (const f of this.findings) {
      if (!f.row || f.level === "ok") continue;
      const el = this.box.querySelector(`[data-row="${CSS.escape(f.row)}"]`);
      if (!el) continue;
      if (f.level === "error" || !el.classList.contains("error")) el.classList.add(f.level);
      const why = h("div", { className: "set-why", textContent: f.message });
      (el.tagName === "TR" ? el.cells[0] : el).append(why);
    }
  }

  async save() {
    this.saving = true;
    try {
      const r = await this.conn.call("config.write", { doc: this.doc, stamp: this.stamp });
      this.findings = r.problems;
      if (r.saved) {
        this.wire = r.config;
        this.load(r.config);
        this.status = "Saved";
      } else this.status = `Not saved: ${r.problems.length} problem${r.problems.length === 1 ? "" : "s"}`;
    } catch (e) {
      if (e.code === "stale") this.stale = true;
      this.status = e.message;
    } finally {
      this.saving = false;
    }
    this.draw();
  }

  async runDoctor() {
    this.status = "Running the doctor…";
    document.getElementById("set-status").textContent = this.status;
    try {
      this.findings = await this.conn.call("config.doctor");
      const n = (l) => this.findings.filter((f) => f.level === l).length;
      this.status = `${n("error")} errors, ${n("warn")} warnings`;
    } catch (e) {
      this.status = e.message;
    }
    this.draw();
  }

  async setup() {
    try {
      this.doc = await this.conn.call("config.propose");
    } catch (e) {
      this.status = e.message;
      return this.draw();
    }
    this.stamp = null;
    this.touch();
    this.draw();
  }
}
```

The Add agent row starts with empty effort and permission on purpose: the loader fills in no defaults, so the server's validation names them until the person picks.

- [ ] **Step 5: Wire it into `app.js`**

- Import: `import { Settings } from "./settings.js";`
- After `const conn = new Connection(...)`: `const settings = new Settings(conn, $("settings"));`
- `route()`: before `return { view: "fleet" };` add `if (h === "settings") return { view: "settings" };` and update the comment above `route` to list `#settings`.
- In the token branch next to the `sessions` subscription:

```js
  conn.subscribe(
    "config",
    (w) => settings.onConfig(w),
    (ops) => {
      for (const op of ops) if (op.set) settings.onConfig(op.set);
      loadAgents();
    },
  );
```

- `render()`: after the `$("tab-add")` toggle add `$("settings-btn").classList.toggle("on", r.view === "settings");`, and before the final `} else {` (the read view) add:

```js
  } else if (r.view === "settings") {
    watchHost(false);
    follow(null);
    show("settings");
    if (newView) settings.open();
    document.title = "Settings · aegis";
```

- Next to the other header listeners: `$("settings-btn").addEventListener("click", () => go("#settings"));`
- In the `installKeys` actions: `settings: () => go("#settings"),`
- In `loadAgents`, replace the `$("sp-error").textContent = roster.agents.length ? "" : …` line with:

```js
  $("sp-error").textContent = roster.config_error
    ? `.aegis.yaml does not parse; aegis is using the last version that did. ${roster.config_error}`
    : roster.agents.length
      ? ""
      : "No agents yet. Set them up in Settings (Alt+S).";
```

- [ ] **Step 6: Styles** (append to `base.css`)

```css
#a2[data-view=settings] .v-settings{display:block;overflow:auto}
#a2 .settings{max-width:1040px;margin:0 auto;padding:24px 20px 40px;font-family:var(--font-chrome);color:var(--ink)}
#a2 .set-head{display:flex;align-items:baseline;gap:14px;margin-bottom:12px}
#a2 .set-head h3,#a2 .settings h4{margin:0;font-family:var(--font-head);font-weight:500;color:var(--strong)}
#a2 .settings h4{font-size:13px;margin:18px 0 6px}
#a2 #set-path{color:var(--faint);font-family:var(--font-mono);font-size:12px}
#a2 .set-band{border:1px solid var(--rule);border-radius:var(--r);padding:8px 10px;color:var(--muted);font-size:12.5px}
#a2 .set-band.err{border-color:var(--err);color:var(--err);background:var(--err-bg)}
#a2 .set-table{width:100%;border-collapse:collapse}
#a2 .set-table th{text-align:left;font-weight:500;color:var(--faint);font-size:11.5px;padding:4px 6px}
#a2 .set-table td{padding:3px 6px;vertical-align:top}
#a2 .settings input,#a2 .settings select,#a2 .settings textarea{width:100%;box-sizing:border-box;background:var(--surface);border:1px solid var(--rule);border-radius:var(--r);color:var(--strong);font-family:var(--font-mono);font-size:12px;padding:3px 6px}
#a2 .settings button{background:var(--surface);border:1px solid var(--rule);border-radius:var(--r);color:var(--muted);font-family:var(--font-chrome);font-size:12px;padding:3px 10px;cursor:pointer;margin-top:6px}
#a2 .settings button:hover{color:var(--strong)}
#a2 .set-default select{max-width:280px}
#a2 [data-row].warn td:first-child,#a2 .set-default.warn{box-shadow:inset 3px 0 var(--warn)}
#a2 [data-row].error td:first-child,#a2 .set-default.error{box-shadow:inset 3px 0 var(--err)}
#a2 .set-why{font-size:11.5px;color:var(--muted);margin-top:3px;white-space:normal}
#a2 .set-priming summary{color:var(--faint);font-size:11.5px;cursor:pointer}
#a2 .set-actions{display:flex;align-items:center;gap:10px;margin-top:18px}
#a2 #set-status{color:var(--muted);font-size:12px}
#a2 #set-findings{list-style:none;padding:0;margin:0 0 8px;font-size:12px}
#a2 #set-findings li{padding:2px 0;color:var(--muted)}
#a2 #set-findings li.ok{color:var(--faint)}
#a2 #set-findings li.warn b{color:var(--warn)}
#a2 #set-findings li.error b{color:var(--err)}
#a2 #set-findings b{display:inline-block;width:3.5em;font-weight:500}
#a2 #settings-btn.on{color:var(--strong);border-color:var(--accent)}
```

- [ ] **Step 7: Run the new browser tests, then the whole browser file**

Run: `uv run playwright install chromium && uv run pytest -q -m browser tests/test_browser.py -k "settings or set_up or doctor_marks"`
Expected: all PASS. Then `uv run pytest -q -m browser tests/test_browser.py`, all PASS (the `?` overlay test should now list Alt+S with no change to it).

- [ ] **Step 8: Look at it**

Start a throwaway server on this branch over a scratch root: `d=$(mktemp -d) && cp /home/apiad/Workspace/.aegis.yaml "$d"/ && uv run aegis serve --root "$d" --port 8799`. Do not use the Workspace root itself, because a save would rewrite Alex's real config. Open the printed URL with the `saidkick` skill, go to Settings in each of the three themes, run the doctor, and take screenshots. Fix anything that is broken, misaligned or unreadable before committing. Stop the server by port: `fuser -k 8799/tcp`.

- [ ] **Step 9: Commit**

```bash
git commit -m "feat(client): a Settings page that edits .aegis.yaml and runs the doctor (#193)

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>" -- src/aegis/client/js/settings.js src/aegis/client/index.html src/aegis/client/js/app.js src/aegis/client/js/keys.js src/aegis/client/css/base.css tests/test_browser.py
```

---

### Task 8: Docs, gates, PR

**Files:**
- Modify: `DESIGN.md` (the `.aegis.yaml` sentence in "Its own state, never mixed with the legacy tree's")
- Create: `changelog.d/193-config-init-doctor-settings.added.md`
- Modify: `docs/superpowers/specs/2026-10-08-aegis-config-init-doctor-settings-design.md` (status line; the "`read_config`, `load_agents`… go" sentence)

- [ ] **Step 1: DESIGN.md**

Replace the sentence starting "Of `.aegis.yaml`, aegis reads the `agents:` and `queues:` maps" through "with nothing to show it." with:

```markdown
Of `.aegis.yaml`, aegis reads the `agents:` and `queues:` maps and
`default_agent`, and nothing in them is a default: an agent names its harness,
model, effort and permission, a queue its agent and `max_parallel`, and one that
does not is reported by name rather than filled in or dropped. A setting the
loader filled in would be one nobody chose, with nothing to show it.

**The file is the configuration.** The server holds a parsed copy and re-reads
it whenever its stamp changes (`config.py`), so an edit made in an editor and a
save from the Settings page take the same path, and nothing patches server state
behind the file's back. A file that does not parse, or is empty, leaves the last
one that did in force and says so, because a half-typed edit must not stop every
spawn. `aegis init` and the Settings page write through one writer that keeps
comments and refuses a stale write; `aegis doctor` and `config.doctor` check the
file against the harnesses actually installed (`doctor.py`).
```

- [ ] **Step 2: The changelog fragment**

`changelog.d/193-config-init-doctor-settings.added.md`:

```markdown
- **aegis creates, checks and edits `.aegis.yaml`, and follows it live.** `aegis init` writes a first config from the harnesses installed on the machine and their models, asking for each value with the proposal filled in (`--yes` takes them all). `aegis doctor` names every problem by its place in the file: a harness that is not on PATH, a model its harness does not list, an effort the model does not take, a queue on a missing agent, a key aegis does not read. A Settings page (Alt+S) edits agents, the default agent and queues, keeps the file's comments, and runs the doctor on its rows. aegis re-reads the file whenever it changes, so a queue added in an editor takes tasks without a restart, and a file that does not parse leaves the last good one in force instead of stopping every spawn.
```

Run: `make changelog-check`
Expected: PASS.

- [ ] **Step 3: The spec**

Set the status line to `**Status: implemented, 2026-10-08** (issue #193), following docs/superpowers/plans/2026-10-08-aegis-config-init-doctor-settings.md.` In "The config holder", replace "`read_config`, `load_agents`, `default_agent` and the per-call file reads go." with "The server no longer reads the file per call; `load_agents` and `load_queues` stay as one-shot readers for tests, and `default_agent()` goes." Add one line under "The config holder" saying that an empty file counts as one that does not parse.

- [ ] **Step 4: Every gate**

Run, reading each exit code directly (never through a pipe):

```bash
make check; echo "make check rc=$?"
rift check; echo "rift rc=$?"
make bench; echo "bench rc=$?"
```

Expected: `make check` rc=0 (format, lint, lint-docs, changelog-check, test, typecheck). If `ruff format` rewrote files, commit them as `style:`, naming the paths. If `ty` flags `WriteParams.stamp` or the `Found` tuples, fix the types rather than ignoring them.

- [ ] **Step 5: Live check against the real harnesses**

Run `make test-live` if it covers MCP tools (a real Claude session calling `config_doctor` is the change to how agents use aegis). Also run `d=$(mktemp -d) && uv run aegis init --root "$d" --yes && uv run aegis doctor --root "$d"; echo "rc=$?"` and paste both outputs into the PR body.

- [ ] **Step 6: Commit the docs, push and open the PR**

```bash
git commit -m "docs: the file is the configuration; init, doctor and Settings (#193)

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>" -- DESIGN.md changelog.d/193-config-init-doctor-settings.added.md docs/superpowers/specs/2026-10-08-aegis-config-init-doctor-settings-design.md docs/superpowers/plans/2026-10-08-aegis-config-init-doctor-settings.md
git push origin feat/config-doctor
gh pr create --title "aegis creates, checks and edits .aegis.yaml, and follows it live (#193)" --body-file <body>
```

The PR body carries: what was measured (the probe times, the live `init`/`doctor` output, the `make bench` table), what was rejected (inotify, a raw YAML editor, `doctor --fix`), the empty-file rule found while planning, and the screenshots from Task 7 Step 8. It ends with `Closes #193` and the line `🤖 Generated with [Claude Code](https://claude.com/claude-code)`.

- [ ] **Step 7: Watch CI**

Arm an aegis monitor on the PR's checks (`gh pr checks <n>`) with a progress command counting finished checks over the total, and end the turn. On red, compare against `main`'s latest run before assuming it is ours (`know-how/landing-a-change.md` §5).
