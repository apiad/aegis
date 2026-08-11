# aegis comms format — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give every call into the aegis MCP layer a recognisable rendered
form and a recorded envelope, so a transcript shows agents talking and a
ledger can reconstruct who said what to whom.

**Architecture:** One pure registry (`src/aegis/comms/descriptors.py`) maps
each of the 72 registered `aegis_*` tools to a glyph, a family, a one-line
description and a typed target. Two consumers read it: the three renderers
(TUI, HTML export, web) ask for glyph + description; a FastMCP `on_call_tool`
middleware asks for family + target and appends an envelope to
`.aegis/state/comms/YYYY-MM-DD.jsonl`. No tool signature, return value or
semantic changes.

**Tech Stack:** Python 3.13, FastMCP 3.2.0, Rich/Textual 8.x, pytest, plain
ES modules for the web client, typer for the CLI.

**Spec:** `docs/superpowers/specs/2026-08-11-aegis-comms-format-design.md`

## Global Constraints

- Package manager is `uv`. Run tests with `uv run python -m pytest`.
- TDD: failing test first, minimal implementation, one commit per task.
- Never use `-k "not live"` — it matches `live` as a substring. Use
  `-m "not live"`.
- `src/aegis/comms/descriptors.py` and `models.py` are **pure**: no Rich, no
  Textual, no I/O, no bridge import. They are imported by `render_shared.py`,
  which the web wire also imports, so a heavy import there costs everywhere.
- Glyphs are East Asian Ambiguous: always emit `glyph + " "`, and measure any
  width budget with `rich.cells.cell_len`, never `len()`.
- All code, comments, identifiers and commit messages in English.
- Commit with `git add <explicit paths>` — never `git add -A`/`.`/`-u`. This
  is a shared checkout.
- The four family constants are exactly `"conversation"`, `"coordination"`,
  `"introspection"`, `"admin"`.
- Target kinds are exactly `agent`, `queue`, `canvas`, `group`, `term`,
  `path`, `claim`, `self`.

---

### Task 1: The descriptor core and the agent-to-agent verbs

**Files:**
- Create: `src/aegis/comms/__init__.py`
- Create: `src/aegis/comms/descriptors.py`
- Test: `tests/test_comms_descriptors.py`

**Interfaces:**
- Consumes: nothing.
- Produces:
  - `Target(kind: str, id: str)` — frozen dataclass.
  - `AegisToolDescriptor(verb: str, family: str, glyph: str | Callable[[dict], str], describe: Callable[[dict], str], target: Callable[[dict], Target | None])` — frozen dataclass.
  - `DESCRIPTORS: dict[str, AegisToolDescriptor]` — keyed by bare verb.
  - `descriptor_for(name: str) -> AegisToolDescriptor | None`
  - `aegis_glyph(name: str, args: dict | None = None) -> str | None`
  - `aegis_describe(name: str, args: dict | None = None) -> str | None`
  - `aegis_target(name: str, args: dict | None = None) -> Target | None`
  - `aegis_family(name: str) -> str | None`
  - Constants `CONVERSATION`, `COORDINATION`, `INTROSPECTION`, `ADMIN`, `PALE_GLYPH`.

- [x] **Step 1: Write the failing test**

Create `tests/test_comms_descriptors.py`:

```python
"""The pure descriptor registry: name normalisation, glyphs, lines, targets."""
from __future__ import annotations

import pytest

from aegis.comms.descriptors import (
    CONVERSATION, Target, aegis_describe, aegis_family, aegis_glyph,
    aegis_target, descriptor_for,
)


@pytest.mark.parametrize("name", [
    "aegis_handoff",                  # what the middleware sees
    "mcp__aegis__aegis_handoff",      # what the claude transcript carries
])
def test_both_name_shapes_resolve_to_the_same_descriptor(name):
    d = descriptor_for(name)
    assert d is not None
    assert d.verb == "handoff"


@pytest.mark.parametrize("name", ["Bash", "Read", "", "mcp__other__thing",
                                  "aegis_not_a_tool"])
def test_non_aegis_names_have_no_descriptor(name):
    assert descriptor_for(name) is None
    assert aegis_glyph(name) is None
    assert aegis_describe(name) is None


def test_handoff_names_the_peer_and_quotes_the_context():
    args = {"from_handle": "me", "target_handle": "weary-turing",
            "context": "the parser is green, the render is yours"}
    assert aegis_glyph("aegis_handoff", args) == "⇄"
    assert aegis_describe("aegis_handoff", args) == (
        'weary-turing · "the parser is green, the render is yours"')
    assert aegis_target("aegis_handoff", args) == Target("agent",
                                                         "weary-turing")
    assert aegis_family("aegis_handoff") == CONVERSATION


def test_an_interrupting_handoff_takes_the_cut_glyph():
    args = {"target_handle": "weary-turing", "context": "stop, wrong branch",
            "interrupt": True}
    assert aegis_glyph("aegis_handoff", args) == "⇅"
    assert aegis_describe("aegis_handoff", args) == (
        'weary-turing · cut · "stop, wrong branch"')


def test_spawn_shows_profile_at_host_because_the_handle_does_not_exist_yet():
    args = {"agent": "main", "prompt": "audit the ledger", "host": "vps",
            "from_handle": "me"}
    assert aegis_glyph("aegis_spawn", args) == "✧"
    assert aegis_describe("aegis_spawn", args) == 'main@vps · "audit the ledger"'
    assert aegis_target("aegis_spawn", args) == Target("agent", "main")


def test_spawn_without_a_host_omits_the_at_suffix():
    args = {"agent": "main", "prompt": "audit the ledger"}
    assert aegis_describe("aegis_spawn", args) == 'main · "audit the ledger"'


def test_long_excerpts_are_collapsed_and_truncated():
    args = {"target_handle": "peer",
            "context": "word " * 40}
    line = aegis_describe("aegis_handoff", args)
    assert line.startswith('peer · "word word')
    assert line.endswith('…"')
    assert "  " not in line


def test_missing_arguments_never_raise_and_never_leave_stray_separators():
    for verb in ("handoff", "spawn", "fork", "close", "enqueue", "delegate",
                 "cancel"):
        line = aegis_describe(f"aegis_{verb}", {})
        assert line is not None
        assert not line.startswith("·")
        assert not line.endswith("·")


def test_queue_verbs_point_at_the_queue_and_cancel_at_the_task():
    enq = {"queue": "general", "payload": "port the fixtures",
           "from_handle": "me"}
    assert aegis_glyph("aegis_enqueue", enq) == "⇉"
    assert aegis_describe("aegis_enqueue", enq) == 'general · "port the fixtures"'
    assert aegis_target("aegis_enqueue", enq) == Target("queue", "general")

    dele = {"queue": "general", "payload": "resolve the merge"}
    assert aegis_describe("aegis_delegate", dele) == (
        'general · blocking · "resolve the merge"')

    assert aegis_glyph("aegis_cancel", {"task_id": "01K4TZ"}) == "⇎"
    assert aegis_target("aegis_cancel", {"task_id": "01K4TZ"}) == Target(
        "queue", "01K4TZ")


def test_fork_and_close_name_the_handle_they_act_on():
    fork = {"target_handle": "weary-turing", "prompt": "take the perf angle"}
    assert aegis_glyph("aegis_fork", fork) == "✧"
    assert aegis_describe("aegis_fork", fork) == (
        'weary-turing · forked · "take the perf angle"')

    close = {"handle": "calm-hopper", "from_handle": "me"}
    assert aegis_glyph("aegis_close", close) == "✦"
    assert aegis_describe("aegis_close", close) == "calm-hopper · reaped"
    assert aegis_target("aegis_close", close) == Target("agent", "calm-hopper")
```

- [x] **Step 2: Run the test to verify it fails**

Run: `uv run python -m pytest tests/test_comms_descriptors.py -q`
Expected: collection error — `ModuleNotFoundError: No module named 'aegis.comms'`.

- [x] **Step 3: Write the implementation**

Create `src/aegis/comms/__init__.py`:

```python
"""The aegis comms layer: what a call into the aegis MCP surface looks
like on screen, and what it leaves behind on disk."""
```

Create `src/aegis/comms/descriptors.py`:

