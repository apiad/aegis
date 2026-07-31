# `/btw` Deferred Rendering Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [x]`) syntax for tracking.

**Goal:** Take `/btw` off the Textual input handler so it no longer freezes the pane for 12-17s, give it a live spinner block, render its answer as Markdown, allow one at a time, and let ESC cancel it.

**Architecture:** `SlashCommand` gains a declared `deferred` property. A deferred command is not awaited in `on_growing_input_submitted`; the pane mounts a placeholder block, runs `dispatch()` in a Textual worker, and rewrites that block in place when the result lands. The existing per-tool 10 Hz ticker and `_TOOL_SPINNER` drive the placeholder. Effect application moves out of the input handler into one method used by both the inline and deferred paths.

**Tech Stack:** Python 3.12, Textual, Rich, pytest (`uv run pytest`).

## Global Constraints

- Spec: `docs/superpowers/specs/2026-07-31-aegis-btw-deferred-render-design.md` (commit `1ce07ef`).
- Package management is `uv`, never pip. Run tests as `uv run python -m pytest`.
- **TUI only.** Do not touch `src/aegis/web/` or `src/aegis/web/static/js/app.js`.
- **Do not touch `/peer`'s `SlashCommand` registration** in `commands/builtins/core.py`. `aegis-at-mentions` owns the `@peer` flag flip. Only `/btw`'s entry (line 380) is in scope.
- **Do not modify the `AppBridge` Protocol.** Nothing in this plan changes it.
- Commit per task, conventional commits, `git commit -- <explicit paths>` only. **Never `git add -A`** — this is a shared checkout with another agent working in `src/aegis/peer/`, `core/`, `mcp/`, `queue/schema.py`, `commands/builtins/core.py`.
- The full suite flakes 1-2 inotify/watchdog TUI tests on zion. Gate on the named subsets in each task; run the full suite as a check, read the flakes, don't treat them as red.

---

## File Structure

| File | Responsibility | Change |
|---|---|---|
| `src/aegis/commands/__init__.py` | `SlashCommand` declares `deferred` + `cancel_note`; `resolve_deferred()` lets a frontend decide *how* to run a command before running it | Modify |
| `src/aegis/themes/__init__.py` | `AegisColors` gains `panel` + `rule` — the aside surface | Modify |
| `src/aegis/render.py` | `render_side_note` / `render_peer_answer` return an `_aside` Panel with `Markdown` on the ok path; new `render_deferred` for the placeholder + tombstone | Modify |
| `src/aegis/tui/pane.py` | `_DeferredTrack`, `_apply_command_result`, `_put_block`, deferred dispatch, spinner ticking, cancel | Modify |
| `src/aegis/tui/app.py` | one rung in `action_interrupt` | Modify (~6 lines) |
| `src/aegis/commands/builtins/core.py` | `/btw` registration gains `deferred=True` | Modify (1 line) |
| `tests/test_btw_command.py` | 4 `.plain` conversions + new deferred tests | Modify |
| `tests/test_peer_command.py` | 1 `.plain` conversion | Modify |
| `tests/test_deferred_commands.py` | the `deferred` primitive + pane behaviour | Create |

### Deliberate deviation from the spec

The spec calls the track `_BtwTrack`. **Implement it as `_DeferredTrack`, with one slot per pane** (`self._deferred: _DeferredTrack | None`), not a dict keyed by command.

Reason: the primitive is general — `@peer` adopts it next — and a per-pane single slot makes the ESC rung unambiguous. Two spinners racing in one pane would leave ESC with no defensible answer about which it cancels, which is exactly the ambiguity the one-at-a-time rule exists to prevent. Update the spec's wording as part of Task 6.

---

### Task 1: `deferred` + `cancel_note` on `SlashCommand` — DONE `811e8b7`

Lands the primitive alone so `aegis-at-mentions` can build `@peer` VS2 on it without waiting for the rest.

**Files:**
- Modify: `src/aegis/commands/__init__.py:43-50` (the dataclass), and add `resolve_deferred` next to `dispatch` (~line 102)
- Test: `tests/test_deferred_commands.py` (create)

**Interfaces:**
- Consumes: nothing.
- Produces:
  - `SlashCommand.deferred: bool = False`
  - `SlashCommand.cancel_note: str = "cancelled"`
  - `resolve_deferred(text: str) -> tuple[SlashCommand, dict] | None`

- [x] **Step 1: Write the failing test**

Create `tests/test_deferred_commands.py`:

```python
"""The `deferred` primitive: a command that must not be awaited in a
frontend's input handler."""
import pytest

from aegis.commands import ArgSpec, Arg, SlashCommand, register, REGISTRY


@pytest.fixture
def slow_command():
    """A registered deferred command, removed again afterwards."""
    async def _run(ctx, args):
        raise AssertionError("resolve_deferred must not run the handler")

    cmd = SlashCommand(
        "slowly", "a slow command", "/slowly <handle> <question>", _run,
        spec=ArgSpec(positionals=(Arg("handle", required=False),
                                  Arg("question", required=False,
                                      greedy=True))),
        deferred=True,
        cancel_note="stopped waiting — {handle}'s turn is still running",
    )
    register(cmd)
    yield cmd
    REGISTRY.pop("slowly", None)


def test_commands_are_not_deferred_by_default():
    """The flag is opt-in: every command that existed before this change
    keeps being awaited inline."""
    assert REGISTRY["help"].deferred is False
    assert REGISTRY["help"].cancel_note == "cancelled"


def test_resolve_deferred_returns_the_command_and_its_parsed_args(
        slow_command):
    resolved = resolve = __import__(
        "aegis.commands", fromlist=["resolve_deferred"]).resolve_deferred(
        "/slowly beta is the build green?")
    assert resolved is not None
    cmd, args = resolved
    assert cmd is slow_command
    assert args["handle"] == "beta"
    assert args["question"] == "is the build green?"


def test_resolve_deferred_is_none_for_an_ordinary_command():
    from aegis.commands import resolve_deferred
    assert resolve_deferred("/help") is None


def test_resolve_deferred_is_none_for_an_unknown_verb():
    from aegis.commands import resolve_deferred
    assert resolve_deferred("/nosuchverb x") is None


def test_bad_args_fall_through_to_the_inline_path(slow_command):
    """A typo should produce dispatch()'s usage error immediately, not a
    spinner. Returning None sends it down the normal await path."""
    from aegis.commands import resolve_deferred
    assert resolve_deferred("/slowly --nosuchflag") is None


def test_cancel_note_resolves_against_the_parsed_args(slow_command):
    from aegis.commands import resolve_deferred
    cmd, args = resolve_deferred("/slowly beta is the build green?")
    assert cmd.cancel_note.format(**args) == (
        "stopped waiting — beta's turn is still running")
```

Fix the awkward import in `test_resolve_deferred_returns_the_command_and_its_parsed_args` to a plain `from aegis.commands import resolve_deferred` once the symbol exists; it is written defensively only so the file imports before the function is added.

- [x] **Step 2: Run test to verify it fails**

Run: `uv run python -m pytest tests/test_deferred_commands.py -v`
Expected: FAIL — `TypeError: SlashCommand.__init__() got an unexpected keyword argument 'deferred'`

- [x] **Step 3: Add the fields**

In `src/aegis/commands/__init__.py`, the dataclass at line 43:

