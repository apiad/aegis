# Slash Commands 2D — Command palette (drop-up typeahead) — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add an inline drop-up completion panel that offers fuzzy-matched commands, subverbs, and live argument values as the operator types `/`, identically in the TUI and web client.

**Architecture:** A pure `complete(text, bridge) -> Completions` in the harness-agnostic commands core drives everything; it introspects the registry and each command's `ArgSpec`, where a new `Arg.completer` seam enumerates candidate values (static tuple or a `(bridge) -> choices` callable). A small pure `fuzzy` scorer ranks matches. The TUI mounts a `CommandPalette` widget above `GrowingInput` (with a key-interception hook so Up/Down/Tab/Enter/Esc drive the panel while open); the web client renders a drop-up `<div>` fed by a `complete` WS RPC. Both frontends splice the chosen completion back into the input.

**Tech Stack:** Python 3.13+, `dataclasses`, pytest (`-m "not live"`), Textual 8.x (TUI panel only), vanilla JS (web client).

## Global Constraints

- Python **3.13+**.
- Package manager is **`uv`** — `uv run python -m pytest`. Never bare `pip`.
- Test selector is **`-m "not live"`** (marker), never `-k "not live"`.
- TDD: failing test first, minimal implementation, commit per logical unit.
- The commands core (`src/aegis/commands/`) stays **harness-agnostic** — no Textual/web imports. `complete()` is pure; completers receive the bridge as an opaque `object`.
- `complete()` **never raises**: a completer callable that throws is swallowed and contributes no items.
- Ranking is fuzzy-score only (no usage-frequency learning). Builtins rank before other sources on ties.
- Run the gate as its own step; never pipe pytest/ruff through `tail` in an `&&` chain.
- TUI/watchdog tests flake on zion (inotify) — re-run a failing TUI test alone before treating it as real.
- Fast gate during iteration: `uv run python -m pytest tests/test_command_fuzzy.py tests/test_command_complete.py -q`.
- Hold a ws-lock before multi-file writes; `bin/ws-lock gc` at the end.

---

### Task 1: `fuzzy.py` — pure subsequence scorer

**Files:**
- Create: `src/aegis/commands/fuzzy.py`
- Test: `tests/test_command_fuzzy.py`

**Interfaces:**
- Consumes: nothing (stdlib only).
- Produces: `fuzzy_match(query: str, candidate: str) -> tuple[float, tuple[int, ...]] | None` (score + matched indices, or None on no match); `fuzzy_rank(query: str, items: list, key=lambda x: x) -> list` (matching items sorted by score desc, stable on ties).

- [ ] **Step 1: Write the failing test**

```python
# tests/test_command_fuzzy.py
from aegis.commands.fuzzy import fuzzy_match, fuzzy_rank


def test_subsequence_matches():
    assert fuzzy_match("sp", "spawn") is not None
    assert fuzzy_match("swn", "spawn") is not None       # scattered subsequence


def test_non_subsequence_is_none():
    assert fuzzy_match("xyz", "spawn") is None


def test_empty_query_matches_with_zero_score():
    score, positions = fuzzy_match("", "spawn")
    assert positions == ()


def test_case_insensitive():
    assert fuzzy_match("SP", "spawn") is not None


def test_contiguous_outranks_scattered():
    s_contig, _ = fuzzy_match("sp", "spawn")     # "sp" adjacent
    s_scatter, _ = fuzzy_match("sn", "spawn")    # s..n scattered
    assert s_contig > s_scatter


def test_start_of_word_bonus():
    s_start, _ = fuzzy_match("q", "queues")      # at index 0
    s_mid, _ = fuzzy_match("u", "queues")        # not word-start
    assert s_start > s_mid


def test_positions_point_at_matched_chars():
    _, positions = fuzzy_match("pn", "spawn")
    assert positions == (1, 4)


def test_rank_orders_by_score_and_drops_nonmatches():
    ranked = fuzzy_rank("se", ["sessions", "spawn", "schedules"])
    assert ranked[0] == "sessions"               # best subsequence
    assert "spawn" not in ranked                 # no "se" subsequence


def test_rank_with_key():
    items = [{"n": "spawn"}, {"n": "sessions"}]
    ranked = fuzzy_rank("se", items, key=lambda d: d["n"])
    assert ranked == [{"n": "sessions"}]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run python -m pytest tests/test_command_fuzzy.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'aegis.commands.fuzzy'`.

- [ ] **Step 3: Write minimal implementation**