```python
"""What matters about each aegis MCP call.

One registry, two consumers. The transcript renderers ask for a glyph and a
one-line description; the comms middleware asks for a family and a target.
Keeping both answers in one place is the point: if they disagreed about who
the counterpart is, the ledger would contradict the screen.

Pure by contract — no Rich, no Textual, no I/O, no bridge. ``render_shared``
imports this, and the web wire imports ``render_shared``.
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

CONVERSATION = "conversation"
COORDINATION = "coordination"
INTROSPECTION = "introspection"
ADMIN = "admin"

#: The one glyph every read-the-room call shares. Rendered dimmer than a
#: native tool line, so polling never wears the conversation's colour.
PALE_GLYPH = "∘"

_PREFIX = "aegis_"


@dataclass(frozen=True)
class Target:
    """Who or what is on the other end of a call.

    ``kind`` is one of: agent, queue, canvas, group, term, path, claim, self.
    """
    kind: str
    id: str


@dataclass(frozen=True)
class AegisToolDescriptor:
    verb: str
    family: str
    #: A constant glyph, or a function of the arguments when the same tool
    #: means two different acts (``handoff`` with and without ``interrupt``).
    glyph: str | Callable[[dict], str]
    describe: Callable[[dict], str]
    target: Callable[[dict], Target | None] = field(
        default=lambda args: None)


# --- argument helpers -------------------------------------------------

def _s(args: dict, *keys: str) -> str:
    """The first non-empty string among ``keys``. Never raises: the renderer
    sees whatever the model actually sent, which may be nothing."""
    for key in keys:
        val = args.get(key)
        if isinstance(val, str) and val.strip():
            return val.strip()
    return ""


def _quote(text: str, limit: int = 48) -> str:
    """A whitespace-collapsed, truncated, quoted excerpt — or "" if empty."""
    collapsed = " ".join(str(text or "").split())
    if not collapsed:
        return ""
    if len(collapsed) > limit:
        collapsed = collapsed[:limit - 1] + "…"
    return f'"{collapsed}"'


def _join(*parts: str) -> str:
    """Join non-empty segments with the house separator. Dropping empties is
    what keeps a call with missing arguments from rendering as ' · · '."""
    return " · ".join(p for p in parts if p)


def _at(name: str, host: str) -> str:
    return f"{name}@{host}" if name and host else name


def _agent_at(key: str) -> Callable[[dict], Target | None]:
    def target(args: dict) -> Target | None:
        val = _s(args, key)
        return Target("agent", val) if val else None
    return target


def _target_at(kind: str, *keys: str) -> Callable[[dict], Target | None]:
    def target(args: dict) -> Target | None:
        val = _s(args, *keys)
        return Target(kind, val) if val else None
    return target


# --- descriptions -----------------------------------------------------

def _d_handoff(a: dict) -> str:
    return _join(_s(a, "target_handle"),
                 "cut" if a.get("interrupt") else "",
                 _quote(_s(a, "context")))


def _g_handoff(a: dict) -> str:
    return "⇅" if a.get("interrupt") else "⇄"


def _d_spawn(a: dict) -> str:
    return _join(_at(_s(a, "agent"), _s(a, "host")), _quote(_s(a, "prompt")))


def _d_fork(a: dict) -> str:
    return _join(_s(a, "target_handle"), "forked", _quote(_s(a, "prompt")))


def _d_close(a: dict) -> str:
    return _join(_s(a, "handle"), "reaped")


def _d_enqueue(a: dict) -> str:
    return _join(_s(a, "queue"), _quote(_s(a, "payload")))


def _d_delegate(a: dict) -> str:
    return _join(_s(a, "queue"), "blocking", _quote(_s(a, "payload")))


def _d_cancel(a: dict) -> str:
    return _s(a, "task_id")


DESCRIPTORS: dict[str, AegisToolDescriptor] = {
    "handoff": AegisToolDescriptor(
        "handoff", CONVERSATION, _g_handoff, _d_handoff,
        _agent_at("target_handle")),
    "spawn": AegisToolDescriptor(
        "spawn", CONVERSATION, "✧", _d_spawn, _agent_at("agent")),
    "fork": AegisToolDescriptor(
        "fork", CONVERSATION, "✧", _d_fork, _agent_at("target_handle")),
    "close": AegisToolDescriptor(
        "close", CONVERSATION, "✦", _d_close, _agent_at("handle")),
    "enqueue": AegisToolDescriptor(
        "enqueue", CONVERSATION, "⇉", _d_enqueue,
        _target_at("queue", "queue")),
    "delegate": AegisToolDescriptor(
        "delegate", CONVERSATION, "⇉", _d_delegate,
        _target_at("queue", "queue")),
    "cancel": AegisToolDescriptor(
        "cancel", CONVERSATION, "⇎", _d_cancel,
        _target_at("queue", "task_id")),
}


# --- lookup -----------------------------------------------------------

def _bare_verb(name: str) -> str:
    """``mcp__aegis__aegis_handoff`` and ``aegis_handoff`` both give
    ``handoff``.

    The two consumers see two different names: the renderer gets whatever the
    harness called the tool (claude prefixes ``mcp__<server>__``), while the
    middleware gets the bare registered name. Both land here.
    """
    if name.startswith("mcp__"):
        parts = name.split("__")
        if len(parts) >= 3:
            name = parts[-1]
    return name[len(_PREFIX):] if name.startswith(_PREFIX) else ""


def descriptor_for(name: str) -> AegisToolDescriptor | None:
    return DESCRIPTORS.get(_bare_verb(name))


def aegis_glyph(name: str, args: dict | None = None) -> str | None:
    d = descriptor_for(name)
    if d is None:
        return None
    return d.glyph(args or {}) if callable(d.glyph) else d.glyph


def aegis_describe(name: str, args: dict | None = None) -> str | None:
    d = descriptor_for(name)
    return None if d is None else d.describe(args or {})


def aegis_target(name: str, args: dict | None = None) -> Target | None:
    d = descriptor_for(name)
    return None if d is None else d.target(args or {})


def aegis_family(name: str) -> str | None:
    d = descriptor_for(name)
    return None if d is None else d.family
```

- [x] **Step 4: Run the test to verify it passes**

Run: `uv run python -m pytest tests/test_comms_descriptors.py -q`
Expected: PASS, 10 tests.

- [x] **Step 5: Commit**

```bash
git add src/aegis/comms/__init__.py src/aegis/comms/descriptors.py \
        tests/test_comms_descriptors.py
git commit -m "feat(comms): the descriptor registry and the agent verbs"
```

---

### Task 2: Descriptors for shared surfaces and groups

**Files:**
- Modify: `src/aegis/comms/descriptors.py` (extend `DESCRIPTORS`)
- Test: `tests/test_comms_descriptors.py` (append)

**Interfaces:**
- Consumes: `AegisToolDescriptor`, `Target`, `_s`, `_quote`, `_join`,
  `_target_at`, `CONVERSATION`, `COORDINATION` from Task 1.
- Produces: 16 more entries in `DESCRIPTORS` — `canvas_open`,
  `canvas_write_section`, `canvas_append_to_section`, `canvas_subscribe`,
  `canvas_unsubscribe`, `term_spawn`, `term_run`, `term_keys`,
  `term_subscribe`, `term_unsubscribe`, `term_close`, `group_spawn`,
  `group_spawn_mixed`, `group_broadcast`, `group_wait_all`,
  `group_wait_any`, `group_rename`, `group_dissolve`, `group_move_member`
  (19 entries).

- [x] **Step 1: Write the failing test**

Append to `tests/test_comms_descriptors.py`:

```python
def test_canvas_writes_name_the_section_and_count_the_lines():
    write = {"name": "report", "section": "Findings",
             "content": "a\nb\nc", "from_handle": "me"}
    assert aegis_glyph("aegis_canvas_write_section", write) == "▤"
    assert aegis_describe("aegis_canvas_write_section", write) == (
        "report §Findings · 3 lines")
    assert aegis_target("aegis_canvas_write_section", write) == Target(
        "canvas", "report")

    append = {"name": "report", "section": "Log", "text": "x\ny"}
    assert aegis_describe("aegis_canvas_append_to_section", append) == (
        "report §Log · +2 lines")


def test_canvas_attach_and_detach_take_opposite_glyphs():
    assert aegis_glyph("aegis_canvas_open", {"name": "report"}) == "▥"
    assert aegis_glyph("aegis_canvas_subscribe", {"name": "report"}) == "▥"
    assert aegis_glyph("aegis_canvas_unsubscribe", {"name": "report"}) == "▧"
    assert aegis_describe("aegis_canvas_subscribe", {"name": "report"}) == (
        "report · all sections")
    assert aegis_describe(
        "aegis_canvas_subscribe",
        {"name": "report", "sections": ["Findings", "Log"]}) == (
        "report · Findings, Log")


def test_terminal_verbs_name_the_terminal():
    run = {"name": "build", "cmd": "pytest -q", "from_handle": "me"}
    assert aegis_glyph("aegis_term_run", run) == "■"
    assert aegis_describe("aegis_term_run", run) == 'build · "pytest -q"'
    assert aegis_target("aegis_term_run", run) == Target("term", "build")

    assert aegis_glyph("aegis_term_spawn", {"name": "build"}) == "▥"
    assert aegis_glyph("aegis_term_close", {"name": "build"}) == "▧"
    assert aegis_describe("aegis_term_close", {"name": "build"}) == (
        "build · closed")


def test_term_keys_renders_control_bytes_readably():
    assert aegis_describe("aegis_term_keys",
                          {"name": "build", "keys": "\x03"}) == "build · ^C"
    assert aegis_describe("aegis_term_keys",
                          {"name": "build", "keys": "y\n"}) == 'build · "y⏎"'


def test_group_broadcast_and_waits_point_at_the_group():
    bc = {"from_handle": "me", "group": "reviewers",
          "objective": "review your section"}
    assert aegis_glyph("aegis_group_broadcast", bc) == "⁂"
    assert aegis_describe("aegis_group_broadcast", bc) == (
        'reviewers · "review your section"')
    assert aegis_target("aegis_group_broadcast", bc) == Target(
        "group", "reviewers")

    assert aegis_glyph("aegis_group_wait_all", {"group": "reviewers"}) == "⁑"
    assert aegis_describe("aegis_group_wait_all",
                          {"group": "reviewers", "reducer": "concat"}) == (
        "reviewers · all · concat")
    assert aegis_describe("aegis_group_wait_any",
                          {"group": "reviewers"}) == "reviewers · any"


def test_group_membership_verbs_take_the_reshape_glyph():
    assert aegis_glyph("aegis_group_spawn",
                       {"profile": "main", "group": "reviewers"}) == "✧"
    assert aegis_describe("aegis_group_spawn",
                          {"profile": "main", "group": "reviewers"}) == (
        "reviewers · main")
    assert aegis_describe("aegis_group_spawn_mixed",
                          {"group": "reviewers",
                           "profiles": ["main", "fast"]}) == (
        "reviewers · main, fast")
    assert aegis_glyph("aegis_group_rename", {"old": "a", "new": "b"}) == "⌗"
    assert aegis_describe("aegis_group_rename",
                          {"old": "a", "new": "b"}) == "a · renamed to b"
    assert aegis_describe("aegis_group_dissolve",
                          {"group": "reviewers"}) == "reviewers · dissolved"
    assert aegis_describe("aegis_group_move_member",
                          {"handle": "calm-hopper", "from_group": "a",
                           "to_group": "b"}) == "calm-hopper · a to b"
```

- [x] **Step 2: Run the test to verify it fails**

Run: `uv run python -m pytest tests/test_comms_descriptors.py -q`
Expected: 6 failures, each `TypeError: 'NoneType' object is not ...` or
`AssertionError: assert None == '▤'` — the descriptors do not exist yet.

- [x] **Step 3: Write the implementation**

Add to `src/aegis/comms/descriptors.py`, above `DESCRIPTORS` (the helpers)
and inside it (the entries):