```python
@dataclass(frozen=True)
class SlashCommand:
    name: str
    summary: str          # one line, shown by /help
    usage: str            # e.g. "/spawn <agent> [prompt]"
    run: Handler
    source: str = "builtin"          # builtin | user | plugin
    spec: ArgSpec = field(default_factory=ArgSpec)
    # A deferred command must not be awaited in a frontend's input
    # handler. /btw takes 12-17s and @peer up to PEER_ASK_TIMEOUT_S=300s;
    # awaiting either inside a Textual message handler holds that pane's
    # message pump for the duration, freezing spinners and input. A
    # frontend that understands the flag mounts a placeholder, dispatches
    # off the handler, and rewrites the block when the result lands. One
    # that does not (today: the web client) ignores it and keeps the old
    # synchronous behaviour.
    deferred: bool = False
    # What the frontend says when the operator cancels a running deferred
    # command. A template, resolved against the parsed args. It is
    # per-command because the truth is per-command: cancelling /btw is
    # clean (it never touched a harness session), while cancelling @peer
    # is not — the peer already took the turn and will finish into its own
    # transcript whether or not anyone is listening.
    cancel_note: str = "cancelled"
```

- [x] **Step 4: Add `resolve_deferred`**

In the same file, immediately after `dispatch` (which ends at line 101):

```python
def resolve_deferred(text: str) -> "tuple[SlashCommand, dict] | None":
    """``(command, parsed args)`` when ``text`` names a deferred command.

    Pure lookup so a frontend can decide *how* to run a command before
    running it. Verb parsing mirrors ``dispatch`` exactly — keep the two in
    step.

    Bad args return None rather than a deferred command: a typo should get
    ``dispatch``'s usage error inline and immediately, not a spinner.
    """
    body = text[1:] if text.startswith("/") else text
    parts = body.split(None, 1)
    verb = parts[0].lower() if parts and parts[0] else "help"
    cmd = REGISTRY.get(verb)
    if cmd is None or not cmd.deferred:
        return None
    try:
        args = parse(cmd.spec, parts[1] if len(parts) > 1 else "")
    except ArgError:
        return None
    return cmd, args
```

- [x] **Step 5: Run tests to verify they pass**

Run: `uv run python -m pytest tests/test_deferred_commands.py -v`
Expected: PASS (6 tests)

- [x] **Step 6: Verify nothing else regressed**

Run: `uv run python -m pytest tests/test_commands.py tests/test_btw_command.py tests/test_peer_command.py -q`
Expected: PASS — the fields are additive with defaults.

- [x] **Step 7: Commit**

```bash
git add tests/test_deferred_commands.py src/aegis/commands/__init__.py
git commit -m "feat(commands): declare deferred + cancel_note on SlashCommand

A command that takes 12-17s (/btw) or up to 300s (@peer) must not be
awaited in a frontend's input handler — in Textual that holds the pane's
message pump, freezing every spinner and the input itself.

The property is declared on the command rather than checked by verb
because two commands landed hours apart hit the same defect for the same
reason. cancel_note is per-command because 'cancelled' is a lie for
@peer: the peer already took the turn and finishes into its own
transcript whether or not anyone is still listening.

resolve_deferred() lets a frontend decide how to run a command before
running it. Bad args return None so a typo gets dispatch()'s usage error
inline rather than a spinner.

No behaviour change: both fields default, no command sets them yet." \
  -- tests/test_deferred_commands.py src/aegis/commands/__init__.py
```

- [x] **Step 8: Tell the peer it's landed**

`aegis-at-mentions` is waiting on this to build `@peer` VS2. Send the commit SHA via `aegis_handoff`, with the `cancel_note` template it supplied:
`cancel_note="stopped waiting — {handle}'s turn is still running, so go read its tab"`

---

### Task 2: Markdown in both renderers, ok-path only — DONE `3ce8786`

Independent of everything else — pure `render.py`. Delivers half of what was asked.

**Files:**
- Modify: `src/aegis/render.py:247-291` (`render_side_note`, `render_peer_answer`)
- Test: `tests/test_btw_command.py:128-153`, `tests/test_peer_command.py:95-101`

**Interfaces:**
- Consumes: nothing.
- Produces: `render_side_note(note, colors) -> Group`, `render_peer_answer(answer, colors) -> Group`. Both always return a `Group`; `group.renderables[0]` is the header `Text`.

- [x] **Step 1: Write the failing tests**

Add this helper to `tests/test_btw_command.py`, directly under `palette()` at line 126:

```python
def rendered(renderable) -> str:
    """Plain text as it reaches the terminal.

    A Group has no `.plain`, and rendering through a real Console asserts
    on what the user sees rather than on a string the renderer happened to
    assemble internally.
    """
    from rich.console import Console
    console = Console(width=100, no_color=True)
    with console.capture() as cap:
        console.print(renderable)
    return cap.get()
```

Replace the four tests at lines 128-153 with:

```python
def test_render_shows_the_answer_and_the_price():
    from aegis.render import render_side_note
    note = SideNote(answer="core/manager.py", header="last 6 of 47 turns",
                    model="haiku", duration_ms=5200, cost_usd=0.0044, ok=True)
    text = rendered(render_side_note(note, palette()))
    assert "core/manager.py" in text
    assert "haiku" in text and "5.2s" in text and "$0.0044" in text
    assert "last 6 of 47 turns" in text


def test_render_points_at_fork_when_the_window_was_not_enough():
    from aegis.render import render_side_note
    note = SideNote(answer="not in the window", needs_more=True, ok=True,
                    header="last 6 of 47 turns", model="haiku")
    assert "/fork" in rendered(render_side_note(note, palette()))


def test_render_stays_quiet_about_fork_when_the_window_sufficed():
    from aegis.render import render_side_note
    note = SideNote(answer="core/manager.py", ok=True, model="haiku")
    assert "/fork" not in rendered(render_side_note(note, palette()))


def test_render_shows_the_reason_a_note_failed():
    from aegis.render import render_side_note
    text = rendered(render_side_note(SideNote(ok=False, error="boom"),
                                     palette()))
    assert "boom" in text


def test_a_markdown_answer_renders_as_markdown_not_asterisks():
    """The point of the change: a model asked a technical question answers
    in markdown, and you should not be reading the syntax."""
    from aegis.render import render_side_note
    note = SideNote(answer="use **replay_events**, not `load_config`",
                    ok=True, model="haiku")
    text = rendered(render_side_note(note, palette()))
    assert "replay_events" in text
    assert "**" not in text


def test_an_error_is_not_run_through_markdown():
    """An error is aegis speaking a fixed sentence, and it carries the
    alternative the operator must act on. Markdown would strip its tint,
    so the failure branch stays a plain tinted Text."""
    from rich.text import Text
    from aegis.render import render_side_note
    g = render_side_note(SideNote(ok=False, error="beta is mid-turn. Wait "
                                  "for it, or /enqueue the task instead."),
                         palette())
    assert all(isinstance(r, Text) for r in g.renderables)
    assert "/enqueue" in rendered(g)
```

In `tests/test_peer_command.py`, replace `test_render_peer_answer_leads_with_the_target` (line ~95-101) with:

```python
def test_render_peer_answer_leads_with_the_target():
    """In a pane full of transient blocks the first token is how you tell
    'beta answered this' from 'this is my own agent talking'. Asserted
    against the header renderable directly rather than a rendered frame."""
    from aegis.render import render_peer_answer
    from aegis.tui.themes import INK, aegis_colors
    colors = aegis_colors(INK)
    g = render_peer_answer(
        PeerAnswer(answer="green", target="beta", ok=True), colors)
    assert g.renderables[0].plain.startswith("@beta ")

    from rich.console import Console
    console = Console(width=100, no_color=True)
    with console.capture() as cap:
        console.print(g)
    assert "green" in cap.get()
```

- [x] **Step 2: Run tests to verify they fail**

Run: `uv run python -m pytest tests/test_btw_command.py tests/test_peer_command.py -q`
Expected: FAIL — `AttributeError: 'Text' object has no attribute 'renderables'` on the peer test, and the markdown test fails on `"**" not in text`.

- [x] **Step 3: Rewrite `render_side_note`**

Replace `src/aegis/render.py:247-271` entirely:

```python
def render_side_note(note, colors) -> Group:
    """Visible block for a `/btw` side note.

    Its own treatment, because it is neither a user line nor agent output
    — it is a third voice, and one that is not part of the conversation.
    The footer carries model, latency and cost: a side note is a paid call
    and the price should be visible.

    The answer is rendered as Markdown; the error is not. An error is not
    model prose — it is aegis speaking a fixed sentence, and it carries
    the alternative the operator has to act on. Markdown imposes its own
    styling, which is exactly why it is right for the answer and wrong for
    the failure line's `colors.error` tint.

    Transient by design. This block goes into the pane's ``_history`` (so
    scrolling keeps it) and is never appended to the session log, so it
    does not survive a reload and never enters the window a later `/btw`
    assembles. Side notes do not compound.
    """
    tint = colors.error if not note.ok else colors.accent
    parts: list[RenderableType] = [Text("btw", style=f"bold italic {tint}")]
    if note.ok:
        parts.append(Markdown(note.answer))
    else:
        parts.append(Text(note.error or "no answer", style=tint))
    if note.ok and note.needs_more:
        parts.append(Text(
            f"  answered from {note.header} — /fork if you want it to "
            f"actually go look.", style=f"italic {colors.working}"))
    if note.footer:
        parts.append(Text(f"  {note.footer}", style=colors.muted))
    return Group(*parts)
```

- [x] **Step 4: Rewrite `render_peer_answer`**

Replace `src/aegis/render.py:274-291` entirely (keep the existing docstring body, add the markdown paragraph):

```python
def render_peer_answer(answer, colors) -> Group:
    """Visible block for an `@peer` answer.

    Transient in *this* pane, exactly as a side note is — it lands in
    ``_history`` and is never appended to this session's log, so the
    agent you are sitting with neither sees it nor pays for it. The same
    answer is a real turn in the peer's own transcript, which is where it
    belongs: a log holding a question with no answer would corrupt every
    window later assembled from it.

    The header leads with the target, and is the first renderable of the
    returned Group: in a pane full of transient blocks the first token is
    how you tell "beta answered this" from "this is my own agent
    talking".

    Markdown on the ok path only, for the reason given in
    ``render_side_note`` — a refusal like "beta is mid-turn. Wait for it
    to finish, or /enqueue the task instead." is aegis speaking, and it
    keeps its `colors.error` tint.
    """
    tint = colors.error if not answer.ok else colors.accent
    parts: list[RenderableType] = [
        Text(f"@{answer.target or '?'} ", style=f"bold italic {tint}")]
    if answer.ok:
        parts.append(Markdown(answer.answer))
    else:
        parts.append(Text(answer.error or "no answer", style=tint))
    if answer.footer:
        parts.append(Text(f"  {answer.footer}", style=colors.muted))
    return Group(*parts)
```

- [x] **Step 5: Run tests to verify they pass**

Run: `uv run python -m pytest tests/test_btw_command.py tests/test_peer_command.py -v`
Expected: PASS

- [x] **Step 6: Mutation-check the markdown assertion**

Temporarily revert `parts.append(Markdown(note.answer))` to `parts.append(Text(note.answer))` and run `test_a_markdown_answer_renders_as_markdown_not_asterisks`.
Expected: FAIL on `"**" not in text`. Restore the `Markdown` line.

A test that cannot fail licenses shipping. Confirm this one can.

- [x] **Step 7: Commit**

```bash
git add src/aegis/render.py tests/test_btw_command.py tests/test_peer_command.py
git commit -m "feat(render): markdown for /btw and @peer answers, ok path only

A model asked a technical question answers in markdown, and you were
reading the asterisks. Both renderers now return a Group whose body is
Markdown(answer).

The failure branch stays a tinted Text. An error is not model prose — it
is aegis speaking a fixed sentence, and it carries the alternative the
operator must act on ('beta is mid-turn. Wait for it to finish, or
/enqueue the task instead.'). Markdown imposes its own styling, so the
property that makes it right for the answer makes it wrong there.

Five .plain assertions converted to render through a Console, which
asserts on what reaches the terminal rather than on a string the
renderer assembled. The @peer test now asserts against the header
renderable directly — more precise than its string prefix and less
brittle than matching a rendered frame." \
  -- src/aegis/render.py tests/test_btw_command.py tests/test_peer_command.py
```

---

### Task 2b: The aside surface — DONE `f6224c1`

Added after Task 2 landed, on Alex's request: *"some kind of gray
background or subtle border for these things so it doesn't look exactly
as the same agent, both btw and peer."*

The request is correct and Task 2 is what created the need for it. Once
`render_side_note` renders `Markdown(answer)`, it uses the identical code
path as `render_event(AssistantText)` — a `/btw` answer and the agent's
own prose became pixel-identical. Making the answer beautiful made it
camouflage, and an `@peer` answer reading as your own agent is worse than
ugly: it is a real turn from a *different session* wearing this one's
voice.

**Files:**
- Modify: `src/aegis/themes/__init__.py` — `AegisColors.panel`, `.rule`
- Modify: `src/aegis/render.py` — `_ASIDE_BOX`, `_aside`, both renderers
- Test: `tests/test_btw_command.py`, `tests/test_peer_command.py`

**What landed:**

- `AegisColors` gains `panel` (raised background) and `rule` (border).
  Neither via `var()`, whose fallback is the foreground — as a border or
  background that is the *loudest* thing on screen rather than the
  quietest. They degrade toward `surface` / `muted` instead. All three
  shipped themes already carried `panel:` and `aegis-rule:`.
- `_ASIDE_BOX` is a custom `box.Box` with a left edge and nothing else.
  **`box.MINIMAL` was the first attempt and is a trap worth recording:**
  it draws its verticals as spaces, so the border rendered as nothing and
  only the background did any work. Caught by looking at the output, not
  by a test — which is why `test_the_aside_draws_a_visible_left_bar` now
  exists.
- Both renderers return `_aside(parts, colors)` → a `Panel` with the
  panel background, the `rule`-coloured bar, and `padding=(0, 1)`.
- Return type is now `Panel`, so structural assertions moved from
  `block.renderables[0]` to `block.renderable.renderables[0]`.