```python
# src/aegis/commands/fuzzy.py
"""Pure fuzzy subsequence matcher for the command palette. Case-insensitive;
scores contiguity and start-of-word matches higher; returns matched positions
so the UI can bold them. No registry, no bridge, no UI."""
from __future__ import annotations

from collections.abc import Callable
from typing import Any


def fuzzy_match(query: str, candidate: str) -> tuple[float, tuple[int, ...]] | None:
    """Return (score, matched_indices) if every char of ``query`` appears in
    ``candidate`` in order (case-insensitive), else None. Empty query matches
    with score 0.0 and no positions."""
    if not query:
        return 0.0, ()
    q = query.lower()
    c = candidate.lower()
    positions: list[int] = []
    score = 0.0
    ci = 0
    prev = -2
    for ch in q:
        idx = c.find(ch, ci)
        if idx == -1:
            return None
        if idx == prev + 1:
            score += 2.0                      # contiguous run
        if idx == 0 or not c[idx - 1].isalnum():
            score += 3.0                      # start-of-word
        positions.append(idx)
        prev = idx
        ci = idx + 1
    score -= len(candidate) * 0.01            # prefer shorter candidates
    return score, tuple(positions)


def fuzzy_rank(query: str, items: list, key: Callable[[Any], str] = lambda x: x) -> list:
    """Keep items whose ``key`` fuzzy-matches ``query``, sorted by score desc.
    Stable: equal scores preserve input order."""
    scored: list[tuple[float, int, Any]] = []
    for i, item in enumerate(items):
        m = fuzzy_match(query, key(item))
        if m is not None:
            scored.append((m[0], i, item))
    scored.sort(key=lambda t: (-t[0], t[1]))
    return [item for _, _, item in scored]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run python -m pytest tests/test_command_fuzzy.py -q`
Expected: PASS (9 tests).

- [ ] **Step 5: Commit**

```bash
git add src/aegis/commands/fuzzy.py tests/test_command_fuzzy.py
git commit -m "feat(commands): pure fuzzy subsequence matcher for the palette"
```

---

### Task 2: `Arg.completer` field + `Completion`/`Completions` + `complete()`

**Files:**
- Modify: `src/aegis/commands/args.py` (add `completer` field to `Arg`)
- Modify: `src/aegis/commands/__init__.py` (`Completion`, `Completions`, `complete()`)
- Test: `tests/test_command_complete.py`

**Interfaces:**
- Consumes: `REGISTRY`, `SlashCommand` (has `.spec`, `.summary`, `.usage`, `.source`), `ArgSpec` (`.positionals`, `.flags`), `Arg` (`.name`, `.required`, `.greedy`, `.completer`), `Flag` (`.name`), `fuzzy_rank` (Task 1).
- Produces: `Arg.completer: Completer | None`; `Completion(insert, label, detail="")`; `Completions(items: tuple[Completion, ...], hint="")`; `complete(text: str, bridge: object) -> Completions`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_command_complete.py
from aegis.commands import complete, Completions
from aegis.commands.args import Arg, ArgSpec
from aegis.commands import SlashCommand, register, REGISTRY, CommandResult


class FakeBridge:
    def list_agents(self):
        return ["default", "opus"]


async def _noop(ctx, args):
    return CommandResult(True, "ok")


def _register_probe():
    # a command with a static-tuple subverb completer + a callable agent completer
    register(SlashCommand(
        "probe2d", "probe", "/probe2d [sub] [agent]", _noop,
        spec=ArgSpec(positionals=(
            Arg("sub", required=False, completer=("alpha", "beta")),
            Arg("agent", required=False,
                completer=lambda b: b.list_agents()))),
        source="builtin"))


def test_verb_in_progress_lists_commands():
    res = complete("/sess", FakeBridge())
    assert any(c.label == "/sessions" for c in res.items)
    assert all(c.insert.endswith(" ") for c in res.items)   # ready for args


def test_bare_slash_lists_all():
    res = complete("/", FakeBridge())
    assert len(res.items) >= 5


def test_not_a_command_is_empty():
    assert complete("hello", FakeBridge()).items == ()


def test_static_tuple_completer():
    _register_probe()
    try:
        res = complete("/probe2d al", FakeBridge())
        assert [c.label for c in res.items] == ["alpha"]
    finally:
        REGISTRY.pop("probe2d", None)


def test_callable_completer_uses_bridge():
    _register_probe()
    try:
        res = complete("/probe2d alpha op", FakeBridge())
        assert [c.label for c in res.items] == ["opus"]
    finally:
        REGISTRY.pop("probe2d", None)


def test_hint_reflects_positionals():
    res = complete("/spawn ", FakeBridge())
    assert "agent" in res.hint


def test_flag_completion():
    _register_probe()
    try:
        # /agents has --effort/--permission flags in 2B; use it
        res = complete("/agents add r claude-code sonnet --eff", FakeBridge())
        assert any(c.label == "--effort" for c in res.items)
    finally:
        REGISTRY.pop("probe2d", None)