```python
def _lines(text: str) -> int:
    return len(str(text or "").splitlines())


def _d_canvas_write(a: dict) -> str:
    n = _lines(_s(a, "content"))
    return _join(f"{_s(a, 'name')} §{_s(a, 'section')}", f"{n} lines")


def _d_canvas_append(a: dict) -> str:
    n = _lines(_s(a, "text"))
    return _join(f"{_s(a, 'name')} §{_s(a, 'section')}", f"+{n} lines")


def _d_canvas_subscribe(a: dict) -> str:
    sections = a.get("sections")
    which = ", ".join(sections) if sections else "all sections"
    return _join(_s(a, "name"), which)


def _d_canvas_open(a: dict) -> str:
    return _join(_s(a, "name"), _s(a, "file"))


def _d_canvas_unsubscribe(a: dict) -> str:
    return _join(_s(a, "name"), "unsubscribed")


#: The control bytes worth naming. Anything else is quoted with newlines
#: shown as ⏎ — a raw \n in a transcript line would break the row.
_CONTROL = {"\x03": "^C", "\x04": "^D", "\x1a": "^Z", "\x1b": "ESC"}


def _d_term_keys(a: dict) -> str:
    keys = a.get("keys")
    keys = keys if isinstance(keys, str) else ""
    named = _CONTROL.get(keys)
    if named:
        return _join(_s(a, "name"), named)
    return _join(_s(a, "name"), _quote(keys.replace("\n", "⏎"), 24))


def _d_term_run(a: dict) -> str:
    return _join(_s(a, "name"), _quote(_s(a, "cmd")))


def _d_term_spawn(a: dict) -> str:
    return _join(_s(a, "name"), _s(a, "cwd"))


def _d_group_wait(mode: str) -> Callable[[dict], str]:
    def describe(a: dict) -> str:
        return _join(_s(a, "group"), mode,
                     _s(a, "reducer") if mode == "all" else "")
    return describe


def _d_group_spawn(a: dict) -> str:
    return _join(_s(a, "group"), _s(a, "profile"))


def _d_group_spawn_mixed(a: dict) -> str:
    profiles = a.get("profiles") or []
    which = ", ".join(profiles) if profiles else _s(a, "preset")
    return _join(_s(a, "group"), which)


def _d_group_rename(a: dict) -> str:
    return _join(_s(a, "old"), f"renamed to {_s(a, 'new')}"
                 if _s(a, "new") else "")


def _d_group_move(a: dict) -> str:
    hop = f"{_s(a, 'from_group')} to {_s(a, 'to_group')}"
    return _join(_s(a, "handle"), hop if _s(a, "to_group") else "")
```

Then extend `DESCRIPTORS` with:

```python
    # --- shared surfaces ---
    "canvas_open": AegisToolDescriptor(
        "canvas_open", CONVERSATION, "▥", _d_canvas_open,
        _target_at("canvas", "name")),
    "canvas_write_section": AegisToolDescriptor(
        "canvas_write_section", CONVERSATION, "▤", _d_canvas_write,
        _target_at("canvas", "name")),
    "canvas_append_to_section": AegisToolDescriptor(
        "canvas_append_to_section", CONVERSATION, "▤", _d_canvas_append,
        _target_at("canvas", "name")),
    "canvas_subscribe": AegisToolDescriptor(
        "canvas_subscribe", CONVERSATION, "▥", _d_canvas_subscribe,
        _target_at("canvas", "name")),
    "canvas_unsubscribe": AegisToolDescriptor(
        "canvas_unsubscribe", CONVERSATION, "▧", _d_canvas_unsubscribe,
        _target_at("canvas", "name")),
    "term_spawn": AegisToolDescriptor(
        "term_spawn", CONVERSATION, "▥", _d_term_spawn,
        _target_at("term", "name")),
    "term_run": AegisToolDescriptor(
        "term_run", CONVERSATION, "■", _d_term_run,
        _target_at("term", "name")),
    "term_keys": AegisToolDescriptor(
        "term_keys", CONVERSATION, "■", _d_term_keys,
        _target_at("term", "name")),
    "term_subscribe": AegisToolDescriptor(
        "term_subscribe", CONVERSATION, "▥",
        lambda a: _join(_s(a, "name"), "subscribed"),
        _target_at("term", "name")),
    "term_unsubscribe": AegisToolDescriptor(
        "term_unsubscribe", CONVERSATION, "▧",
        lambda a: _join(_s(a, "name"), "unsubscribed"),
        _target_at("term", "name")),
    "term_close": AegisToolDescriptor(
        "term_close", CONVERSATION, "▧",
        lambda a: _join(_s(a, "name"), "closed"),
        _target_at("term", "name")),
    # --- groups ---
    "group_spawn": AegisToolDescriptor(
        "group_spawn", CONVERSATION, "✧", _d_group_spawn,
        _target_at("group", "group")),
    "group_spawn_mixed": AegisToolDescriptor(
        "group_spawn_mixed", CONVERSATION, "✧", _d_group_spawn_mixed,
        _target_at("group", "group")),
    "group_broadcast": AegisToolDescriptor(
        "group_broadcast", CONVERSATION, "⁂",
        lambda a: _join(_s(a, "group"), _quote(_s(a, "objective"))),
        _target_at("group", "group")),
    "group_wait_all": AegisToolDescriptor(
        "group_wait_all", CONVERSATION, "⁑", _d_group_wait("all"),
        _target_at("group", "group")),
    "group_wait_any": AegisToolDescriptor(
        "group_wait_any", CONVERSATION, "⁑", _d_group_wait("any"),
        _target_at("group", "group")),
    "group_rename": AegisToolDescriptor(
        "group_rename", COORDINATION, "⌗", _d_group_rename,
        _target_at("group", "old")),
    "group_dissolve": AegisToolDescriptor(
        "group_dissolve", COORDINATION, "⌗",
        lambda a: _join(_s(a, "group"), "dissolved"),
        _target_at("group", "group")),
    "group_move_member": AegisToolDescriptor(
        "group_move_member", COORDINATION, "⌗", _d_group_move,
        _target_at("group", "to_group")),
```

- [x] **Step 4: Run the test to verify it passes**

Run: `uv run python -m pytest tests/test_comms_descriptors.py -q`
Expected: PASS, 16 tests.

- [x] **Step 5: Commit**

```bash
git add src/aegis/comms/descriptors.py tests/test_comms_descriptors.py
git commit -m "feat(comms): descriptors for canvases, terminals and groups"
```

---

### Task 3: The coordination verbs, the pale tier, and the coverage gate

**Files:**
- Modify: `src/aegis/comms/descriptors.py`
- Test: `tests/test_comms_descriptors.py` (append)
- Test: `tests/test_comms_coverage.py` (create)

**Interfaces:**
- Consumes: everything from Tasks 1–2.
- Produces: `DESCRIPTORS` covering all 72 registered tools;
  `pale_descriptor(verb: str) -> AegisToolDescriptor`.

- [x] **Step 1: Write the failing tests**

Append to `tests/test_comms_descriptors.py`:

```python
from aegis.comms.descriptors import (ADMIN, COORDINATION, INTROSPECTION,
                                     PALE_GLYPH)


def test_claim_and_release_are_opposite_circles():
    claim = {"paths": ["src/aegis/mcp/", "src/aegis/comms/"],
             "from_handle": "me", "intent": "exclusive"}
    assert aegis_glyph("aegis_claim", claim) == "⊙"
    assert aegis_describe("aegis_claim", claim) == (
        "exclusive · src/aegis/mcp/ · 2 paths")
    assert aegis_target("aegis_claim", claim) == Target(
        "path", "src/aegis/mcp/")
    assert aegis_family("aegis_claim") == COORDINATION

    rel = {"claim_id": "01K4TZ", "from_handle": "me"}
    assert aegis_glyph("aegis_release", rel) == "⊚"
    assert aegis_describe("aegis_release", rel) == "01K4TZ"
    assert aegis_target("aegis_release", rel) == Target("claim", "01K4TZ")


def test_a_single_path_claim_does_not_say_one_paths():
    claim = {"paths": ["src/aegis/mcp/"], "intent": "shared"}
    assert aegis_describe("aegis_claim", claim) == "shared · src/aegis/mcp/"


def test_wakers_arm_and_disarm():
    mon = {"from_handle": "me", "description": "pytest", "done": "test -f ok"}
    assert aegis_glyph("aegis_monitor", mon) == "◷"
    assert aegis_describe("aegis_monitor", mon) == "pytest"
    assert aegis_target("aegis_monitor", mon) == Target("self", "me")

    rem = {"from_handle": "me", "note": "check the tag", "after": "20m"}
    assert aegis_glyph("aegis_remind", rem) == "◷"
    assert aegis_describe("aegis_remind", rem) == 'in 20m · "check the tag"'
    assert aegis_describe("aegis_remind",
                          {"note": "check the tag"}) == (
        'at turn end · "check the tag"')

    assert aegis_glyph("aegis_monitor_cancel", {"monitor_id": "m1"}) == "◶"
    assert aegis_describe("aegis_monitor_cancel", {"monitor_id": "m1"}) == "m1"
    assert aegis_glyph("aegis_reminder_cancel", {"reminder_id": "r1"}) == "◶"


def test_loop_stop_and_self_naming():
    stop = {"from_handle": "me", "reason": "wired end to end"}
    assert aegis_glyph("aegis_loop_stop", stop) == "◼"
    assert aegis_describe("aegis_loop_stop", stop) == '"wired end to end"'

    ren = {"old_handle": "civic-cook", "new_handle": "aegis-call-format",
           "title": "design the call format"}
    assert aegis_glyph("aegis_rename", ren) == "❖"
    assert aegis_describe("aegis_rename", ren) == (
        'aegis-call-format · "design the call format"')
    assert aegis_target("aegis_rename", ren) == Target(
        "agent", "aegis-call-format")

    tit = {"from_handle": "me", "title": "fix the eviction race"}
    assert aegis_glyph("aegis_title", tit) == "❖"
    assert aegis_describe("aegis_title", tit) == '"fix the eviction race"'


def test_the_pale_tier_shares_one_glyph_and_describes_the_call():
    assert aegis_glyph("aegis_list_sessions", {}) == PALE_GLYPH
    assert aegis_describe("aegis_list_sessions", {}) == "list sessions"
    assert aegis_family("aegis_list_sessions") == INTROSPECTION

    assert aegis_describe("aegis_read_peer", {"handle": "weary-turing"}) == (
        "read peer · weary-turing")
    assert aegis_describe("aegis_config_show", {}) == "config show"
    assert aegis_family("aegis_config_show") == ADMIN
    assert aegis_target("aegis_list_sessions", {}) is None
```