**Tests:** the surface is a `Panel` with the theme's background and rule;
the header still leads the group; a *failed* note keeps the surface (a
failure is still an aside); every rendered line starts with `▏`; and
agent prose has no bar — the other half of the property, since the bar
means "not the conversation".

### Task 3: Extract `_apply_command_result` — DONE `d212f14`

Pure refactor, no behaviour change. Must land before Task 4, which needs the chain callable from two places.

**Files:**
- Modify: `src/aegis/tui/pane.py:1361-1419` (the chain inside `on_growing_input_submitted`), adding `_put_block` and `_apply_command_result` near `_apply_command_effect` (line 1106)

**Interfaces:**
- Consumes: `render_side_note`/`render_peer_answer` returning `Group` (Task 2).
- Produces:
  - `ConversationPane._put_block(renderable, payload, *, at_idx: int | None = None) -> None`
  - `ConversationPane._apply_command_result(result, width: int, *, at_idx: int | None = None) -> str | None` — returns the text to deliver for a `deliver` effect, else `None`.

- [x] **Step 1: Write the failing test**

Append to `tests/test_deferred_commands.py`:

```python
# ---------- the effect chain, callable from two places -------------------

class _FakePane:
    """The three ConversationPane methods _apply_command_result touches,
    recorded rather than mounted. Keeps the chain testable without a
    running Textual app."""

    def __init__(self):
        from aegis.tui.themes import INK, aegis_colors
        self.blocks: list[tuple[object, str, int | None]] = []
        self.effects: list[dict] = []
        self._palette = aegis_colors(INK)
        self.flushed = 0

    def _flush_streaming(self):
        self.flushed += 1

    def _put_block(self, renderable, payload, *, at_idx=None):
        self.blocks.append((renderable, payload, at_idx))

    def _apply_command_effect(self, effect):
        self.effects.append(effect)


def _apply(pane, result, width=80, at_idx=None):
    from aegis.tui.pane import ConversationPane
    return ConversationPane._apply_command_result(
        pane, result, width, at_idx=at_idx)


def test_a_side_note_effect_mounts_a_side_note_block():
    from aegis.commands import CommandResult
    from dataclasses import asdict
    from aegis.btw import SideNote
    note = SideNote(answer="core/manager.py", ok=True, model="haiku")
    pane = _FakePane()
    out = _apply(pane, CommandResult(True, note.answer, "",
                                     effect={"kind": "side_note",
                                             "note": asdict(note)}))
    assert out is None
    assert len(pane.blocks) == 1
    assert "core/manager.py" in pane.blocks[0][1]


def test_a_deliver_effect_returns_the_text_and_mounts_nothing():
    from aegis.commands import CommandResult
    pane = _FakePane()
    out = _apply(pane, CommandResult(True, "", "",
                                     effect={"kind": "deliver",
                                             "text": "hello"}))
    assert out == "hello"
    assert pane.blocks == []


def test_an_ordinary_result_mounts_a_command_block_and_applies_effects():
    from aegis.commands import CommandResult
    pane = _FakePane()
    out = _apply(pane, CommandResult(True, "switched", "",
                                     effect={"kind": "theme",
                                             "name": "ink"}))
    assert out is None
    assert len(pane.blocks) == 1
    assert pane.effects == [{"kind": "theme", "name": "ink"}]


def test_at_idx_is_forwarded_so_a_result_can_replace_a_placeholder():
    from aegis.commands import CommandResult
    from dataclasses import asdict
    from aegis.btw import SideNote
    pane = _FakePane()
    _apply(pane, CommandResult(True, "x", "",
                               effect={"kind": "side_note",
                                       "note": asdict(SideNote(answer="x",
                                                               ok=True))}),
           at_idx=7)
    assert pane.blocks[0][2] == 7
```

- [x] **Step 2: Run test to verify it fails**

Run: `uv run python -m pytest tests/test_deferred_commands.py -k effect -v`
Expected: FAIL — `AttributeError: type object 'ConversationPane' has no attribute '_apply_command_result'`

- [x] **Step 3: Add `_put_block`**

In `src/aegis/tui/pane.py`, immediately before `_mount_block` (line 1130):

```python
    def _put_block(self, renderable: RenderableType, payload: str,
                   *, at_idx: int | None = None) -> None:
        """Mount a new block, or rewrite the block at ``at_idx`` in place.

        The deferred path needs the second: its placeholder was mounted
        when the command started, and the result must land *there* rather
        than at the tail — so a side note stays where you asked it while
        the agent's output streams past underneath. Mirrors what
        ``_render_tool_block`` does for a tool call's result.
        """
        if at_idx is None:
            self._mount_block(renderable, payload)
            return
        rec = self._history[at_idx]
        rec.renderable = renderable
        rec.payload = payload
        pos = at_idx - self._window_start
        if 0 <= pos < len(self._mounted_blocks):
            self._mounted_blocks[pos].update_content(renderable, payload)
            if self._stick_to_bottom:
                self._transcript().scroll_end(animate=False)
```

- [x] **Step 4: Add `_apply_command_result`**

Immediately after `_apply_command_effect` (which ends at line 1128):

```python
    def _apply_command_result(self, result, width: int,
                              *, at_idx: int | None = None) -> str | None:
        """Render one command result into the transcript.

        Extracted from ``on_growing_input_submitted`` because deferring
        splits the call into two sites — inline and worker-completion —
        and a duplicated chain would drift. The first casualty would be
        `peer_answer`, which has to keep working on both paths while
        `@peer` lands with ``deferred=False`` and flips to ``True``
        later.

        Returns the text to deliver to the agent for a ``deliver``
        effect, else None. ``deliver`` is the one branch that cannot be
        deferred, since it produces a message to send rather than a block
        to mount; prompt commands are not deferred, so this costs
        nothing.
        """
        eff = result.effect or {}
        kind = eff.get("kind")
        if kind == "deliver":
            # Prompt command: its expansion is delivered to the agent as a
            # normal user message (rendered as a user line by
            # _on_core_dispatch), not a command-result block.
            return eff["text"]
        if kind == "side_note":
            # A /btw answer gets its own treatment — it is neither a user
            # line nor agent output. It lands in _history (so scrolling
            # keeps it) and is never appended to the session log, so it
            # does not survive a reload and never enters the window a
            # later /btw assembles.
            from aegis.btw import SideNote
            from aegis.render import render_side_note
            note = SideNote(**eff["note"])
            self._flush_streaming()
            self._put_block(render_side_note(note, self._palette),
                            f"btw: {note.answer}\n{note.footer}".strip(),
                            at_idx=at_idx)
            return None
        if kind == "peer_answer":
            # An @peer answer is transient *here* and real *there*: it
            # lands in this pane's _history and is never appended to this
            # session's log, so the agent you are sitting with neither
            # sees it nor pays for it. The same answer is a genuine turn
            # in the peer's own transcript.
            from aegis.peer import PeerAnswer
            from aegis.render import render_peer_answer
            answer = PeerAnswer(**eff["answer"])
            self._flush_streaming()
            self._put_block(
                render_peer_answer(answer, self._palette),
                f"@{answer.target}: {answer.answer}\n"
                f"{answer.footer}".strip(), at_idx=at_idx)
            return None
        from aegis.render import render_command_block
        self._flush_streaming()
        self._put_block(render_command_block(result, self._palette, width),
                        f"{result.title}\n{result.body}".strip(),
                        at_idx=at_idx)
        if result.effect:
            self._apply_command_effect(result.effect)
        return None
```