def test_greedy_positional_yields_no_items():
    res = complete("/spawn opus write a ", FakeBridge())
    assert res.items == ()


def test_throwing_completer_is_swallowed():
    def _boom(b):
        raise RuntimeError("nope")
    register(SlashCommand(
        "probe2dboom", "x", "/probe2dboom [a]", _noop,
        spec=ArgSpec(positionals=(Arg("a", required=False, completer=_boom),)),
        source="builtin"))
    try:
        res = complete("/probe2dboom x", FakeBridge())
        assert res.items == ()
    finally:
        REGISTRY.pop("probe2dboom", None)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run python -m pytest tests/test_command_complete.py -q`
Expected: FAIL — `ImportError: cannot import name 'complete'`.

- [ ] **Step 3: Write minimal implementation**

In `src/aegis/commands/args.py`, add the `completer` field (parser ignores it):

```python
from collections.abc import Callable

# A completer yields choices for an argument. Each choice is a bare "value" or
# a "(value, detail)" pair; complete() normalises both. Static tuple or a
# callable of the bridge (typed ``object`` to keep this module dependency-free).
Choice = "str | tuple[str, str]"
Completer = "tuple[Choice, ...] | Callable[[object], list]"


@dataclass(frozen=True)
class Arg:
    name: str
    required: bool = True
    greedy: bool = False
    completer: "Completer | None" = None
```

In `src/aegis/commands/__init__.py`, add the types and `complete()` (import `fuzzy_rank` and `shlex`):

```python
import shlex
from aegis.commands.fuzzy import fuzzy_rank


@dataclass(frozen=True)
class Completion:
    insert: str       # text spliced into the input for this choice
    label: str        # matched text shown (e.g. "/spawn" or "opus")
    detail: str = ""  # dim right-column (summary / agent config / "")


@dataclass(frozen=True)
class Completions:
    items: tuple[Completion, ...] = ()
    hint: str = ""


def _usage_hint(spec, bound: int) -> str:
    parts = []
    for i, p in enumerate(spec.positionals):
        token = f"<{p.name}>" if p.required else f"[{p.name}]"
        parts.append(token if i >= bound else f"·{p.name}")
    return " ▸ ".join(parts)


def _norm_choice(ch) -> tuple[str, str]:
    if isinstance(ch, tuple):
        return ch[0], (ch[1] if len(ch) > 1 else "")
    return ch, ""


def complete(text: str, bridge: object) -> Completions:
    """Return completion candidates for the current input. Pure; never raises.
    Empty items when ``text`` is not a slash command."""
    if not text.startswith("/"):
        return Completions()
    body = text[1:]
    # Split into already-typed tokens + the current partial (text after the
    # last space; empty when the input ends with a space).
    if " " not in body:
        # still typing the verb
        cmds = [c for c in REGISTRY.values()]
        ranked = fuzzy_rank(body, cmds, key=lambda c: c.name)
        ranked.sort(key=lambda c: 0 if c.source == "builtin" else 1)  # stable 2nd key
        items = tuple(
            Completion(insert=f"/{c.name} ", label=f"/{c.name}", detail=c.summary)
            for c in ranked)
        return Completions(items=items)

    parts = body.split(None, 1)
    verb = parts[0]
    rest = parts[1] if len(parts) > 1 else ""
    cmd = REGISTRY.get(verb.lower())
    if cmd is None:
        return Completions()
    # tokens already bound (non-flag) + current partial
    trailing_space = text.endswith(" ")
    toks = rest.split()
    partial = "" if trailing_space or not toks else toks[-1]
    bound_toks = toks if trailing_space else toks[:-1]
    positional_bound = sum(1 for t in bound_toks if not t.startswith("--"))
    spec = cmd.spec
    hint = _usage_hint(spec, positional_bound)

    # flag completion
    if partial.startswith("--"):
        names = [f"--{f.name}" for f in spec.flags]
        ranked = fuzzy_rank(partial[2:], names, key=lambda n: n[2:])
        return Completions(
            items=tuple(Completion(insert=n + " ", label=n) for n in ranked),
            hint=hint)

    # positional value completion
    if positional_bound >= len(spec.positionals):
        return Completions(hint=hint)
    arg = spec.positionals[positional_bound]
    if arg.greedy or arg.completer is None:
        return Completions(hint=hint)
    try:
        raw = (arg.completer if isinstance(arg.completer, tuple)
               else arg.completer(bridge))
        choices = [_norm_choice(ch) for ch in raw]
    except Exception:                       # noqa: BLE001 — bad completer must not break typing
        return Completions(hint=hint)
    ranked = fuzzy_rank(partial, choices, key=lambda vd: vd[0])
    items = tuple(Completion(insert=f"{v} ", label=v, detail=d) for v, d in ranked)
    return Completions(items=items, hint=hint)