Create `tests/test_comms_coverage.py`:

```python
"""Every tool the MCP server registers must have a descriptor.

This is the test that stops tool seventy-three from silently falling out of
the format. A tool with no descriptor renders as the generic dot with the
first-stringy-argument fallback — which is exactly the state this whole
feature exists to end, and nothing else would notice.
"""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from aegis.comms.descriptors import (ADMIN, CONVERSATION, COORDINATION,
                                     INTROSPECTION, descriptor_for)
from aegis.mcp.server import build_server

_FAMILIES = {CONVERSATION, COORDINATION, INTROSPECTION, ADMIN}


@pytest.fixture(scope="module")
def tool_names() -> list[str]:
    import asyncio
    server = build_server(MagicMock())
    return sorted(t.name for t in asyncio.run(server.list_tools()))


def test_every_registered_tool_has_a_descriptor(tool_names):
    missing = [n for n in tool_names if descriptor_for(n) is None]
    assert missing == [], (
        f"{len(missing)} aegis tools have no comms descriptor: {missing}")


def test_every_descriptor_declares_a_known_family(tool_names):
    for name in tool_names:
        d = descriptor_for(name)
        assert d.family in _FAMILIES, f"{name} has family {d.family!r}"


def test_every_descriptor_survives_empty_arguments(tool_names):
    """The model can call any tool with anything. A descriptor that raises
    on a missing argument would take down the transcript paint."""
    for name in tool_names:
        d = descriptor_for(name)
        glyph = d.glyph({}) if callable(d.glyph) else d.glyph
        assert isinstance(glyph, str) and glyph
        assert isinstance(d.describe({}), str)
        assert d.target({}) is None or d.target({}).kind
```

- [x] **Step 2: Run the tests to verify they fail**

Run: `uv run python -m pytest tests/test_comms_descriptors.py tests/test_comms_coverage.py -q`
Expected: the coverage test fails with a list of ~53 unmapped tool names; the
descriptor tests fail on the missing coordination verbs.

- [x] **Step 3: Write the implementation**

Add to `src/aegis/comms/descriptors.py`:

```python
def _d_claim(a: dict) -> str:
    paths = a.get("paths") or []
    first = str(paths[0]) if paths else ""
    count = f"{len(paths)} paths" if len(paths) > 1 else ""
    return _join(_s(a, "intent") or "shared", first, count)


def _t_claim(a: dict) -> Target | None:
    paths = a.get("paths") or []
    return Target("path", str(paths[0])) if paths else None


def _d_remind(a: dict) -> str:
    after = a.get("after")
    when = f"in {after}" if after else "at turn end"
    return _join(when, _quote(_s(a, "note")))


def _d_rename(a: dict) -> str:
    return _join(_s(a, "new_handle"), _quote(_s(a, "title")))


def pale_descriptor(verb: str) -> AegisToolDescriptor:
    """A read-the-room call: one shared glyph, the verb spelled out, and the
    single argument worth naming when there is one."""
    label = verb.replace("_", " ")
    keys = _PALE_ARGS.get(verb, ())

    def describe(args: dict) -> str:
        return _join(label, _s(args, *keys) if keys else "")

    return AegisToolDescriptor(verb, _PALE_FAMILY[verb], PALE_GLYPH, describe)


#: The one argument worth showing beside a pale verb, where one exists.
_PALE_ARGS: dict[str, tuple[str, ...]] = {
    "peer_plan": ("handle",),
    "read_peer": ("handle",),
    "view_file": ("path",),
    "canvas_read": ("name",),
    "term_read": ("name",),
    "task_status": ("task_id",),
    "workflow_status": ("workflow_id",),
    "workflow_cancel": ("workflow_id",),
    "group_status": ("group",),
    "monitors": ("from_handle",),
    "reminders": ("from_handle",),
    "run_workflow": ("name",),
    "config_add_agent": ("slug",),
    "config_remove_agent": ("slug",),
    "config_add_queue": ("name",),
    "config_remove_queue": ("name",),
    "config_add_plugin_dir": ("path",),
    "config_remove_plugin_dir": ("path",),
    "config_set_schedule_enabled": ("name",),
    "config_toggle_schedule_enabled": ("name",),
    "schedule_show": ("name",),
    "schedule_logs": ("name",),
    "schedule_remove": ("name",),
    "schedule_push": ("name",),
}

_INTROSPECTION_VERBS = (
    "meta", "list_sessions", "list_agents", "peer_plan", "read_peer",
    "view_file", "claims", "monitors", "reminders", "canvas_list",
    "canvas_read", "term_list", "term_read", "task_status", "budget_status",
    "workflow_status", "group_status",
)

_ADMIN_VERBS = (
    "config_show", "config_list_agents", "config_list_queues",
    "config_list_schedules", "config_add_agent", "config_remove_agent",
    "config_add_queue", "config_remove_queue", "config_add_plugin_dir",
    "config_remove_plugin_dir", "config_set_schedule_enabled",
    "config_toggle_schedule_enabled", "schedule_list", "schedule_logs",
    "schedule_push", "schedule_remove", "schedule_show", "run_workflow",
    "run_dynamic_workflow", "workflow_cancel",
)

_PALE_FAMILY: dict[str, str] = (
    {v: INTROSPECTION for v in _INTROSPECTION_VERBS}
    | {v: ADMIN for v in _ADMIN_VERBS}
)
```

Extend `DESCRIPTORS` with the coordination verbs:

```python
    "claim": AegisToolDescriptor(
        "claim", COORDINATION, "⊙", _d_claim, _t_claim),
    "release": AegisToolDescriptor(
        "release", COORDINATION, "⊚", lambda a: _s(a, "claim_id"),
        _target_at("claim", "claim_id")),
    "monitor": AegisToolDescriptor(
        "monitor", COORDINATION, "◷", lambda a: _s(a, "description"),
        _target_at("self", "from_handle")),
    "remind": AegisToolDescriptor(
        "remind", COORDINATION, "◷", _d_remind,
        _target_at("self", "from_handle")),
    "monitor_cancel": AegisToolDescriptor(
        "monitor_cancel", COORDINATION, "◶", lambda a: _s(a, "monitor_id")),
    "reminder_cancel": AegisToolDescriptor(
        "reminder_cancel", COORDINATION, "◶",
        lambda a: _s(a, "reminder_id")),
    "loop_stop": AegisToolDescriptor(
        "loop_stop", COORDINATION, "◼", lambda a: _quote(_s(a, "reason")),
        _target_at("self", "from_handle")),
    "rename": AegisToolDescriptor(
        "rename", COORDINATION, "❖", _d_rename,
        _agent_at("new_handle")),
    "title": AegisToolDescriptor(
        "title", COORDINATION, "❖", lambda a: _quote(_s(a, "title")),
        _target_at("self", "from_handle")),
```

Finally, after the literal `DESCRIPTORS` definition, fold in the pale tier:

```python
DESCRIPTORS.update({v: pale_descriptor(v) for v in _PALE_FAMILY})
```

- [x] **Step 4: Run the tests to verify they pass**

Run: `uv run python -m pytest tests/test_comms_descriptors.py tests/test_comms_coverage.py -q`
Expected: PASS. `test_every_registered_tool_has_a_descriptor` covers 72 tools.

- [x] **Step 5: Mutate the gate and confirm it fails**

A gate that cannot fail licenses shipping. Prove this one bites:

```bash
# Temporarily drop one entry, then run only the coverage test.
uv run python - <<'PY'
import pathlib
p = pathlib.Path("src/aegis/comms/descriptors.py")
p.write_text(p.read_text().replace(
    'DESCRIPTORS.update({v: pale_descriptor(v) for v in _PALE_FAMILY})',
    'DESCRIPTORS.update({v: pale_descriptor(v) for v in _PALE_FAMILY '
    'if v != "meta"})'))
PY
uv run python -m pytest tests/test_comms_coverage.py -q
```

Expected: FAIL with `1 aegis tools have no comms descriptor: ['aegis_meta']`.
Then revert:

```bash
git checkout src/aegis/comms/descriptors.py
```

and re-apply Step 3's implementation (or `git stash pop` if you stashed).
Simpler: make the edit by hand, observe the red, undo the edit by hand.

- [x] **Step 6: Run the tests once more to confirm green after the revert**

Run: `uv run python -m pytest tests/test_comms_descriptors.py tests/test_comms_coverage.py -q`
Expected: PASS.

- [x] **Step 7: Commit**

```bash
git add src/aegis/comms/descriptors.py tests/test_comms_descriptors.py \
        tests/test_comms_coverage.py
git commit -m "feat(comms): coordination verbs, the pale tier, and the coverage gate"
```

---

### Task 4: A colour role for the aegis layer

**Files:**
- Modify: `src/aegis/themes/__init__.py:14-58` (`AegisColors`, `aegis_colors`)
- Test: `tests/test_comms_theme.py` (create)

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces: `AegisColors.comms: str` — the colour every aegis-layer glyph and
  counterpart is painted in.

- [x] **Step 1: Write the failing test**

Create `tests/test_comms_theme.py`:

```python
"""The aegis layer gets its own colour, and it costs no theme YAML."""
from __future__ import annotations

import pytest

from aegis.themes import list_theme_names, load_theme


@pytest.mark.parametrize("name", list_theme_names())
def test_every_bundled_theme_yields_a_comms_colour(name):
    colors = load_theme(name).to_aegis_colors()
    assert colors.comms
    assert colors.comms.startswith("#")


@pytest.mark.parametrize("name", list_theme_names())
def test_comms_is_the_theme_primary(name):
    theme = load_theme(name)
    assert theme.to_aegis_colors().comms == theme.colors["primary"]


def test_comms_falls_back_to_the_accent_when_primary_is_absent():
    from textual.theme import Theme as TextualTheme

    from aegis.themes import aegis_colors
    bare = TextualTheme(name="bare", dark=True, foreground="#DDDDDD",
                        accent="#FF00FF")
    assert aegis_colors(bare).comms == "#FF00FF"
```