- [x] **Step 5: Replace the inline chain**

In `on_growing_input_submitted`, replace lines 1361-1419 (from `elif text.startswith("/"):` through the end of the `else: text = payload` branch) with:

```python
        elif text.startswith("/"):
            # Slash family: `/cmd` is a command aegis executes directly and
            # renders in the transcript (never delivered to the agent); `//x`
            # is an escape that delivers a literal `/x` message.
            from aegis.commands import (
                CommandContext, classify_input, dispatch)
            kind, payload = classify_input(text)
            if kind == "command":
                width = self._transcript().size.width or 80
                result = await dispatch(
                    payload, CommandContext(bridge=self.app,
                                            handle=self.handle))
                delivered = self._apply_command_result(result, width)
                if delivered is None:
                    return
                text = delivered
            else:
                text = payload   # "//foo" → deliver "/foo" as a normal message
```

Remove the now-unused `from aegis.render import render_command_block` import from the handler if it is left orphaned.

- [x] **Step 6: Run tests to verify they pass**

Run: `uv run python -m pytest tests/test_deferred_commands.py -v`
Expected: PASS

- [x] **Step 7: Verify no behaviour change**

Run: `uv run python -m pytest tests/test_btw_command.py tests/test_peer_command.py tests/test_commands.py tests/test_pane.py -q`
Expected: PASS. This is a pure refactor — any failure here is a real regression, not a test that needs updating.

- [x] **Step 8: Commit**

```bash
git add src/aegis/tui/pane.py tests/test_deferred_commands.py
git commit -m "refactor(tui): one place where command results are applied

Deferring /btw splits the effect if/elif chain across two call sites —
inline and worker-completion. Duplicating it would drift, and the first
casualty would be peer_answer, which has to keep working on both paths
while @peer lands deferred=False and flips to True later.

_put_block either mounts or rewrites at an index, so a deferred result
can land in the placeholder its command mounted rather than at the tail.

Pure refactor: no behaviour change, existing suites green." \
  -- src/aegis/tui/pane.py tests/test_deferred_commands.py
```

---

### Task 4: `_DeferredTrack` — the spinner, and the end of the freeze — DONE `07b21e8`

The vertical slice. After this task `/btw` no longer freezes the pane and shows a live spinner.

**Files:**
- Modify: `src/aegis/render.py` (add `render_deferred` after `render_tool_use`, ~line 60)
- Modify: `src/aegis/tui/pane.py` — `_DeferredTrack` dataclass (after `_ToolTrack`, line 89-99); `self._deferred = None` in `__init__` (near line 763); deferred branch in `on_growing_input_submitted`; `_any_spinner_running`, `_tick_tools`, `_freeze_all_tools`
- Modify: `src/aegis/commands/builtins/core.py:380-384` (one line)
- Test: `tests/test_deferred_commands.py`

**Interfaces:**
- Consumes: `resolve_deferred` (Task 1), `_apply_command_result` / `_put_block` (Task 3).
- Produces:
  - `render_deferred(label, subject, elapsed, colors, *, frame=0, cancelled=False, cancel_note="") -> Text`
  - `_DeferredTrack(idx, start, label, subject, cancel_note, worker=None, done=False, elapsed=None)`
  - `ConversationPane._start_deferred(payload, cmd, args, width) -> None`
  - `ConversationPane._any_spinner_running() -> bool`

- [x] **Step 1: Write the failing test for the renderer**

Append to `tests/test_deferred_commands.py`:

```python
# ---------- the placeholder block ----------------------------------------

def _colors():
    from aegis.tui.themes import INK, aegis_colors
    return aegis_colors(INK)


def test_a_running_placeholder_shows_spinner_label_subject_and_elapsed():
    """A spinner with no subject is just anxiety — by second twelve you
    have forgotten which side question you asked."""
    from aegis.render import render_deferred
    text = render_deferred("btw", "which path does resume take?", 4.2,
                           _colors(), frame=2).plain
    assert "btw" in text
    assert "which path does resume take?" in text
    assert "4.2s" in text
    assert text[0] in "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"


def test_the_spinner_glyph_advances_with_the_frame():
    from aegis.render import render_deferred
    a = render_deferred("btw", "q", 1.0, _colors(), frame=0).plain[0]
    b = render_deferred("btw", "q", 1.0, _colors(), frame=1).plain[0]
    assert a != b


def test_a_cancelled_placeholder_carries_the_commands_own_note():
    """'cancelled' is a lie for @peer: the peer already took the turn and
    finishes into its own transcript whether or not anyone is listening."""
    from aegis.render import render_deferred
    text = render_deferred(
        "@beta", "is it green?", 4.2, _colors(), cancelled=True,
        cancel_note="stopped waiting — beta's turn is still running").plain
    assert "stopped waiting — beta's turn is still running" in text
    assert "4.2s" in text
    assert text[0] not in "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"


def test_a_cancelled_placeholder_shows_no_cost():
    """A cancelled call returns no usage, so we do not know what it cost.
    Inventing a number for a line whose purpose is honesty about price
    would be worse than the omission."""
    from aegis.render import render_deferred
    text = render_deferred("btw", "q", 4.2, _colors(), cancelled=True,
                           cancel_note="cancelled").plain
    assert "$" not in text
```

- [x] **Step 2: Run to verify it fails**

Run: `uv run python -m pytest tests/test_deferred_commands.py -k placeholder -v`
Expected: FAIL — `ImportError: cannot import name 'render_deferred'`

- [x] **Step 3: Add `render_deferred`**

In `src/aegis/render.py`, after `render_tool_use` ends (~line 60):

```python
def render_deferred(label: str, subject: str, elapsed: float, colors, *,
                    frame: int = 0, cancelled: bool = False,
                    cancel_note: str = "") -> Text:
    """The block a deferred command occupies while it runs, and after it
    is cancelled.

    Running, it echoes the subject: by the time a 12-17s call returns you
    have forgotten which side question you asked, and a spinner with no
    subject is just anxiety.

    Cancelled, it is a muted tombstone carrying the command's own
    ``cancel_note`` — a tombstone rather than a removal, because ESC
    silently deleting something you can see reads as a glitch, and this
    block is the only record that you spent anything at all.

    No cost is shown on the cancelled line. A cancelled call returns no
    usage, so we do not know what it cost, and inventing a number for a
    line whose entire purpose is honesty about price would be worse than
    the omission. Elapsed time is what we actually know.
    """
    line = Text()
    if cancelled:
        line.append(f"{label} ", style=f"bold italic {colors.muted}")
        line.append(f"· {cancel_note} · {_fmt_dur(elapsed)}",
                    style=colors.muted)
        return line
    line.append(f"{_TOOL_SPINNER[frame % len(_TOOL_SPINNER)]}  ",
                style=colors.working)
    line.append(f"{label} ", style=f"bold italic {colors.accent}")
    if subject:
        line.append(f"· {subject} ", style=colors.muted)
    line.append(f"· {_fmt_dur(elapsed)}", style=colors.muted)
    return line
```

- [x] **Step 4: Run to verify the renderer tests pass**

Run: `uv run python -m pytest tests/test_deferred_commands.py -k placeholder -v`
Expected: PASS (4 tests)

- [x] **Step 5: Write the failing pane test**

Append to `tests/test_deferred_commands.py`:

```python
# ---------- the pane runs it off the input handler ------------------------