```

(Place the `complete()` definition after `dispatch`/`classify_input`, and re-export `Completion`, `Completions`, `complete` implicitly via the module namespace — they are module-level, so `from aegis.commands import complete, Completions` works.)

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run python -m pytest tests/test_command_complete.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/aegis/commands/args.py src/aegis/commands/__init__.py tests/test_command_complete.py
git commit -m "feat(commands): Arg.completer seam + complete() palette engine"
```

---

### Task 3: Wire completers onto the builtins

**Files:**
- Modify: `src/aegis/commands/builtins/core.py`, `builtins/coordination.py`, `builtins/terminals.py`, `builtins/session_ctl.py`
- Test: `tests/test_command_complete.py` (per-command wiring)

**Interfaces:**
- Consumes: bridge reads that already exist — `list_agents()`, `list_sessions() -> [SessionInfo(handle, agent_slug, state)]`, `groups.list_groups() -> [{name}]`, `queue_manager.list_queues() -> [str]`, `terminal_manager.list() -> [TerminalInfo(name)]`, `scheduler`/`state_root`/`inline_schedule_names()` via `aegis.scheduler.push.list_payload`; `aegis.theme_names.THEME_NAMES`; `aegis.config.edit._VALID_PROVIDERS`.
- Produces: completers on the relevant Args so `complete()` returns live values.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_command_complete.py` (extend `FakeBridge` with the reads the wired completers use):

```python
class RichBridge:
    def list_agents(self):
        return ["default", "opus"]
    def list_sessions(self):
        from types import SimpleNamespace
        return [SimpleNamespace(handle="alpha", agent_slug="opus", state="ready")]
    class _G:
        def list_groups(self):
            return [{"name": "g1", "members": 1}]
    groups = _G()
    class _Q:
        def list_queues(self):
            return ["build"]
    queue_manager = _Q()
    class _T:
        def list(self):
            from types import SimpleNamespace
            return [SimpleNamespace(name="t1")]
    terminal_manager = _T()
    _agents = {}


def test_spawn_completes_agents():
    res = complete("/spawn op", RichBridge())
    assert [c.label for c in res.items] == ["opus"]


def test_close_completes_sessions():
    res = complete("/close al", RichBridge())
    assert [c.label for c in res.items] == ["alpha"]
    assert "opus" in res.items[0].detail          # agent_slug · state


def test_themes_completes_theme_names():
    from aegis.theme_names import THEME_NAMES
    res = complete("/themes ", RichBridge())
    assert [c.label for c in res.items] == list(THEME_NAMES)


def test_groups_subverb_then_name():
    sub = complete("/groups ", RichBridge())
    assert {"list", "status", "dissolve"} <= {c.label for c in sub.items}
    name = complete("/groups status g", RichBridge())
    assert [c.label for c in name.items] == ["g1"]


def test_agents_add_harness_completes_providers():
    res = complete("/agents add slug cla", RichBridge())
    assert any("claude-code" == c.label for c in res.items)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run python -m pytest tests/test_command_complete.py -q -k "spawn_completes or close_completes or themes_completes or groups_subverb or agents_add_harness"`
Expected: FAIL — Args have no completers yet.

- [ ] **Step 3: Write minimal implementation**

In `builtins/core.py`, add completers to the relevant Args. `/spawn` agent + `/queues` subverb & agent + `/agents` subverb, harness, model:

```python
from aegis.config.edit import _VALID_PROVIDERS

# /spawn
Arg("agent", completer=lambda b: b.list_agents()),
# ...
# /queues
Arg("subverb", required=False, completer=("list", "new")),
Arg("name", required=False),
Arg("agent", required=False, completer=lambda b: b.list_agents()),
# /agents
Arg("subverb", required=False, completer=("list", "add", "remove")),
Arg("slug", required=False),
Arg("harness", required=False, completer=tuple(sorted(_VALID_PROVIDERS))),
Arg("model", required=False),
```

For `/agents remove <slug>` the slug should complete existing agents; since the
`slug` positional is shared between add (freeform) and remove (existing), leave
it uncompleted in v1 (subverb-dependent completion is a documented non-goal).

In `builtins/coordination.py`:

```python
# /groups
Arg("subverb", required=False, completer=("list", "status", "dissolve")),
Arg("name", required=False, completer=lambda b: [g["name"] for g in b.groups.list_groups()]),
# /schedules
Arg("subverb", required=False,
    completer=("list", "show", "enable", "disable", "remove", "logs")),