- [x] **Step 2: Run the test to verify it fails**

Run: `uv run python -m pytest tests/test_comms_theme.py -q`
Expected: FAIL with `AttributeError: 'AegisColors' object has no attribute 'comms'`.

- [x] **Step 3: Write the implementation**

In `src/aegis/themes/__init__.py`, add one field to `AegisColors` after
`rule`:

```python
    # The aegis layer's own colour: every glyph and counterpart on a call
    # into the MCP surface. Derived from the theme's `primary`, which is
    # the one colour every theme declares and this mapping never read —
    # so no theme YAML has to grow a key for it.
    comms: str = ""
```

And in `aegis_colors()`, add to the constructor call:

```python
        comms=theme.primary or theme.accent or fg,
```

- [x] **Step 4: Run the test to verify it passes**

Run: `uv run python -m pytest tests/test_comms_theme.py -q`
Expected: PASS, 7 tests (3 themes × 2 parametrised + 1).

- [x] **Step 5: Commit**

```bash
git add src/aegis/themes/__init__.py tests/test_comms_theme.py
git commit -m "feat(themes): a colour role for the aegis layer"
```

---

### Task 5: The TUI and HTML renderers read the registry

**Files:**
- Modify: `src/aegis/render_shared.py:45-101` (`describe_tool`)
- Modify: `src/aegis/render.py:108-130` (`render_tool_use`)
- Modify: `src/aegis/render_html.py:41-46`
- Test: `tests/test_comms_render.py` (create)

**Interfaces:**
- Consumes: `aegis_describe`, `aegis_glyph`, `descriptor_for`, `PALE_GLYPH`
  from Task 1–3; `AegisColors.comms` from Task 4.
- Produces: `describe_tool` returning the registry's line for aegis tools;
  `render_tool_use` painting the aegis glyph in `colors.comms`.

- [x] **Step 1: Write the failing test**

Create `tests/test_comms_render.py`:

```python
"""The renderers speak the registry's language."""
from __future__ import annotations

from rich.cells import cell_len
from rich.console import Console

from aegis.events import ToolUse
from aegis.render import render_tool_use
from aegis.render_html import render_event_html
from aegis.render_shared import describe_tool
from aegis.themes import load_theme


def _tool_use(name: str, raw_input: dict) -> ToolUse:
    return ToolUse(name=name, raw_input=raw_input, tool_call_id="t1")


def test_describe_tool_prefers_the_aegis_registry():
    line = describe_tool("mcp__aegis__aegis_handoff", {
        "from_handle": "me", "target_handle": "weary-turing",
        "context": "the render is yours"})
    assert line == 'weary-turing · "the render is yours"'


def test_describe_tool_leaves_native_tools_alone():
    assert describe_tool("Bash", {"description": "run tests",
                                  "command": "pytest -q"}) == (
        "run tests  ·  pytest -q")


def test_the_old_fallback_no_longer_leaks_arguments():
    """Before this feature a handoff rendered as its first stringy arg —
    the calling agent's own handle, which says nothing about the call."""
    line = describe_tool("mcp__aegis__aegis_handoff", {
        "from_handle": "me", "target_handle": "weary-turing",
        "context": "x"})
    assert not line.startswith("me")


def _plain(renderable) -> str:
    console = Console(width=120, no_color=True)
    with console.capture() as cap:
        console.print(renderable)
    return cap.get()


def test_render_tool_use_uses_the_aegis_glyph():
    colors = load_theme("aegis-ink").to_aegis_colors()
    out = _plain(render_tool_use(
        _tool_use("mcp__aegis__aegis_claim",
                  {"paths": ["src/aegis/mcp/"], "intent": "exclusive"}),
        colors))
    assert out.startswith("⊙ ")
    assert "exclusive · src/aegis/mcp/" in out


def test_a_native_tool_keeps_its_emoji():
    colors = load_theme("aegis-ink").to_aegis_colors()
    out = _plain(render_tool_use(
        _tool_use("Read", {"file_path": "/tmp/a.py"}), colors))
    assert out.startswith("📖 ")


def test_the_glyph_is_always_followed_by_a_space():
    """East Asian Ambiguous: Rich measures one cell, terminals draw wider.
    Without the separator the glyph overlaps its neighbour."""
    colors = load_theme("aegis-ink").to_aegis_colors()
    for name, args in [("aegis_handoff", {"target_handle": "p"}),
                       ("aegis_claim", {"paths": ["a"]}),
                       ("aegis_meta", {})]:
        out = _plain(render_tool_use(_tool_use(name, args), colors))
        assert out[1] == " ", f"{name} rendered {out[:4]!r}"
        assert cell_len(out[0]) == 1


def test_html_export_uses_the_same_glyph_and_line():
    html = render_event_html(
        _tool_use("mcp__aegis__aegis_enqueue",
                  {"queue": "general", "payload": "port the fixtures"}))
    assert "⇉" in html
    assert 'general · &quot;port the fixtures&quot;' in html or (
        'general · "port the fixtures"' in html)
```

- [x] **Step 2: Run the test to verify it fails**

Run: `uv run python -m pytest tests/test_comms_render.py -q`
Expected: FAIL — `describe_tool` returns `"me"` (the first stringy arg) and
`render_tool_use` starts with `⏺ `.

- [x] **Step 3: Write the implementation**

In `src/aegis/render_shared.py`, add the import at the top:

```python
from aegis.comms.descriptors import aegis_describe, aegis_glyph
```

and insert this as the **first** statement inside `describe_tool`, before
`inp = raw_input or {}`:

```python
    # The aegis layer answers for itself: one registry knows what matters
    # about each of its calls, and the ledger reads the same one.
    aegis_line = aegis_describe(name, raw_input or {})
    if aegis_line is not None:
        return aegis_line
```

Then add, at module level:

```python
def tool_glyph(name: str, kind: str | None, raw_input: dict | None = None
               ) -> str:
    """The leading glyph for a tool line: the aegis layer's own when the
    call is one of ours, else the native per-kind emoji."""
    return aegis_glyph(name, raw_input or {}) or KIND_ICON.get(kind or "",
                                                               "⏺")
```

In `src/aegis/render.py`, replace line 115 and the assembly on line 117:

```python
    icon = tool_glyph(ev.name, ev.kind, ev.raw_input)
    desc = describe_tool(ev.name, ev.raw_input, ev.summary, ev.locations)
    style = colors.comms if aegis_glyph(ev.name, ev.raw_input) else \
        colors.accent
    line = Text.assemble((f"{icon} ", style), desc)
```

and extend the imports on line 16 with `tool_glyph` and, from
`aegis.comms.descriptors`, `aegis_glyph`.

In `src/aegis/render_html.py`, replace line 42:

```python
        icon = tool_glyph(ev.name, ev.kind, ev.raw_input)
```

and add `tool_glyph` to the `render_shared` import on line 16.

- [x] **Step 4: Run the test to verify it passes**

Run: `uv run python -m pytest tests/test_comms_render.py -q`
Expected: PASS, 7 tests.

- [x] **Step 5: Run the render regression suite**

Run: `uv run python -m pytest tests/ -q -m "not live" -k "render or html or transcript"`
Expected: PASS. Existing snapshot-ish assertions about native tools must be
untouched; if one fails on an aegis tool line, it was asserting the old
fallback and should be updated to the new line.

- [x] **Step 6: Commit**

```bash
git add src/aegis/render_shared.py src/aegis/render.py \
        src/aegis/render_html.py tests/test_comms_render.py
git commit -m "feat(render): the transcript speaks the aegis layer's language"
```

---

### Task 6: The web client renders from the wire, not a second table

**Files:**
- Modify: `src/aegis/web/compact.py:28-39`
- Modify: `src/aegis/web/static/js/renderEvent.js:7-10,188-193`
- Modify: `src/aegis/web/static/css/base.css:234-235`
- Test: `tests/test_comms_web_wire.py` (create)

**Interfaces:**
- Consumes: `tool_glyph` from Task 5.
- Produces: the compact `ToolUse` wire dict gains `icon` (resolved
  server-side) and `comms` (bool — whether this is an aegis-layer call).

- [x] **Step 1: Write the failing test**

Create `tests/test_comms_web_wire.py`:

```python
"""The browser gets the glyph off the wire, so there is one glyph table."""
from __future__ import annotations

import re
from pathlib import Path

from aegis.web.compact import compact_encoded

_JS = Path("src/aegis/web/static/js/renderEvent.js")


def _tool_use(name: str, raw_input: dict) -> dict:
    return {"t": "ToolUse", "name": name, "raw_input": raw_input,
            "kind": None, "summary": "", "locations": []}


def test_the_wire_carries_the_resolved_aegis_glyph():
    out, changed = compact_encoded(_tool_use(
        "mcp__aegis__aegis_handoff",
        {"target_handle": "weary-turing", "context": "the render is yours"}))
    assert changed
    assert out["icon"] == "⇄"
    assert out["comms"] is True
    assert out["desc"] == 'weary-turing · "the render is yours"'
    assert "raw_input" not in out


def test_the_wire_carries_the_native_emoji_too():
    out, _ = compact_encoded(_tool_use("Read", {"file_path": "/tmp/a.py"}))
    assert out["icon"] == "📖"
    assert out["comms"] is False


def test_the_browser_no_longer_keeps_its_own_glyph_table():
    """One table, in Python. The duplicate drifted the moment a glyph was
    added on one side only — which is exactly what this feature would have
    done to it."""
    src = _JS.read_text(encoding="utf-8")
    assert "KIND_ICON" not in src


def test_the_browser_paints_aegis_calls_with_their_own_class():
    src = _JS.read_text(encoding="utf-8")
    assert re.search(r"ev\.comms", src)
    assert "tool-use comms" in src
```

- [x] **Step 2: Run the test to verify it fails**

Run: `uv run python -m pytest tests/test_comms_web_wire.py -q`
Expected: 4 failures — `KeyError: 'icon'`, and `KIND_ICON` still present.

- [x] **Step 3: Write the implementation**

In `src/aegis/web/compact.py`, change the import on line 6 and the `ToolUse`
branch:

```python
from aegis.comms.descriptors import aegis_glyph
from aegis.render_shared import describe_tool, tool_glyph
```