import asyncio


class _SlowBridge:
    """A bridge whose side_note blocks until released, so 'still running'
    is a deterministic state rather than a timing race."""

    def __init__(self):
        self.gate = asyncio.Event()
        self.started = asyncio.Event()
        self.cancelled = False

    async def side_note(self, handle, prompt):
        from aegis.btw import SideNote
        self.started.set()
        try:
            await self.gate.wait()
        except asyncio.CancelledError:
            self.cancelled = True
            raise
        return SideNote(answer="from the window", ok=True, model="haiku")


async def test_the_input_handler_does_not_await_a_deferred_command(
        deferred_pane):
    """The whole point. If this await blocks, the pane's message pump is
    held and every spinner in it freezes."""
    pane, bridge = deferred_pane
    await asyncio.wait_for(pane.submit("/btw which path?"), timeout=0.5)
    assert pane._deferred is not None
    assert not pane._deferred.done
    bridge.gate.set()


async def test_the_placeholder_is_replaced_in_place_by_the_answer(
        deferred_pane):
    pane, bridge = deferred_pane
    await pane.submit("/btw which path?")
    idx = pane._deferred.idx
    bridge.gate.set()
    await pane.settle()
    assert pane._deferred is None
    assert "from the window" in pane._history[idx].payload
```

Write the `deferred_pane` fixture against the real `ConversationPane` if it can be constructed headless in this suite; otherwise build a minimal harness exposing `_deferred`, `_history`, `submit()` (calls the real `on_growing_input_submitted` logic) and `settle()` (awaits the worker). Check `tests/test_pane.py` for the project's existing pane-construction pattern and follow it rather than inventing a second one.

- [x] **Step 6: Run to verify it fails**

Run: `uv run python -m pytest tests/test_deferred_commands.py -k deferred_command -v`
Expected: FAIL — `AttributeError: '_Pane' object has no attribute '_deferred'`

- [x] **Step 7: Add the track dataclass**

In `src/aegis/tui/pane.py`, after `_ToolTrack` (line 99):

```python
@dataclass(slots=True)
class _DeferredTrack:
    """Live state for one running deferred command: enough to re-render
    its placeholder with a ticking timer, replace it with the result, or
    tombstone it on cancel.

    One per pane, not one per command. The primitive is general — @peer
    adopts it next — and a single slot keeps the ESC rung unambiguous:
    two spinners racing in one pane would leave ESC with no defensible
    answer about which it cancels.
    """
    idx: int                        # history index of its block
    start: float                    # time.monotonic() at dispatch
    label: str                      # "btw", "@beta"
    subject: str                    # the question, echoed while it runs
    cancel_note: str                # already resolved against parsed args
    worker: object = None
    done: bool = False
    elapsed: float | None = None
```

- [x] **Step 8: Initialise it**

In `ConversationPane.__init__`, next to `self._spin_frame = 0` (line 764):

```python
        # The one running deferred command (/btw today, @peer next), or
        # None. Shares the tool ticker: a deferred placeholder and a tool
        # call spin off the same 10 Hz timer.
        self._deferred: _DeferredTrack | None = None
```

- [x] **Step 9: Add the dispatch branch**

In `on_growing_input_submitted`, inside `if kind == "command":`, before the `result = await dispatch(...)` line added in Task 3:

```python
                from aegis.commands import resolve_deferred
                deferred = resolve_deferred(payload)
                if deferred is not None:
                    self._start_deferred(payload, *deferred, width)
                    return
```

**`payload`, never `text`.** `resolve_deferred` parses a verb, and an
`@`-line has no verb until `classify_input` has rewritten `@beta hi` into
`/peer beta hi` — `REGISTRY.get("@beta")` is None. Resolving the raw input
therefore returns None for every `@` spelling, and the failure is silent
in the direction that looks like success: `@peer` does not crash, it falls
back to the inline await and freezes the pane for up to 300s. That is the
exact bug this primitive exists to delete, reappearing on one spelling
while `/peer beta hi` works perfectly — green suite, found by Alex.

Hence the placement inside `if kind == "command":`, and hence Step 9b.

- [x] **Step 9b: Pin both spellings at the seam**

Add to `tests/test_deferred_commands.py`. A test on `/peer` alone would
pass with the bug present.

```python
@pytest.mark.parametrize("typed", ["/slowly beta hi", "@slowly beta hi"])
def test_both_spellings_reach_the_deferred_path(slow_command, typed,
                                                monkeypatch):
    """`@x` is sugar `classify_input` rewrites into `/peer x`, so the
    frontend must classify first and resolve on the *payload*. Resolving
    the raw line returns None for every `@` spelling and silently drops
    it back onto the inline-await path — the freeze, on one spelling."""
    from aegis.commands import classify_input, resolve_deferred
    monkeypatch.setattr("aegis.commands.AT_VERB", "slowly", raising=False)
    kind, payload = classify_input(typed)
    assert kind == "command"
    assert resolve_deferred(payload) is not None
```

Adapt the `@`-rewrite target to whatever `classify_input` actually maps
`@` to — read it rather than assuming; at time of writing it rewrites to
`/peer`, so the parametrised case may need to exercise `@beta hi` against
the real `/peer` registration instead of the `slow_command` fixture.

Mutation: change Step 9's `resolve_deferred(payload)` to
`resolve_deferred(text)` and confirm the `@` case fails while the `/`
case still passes. If both still pass, the test is not at the seam.

- [x] **Step 10: Add the pane methods**

Add near `_render_tool_block` (line 1762):

```python
    def _start_deferred(self, payload: str, cmd, args: dict,
                        width: int) -> None:
        """Mount a placeholder and run ``payload`` off the input handler.

        Awaiting a 12-17s command in a Textual message handler holds this
        pane's message pump for the duration — no working indicator, no
        tool spinners, no input. The placeholder is what fills the gap
        once the await moves to a worker.
        """
        label = f"/{cmd.name}" if cmd.name != "btw" else "btw"
        subject = " ".join(str(v) for v in args.values() if v)
        if self._deferred is not None and not self._deferred.done:
            from aegis.commands import CommandResult
            from aegis.render import render_command_block
            running = self._deferred
            self._flush_streaming()
            self._mount_block(
                render_command_block(
                    CommandResult(False, f"{running.label} is already running",
                                  "ESC to cancel it"),
                    self._palette, width),
                f"{running.label} is already running")
            return
        try:
            note = cmd.cancel_note.format(**args)
        except (KeyError, IndexError):
            note = cmd.cancel_note
        self._flush_streaming()
        self._mount_block(Text(""), "")
        track = _DeferredTrack(idx=len(self._history) - 1,
                               start=time.monotonic(), label=label,
                               subject=subject, cancel_note=note)
        self._deferred = track
        self._render_deferred_block(track)
        self._ensure_tool_timer()
        track.worker = self.run_worker(
            self._run_deferred(payload, track, width), exclusive=False)

    async def _run_deferred(self, payload: str, track: "_DeferredTrack",
                            width: int) -> None:
        from aegis.commands import CommandContext, dispatch
        result = await dispatch(
            payload, CommandContext(bridge=self.app, handle=self.handle))
        if track.done or self._deferred is not track:
            return          # cancelled mid-flight: the result is dropped
        track.done = True
        track.elapsed = time.monotonic() - track.start
        self._deferred = None
        self._apply_command_result(result, width, at_idx=track.idx)
        if not self._any_spinner_running():
            self._stop_tool_timer()

    def _render_deferred_block(self, track: "_DeferredTrack", *,
                               layout: bool = True,
                               cancelled: bool = False) -> None:
        from aegis.render import render_deferred
        elapsed = (track.elapsed if track.elapsed is not None
                   else time.monotonic() - track.start)
        line = render_deferred(track.label, track.subject, elapsed,
                               self._palette, frame=self._spin_frame,
                               cancelled=cancelled,
                               cancel_note=track.cancel_note)
        payload = (f"{track.label} · {track.cancel_note}" if cancelled
                   else f"{track.label} · {track.subject}")
        self._put_block(line, payload, at_idx=track.idx)

    def _any_spinner_running(self) -> bool:
        """Whether the 10 Hz ticker still has anything to animate."""
        return self._any_tool_running() or (
            self._deferred is not None and not self._deferred.done)