Arg("name", required=False, completer=_schedule_names),
```

Add a module-level helper in `coordination.py`:

```python
def _schedule_names(b):
    from aegis.scheduler.push import list_payload
    rows = list_payload(getattr(b, "scheduler", None), b.state_root,
                        b.inline_schedule_names()).get("schedules", [])
    return [r["name"] for r in rows]
```

In `builtins/terminals.py`:

```python
Arg("subverb", required=False, completer=("list", "new", "run", "close")),
Arg("name", required=False, completer=lambda b: [i.name for i in b.terminal_manager.list()]),
```

In `builtins/session_ctl.py`:

```python
# /close
Arg("handle", required=False,
    completer=lambda b: [(s.handle, f"{s.agent_slug} · {s.state}")
                         for s in b.list_sessions()]),
# /themes
Arg("name", required=False, completer=tuple(__import__("aegis.theme_names",
        fromlist=["THEME_NAMES"]).THEME_NAMES)),
```

(Prefer a top-of-file `from aegis.theme_names import THEME_NAMES` and
`completer=THEME_NAMES` over the inline import above — the import shim is shown
only to make the value explicit.)

Enrich `/spawn` and `/queues`/`/agents` agent completers with config detail when
the bridge exposes the agent map, matching the `/agents` list format:

```python
def _agent_choices(b):
    cfgs = getattr(b, "_agents", {}) or {}
    out = []
    for name in b.list_agents():
        a = cfgs.get(name)
        if a is None:
            out.append(name)
        else:
            perm = getattr(getattr(a, "permission", ""), "value",
                           getattr(a, "permission", "")) or "?"
            out.append((name, f"{getattr(a, 'harness', '?')} · "
                              f"{getattr(a, 'model', '?')} · {perm}"))
    return out
```

Use `completer=_agent_choices` for the agent Args (define `_agent_choices` in
`core.py` and import it where needed, or duplicate the tiny helper per module —
your call; keep it DRY by putting it in `core.py` and importing).

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run python -m pytest tests/test_command_complete.py -q`
Expected: PASS. Then run the full command suite to confirm the added `completer` kwargs did not disturb parsing: `uv run python -m pytest tests/test_slash_commands.py -q`.

- [ ] **Step 5: Commit**

```bash
git add src/aegis/commands/builtins/ tests/test_command_complete.py
git commit -m "feat(commands): wire palette completers onto builtins (subverbs + live values)"
```

---

### Task 4: TUI drop-up `CommandPalette` panel

**Files:**
- Create: `src/aegis/tui/palette.py` (`CommandPalette` widget)
- Modify: `src/aegis/tui/widgets.py` (`GrowingInput` gains a `key_interceptor` hook)
- Modify: `src/aegis/tui/pane.py` (mount panel above input; drive it from input changes + key interception; splice on accept)
- Test: `tests/test_pane_palette.py`

**Interfaces:**
- Consumes: `complete(text, self.app) -> Completions`; `GrowingInput` (`.value`, `.text`); `Completions.items[i]` (`insert`, `label`, `detail`), `.hint`.
- Produces: a `CommandPalette` widget with `update(completions)`, `move(delta)`, `current() -> Completion | None`, `visible` state; `GrowingInput.key_interceptor: Callable[[events.Key], bool] | None`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_pane_palette.py
from __future__ import annotations

import pytest

from aegis.config import Agent
from aegis.events import Result
from aegis.tui.app import AegisApp
from aegis.tui.widgets import GrowingInput
# reuse the harness shape from tests/test_pane_slash_command.py
from tests.test_pane_slash_command import GatedSession, FakeMCP, _agent, _app


async def _type(pane, text):
    inp = pane.query_one(GrowingInput)
    inp.text = text
    # trigger the change handler the pane subscribes to
    pane.on_text_area_changed(None)


@pytest.mark.asyncio
async def test_palette_shows_commands_on_slash():
    app = _app(GatedSession())
    async with app.run_test() as pilot:
        pane = app._panes[0]
        await _type(pane, "/sp")
        await pilot.pause()
        from aegis.tui.palette import CommandPalette
        pal = pane.query_one(CommandPalette)
        assert pal.display is True
        assert any(c.label == "/spawn" for c in pal._items)


@pytest.mark.asyncio
async def test_palette_hidden_for_plain_text():
    app = _app(GatedSession())
    async with app.run_test() as pilot:
        pane = app._panes[0]
        await _type(pane, "hello")
        await pilot.pause()
        from aegis.tui.palette import CommandPalette
        assert pane.query_one(CommandPalette).display is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run python -m pytest tests/test_pane_palette.py -q`
Expected: FAIL — no `CommandPalette` widget / not mounted.

- [ ] **Step 3: Write minimal implementation**

Create `src/aegis/tui/palette.py`:

```python
"""Drop-up command palette panel: an OptionList-style list of completions that
grows upward above the input. Pure view over a Completions; the pane owns the
data flow and key routing."""
from __future__ import annotations