```python
    if t == "ToolUse":
        if d.get("raw_input") is None:
            return d, False
        # Precompute the human description AND the glyph server-side, then
        # drop raw_input from the wire — the full args are fetched on demand
        # via get_event when expanded. The glyph goes over the wire rather
        # than being resolved in the browser so there is exactly one glyph
        # table, in Python, for the TUI, the HTML export and the web client.
        out = dict(d)
        name = d.get("name", "")
        raw = d.get("raw_input")
        out["desc"] = describe_tool(name, raw, d.get("summary", ""),
                                    d.get("locations") or ())
        out["icon"] = tool_glyph(name, d.get("kind"), raw)
        out["comms"] = aegis_glyph(name, raw or {}) is not None
        out.pop("raw_input", None)
        return out, True
```

In `src/aegis/web/static/js/renderEvent.js`, delete the `KIND_ICON` constant
(lines 7–10) and change the `ToolUse` branch:

```js
  if (t === "ToolUse") {
    // The glyph is resolved server-side (aegis.render_shared.tool_glyph) and
    // arrives on the wire, so the browser keeps no glyph table of its own.
    const icon = ev.icon || "⏺";
    const cls = ev.comms ? "tool-use comms" : "tool-use";
    const desc = ev.desc || describeTool(ev);
    const ctl = rec.truncated ? " " + expandControl(rec, "⋯") : "";
    const useHtml = `<div class="${cls}"><span class="icon">${icon}</span> `
      + `<span class="tool-desc">${esc(desc)}</span>${ctl}</div>`;
```

In `src/aegis/web/static/css/base.css`, after line 235, add:

```css
.tool-use.comms .icon,
.tool-use.comms .tool-desc { color: var(--aegis-primary); }
```

- [x] **Step 4: Run the test to verify it passes**

Run: `uv run python -m pytest tests/test_comms_web_wire.py -q`
Expected: PASS, 4 tests.

- [x] **Step 5: Check the JS still parses as a module**

`node --check` returns 0 on syntax errors in ESM files. Check it as `.mjs`:

```bash
cp src/aegis/web/static/js/renderEvent.js /tmp/renderEvent.mjs
node --check /tmp/renderEvent.mjs; echo "rc=$?"
```

Expected: `rc=0`. Then prove the check can fail — append a stray `}` to the
copy, re-run, confirm a non-zero rc, and delete the copy.

- [x] **Step 6: Run the web suite**

Run: `uv run python -m pytest tests/ -q -m "not live" -k "web or compact or wire"`
Expected: PASS.

- [x] **Step 7: Commit**

```bash
git add src/aegis/web/compact.py src/aegis/web/static/js/renderEvent.js \
        src/aegis/web/static/css/base.css tests/test_comms_web_wire.py
git commit -m "feat(web): the glyph comes off the wire, and the duplicate table goes"
```

---

### Task 7: The envelope and its ledger

**Files:**
- Create: `src/aegis/comms/models.py`
- Create: `src/aegis/comms/persistence.py`
- Test: `tests/test_comms_ledger.py` (create)

**Interfaces:**
- Consumes: `Target` from Task 1; `new_ulid` / `now_iso` from
  `aegis/queue/schema.py:66,82`; `append_record` from
  `aegis/queue/jsonl.py:16`.
- Produces:
  - `Envelope` frozen dataclass with `.to_record() -> dict`.
  - `CommsLedger(state_dir: Path)` with `.path(day: str) -> Path`,
    `.write(env: Envelope) -> None`, `.read(day: str) -> list[dict]`,
    `.days() -> list[str]`, `.read_all() -> list[dict]`. The day string is
    `env.ts[:10]`, so the ledger never reads a clock of its own.

- [x] **Step 1: Write the failing test**

Create `tests/test_comms_ledger.py`:

```python
"""The envelope round-trips, and a torn ledger degrades instead of raising."""
from __future__ import annotations

from pathlib import Path

from aegis.comms.descriptors import CONVERSATION, Target
from aegis.comms.models import Envelope
from aegis.comms.persistence import CommsLedger


def _env(**over) -> Envelope:
    base = dict(call_id="01K4TZ", ts="2026-08-11T14:22:07Z",
                from_handle="aegis-call-format",
                to=Target("agent", "weary-turing"), family=CONVERSATION,
                verb="handoff", thread="01K4TZ", outcome="ok",
                duration_ms=41)
    base.update(over)
    return Envelope(**base)


def test_the_record_is_flat_json_with_a_typed_target():
    rec = _env().to_record()
    assert rec["from"] == "aegis-call-format"
    assert rec["to"] == {"kind": "agent", "id": "weary-turing"}
    assert rec["verb"] == "handoff"
    assert rec["outcome"] == "ok"
    assert rec["duration_ms"] == 41


def test_an_absent_target_serialises_as_null_not_a_missing_key():
    rec = _env(to=None).to_record()
    assert "to" in rec and rec["to"] is None


def test_an_unattributed_call_keeps_an_explicit_empty_from():
    """from_handle is a convention, not a transport fact. A call without one
    is recorded unattributed rather than guessed at."""
    rec = _env(from_handle="").to_record()
    assert rec["from"] == ""


def test_write_then_read_round_trips(tmp_path: Path):
    ledger = CommsLedger(tmp_path)
    ledger.write(_env())
    ledger.write(_env(call_id="01K4U0", verb="enqueue"))
    rows = ledger.read_all()
    assert [r["verb"] for r in rows] == ["handoff", "enqueue"]
    assert rows[0]["v"] == 1


def test_the_day_file_is_named_for_the_envelope_timestamp(tmp_path: Path):
    ledger = CommsLedger(tmp_path)
    ledger.write(_env(ts="2026-08-11T14:22:07Z"))
    assert (tmp_path / "comms" / "2026-08-11.jsonl").is_file()


def test_a_torn_trailing_line_is_skipped_not_raised(tmp_path: Path):
    ledger = CommsLedger(tmp_path)
    ledger.write(_env())
    ledger.write(_env(call_id="01K4U0", verb="enqueue"))
    path = ledger.path("2026-08-11")
    with path.open("a", encoding="utf-8") as f:
        f.write('{"v":1,"call_id":"01K4U1","verb":"cla')
    rows = ledger.read_all()
    assert [r["verb"] for r in rows] == ["handoff", "enqueue"]


def test_reading_a_ledger_that_does_not_exist_yet_is_empty(tmp_path: Path):
    assert CommsLedger(tmp_path).read_all() == []
```

- [x] **Step 2: Run the test to verify it fails**

Run: `uv run python -m pytest tests/test_comms_ledger.py -q`
Expected: collection error — `No module named 'aegis.comms.models'`.

- [x] **Step 3: Write the implementation**

Create `src/aegis/comms/models.py`:

```python
"""One record per call into the aegis MCP surface."""
from __future__ import annotations

from dataclasses import dataclass

from aegis.comms.descriptors import Target


@dataclass(frozen=True)
class Envelope:
    call_id: str
    ts: str
    #: The calling agent's handle, from the call's ``from_handle`` argument.
    #: Empty when the tool does not take one, or the caller omitted it — the
    #: MCP server is co-resident and shared, so there is no transport
    #: identity to fall back on and guessing would be worse than a gap.
    from_handle: str
    to: Target | None
    family: str
    verb: str
    thread: str
    outcome: str
    duration_ms: int

    def to_record(self) -> dict:
        return {
            "call_id": self.call_id,
            "ts": self.ts,
            "from": self.from_handle,
            "to": ({"kind": self.to.kind, "id": self.to.id}
                   if self.to is not None else None),
            "family": self.family,
            "verb": self.verb,
            "thread": self.thread,
            "outcome": self.outcome,
            "duration_ms": self.duration_ms,
        }
```

Create `src/aegis/comms/persistence.py`:

```python
"""The comms ledger: one append-only JSONL per day, per aegis instance.

Per instance rather than per handle — the whole value of the record is the
cross-agent view of who spoke to whom.

Writes go through ``queue.jsonl.append_record``, which already creates the
parent directory and stamps the schema version. Reads do NOT go through its
``read_records``: that one calls ``json.loads`` per line and raises on a
truncated trailing record, which is right for queue replay (a corrupt
lifecycle log should stop the boot) and wrong here, where a torn line must
cost one record and nothing else.
"""
from __future__ import annotations

import json
from pathlib import Path

from aegis.comms.models import Envelope
from aegis.queue.jsonl import append_record


class CommsLedger:
    def __init__(self, state_dir: Path) -> None:
        self._root = Path(state_dir) / "comms"

    def path(self, day: str) -> Path:
        return self._root / f"{day}.jsonl"

    def write(self, env: Envelope) -> None:
        append_record(self.path(env.ts[:10]), env.to_record())

    def read(self, day: str) -> list[dict]:
        path = self.path(day)
        if not path.is_file():
            return []
        rows: list[dict] = []
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                continue
        return rows

    def days(self) -> list[str]:
        if not self._root.is_dir():
            return []
        return sorted(p.stem for p in self._root.glob("*.jsonl"))

    def read_all(self) -> list[dict]:
        rows: list[dict] = []
        for day in self.days():
            rows.extend(self.read(day))
        return rows
```

- [x] **Step 4: Run the test to verify it passes**

Run: `uv run python -m pytest tests/test_comms_ledger.py -q`
Expected: PASS, 7 tests.

- [x] **Step 5: Commit**

```bash
git add src/aegis/comms/models.py src/aegis/comms/persistence.py \
        tests/test_comms_ledger.py
git commit -m "feat(comms): the envelope and its append-only ledger"
```

---

### Task 8: The middleware that mints envelopes

**Files:**
- Create: `src/aegis/comms/middleware.py`
- Modify: `src/aegis/mcp/server.py:588-591` (mount it in `build_server`)
- Test: `tests/test_comms_middleware.py` (create)

**Interfaces:**
- Consumes: `aegis_family`, `aegis_target`, `descriptor_for` (Task 1–3);
  `Envelope` (Task 7); `CommsLedger` (Task 7); `new_ulid`, `now_iso`
  (`aegis/queue/schema.py`).
- Produces: `CommsMiddleware(ledger: CommsLedger)`, mounted via
  `server.add_middleware(...)` in `build_server`.