```

- [x] **Step 11: Tick the deferred track**

Replace `_tick_tools` (line 1753):

```python
    def _tick_tools(self) -> None:
        if not self._any_spinner_running():
            self._stop_tool_timer()
            return
        self._spin_frame += 1
        for track in self._tools.values():
            if not track.done:
                self._render_tool_block(track, layout=False)
        if self._deferred is not None and not self._deferred.done:
            self._render_deferred_block(self._deferred, layout=False)
```

- [x] **Step 12: Stop the turn from freezing the note**

`_freeze_all_tools` (line 1787) runs at turn end and calls `_stop_tool_timer()` unconditionally. `/btw` is legal mid-turn and independent of it, so a turn ending must not freeze a running side note. Change its last line from `self._stop_tool_timer()` to:

```python
        if not self._any_spinner_running():
            self._stop_tool_timer()
```

Apply the same guard at the `_stop_tool_timer()` call inside `_attach_tool_result` (line ~1729).

- [x] **Step 13: Flip `/btw`**

In `src/aegis/commands/builtins/core.py`, the registration at line 380:

```python
    SlashCommand("btw", "answer a side question from the recent window",
                 "/btw <question>", _btw,
                 spec=ArgSpec(
                     positionals=(
                         Arg("prompt", required=False, greedy=True),)),
                 deferred=True),
```

**Do not touch the `/peer` entry immediately below it.** If `aegis-at-mentions` still holds exclusive on this file, hand it this one-line diff instead of editing.

- [x] **Step 14: Run the tests**

Run: `uv run python -m pytest tests/test_deferred_commands.py tests/test_btw_command.py -v`
Expected: PASS

- [x] **Step 15: Verify against the real TUI**

Unit tests cannot show that the freeze is gone. Run `aegis`, start a turn, and fire `/btw <a real question>` while the agent is working. Confirm all three: the btw spinner ticks, **the agent's own tool spinners keep ticking**, and the input still accepts keys. Then confirm the answer replaces the placeholder in place rather than at the tail.

The freeze is the defect this task exists to fix — a green unit suite is not evidence it is fixed.

- [x] **Step 16: Commit**

```bash
git add src/aegis/render.py src/aegis/tui/pane.py \
        src/aegis/commands/builtins/core.py tests/test_deferred_commands.py
git commit -m "feat(btw): /btw runs off the input handler, with a live block

/btw was awaited inside on_growing_input_submitted for the 12-17s the
call takes. That is a Textual message handler, so the pane's pump was
held for the duration: no working indicator, no tool spinners, no input.
'A side note that doesn't cost a conversation' was true about money and
false about attention.

A deferred command now mounts a placeholder, dispatches in a worker, and
rewrites that block in place when the result lands — in place, at the
index where it was mounted, so a note stays where you asked it while the
agent's output streams past underneath.

_DeferredTrack is one slot per pane rather than the spec's per-command
dict: the primitive is general and a single slot keeps the ESC rung
unambiguous.

Also fixes _freeze_all_tools stopping the ticker at turn end, which
would have frozen a mid-turn side note's spinner — /btw is independent
of the turn, which is the whole reason it is legal mid-turn." \
  -- src/aegis/render.py src/aegis/tui/pane.py \
     src/aegis/commands/builtins/core.py tests/test_deferred_commands.py
```

---

### Task 5: One at a time — DONE `07b21e8`

The guard shipped in Task 4's `_start_deferred`; this task pins it with tests and proves it can fail.

**Files:**
- Test: `tests/test_deferred_commands.py`

**Interfaces:**
- Consumes: `_start_deferred` (Task 4).
- Produces: nothing new.

- [x] **Step 1: Write the test**

```python
async def test_a_second_btw_is_refused_while_one_is_running(deferred_pane):
    pane, bridge = deferred_pane
    await pane.submit("/btw first question")
    first = pane._deferred
    await pane.submit("/btw second question")
    assert pane._deferred is first, "the second must not displace the first"
    assert "already running" in pane._history[-1].payload
    bridge.gate.set()


async def test_an_ordinary_command_still_works_while_a_note_runs(
        deferred_pane):
    """Only a second deferred command is refused. Replacing a freeze with
    a lock would be no improvement."""
    pane, bridge = deferred_pane
    await pane.submit("/btw a question")
    await pane.submit("/help")
    assert "help" in pane._history[-1].payload.lower()
    assert pane._deferred is not None
    bridge.gate.set()


async def test_a_second_btw_after_the_first_finished_is_allowed(
        deferred_pane):
    pane, bridge = deferred_pane
    await pane.submit("/btw first")
    bridge.gate.set()
    await pane.settle()
    bridge.gate.clear()
    await pane.submit("/btw second")
    assert pane._deferred is not None
    assert "already running" not in pane._history[-1].payload
    bridge.gate.set()
```

- [x] **Step 2: Run to verify they pass**

Run: `uv run python -m pytest tests/test_deferred_commands.py -k "second or ordinary" -v`
Expected: PASS

- [x] **Step 3: Mutation-check the guard**

Temporarily delete the `if self._deferred is not None and not self._deferred.done:` block from `_start_deferred` and re-run.
Expected: `test_a_second_btw_is_refused_while_one_is_running` FAILS. Restore the guard.

A guard test that cannot fail is worth less than none, because it licenses shipping.

- [x] **Step 4: Commit**

```bash
git add tests/test_deferred_commands.py
git commit -m "test(btw): pin one-deferred-command-at-a-time per pane

Includes the mutation check: deleting the guard fails the test. Also
pins that ordinary commands and normal messages keep working while a
note runs — replacing a freeze with a lock would be no improvement." \
  -- tests/test_deferred_commands.py