from rich.text import Text
from textual.widgets import OptionList
from textual.widgets.option_list import Option

from aegis.commands import Completion, Completions


class CommandPalette(OptionList):
    DEFAULT_CSS = """
    CommandPalette {
        display: none; height: auto; max-height: 8; border: round $accent;
        background: $surface; margin: 0 0 0 0;
    }
    """

    def __init__(self, palette) -> None:
        super().__init__()
        self._palette = palette
        self._items: list[Completion] = []
        self._hint: str = ""

    def update(self, completions: Completions) -> None:
        self._items = list(completions.items)
        self._hint = completions.hint
        self.clear_options()
        if not self._items:
            self.display = False
            return
        rows = []
        for c in self._items:
            t = Text(c.label, style=self._palette.accent)
            if c.detail:
                t.append(f"   {c.detail}", style=self._palette.muted)
            rows.append(Option(t))
        self.add_options(rows)
        self.display = True
        self.highlighted = 0

    def move(self, delta: int) -> None:
        if not self._items:
            return
        n = len(self._items)
        cur = self.highlighted if self.highlighted is not None else 0
        self.highlighted = (cur + delta) % n

    def current(self) -> Completion | None:
        if not self._items or self.highlighted is None:
            return None
        return self._items[self.highlighted]

    def hide(self) -> None:
        self.display = False
        self._items = []
```

In `src/aegis/tui/widgets.py`, add the key-interception hook to `GrowingInput`.
In `__init__` set `self.key_interceptor = None`; at the very top of `_on_key`:

```python
    async def _on_key(self, event: events.Key) -> None:
        if self.key_interceptor is not None and self.key_interceptor(event):
            event.prevent_default()
            event.stop()
            return
        # ... existing body unchanged ...
```

In `src/aegis/tui/pane.py`:

1. In `compose()` (before `yield PendingStrip(...)`), yield the panel so it sits above the input:

```python
            yield CommandPalette(self._palette)
            yield PendingStrip(self._palette)
            yield GrowingInput(placeholder="type a message…")
```

Import at the top: `from aegis.tui.palette import CommandPalette`.

2. On mount / after compose, register the interceptor on the input:

```python
        self.query_one(GrowingInput).key_interceptor = self._palette_key
```

(Set this in the pane's existing mount path — where the input is first queried.)

3. Extend `on_text_area_changed` to drive the palette:

```python
    def on_text_area_changed(self, _event) -> None:
        value = self.query_one(GrowingInput).value
        # ...existing slash-command outline class toggling...
        pal = self.query_one(CommandPalette)
        if value.startswith("/"):
            from aegis.commands import complete
            pal.update(complete(value, self.app))
        else:
            pal.hide()
```

4. Add the key handler + splice:

```python
    def _palette_key(self, event) -> bool:
        pal = self.query_one(CommandPalette)
        if not pal.display:
            return False
        if event.key in ("up", "down"):
            pal.move(-1 if event.key == "up" else 1)
            return True
        if event.key in ("tab", "enter"):
            choice = pal.current()
            if choice is None:
                return False
            self._accept_completion(choice)
            return True
        if event.key == "escape":
            pal.hide()
            return True
        return False

    def _accept_completion(self, choice) -> None:
        inp = self.query_one(GrowingInput)
        value = inp.value
        # splice: replace the current partial token (after the last space) with
        # the completion's insert text.
        head, _, _ = value.rpartition(" ") if " " in value else ("", "", value)
        if value.startswith("/") and " " not in value:
            new = choice.insert                      # completing the verb
        else:
            new = (head + " " if head else "") + choice.insert
        inp.text = new
        inp.move_cursor(inp.document.end)
        from aegis.commands import complete
        self.query_one(CommandPalette).update(complete(new, self.app))
```

(The `head` splice for the verb case: when completing `/sp`, `value` has no
space, so `insert="/spawn "` replaces the whole thing. For an arg, `head` is the
text up to and including the prior tokens and `insert` is the chosen value.)

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run python -m pytest tests/test_pane_palette.py tests/test_pane_slash_command.py -q`
Expected: PASS. Re-run a flaky TUI test alone (inotify) before believing a failure.

- [ ] **Step 5: Commit**

```bash
git add src/aegis/tui/palette.py src/aegis/tui/widgets.py src/aegis/tui/pane.py tests/test_pane_palette.py
git commit -m "feat(tui): drop-up command palette panel with key routing + splice"
```

---

### Task 5: Web `complete` RPC + drop-up panel