**Facts established by probing a live FastMCP 3.2.0 server — do not
re-derive them:**
- `context.message.name` is the **bare** registered name (`aegis_enqueue`),
  never `mcp__aegis__aegis_enqueue`.
- `context.message.arguments` is a `dict` or `None`.
- The tool's return dict is on `result.structured_content`.
- A failing tool propagates `fastmcp.exceptions.ToolError` through
  `call_next`.

- [x] **Step 1: Write the failing test**

Create `tests/test_comms_middleware.py`:

```python
"""Every call through the MCP surface leaves exactly one envelope."""
from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from fastmcp import Client, FastMCP

from aegis.comms.middleware import CommsMiddleware
from aegis.comms.persistence import CommsLedger


def _server(ledger: CommsLedger) -> FastMCP:
    server = FastMCP("test")
    server.add_middleware(CommsMiddleware(ledger))

    @server.tool
    async def aegis_enqueue(queue: str, payload: str,
                            from_handle: str) -> dict:
        return {"task_id": "01TASK", "queued_position": 2}

    @server.tool
    async def aegis_handoff(from_handle: str, target_handle: str,
                            context: str) -> dict:
        return {"result": "landed"}

    @server.tool
    async def aegis_list_sessions() -> list[dict]:
        return []

    @server.tool
    async def aegis_claim(paths: list[str], from_handle: str) -> dict:
        raise ValueError("denied")

    return server


async def _call(server: FastMCP, name: str, args: dict) -> None:
    async with Client(server) as client:
        await client.call_tool(name, args)


def test_a_successful_call_writes_one_ok_envelope(tmp_path: Path):
    ledger = CommsLedger(tmp_path)
    asyncio.run(_call(_server(ledger), "aegis_handoff", {
        "from_handle": "me", "target_handle": "weary-turing",
        "context": "the render is yours"}))
    rows = ledger.read_all()
    assert len(rows) == 1
    assert rows[0]["verb"] == "handoff"
    assert rows[0]["from"] == "me"
    assert rows[0]["to"] == {"kind": "agent", "id": "weary-turing"}
    assert rows[0]["family"] == "conversation"
    assert rows[0]["outcome"] == "ok"
    assert rows[0]["duration_ms"] >= 0


def test_the_thread_is_the_substrate_id_from_the_result(tmp_path: Path):
    ledger = CommsLedger(tmp_path)
    asyncio.run(_call(_server(ledger), "aegis_enqueue", {
        "queue": "general", "payload": "port the fixtures",
        "from_handle": "me"}))
    row = ledger.read_all()[0]
    assert row["thread"] == "01TASK"
    assert row["thread"] != row["call_id"]


def test_a_call_with_no_substrate_id_threads_on_its_own_call_id(tmp_path):
    ledger = CommsLedger(tmp_path)
    asyncio.run(_call(_server(ledger), "aegis_handoff", {
        "from_handle": "me", "target_handle": "p", "context": "x"}))
    row = ledger.read_all()[0]
    assert row["thread"] == row["call_id"]


def test_a_failing_tool_still_leaves_an_error_envelope(tmp_path: Path):
    ledger = CommsLedger(tmp_path)
    with pytest.raises(Exception):
        asyncio.run(_call(_server(ledger), "aegis_claim", {
            "paths": ["src/"], "from_handle": "me"}))
    rows = ledger.read_all()
    assert len(rows) == 1
    assert rows[0]["verb"] == "claim"
    assert rows[0]["outcome"] == "error"


def test_a_tool_without_from_handle_is_recorded_unattributed(tmp_path: Path):
    ledger = CommsLedger(tmp_path)
    asyncio.run(_call(_server(ledger), "aegis_list_sessions", {}))
    row = ledger.read_all()[0]
    assert row["from"] == ""
    assert row["to"] is None
    assert row["family"] == "introspection"


def test_a_broken_ledger_never_fails_the_tool(tmp_path: Path):
    """Observability that can break what it observes is a liability."""
    class Exploding(CommsLedger):
        def write(self, env):  # noqa: ANN001
            raise OSError("disk is gone")

    server = _server(Exploding(tmp_path))

    async def run() -> object:
        async with Client(server) as client:
            return await client.call_tool("aegis_handoff", {
                "from_handle": "me", "target_handle": "p", "context": "x"})

    result = asyncio.run(run())
    assert result is not None


def test_the_middleware_ignores_tools_that_are_not_ours(tmp_path: Path):
    ledger = CommsLedger(tmp_path)
    server = FastMCP("test")
    server.add_middleware(CommsMiddleware(ledger))

    @server.tool
    async def some_plugin_tool(x: str) -> str:
        return x

    asyncio.run(_call(server, "some_plugin_tool", {"x": "hi"}))
    assert ledger.read_all() == []
```

- [x] **Step 2: Run the test to verify it fails**

Run: `uv run python -m pytest tests/test_comms_middleware.py -q`
Expected: collection error — `No module named 'aegis.comms.middleware'`.

- [x] **Step 3: Write the implementation**

Create `src/aegis/comms/middleware.py`:

```python
"""One envelope per call into the aegis MCP surface.

``on_call_tool`` is the single point every tool invocation passes through,
including plugin ``@tool``s. Wrapping the sixty-odd tools individually would
work today and silently miss the next one added.
"""
from __future__ import annotations

import logging
import time

from fastmcp.server.middleware import Middleware

from aegis.comms.descriptors import (aegis_family, aegis_target,
                                     descriptor_for)
from aegis.comms.models import Envelope
from aegis.comms.persistence import CommsLedger
from aegis.queue.schema import new_ulid, now_iso

log = logging.getLogger(__name__)

#: Substrate ids, in the order they are looked for. The result wins over the
#: arguments: ``enqueue`` mints its task id inside the call, while ``cancel``
#: and ``release`` are handed one.
_THREAD_KEYS = ("task_id", "monitor_id", "reminder_id", "claim_id",
                "workflow_run_id", "broadcast_id")


def _thread(result_data: object, args: dict, call_id: str) -> str:
    for source in (result_data if isinstance(result_data, dict) else {},
                   args):
        for key in _THREAD_KEYS:
            val = source.get(key)
            if isinstance(val, str) and val:
                return val
    return call_id


class CommsMiddleware(Middleware):
    def __init__(self, ledger: CommsLedger) -> None:
        self._ledger = ledger

    async def on_call_tool(self, context, call_next):  # noqa: ANN001
        name = context.message.name
        if descriptor_for(name) is None:
            return await call_next(context)

        args = context.message.arguments or {}
        call_id = new_ulid()
        ts = now_iso()
        started = time.monotonic()
        outcome = "ok"
        payload: object = None
        try:
            result = await call_next(context)
            payload = getattr(result, "structured_content", None)
            return result
        except Exception:
            outcome = "error"
            raise
        finally:
            self._record(name, args, call_id, ts, started, outcome, payload)

    def _record(self, name: str, args: dict, call_id: str, ts: str,
                started: float, outcome: str, payload: object) -> None:
        try:
            self._ledger.write(Envelope(
                call_id=call_id,
                ts=ts,
                from_handle=str(args.get("from_handle") or ""),
                to=aegis_target(name, args),
                family=aegis_family(name) or "",
                verb=name.removeprefix("aegis_"),
                thread=_thread(payload, args, call_id),
                outcome=outcome,
                duration_ms=int((time.monotonic() - started) * 1000),
            ))
        except Exception:
            # The ledger is observability. It never gets to fail a call —
            # but it is logged rather than swallowed, because a silent
            # except here would make a broken writer look like a working one.
            log.exception("comms ledger write failed for %s", name)
```

In `src/aegis/mcp/server.py`, at the top of `build_server` (after
`server = FastMCP("aegis")`):

```python
    from pathlib import Path

    from aegis.comms.middleware import CommsMiddleware
    from aegis.comms.persistence import CommsLedger

    qm = getattr(bridge, "queue_manager", None)
    state_dir = (getattr(qm, "_state_dir", None) if qm is not None else None)
    server.add_middleware(CommsMiddleware(
        CommsLedger(Path(state_dir) if state_dir
                    else Path.cwd() / ".aegis" / "state")))
```

- [x] **Step 4: Run the test to verify it passes**

Run: `uv run python -m pytest tests/test_comms_middleware.py -q`
Expected: PASS, 7 tests.

- [x] **Step 5: Confirm the real server still builds and still has 72 tools**

Run:

```bash
uv run python -c "
import asyncio
from unittest.mock import MagicMock
from aegis.mcp.server import build_server
s = build_server(MagicMock())
print(len(asyncio.run(s.list_tools())))
"
```

Expected: `72`.

- [x] **Step 6: Run the MCP suite**

Run: `uv run python -m pytest tests/ -q -m "not live" -k "mcp or queue or comms"`
Expected: PASS.

- [x] **Step 7: Commit**

```bash
git add src/aegis/comms/middleware.py src/aegis/mcp/server.py \
        tests/test_comms_middleware.py
git commit -m "feat(comms): mint an envelope for every call through the MCP surface"
```

---

### Task 9: `aegis comms` — the reader that makes the ledger verifiable

**Files:**
- Create: `src/aegis/cli_comms.py`
- Modify: `src/aegis/cli.py:22-41` (mount the subapp)
- Test: `tests/test_comms_cli.py` (create)

**Interfaces:**
- Consumes: `CommsLedger` (Task 7), `Envelope` (Task 7).
- Produces: `comms_app` typer app with one command, `list`, mounted as
  `aegis comms`; `filter_rows(rows, handle, thread, family, since_iso)` —
  the pure filter, tested directly.

- [x] **Step 1: Write the failing test**

Create `tests/test_comms_cli.py`:

```python
"""The ledger has a reader, so the artifact can be exercised as it is used."""
from __future__ import annotations

from pathlib import Path

from typer.testing import CliRunner

from aegis.cli_comms import comms_app, filter_rows
from aegis.comms.descriptors import CONVERSATION, Target
from aegis.comms.models import Envelope
from aegis.comms.persistence import CommsLedger

runner = CliRunner()


def _write(tmp_path: Path) -> CommsLedger:
    ledger = CommsLedger(tmp_path / ".aegis" / "state")
    ledger.write(Envelope(
        call_id="01A", ts="2026-08-11T10:00:00Z", from_handle="alice",
        to=Target("agent", "bob"), family=CONVERSATION, verb="handoff",
        thread="01A", outcome="ok", duration_ms=12))
    ledger.write(Envelope(
        call_id="01B", ts="2026-08-11T10:05:00Z", from_handle="bob",
        to=Target("queue", "general"), family=CONVERSATION, verb="enqueue",
        thread="01TASK", outcome="ok", duration_ms=30))
    ledger.write(Envelope(
        call_id="01C", ts="2026-08-11T10:06:00Z", from_handle="alice",
        to=None, family="introspection", verb="list_sessions",
        thread="01C", outcome="ok", duration_ms=3))
    return ledger


def test_filter_by_handle_matches_either_end(tmp_path: Path):
    rows = _write(tmp_path).read_all()
    assert [r["verb"] for r in filter_rows(rows, handle="bob")] == [
        "handoff", "enqueue"]


def test_filter_by_thread_and_family(tmp_path: Path):
    rows = _write(tmp_path).read_all()
    assert [r["verb"] for r in filter_rows(rows, thread="01TASK")] == [
        "enqueue"]
    assert [r["verb"] for r in
            filter_rows(rows, family="introspection")] == ["list_sessions"]


def test_filter_by_since_drops_older_rows(tmp_path: Path):
    rows = _write(tmp_path).read_all()
    kept = filter_rows(rows, since_iso="2026-08-11T10:05:00Z")
    assert [r["verb"] for r in kept] == ["enqueue", "list_sessions"]


def test_the_command_prints_one_line_per_call(tmp_path: Path, monkeypatch):
    _write(tmp_path)
    monkeypatch.chdir(tmp_path)
    result = runner.invoke(comms_app, ["list"])
    assert result.exit_code == 0
    assert "alice" in result.stdout and "bob" in result.stdout
    assert "handoff" in result.stdout
    assert len(result.stdout.strip().splitlines()) >= 3


def test_the_command_says_so_when_the_ledger_is_empty(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    result = runner.invoke(comms_app, ["list"])
    assert result.exit_code == 0
    assert "no aegis calls recorded" in result.stdout.lower()
```

- [x] **Step 2: Run the test to verify it fails**

Run: `uv run python -m pytest tests/test_comms_cli.py -q`
Expected: collection error — `No module named 'aegis.cli_comms'`.

- [x] **Step 3: Write the implementation**

Create `src/aegis/cli_comms.py`:

```python
"""``aegis comms`` — read back who talked to whom.

A write-only ledger cannot be verified: there is no way to know it works
except opening the file by hand. This is how the artifact gets exercised the
way it will actually be used.
"""
from __future__ import annotations

from pathlib import Path

import typer

from aegis.comms.persistence import CommsLedger

comms_app = typer.Typer(add_completion=False,
                        help="Inspect the inter-agent call ledger.")


def filter_rows(rows: list[dict], handle: str | None = None,
                thread: str | None = None, family: str | None = None,
                since_iso: str | None = None) -> list[dict]:
    """Pure filter over ledger records. ``handle`` matches either end of a
    call — the point of the ledger is the conversation, not one side of it."""
    out = []
    for row in rows:
        if handle:
            to = row.get("to") or {}
            if row.get("from") != handle and to.get("id") != handle:
                continue
        if thread and row.get("thread") != thread:
            continue
        if family and row.get("family") != family:
            continue
        if since_iso and str(row.get("ts", "")) < since_iso:
            continue
        out.append(row)
    return out


def _format(row: dict) -> str:
    to = row.get("to") or {}
    counterpart = f"{to.get('kind')}:{to.get('id')}" if to else "-"
    flag = "" if row.get("outcome") == "ok" else "  ERROR"
    return (f"{row.get('ts', ''):22} {row.get('from') or '(unattributed)':22} "
            f"{row.get('verb', ''):26} {counterpart:28} "
            f"{row.get('thread', ''):14}{flag}")


@comms_app.command("list")
def list_calls(
    handle: str = typer.Option(None, "--handle",
                               help="Only calls with this agent at either end."),
    thread: str = typer.Option(None, "--thread",
                               help="Only calls on this thread id."),
    family: str = typer.Option(None, "--family",
                               help="conversation | coordination | "
                                    "introspection | admin"),
    since: str = typer.Option(None, "--since",
                              help="ISO timestamp; drop anything older."),
) -> None:
    ledger = CommsLedger(Path.cwd() / ".aegis" / "state")
    rows = filter_rows(ledger.read_all(), handle=handle, thread=thread,
                       family=family, since_iso=since)
    if not rows:
        typer.echo("no aegis calls recorded")
        return
    for row in rows:
        typer.echo(_format(row))
```

In `src/aegis/cli.py`, beside the other `add_typer` calls (lines 26–41):

```python
from aegis.cli_comms import comms_app as _comms_app
app.add_typer(_comms_app, name="comms")
```

- [x] **Step 4: Run the test to verify it passes**

Run: `uv run python -m pytest tests/test_comms_cli.py -q`
Expected: PASS, 5 tests.

- [x] **Step 5: Exercise it the way it will be used**

Run the real binary against a real ledger — a proxy signal is not enough:

```bash
uv run aegis comms list --help
cd /tmp && rm -rf comms-smoke && mkdir comms-smoke && cd comms-smoke
uv --project /home/apiad/Workspace/repos/aegis run python -c "
from pathlib import Path
from aegis.comms.models import Envelope
from aegis.comms.descriptors import Target, CONVERSATION
from aegis.comms.persistence import CommsLedger
CommsLedger(Path('.aegis/state')).write(Envelope(
    call_id='01A', ts='2026-08-11T10:00:00Z', from_handle='alice',
    to=Target('agent','bob'), family=CONVERSATION, verb='handoff',
    thread='01A', outcome='ok', duration_ms=12))
"
uv --project /home/apiad/Workspace/repos/aegis run aegis comms list
```

Expected: one row naming `alice`, `handoff` and `agent:bob`.

- [x] **Step 6: Run the full hermetic suite**

Run: `uv run python -m pytest -q -m "not live"`
Expected: PASS. A red run is a regression to investigate, not noise to
re-roll — the flakes that used to justify re-running were fixed in 0.25.0.

- [x] **Step 7: Commit**

```bash
git add src/aegis/cli_comms.py src/aegis/cli.py tests/test_comms_cli.py
git commit -m "feat(cli): aegis comms reads the inter-agent ledger back"
```

---

### Task 10: Document the layer

**Files:**
- Modify: `repos/aegis/AGENTS.md` (Layout section — add `src/aegis/comms/`)
- Modify: `docs/superpowers/specs/2026-08-11-aegis-comms-format-design.md`
  (status header)
- Modify: `CHANGELOG.md`

**Interfaces:**
- Consumes: the shipped feature.
- Produces: nothing code-facing.

- [x] **Step 1: Add the Layout entry**

In `AGENTS.md`, after the `src/aegis/locks/` bullet, add:

```markdown
- `src/aegis/comms/` - what a call into the aegis MCP surface looks like on
  screen and what it leaves behind on disk. `descriptors.py` (the pure
  registry: verb → glyph + family + one-line description + typed target,
  covering all 72 registered tools; `descriptor_for` normalises both name
  shapes — the renderer sees `mcp__aegis__aegis_handoff`, the middleware sees
  the bare `aegis_handoff`); `models.py` (`Envelope`); `persistence.py`
  (`CommsLedger`, daily JSONL under `.aegis/state/comms/`);
  `middleware.py` (`CommsMiddleware`, a FastMCP `on_call_tool` hook mounted
  in `build_server`). Read back with `aegis comms list`.
  Three rules a contributor will otherwise break, each already paid for:
  **the coverage test in `tests/test_comms_coverage.py` is the design** —
  without it a newly-added tool silently reverts to the generic dot and the
  first-stringy-argument fallback, and nothing else notices; **the glyph is
  resolved server-side and sent on the wire**, because the browser's own
  `KIND_ICON` table had already drifted once and a second parallel table
  would drift again; and **the ledger never fails a call** — the middleware
  logs a write error rather than raising, because observability that can
  break what it observes is a liability. Spec:
  `docs/superpowers/specs/2026-08-11-aegis-comms-format-design.md`.
```

- [x] **Step 2: Flip the spec status header**

Change the spec's first status line to:

```markdown
**Status:** implemented 2026-08-11; plan at
`docs/superpowers/plans/2026-08-11-aegis-comms-format.md`
```

- [x] **Step 3: Add the CHANGELOG entry**

Under the unreleased heading, add:

```markdown
### Features

- Every call into the aegis MCP layer now renders with its own glyph and a
  line that names the counterpart, and leaves an envelope in a daily ledger
  under `.aegis/state/comms/`. Read it back with `aegis comms list`.
```

- [x] **Step 4: Commit**

```bash
git add AGENTS.md CHANGELOG.md \
        docs/superpowers/specs/2026-08-11-aegis-comms-format-design.md
git commit -m "docs(comms): record the layer in AGENTS.md and the changelog"
```

---

## Self-review notes

**Spec coverage.** Line grammar → Tasks 1–3, 5. Three groups / two weights →
Tasks 1–3 (families) + 4–5 (colour). Nineteen-act glyph set → Tasks 1–3.
Width discipline → Task 5's `test_the_glyph_is_always_followed_by_a_space`.
Envelope fields → Task 7. `thread` correlation → Task 8. Best-effort `from` →
Tasks 7 and 8. Ledger + tolerant read → Task 7. `aegis comms` reader →
Task 9. Name normalisation → Task 1. Three sutures → Tasks 5 (two) and 6.
Theme role → Task 4. Coverage gate + mutation → Task 3. Out-of-scope items
stay out.

**Known deviations from the spec, deliberate.**
- `group_rename` / `group_dissolve` / `group_move_member` are `COORDINATION`,
  not `CONVERSATION`. They reshape a group rather than speak to one, and the
  spec's own glyph table already gives them the separate `⌗` act.
- `claim`'s `Target.id` is the first path, with the count in the description.
  A `Target` holds one id, and the claim's own thread comes from `claim_id`.