```

---

### Task 6: ESC cancels — DONE

**Files:**
- Modify: `src/aegis/tui/pane.py` (add `cancel_deferred_if_running` near `clear_input_if_present`, line 1284)
- Modify: `src/aegis/tui/app.py:1257-1272` (`action_interrupt`)
- Modify: `docs/superpowers/specs/2026-07-31-aegis-btw-deferred-render-design.md` (the `_BtwTrack` → `_DeferredTrack` deviation)
- Test: `tests/test_deferred_commands.py`

**Interfaces:**
- Consumes: `_DeferredTrack`, `_render_deferred_block`, `_any_spinner_running` (Task 4).
- Produces: `ConversationPane.cancel_deferred_if_running() -> bool`

- [x] **Step 1: Write the failing test**

```python
async def test_esc_cancels_a_running_note_and_leaves_a_tombstone(
        deferred_pane):
    pane, bridge = deferred_pane
    await pane.submit("/btw which path?")
    idx = pane._deferred.idx
    assert pane.cancel_deferred_if_running() is True
    assert pane._deferred is None
    assert "cancelled" in pane._history[idx].payload
    bridge.gate.set()


async def test_esc_reports_not_consumed_when_no_note_is_running(
        deferred_pane):
    """So the app falls through to clear-input, then interrupt."""
    pane, _ = deferred_pane
    assert pane.cancel_deferred_if_running() is False


async def test_a_cancelled_note_that_lands_late_is_dropped(deferred_pane):
    """A side question must never disturb the conversation it sits beside,
    and that includes on the way out."""
    pane, bridge = deferred_pane
    await pane.submit("/btw which path?")
    idx = pane._deferred.idx
    pane.cancel_deferred_if_running()
    bridge.gate.set()
    await pane.settle()
    assert "cancelled" in pane._history[idx].payload
    assert "from the window" not in pane._history[idx].payload
```

- [x] **Step 2: Run to verify it fails**

Run: `uv run python -m pytest tests/test_deferred_commands.py -k esc -v`
Expected: FAIL — `AttributeError: no attribute 'cancel_deferred_if_running'`

- [x] **Step 3: Add the pane method**

In `src/aegis/tui/pane.py`, immediately after `clear_input_if_present` (line 1291):

```python
    def cancel_deferred_if_running(self) -> bool:
        """Esc handler: cancel a running deferred command and report we
        consumed the key. Nothing running → return False so the app falls
        through to clearing the input, then interrupting the turn.
        """
        track = self._deferred
        if track is None or track.done:
            return False
        track.done = True
        track.elapsed = time.monotonic() - track.start
        self._deferred = None
        if track.worker is not None:
            with contextlib.suppress(Exception):
                track.worker.cancel()
        self._render_deferred_block(track, cancelled=True)
        if not self._any_spinner_running():
            self._stop_tool_timer()
        return True
```

- [x] **Step 4: Add the ESC rung**

In `src/aegis/tui/app.py`, inside `action_interrupt`, after the `ModalScreen` block and **before** the `clear_input_if_present` block:

```python
        active = self._active
        # A running /btw is cancelled before a half-typed line is cleared:
        # it is the live thing on screen demanding attention, and it is
        # billing by the second. Interrupting the turn stays last — it is
        # the destructive option and should be hardest to hit by accident.
        if active is not None and hasattr(active,
                                          "cancel_deferred_if_running"):
            if active.cancel_deferred_if_running():
                return
```

The existing `active = self._active` line below it becomes redundant — remove the duplicate, keep one assignment above this block.

- [x] **Step 5: Run to verify they pass**

Run: `uv run python -m pytest tests/test_deferred_commands.py -v`
Expected: PASS

- [x] **Step 6: Verify the ladder in the real TUI**

Four presses, in order, confirming each rung:
1. `/btw <question>` → ESC → tombstone appears, turn is **not** interrupted.
2. Type half a line with no note running → ESC → input clears, turn is **not** interrupted.
3. Nothing typed, agent working → ESC → turn interrupts.
4. Open the dashboard (`F1`/whatever binds it) → ESC → modal dismisses, nothing else happens.

- [x] **Step 7: Update the spec's naming deviation**

In `docs/superpowers/specs/2026-07-31-aegis-btw-deferred-render-design.md`, change the `## The btw track` section's `_BtwTrack` to `_DeferredTrack` and note the single-slot-per-pane decision with its reason (ESC ambiguity). A spec that names a symbol the code does not have misleads the next reader.

- [x] **Step 8: Full suite**

Run: `uv run python -m pytest -q`
Expected: 2450+ passed. Read any failures: 1-2 inotify/watchdog TUI flakes are known on zion; anything in `test_deferred_commands.py`, `test_btw_command.py`, `test_peer_command.py`, `test_pane.py` or `test_commands.py` is real.

- [x] **Step 9: Commit**

```bash
git add src/aegis/tui/pane.py src/aegis/tui/app.py \
        tests/test_deferred_commands.py \
        docs/superpowers/specs/2026-07-31-aegis-btw-deferred-render-design.md
git commit -m "feat(btw): ESC cancels a running side note

Second rung in action_interrupt's ladder, above clear-input and below
modal dismiss: the spinning block is the live thing on screen demanding
attention and it is billing by the second, while clearing the input is
reachable by other means and interrupting the turn is the destructive
option that should stay hardest to hit by accident.

Cancel leaves a tombstone rather than removing the block — ESC silently
deleting something you can see reads as a glitch, and the block is the
only record that you spent anything. No cost on that line: a cancelled
call returns no usage, so elapsed time is what we actually know.

A cancelled note that lands late is dropped. A side question must never
disturb the conversation it sits beside, including on the way out.

Spec updated: _BtwTrack is _DeferredTrack, one slot per pane." \
  -- src/aegis/tui/pane.py src/aegis/tui/app.py \
     tests/test_deferred_commands.py \
     docs/superpowers/specs/2026-07-31-aegis-btw-deferred-render-design.md
```

- [x] **Step 10: Release the claim**

`aegis_release` the exclusive claim on `render.py`, `pane.py`, `app.py` and the two test files, and tell `aegis-at-mentions` the primitive is complete and `@peer` can adopt `deferred=True` + its `cancel_note` template.

---

## Self-Review

**Spec coverage:**

| Spec section | Task |
|---|---|
| The freeze / `deferred` flag | 1, 4 |
| `@peer` is worse (evidence, not a requirement) | — (rationale only) |
| Cancel is per-command / `cancel_note` | 1, 4, 6 |
| The btw track | 4 |
| One place effects are applied | 3 |
| Where the answer lands (in place) | 3 (`_put_block`), 4 |
| Markdown | 2 |
| Markdown on the ok path only | 2 |
| Test fallout (5 `.plain`) | 2 |
| One at a time | 4 (guard), 5 (tests) |
| ESC ladder + tombstone | 6 |
| Scope: TUI only, no `@peer` flip | Global Constraints |
| Testing: fake bridge on an `asyncio.Event`, Console renders, mutation check | 2, 4, 5 |

No gaps.

**Placeholder scan:** One soft spot, deliberate — Task 4 Step 5 says to follow `tests/test_pane.py`'s existing pane-construction pattern rather than specifying a fixture I have not read. Inventing a second harness for a suite that already has one would be the worse failure. Every other step carries its actual code.

**Type consistency:** `_DeferredTrack` field names (`idx`, `start`, `label`, `subject`, `cancel_note`, `worker`, `done`, `elapsed`) are used identically in `_start_deferred`, `_run_deferred`, `_render_deferred_block` and `cancel_deferred_if_running`. `render_deferred`'s signature matches its one call site. `_put_block(renderable, payload, *, at_idx)` matches all three callers. `_apply_command_result(result, width, *, at_idx)` matches both.