**Files:**
- Modify: `src/aegis/web/wssession.py` (`complete` RPC in `_dispatch`)
- Modify: `src/aegis/web/static/js/app.js` (drop-up panel + input/keydown handling)
- Modify: `src/aegis/web/static/css/base.css` (panel styling)
- Test: `tests/test_web_complete.py`

**Interfaces:**
- Consumes: `complete(text, self._m) -> Completions`; the web `_dispatch` chain; the existing `input` element + deliver keydown handler.
- Produces: a `complete` RPC returning `{"items": [{"insert","label","detail"}], "hint": str}`; a drop-up `<div id="palette">` above the input driven by input/keydown.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_web_complete.py
from __future__ import annotations

import pytest

from aegis.web.wssession import WSSession
from tests.test_web_slash import FakeCore, FakeManager, _session


@pytest.mark.asyncio
async def test_complete_rpc_returns_items_for_slash():
    session = _session(FakeCore())
    res = await session._complete("/sess")
    assert any(it["label"] == "/sessions" for it in res["items"])


@pytest.mark.asyncio
async def test_complete_rpc_empty_for_plain():
    session = _session(FakeCore())
    res = await session._complete("hello")
    assert res["items"] == []
```

(`FakeManager` in `tests/test_web_slash.py` already implements `list_agents`/
`list_sessions`; `complete("/sess", …)` only needs the registry for the verb
stage, so the fake suffices.)

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run python -m pytest tests/test_web_complete.py -q`
Expected: FAIL — `WSSession` has no `_complete`.

- [ ] **Step 3: Write minimal implementation**

In `src/aegis/web/wssession.py`, add the method and route it in `_dispatch`:

```python
    async def _complete(self, message: str) -> dict:
        from aegis.commands import complete
        c = complete(message, self._m)
        return {"items": [{"insert": it.insert, "label": it.label,
                           "detail": it.detail} for it in c.items],
                "hint": c.hint}
```

In the `_dispatch` method chain (beside the other `if method == …` lines):

```python
        if method == "complete":
            return await self._complete(params["message"])
```

In `src/aegis/web/static/js/app.js`, add a drop-up panel above the input. Near
the input wiring, create the element and a render/apply pair:

```javascript
const paletteEl = document.createElement("div");
paletteEl.id = "palette";
paletteEl.style.display = "none";
input.parentElement.insertBefore(paletteEl, input);   // above the input
let palItems = [];
let palIdx = 0;

function renderPalette(items) {
  palItems = items || [];
  palIdx = 0;
  paletteEl.innerHTML = "";
  if (!palItems.length) { paletteEl.style.display = "none"; return; }
  palItems.forEach((it, i) => {
    const row = document.createElement("div");
    row.className = "palette-row" + (i === 0 ? " current" : "");
    row.innerHTML = `<span class="pl-label"></span><span class="pl-detail"></span>`;
    row.querySelector(".pl-label").textContent = it.label;
    row.querySelector(".pl-detail").textContent = it.detail || "";
    row.addEventListener("mousedown", (e) => { e.preventDefault(); acceptPalette(i); });
    paletteEl.appendChild(row);
  });
  paletteEl.style.display = "block";
}

function movePalette(delta) {
  if (!palItems.length) return;
  paletteEl.children[palIdx].classList.remove("current");
  palIdx = (palIdx + delta + palItems.length) % palItems.length;
  paletteEl.children[palIdx].classList.add("current");
}

function acceptPalette(i) {
  const it = palItems[i];
  const v = input.value;
  let head = "";
  if (v.startsWith("/") && !v.includes(" ")) {
    input.value = it.insert;
  } else {
    head = v.includes(" ") ? v.slice(0, v.lastIndexOf(" ")) : "";
    input.value = (head ? head + " " : "") + it.insert;
  }
  refreshPalette();
  input.focus();
}

function refreshPalette() {
  const v = input.value;
  if (!v.startsWith("/") || !activeHandle) { renderPalette([]); return; }
  client.rpc("complete", { message: v })
    .then((res) => renderPalette(res.items))
    .catch(() => renderPalette([]));
}
```

Drive it from the existing input listeners: in the `input` listener call
`refreshPalette()`; in the `keydown` listener, before the Enter-submits branch:

```javascript
  input.addEventListener("input", () => { autogrow(); refreshPalette(); });
  input.addEventListener("keydown", (e) => {
    if (paletteEl.style.display === "block") {
      if (e.key === "ArrowUp") { e.preventDefault(); movePalette(-1); return; }
      if (e.key === "ArrowDown") { e.preventDefault(); movePalette(1); return; }
      if (e.key === "Tab") { e.preventDefault(); acceptPalette(palIdx); return; }
      if (e.key === "Enter") { e.preventDefault(); acceptPalette(palIdx); return; }
      if (e.key === "Escape") { e.preventDefault(); renderPalette([]); return; }
    }
    if (e.key === "Enter" && !e.shiftKey) {
      // ...existing deliver branch unchanged...
    }
  });
```

(There are two input/keydown listeners today — the autogrow one (~line 892) and
the deliver one (~line 893). Fold the palette handling into the same keydown
handler that owns Enter-submit so the palette intercepts first; wire
`refreshPalette()` into the autogrow `input` listener.)

Add styling to `src/aegis/web/static/css/base.css`:

```css
#palette { border: 1px solid var(--accent); border-radius: 6px;
  background: var(--surface); max-height: 12rem; overflow-y: auto;
  margin-bottom: 4px; }
.palette-row { display: flex; justify-content: space-between; padding: 2px 8px;
  cursor: pointer; }
.palette-row.current { background: var(--accent); color: var(--surface); }
.pl-detail { opacity: 0.6; margin-left: 1rem; }
```

(If the CSS variable names differ, use the file's existing accent/surface vars.)

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run python -m pytest tests/test_web_complete.py -q`
Expected: PASS (the RPC layer). The JS panel is browser-smoked in Task 6.

- [ ] **Step 5: Commit**

```bash
git add src/aegis/web/wssession.py src/aegis/web/static/js/app.js src/aegis/web/static/css/base.css tests/test_web_complete.py
git commit -m "feat(web): complete RPC + drop-up command palette panel"
```

---

### Task 6: Full-slice verification + docs

**Files:**
- Modify: `TASKS.md` (mark 2D done), `CHANGELOG.md` (2D entry), `AGENTS.md` (commands entry note)

- [ ] **Step 1: Run the hermetic suite**

Run: `uv run python -m pytest -q -m "not live"`
Expected: PASS. Re-run any flaky TUI/watchdog test alone (inotify) before treating it as real.

- [ ] **Step 2: Manual smoke (TUI)**

Run `aegis` in a project with `.aegis.yaml`. Type `/` (panel drops up with all commands), `/sp` (filters to `/spawn`), Down/Tab to accept → `/spawn `, then it lists agents; pick one; type `/groups ` (subverbs), `/themes ` (theme list), `/close ` (session handles with `agent · state`). Esc dismisses; a plain message shows no panel. Note any surprise; fix before proceeding.

- [ ] **Step 3: Manual smoke (web)**

Run `aegis serve`, open the web client, confirm the drop-up appears above the textarea for `/`, filters live, Up/Down/Tab/Enter/Esc behave, and dynamic values (agents, sessions, themes) match the TUI.

- [ ] **Step 4: Update docs**

Mark the 2D bullet `[x]` in `TASKS.md` (command palette shipped — drop-up typeahead over commands/subverbs/live arg values, `Arg.completer` seam + pure `complete()`, TUI + web). Add a `CHANGELOG.md` entry under Unreleased. Extend the `src/aegis/commands/` AGENTS.md layout entry to mention `fuzzy.py` + `complete()` + the palette.

- [ ] **Step 5: Commit**

```bash
git add TASKS.md CHANGELOG.md AGENTS.md
git commit -m "docs: slash commands 2D command palette shipped — update TASKS/CHANGELOG/AGENTS"
```

---

## Self-Review

**Spec coverage** — every 2D spec section maps to a task:
- Completer seam §1 → Task 2 (`Arg.completer`) + Task 3 (wiring).
- `complete()` §2 → Task 2.
- Fuzzy §3 → Task 1.
- Interaction/keys §4 → Task 4 (TUI) + Task 5 (web).
- Value enrichment §5 → Task 3 (`_agent_choices`, `/close` detail).
- Frontends §6 → Task 4 (TUI panel) + Task 5 (web panel).
- Testing §Testing → unit tests per task; Task 6 runs the full gate + smokes.
- Slices → Tasks 1–2 (slice 1), 3 (slice 2), 4 (slice 3), 5 (slice 4).

**Placeholder scan** — the TUI test reuses the concrete `test_pane_slash_command.py` harness (imported by name); the two "if CSS var names differ" / "existing mount path" notes point at concrete in-file references the implementer reads, not unspecified logic. No "TBD".

**Type consistency** — `Completion(insert, label, detail)` / `Completions(items, hint)` defined in Task 2, consumed identically in Tasks 4–5; `complete(text, bridge)` signature stable across Tasks 2–5; `Arg.completer` (tuple or callable, may yield `str` or `(str, str)`) defined in Task 2, `_norm_choice` normalises in Task 2, produced by the wiring in Task 3; `fuzzy_rank(query, items, key)` defined Task 1, used in Task 2; `GrowingInput.key_interceptor` defined Task 4 and set by the pane in the same task.
