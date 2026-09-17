# Turn Attention Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Every finished turn carries one attention category (`needs_input`, `error`, `review`, `waiting`, `done`), shown in the tab bar, the transcript recap header and the F3 sidebar, and persisted across restarts.

**Architecture:** A pure module `aegis/attention.py` owns the categories, the hard-signal resolution and the marks. The turn recap's schema gains an `attention` field the model fills; `AgentSession` resolves it against hard signals (Result error, ephemeral origin, a manager-provided wait probe), stores `attention` and `attention_seq`, and persists the category in `RecapNote`. Views track what they have seen per pane (`attention_acked`), the way `unseen` works today.

**Tech Stack:** Python 3.13, Textual, Rich, pydantic, pytest (`make test` = `uv run pytest -q -n auto -m "not slow" --max-unmarked-duration=3`).

**Spec:** `docs/superpowers/specs/2026-09-17-aegis-turn-attention-design.md`

## Global Constraints

- Only the current palette (`colors.accent`, `colors.error`, `colors.ready`, `colors.muted`, `colors.ink`); no theme gains a colour.
- Marks: `needs_input` `?` on an accent block, `error` `✗` on an error block, `review` `◆` bold ink, `waiting` `⧗` muted, `done` `✓` ready colour.
- Labels: `needs you`, `error`, `review`, `waiting`, `done`.
- No extra paid call; the category rides the existing turn recap.
- Hermetic tests must never reach the real `claude -p` (guarded in `tests/conftest.py`; patch `aegis.core.session.recap_for` in any test that runs a turn with a roster).
- Shared checkout: commit with `git commit -F - -- <paths>`, never `git add -A`, never `--amend`. Format only `src/` files with `uv run ruff format <files>`.
- The gate is `make test` verbatim, run once at the end by the dispatcher; a task runs only its own test file.

---

### Task 1: The attention module

**Files:**
- Create: `src/aegis/attention.py`
- Test: `tests/test_attention.py`

**Interfaces:**
- Produces:
  - `CATEGORIES: tuple[str, ...] = ("needs_input", "error", "review", "waiting", "done")`
  - `LABELS: dict[str, str]`
  - `resolve(model: str | None, *, errored: bool, ephemeral: bool, waiting: bool) -> str`
  - `is_pending(category: str, seq: int, acked: int) -> bool`
  - `mark(category: str, colors, *, blink_off: bool = False) -> str` (Rich markup, one or three cells)
  - `style_for(category: str, colors) -> str` (a Rich colour for borders and labels)

- [ ] **Step 1: Write the failing tests**

```python
"""The attention category of a turn: the model proposes, hard signals decide."""

import pytest

from aegis.attention import CATEGORIES, LABELS, is_pending, mark, resolve, style_for
from aegis.tui.themes import INK, aegis_colors

C = aegis_colors(INK)


def test_the_five_categories_in_urgency_order():
    assert CATEGORIES == ("needs_input", "error", "review", "waiting", "done")
    assert set(LABELS) == set(CATEGORIES)
    assert LABELS["needs_input"] == "needs you"


def test_the_model_decides_when_no_hard_signal_fires():
    for c in CATEGORIES:
        assert resolve(c, errored=False, ephemeral=False, waiting=False) == c


def test_an_error_result_overrides_the_model():
    assert resolve("done", errored=True, ephemeral=False, waiting=False) == "error"
    assert resolve("needs_input", errored=True, ephemeral=False, waiting=True) == "error"


def test_an_ephemeral_worker_never_needs_the_operator():
    assert resolve("needs_input", errored=False, ephemeral=True, waiting=False) == "done"


def test_a_live_wait_turns_calm_categories_into_waiting():
    assert resolve("done", errored=False, ephemeral=False, waiting=True) == "waiting"
    assert resolve("review", errored=False, ephemeral=False, waiting=True) == "waiting"
    assert resolve("needs_input", errored=False, ephemeral=False, waiting=True) == "needs_input"


@pytest.mark.parametrize("model", [None, "", "banana"])
def test_no_usable_model_answer_falls_back_to_the_hard_signals(model):
    assert resolve(model, errored=False, ephemeral=False, waiting=False) == "done"
    assert resolve(model, errored=False, ephemeral=False, waiting=True) == "waiting"


def test_pending_until_acked_except_waiting():
    assert is_pending("needs_input", seq=3, acked=2)
    assert not is_pending("needs_input", seq=3, acked=3)
    assert is_pending("waiting", seq=3, acked=3)
    assert not is_pending("", seq=0, acked=0)


def test_marks_use_the_current_palette_only():
    assert "?" in mark("needs_input", C) and C.accent in mark("needs_input", C)
    assert "✗" in mark("error", C) and C.error in mark("error", C)
    assert "◆" in mark("review", C)
    assert "⧗" in mark("waiting", C) and C.muted in mark("waiting", C)
    assert "✓" in mark("done", C) and C.ready in mark("done", C)


def test_a_blinking_mark_keeps_its_width():
    from rich.text import Text

    on = Text.from_markup(mark("needs_input", C))
    off = Text.from_markup(mark("needs_input", C, blink_off=True))
    assert on.cell_len == off.cell_len
    assert "?" not in off.plain


def test_style_for_each_category():
    assert style_for("needs_input", C) == C.accent
    assert style_for("error", C) == C.error
    assert style_for("done", C) == C.ready
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest -q tests/test_attention.py`
Expected: FAIL, `ModuleNotFoundError: No module named 'aegis.attention'`

- [ ] **Step 3: Implement**

```python
"""Whether a finished turn needs the operator.

Every turn recap carries one category. The model proposes it, because only
the model can tell a question from a report; hard signals decide, because
the model cannot see a failed Result, cannot know a session is a queue
worker whose answer goes elsewhere, and cannot see a monitor it armed.

Seen-ness is per view (``is_pending``), like the unread ``*``: the brain
holds the category and a sequence number, and each pane remembers the
sequence it last showed. ``waiting`` is shown while it holds, seen or not.
"""

from __future__ import annotations

CATEGORIES: tuple[str, ...] = ("needs_input", "error", "review", "waiting", "done")

LABELS: dict[str, str] = {
    "needs_input": "needs you",
    "error": "error",
    "review": "review",
    "waiting": "waiting",
    "done": "done",
}


def resolve(
    model: str | None, *, errored: bool, ephemeral: bool, waiting: bool
) -> str:
    """The category a turn ends with. See the spec's precedence list."""
    if errored:
        return "error"
    cat = model if model in CATEGORIES else "done"
    if ephemeral and cat == "needs_input":
        cat = "done"
    if waiting and cat not in ("error", "needs_input"):
        cat = "waiting"
    return cat


def is_pending(category: str, seq: int, acked: int) -> bool:
    if category not in CATEGORIES:
        return False
    return category == "waiting" or seq > acked


def style_for(category: str, colors) -> str:
    return {
        "needs_input": colors.accent,
        "error": colors.error,
        "review": colors.ink,
        "waiting": colors.muted,
        "done": colors.ready,
    }.get(category, colors.muted)


def mark(category: str, colors, *, blink_off: bool = False) -> str:
    """Rich markup for the category's mark. The two urgent ones sit on a
    filled block; ``blink_off`` blanks the glyph but keeps the cells, so a
    blinking tab never shifts the tabs after it."""
    if category == "needs_input":
        glyph = " " if blink_off else "?"
        return f"[bold {colors.panel} on {colors.accent}] {glyph} [/]"
    if category == "error":
        return f"[bold {colors.panel} on {colors.error}] ✗ [/]"
    if category == "review":
        return f"[bold {colors.ink}]◆[/]"
    if category == "waiting":
        return f"[{colors.muted}]⧗[/]"
    if category == "done":
        return f"[{colors.ready}]✓[/]"
    return ""
```

- [ ] **Step 4: Run to verify it passes**

Run: `uv run pytest -q tests/test_attention.py`
Expected: all pass

- [ ] **Step 5: Commit**

```bash
uv run ruff format src/aegis/attention.py
git add src/aegis/attention.py tests/test_attention.py
git commit -F - -- src/aegis/attention.py tests/test_attention.py <<'EOF'
feat(attention): the categories, their precedence and their marks

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
EOF
```

---

### Task 2: The recap schema and `RecapNote` carry the category

**Files:**
- Modify: `src/aegis/recap/__init__.py` (`TurnRecap`, `Recap`, `_TURN_SYSTEM`, `_one`)
- Modify: `src/aegis/events.py` (`RecapNote`)
- Modify: `src/aegis/state/event_codec.py` (encode/decode `RecapNote`)
- Test: `tests/test_recap_attention_schema.py`

**Interfaces:**
- Consumes: `aegis.attention.CATEGORIES`
- Produces:
  - `TurnRecap.attention: Literal["needs_input", "error", "review", "waiting", "done"]`
  - `Recap.attention: str = ""`
  - `RecapNote(line: str, attention: str = "done")`

- [ ] **Step 1: Write the failing tests**

```python
"""The category rides the turn recap and survives in the log."""

from aegis.events import RecapNote
from aegis.recap import Recap, TurnRecap, recap_turn
from aegis.state.event_codec import decode_event, encode_event


class _Gen:
    def __init__(self, value):
        self.value, self.model, self.duration_ms, self.cost_usd = value, "haiku", 1, 0.001


class _Driver:
    def __init__(self, value):
        self._value = value
        self.system = None

    async def generate_detailed(self, agent, cwd, schema, system, *rest):
        self.system = system
        return _Gen(self._value)


async def test_the_model_category_reaches_the_recap():
    from aegis.digest.models import TurnFacts

    d = _Driver(TurnRecap(line="Asked which fields ship.", attention="needs_input"))
    got = await recap_turn(replay=[], facts=TurnFacts(), driver=d, agent=None, cwd="/tmp")
    assert got.ok and got.attention == "needs_input"
    assert "needs_input" in d.system


def test_the_schema_rejects_an_unknown_category():
    import pydantic
    import pytest

    with pytest.raises(pydantic.ValidationError):
        TurnRecap(line="x", attention="urgent")


def test_a_recap_without_a_category_is_empty_not_done():
    assert Recap(line="x", ok=True).attention == ""


def test_the_note_round_trips_its_category():
    ev = RecapNote(line="Wrote 3 tests.", attention="review")
    assert decode_event(encode_event(ev)) == ev


def test_an_old_note_without_a_category_decodes_as_done():
    assert decode_event({"t": "RecapNote", "line": "x"}) == RecapNote(line="x", attention="done")
```

`asyncio_mode = "auto"` is set in `pyproject.toml`, so the async test needs no marker. The field is required on purpose (a required field is what makes the model fill it), so the three `TurnRecap(line=...)` constructions in `tests/test_recap_generate.py` (lines 37, 47, 57) must gain `attention="done"` in Step 3.

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest -q tests/test_recap_attention_schema.py`
Expected: FAIL (`TurnRecap` has no `attention`, `RecapNote` has no `attention`)

- [ ] **Step 3: Implement**

In `src/aegis/recap/__init__.py`:

```python
from typing import Literal
```

```python
class TurnRecap(BaseModel):
    line: str = Field(
        description="ONE line, past tense, concrete. Name "
        "files and counts. No preamble."
    )
    attention: Literal["needs_input", "error", "review", "waiting", "done"] = Field(
        description="needs_input: the turn ended on a question or a decision "
        "for the operator. error: something failed. review: the turn presents "
        "something for the operator to read, without waiting on it. waiting: "
        "the agent waits on a monitor, a queue, a subagent or CI, not on the "
        "operator. done: it reports finished work and needs nothing."
    )
```

Add `attention: str = ""` to the `Recap` dataclass after `remaining`.

Append to `_TURN_SYSTEM` (keep the existing sentences):

```python
    " Also classify the turn's attention as one of needs_input, error, "
    "review, waiting, done. A question to the operator is needs_input even "
    "when the turn also landed work."
```

In `_one`, in the `Recap(...)` built on success, add `attention=getattr(v, "attention", ""),`.

In `src/aegis/events.py`, `RecapNote` becomes:

```python
@dataclass(frozen=True)
class RecapNote:
    """(existing docstring, plus:) ``attention`` is the turn's category
    (``aegis.attention``); records written before it existed read as done."""

    line: str
    attention: str = "done"
```

In `src/aegis/state/event_codec.py`:

```python
    if isinstance(ev, RecapNote):
        return {"t": "RecapNote", "line": ev.line, "attention": ev.attention}
```

```python
    if t == "RecapNote":
        return RecapNote(line=d["line"], attention=d.get("attention", "done"))
```

Add `attention="done"` to the three `TurnRecap(line=...)` calls in `tests/test_recap_generate.py`.

- [ ] **Step 4: Run to verify it passes**

Run: `uv run pytest -q tests/test_recap_attention_schema.py tests/test_recap_generate.py tests/test_fleet_card_restore.py`
Expected: all pass

- [ ] **Step 5: Commit**

```bash
uv run ruff format src/aegis/recap/__init__.py src/aegis/events.py src/aegis/state/event_codec.py
git add tests/test_recap_attention_schema.py
git commit -F - -- src/aegis/recap/__init__.py src/aegis/events.py src/aegis/state/event_codec.py tests/test_recap_attention_schema.py tests/test_recap_generate.py <<'EOF'
feat(recap): the turn recap classifies the turn's attention

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
EOF
```

---

### Task 3: The session resolves, stores and persists the category

**Files:**
- Modify: `src/aegis/core/session.py` (`__init__`, `_run_turn`, `_run_recap`, `_persist_recap`, `rehydrate_card`)
- Modify: `src/aegis/core/manager.py` (`_sync_spawn`, new `waits_on`)
- Test: `tests/test_session_attention.py`

**Interfaces:**
- Consumes: `aegis.attention.resolve`, `Recap.attention`, `RecapNote.attention`, `aegis.fleet.snapshot._owed_callback(manager, handle)`
- Produces:
  - `AgentSession.attention: str` (`""` until a turn ends)
  - `AgentSession.attention_seq: int`
  - `AgentSession.wait_probe: Callable[[], bool] | None`
  - `AgentSession.effective_attention -> str` (property: `waiting` drops to `done` once the probe says the wait ended)
  - `SessionManager.waits_on(handle: str) -> bool`
  - `_run_recap(facts, *, draw: bool, errored: bool = False)`; the emitted `Recap` carries `attention`

- [ ] **Step 1: Write the failing tests**

```python
"""The session ends each turn with a category, from the model and the facts."""

import dataclasses

import pytest

from aegis.digest.models import TurnFacts
from aegis.events import RecapNote, Result
from aegis.recap import Recap
from aegis.state.session_log import replay_events


class _Harness:
    def __init__(self, error=False):
        self.error = error

    async def start(self): ...
    async def send(self, t): ...
    async def close(self): ...

    async def events(self):
        yield Result(duration_ms=1, is_error=self.error)


def _brain(tmp_path, harness, **spawn):
    from aegis.config import Agent
    from aegis.config.roots import AegisRoots

    from tests.brain import make_brain

    roster = {"opus": Agent(harness="claude-code", model="opus", effort="high", permission="auto")}
    mgr = make_brain(roster, "opus", make_session=lambda p, u, h, **kw: harness,
                     mcp=None, roots=AegisRoots.for_project(tmp_path))
    s = mgr._sync_spawn("opus", **spawn)
    s.recap_enabled = True
    return mgr, s


def _model_says(monkeypatch, attention, line="did a thing"):
    async def fake(**_kw):
        return Recap(line=line, attention=attention, ok=True)

    monkeypatch.setattr("aegis.core.session.recap_for", fake)


async def _turn(s, monkeypatch, facts=TurnFacts()):
    async def build(**_kw):
        return facts

    monkeypatch.setattr(s.digest, "build", build)
    await s.send_and_wait("go")
    if s._recap_task is not None:
        await s._recap_task


@pytest.mark.asyncio
async def test_the_model_category_lands_on_the_session(tmp_path, monkeypatch):
    _, s = _brain(tmp_path, _Harness())
    _model_says(monkeypatch, "needs_input")
    await _turn(s, monkeypatch)
    assert s.attention == "needs_input"
    assert s.attention_seq >= 1


@pytest.mark.asyncio
async def test_an_error_result_wins_over_the_model(tmp_path, monkeypatch):
    _, s = _brain(tmp_path, _Harness(error=True))
    _model_says(monkeypatch, "done")
    await _turn(s, monkeypatch)
    assert s.attention == "error"


@pytest.mark.asyncio
async def test_a_failed_recap_still_sets_the_hard_category(tmp_path, monkeypatch):
    _, s = _brain(tmp_path, _Harness(error=True))

    async def refused(**_kw):
        return Recap(error="no text_generation")

    monkeypatch.setattr("aegis.core.session.recap_for", refused)
    await _turn(s, monkeypatch)
    assert s.attention == "error"


@pytest.mark.asyncio
async def test_a_queue_worker_never_needs_the_operator(tmp_path, monkeypatch):
    from aegis.fleet.models import Origin

    _, s = _brain(tmp_path, _Harness(), origin=Origin(kind="queue", by="tasks"))
    _model_says(monkeypatch, "needs_input")
    await _turn(s, monkeypatch)
    assert s.attention == "done"


@pytest.mark.asyncio
async def test_a_live_wait_makes_it_waiting_until_the_wait_ends(tmp_path, monkeypatch):
    _, s = _brain(tmp_path, _Harness())
    waiting = [True]
    s.wait_probe = lambda: waiting[0]
    _model_says(monkeypatch, "done")
    await _turn(s, monkeypatch)
    assert s.attention == "waiting"
    assert s.effective_attention == "waiting"
    waiting[0] = False
    assert s.effective_attention == "done"


@pytest.mark.asyncio
async def test_a_non_done_recap_is_drawn_even_when_nothing_moved(tmp_path, monkeypatch):
    _, s = _brain(tmp_path, _Harness())
    drawn = []
    s.add_recap_observer(lambda _s, r: drawn.append(r))
    _model_says(monkeypatch, "needs_input")
    await _turn(s, monkeypatch)  # TurnFacts() did not move
    assert [r.attention for r in drawn] == ["needs_input"]


@pytest.mark.asyncio
async def test_a_done_recap_is_not_drawn_when_nothing_moved(tmp_path, monkeypatch):
    _, s = _brain(tmp_path, _Harness())
    drawn = []
    s.add_recap_observer(lambda _s, r: drawn.append(r))
    _model_says(monkeypatch, "done")
    await _turn(s, monkeypatch)
    assert drawn == []


@pytest.mark.asyncio
async def test_the_category_is_persisted_and_restored(tmp_path, monkeypatch):
    _, s = _brain(tmp_path, _Harness())
    _model_says(monkeypatch, "review")
    await _turn(s, monkeypatch)
    notes = [e for e in replay_events(s.state_dir, s.log_id).events if isinstance(e, RecapNote)]
    assert notes[-1].attention == "review"

    _, fresh = _brain(tmp_path, _Harness())
    fresh.rehydrate_card([Result(duration_ms=1, is_error=False), notes[-1]], [1.0, 2.0])
    assert fresh.attention == "review"
    assert fresh.attention_seq >= 1


def test_the_manager_probe_sees_a_live_monitor(tmp_path):
    mgr, s = _brain(tmp_path, _Harness())
    assert mgr.waits_on(s.handle) is False

    class _MM:
        def snapshot(self, *, for_handle=None):
            return [object()] if for_handle == s.handle else []

    mgr.monitor_manager = _MM()
    assert mgr.waits_on(s.handle) is True
    assert s.wait_probe() is True


def test_the_manager_probe_sees_a_working_child(tmp_path):
    from aegis.tui.state import AgentState

    mgr, s = _brain(tmp_path, _Harness())
    child = mgr._sync_spawn("opus", spawned_by=s.handle)
    child.state = AgentState.working
    assert mgr.waits_on(s.handle) is True
    child.state = AgentState.ready
    assert mgr.waits_on(s.handle) is False
```

Check `tests/test_fleet_card_restore.py` for the `_brain` fixture shape before running; if `mgr._sync_spawn` does not accept `origin`, use the `spawn` keyword it does accept (`grep -n 'def _sync_spawn' -A14 src/aegis/core/manager.py`).

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest -q tests/test_session_attention.py`
Expected: FAIL (`AgentSession` has no `attention`)

- [ ] **Step 3: Implement the session side**

In `AgentSession.__init__`, next to `self._card_rehydrated = False`:

```python
        # The last finished turn's attention category (aegis.attention),
        # and a counter a view compares with what it has shown.
        self.attention = ""
        self.attention_seq = 0
        # Set by SessionManager: does this session wait on a monitor, a
        # queue callback or a session it spawned? None when nothing wires it.
        self.wait_probe = None
```

New members (near `_maybe_recap`):

```python
    def _set_attention(self, category: str) -> None:
        self.attention = category
        self.attention_seq += 1

    def _waiting(self) -> bool:
        probe = self.wait_probe
        if probe is None:
            return False
        try:
            return bool(probe())
        except Exception:  # noqa: BLE001 — a probe must never take a turn
            log.exception("wait probe raised; treating as not waiting")
            return False

    @property
    def effective_attention(self) -> str:
        """``waiting`` holds only while the wait does."""
        if self.attention == "waiting" and not self._waiting():
            return "done"
        return self.attention
```

In `_run_turn`:
- Add `turn_errored = False` next to `saw_result = False`.
- In the `if isinstance(ev, Result):` branch add `turn_errored = bool(ev.is_error)`.
- In the hook-blocked branch, before `self._chain_if_pending()`, add `self._set_attention("error")`.
- In the harness-error `except Exception` branch, before `self._chain_if_pending()`, add `self._set_attention("error")`.
- After `if not saw_result:` sets the error state, add `turn_errored = True` inside that `if`.
- Replace `self._maybe_recap(facts)` with:

```python
        # The hard category first, so a refused or failed recap never leaves
        # the previous turn's category behind; the recap refines it.
        from aegis.attention import resolve

        self._set_attention(
            resolve(
                None,
                errored=turn_errored,
                ephemeral=self.origin.ephemeral,
                waiting=self._waiting(),
            )
        )
        self._maybe_recap(facts, errored=turn_errored)
```

`_maybe_recap(self, facts, *, errored: bool = False)` passes `errored=errored` to `_run_recap`.

`_run_recap` becomes:

```python
    async def _run_recap(self, facts, *, draw: bool, errored: bool = False) -> None:
        from dataclasses import replace

        from aegis.attention import resolve

        try:
            recap = await recap_for(
                state_dir=self.state_dir,
                log_id=self.log_id,
                facts=facts,
                agent=self.agent,
                agents=self._agents,
                cwd=str(self.project_root),
                session_scope=False,
                root=self._config_root,
            )
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001
            log.exception("recap failed")
            return
        if not recap.ok or not recap.line:
            return
        category = resolve(
            recap.attention,
            errored=errored,
            ephemeral=self.origin.ephemeral,
            waiting=self._waiting(),
        )
        # The identity guard: the same line with the same category is noise,
        # which is the #56346 failure arriving by another road.
        if (
            recap.line.strip() == self._last_recap_line.strip()
            and category == self.attention
        ):
            return
        self._set_attention(category)
        self._last_recap_line = recap.line
        self._persist_recap(recap.line, category)
        if draw or category != "done":
            self._emit_recap(replace(recap, attention=category))
```

`_persist_recap(self, line: str, attention: str)` writes `RecapNote(line=line, attention=attention)`.

In `rehydrate_card`, where a `RecapNote` is read, also remember `last_attention = ev.attention`; after the loop, where `self._last_recap_line = last_line` is set, add:

```python
            self._set_attention(last_attention)
```

- [ ] **Step 4: Implement the manager side**

In `SessionManager` (`src/aegis/core/manager.py`), add:

```python
    def waits_on(self, handle: str) -> bool:
        """Whether ``handle`` waits on something that is not the operator: a
        live monitor it armed, a queue callback owed to it, or a session it
        spawned that is still working. Read by the turn's attention."""
        from aegis.fleet.snapshot import _owed_callback
        from aegis.tui.state import AgentState

        mm = self.monitor_manager
        if mm is not None and mm.snapshot(for_handle=handle):
            return True
        if _owed_callback(self, handle):
            return True
        return any(
            getattr(s, "spawned_by", None) == handle
            and s.state is AgentState.working
            for s in self._sessions
        )
```

In `_sync_spawn`, after `s.forked_from = forked_from`:

```python
        # Read at turn end, and live while a turn's category is `waiting`.
        # Through the handle attribute, not `h`: a session can be renamed.
        s.wait_probe = lambda s=s: self.waits_on(s.handle)
```

- [ ] **Step 5: Run to verify it passes**

Run: `uv run pytest -q tests/test_session_attention.py tests/test_recap_gate.py tests/test_fleet_card_restore.py tests/test_session_generation_config.py`
Expected: all pass. If `test_recap_gate.py::test_an_identical_line_is_dropped` fails, its fake returns `attention=""` both times, so the category resolves to `done` twice and the guard still drops the second line; check that the first turn's hard `_set_attention` does not change `self.attention` away from `done` (it resolves to `done` too).

- [ ] **Step 6: Commit**

```bash
uv run ruff format src/aegis/core/session.py src/aegis/core/manager.py
git add tests/test_session_attention.py
git commit -F - -- src/aegis/core/session.py src/aegis/core/manager.py tests/test_session_attention.py <<'EOF'
feat(core): each turn ends with an attention category, persisted

The model's category is resolved against hard signals: an error Result,
an ephemeral origin, and a manager probe for live monitors, owed queue
callbacks and working spawned sessions. A non-done recap is drawn even
when the turn moved nothing.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
EOF
```

---

### Task 4: The recap block shows the category

**Files:**
- Modify: `src/aegis/render.py` (`_aside`, `render_recap`)
- Test: `tests/test_render_recap_attention.py`

**Interfaces:**
- Consumes: `aegis.attention.mark`, `LABELS`, `style_for`; `Recap.attention`
- Produces: `render_recap(recap, colors)` unchanged signature; `_aside(parts, colors, border: str | None = None)`

- [ ] **Step 1: Write the failing tests**

```python
from rich.console import Console

from aegis.recap import Recap
from aegis.render import render_recap
from aegis.tui.themes import INK, aegis_colors

C = aegis_colors(INK)


def _text(r) -> str:
    con = Console(record=True, width=100)
    con.print(r)
    return con.export_text()


def test_the_header_names_the_category():
    out = _text(render_recap(Recap(line="Asked which fields ship.", attention="needs_input", ok=True), C))
    assert "recap · ? needs you" in " ".join(out.split()) or "recap ·  ?  needs you" in out
    assert "Asked which fields ship." in out


def test_the_border_takes_the_category_colour():
    panel = render_recap(Recap(line="x", attention="error", ok=True), C)
    assert str(panel.border_style) == C.error


def test_a_recap_without_a_category_looks_as_today():
    panel = render_recap(Recap(line="x", ok=True), C)
    assert str(panel.border_style) == C.rule
    assert "·" not in _text(panel).splitlines()[1]
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest -q tests/test_render_recap_attention.py`
Expected: FAIL

- [ ] **Step 3: Implement**

`_aside(parts, colors, border: str | None = None)` passes `border_style=border or colors.rule`.

In `render_recap`, replace the header line with:

```python
    header = Text("recap", style=f"bold italic {tint}")
    border = None
    if recap.ok and recap.attention:
        from aegis.attention import LABELS, mark, style_for

        header.append(" · ", style=colors.muted)
        header.append_text(Text.from_markup(mark(recap.attention, colors)))
        header.append(
            f" {LABELS.get(recap.attention, recap.attention)}",
            style=style_for(recap.attention, colors),
        )
        if recap.attention != "done":
            border = style_for(recap.attention, colors)
    parts: list[RenderableType] = [header]
```

and return `_aside(parts, colors, border)`. Update the docstring's "never appended to the session log" sentence: the line and its category are now persisted as a `RecapNote`, which the recap window skips.

- [ ] **Step 4: Run to verify it passes**

Run: `uv run pytest -q tests/test_render_recap_attention.py tests/test_render_event.py`
Expected: all pass. Adjust the first assertion to the exact spacing `mark()` produces if needed; it must still check the mark and the label are both in the header line.

- [ ] **Step 5: Commit**

```bash
uv run ruff format src/aegis/render.py
git add tests/test_render_recap_attention.py
git commit -F - -- src/aegis/render.py tests/test_render_recap_attention.py <<'EOF'
feat(render): the recap block names the turn's attention

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
EOF
```

---

### Task 5: The tab bar and the F3 sidebar show pending categories

**Files:**
- Modify: `src/aegis/tui/widgets.py` (`_TabCell.render_tab`, `TabBar.set_tabs` placeholder tuple)
- Modify: `src/aegis/tui/app.py` (`_refresh_tabbar`, `_activate`, `_tick`)
- Modify: `src/aegis/tui/pane.py` (`ConversationPane.__init__`: `attention_acked`; `_sidebar_model`: `state_label`)
- Test: `tests/test_tabbar_attention.py`

**Interfaces:**
- Consumes: `AgentSession.effective_attention`, `attention_seq`; `aegis.attention.is_pending`, `mark`, `LABELS`
- Produces:
  - tab item tuple grows to 8 fields: `(idx, handle, slug, state, unseen, active, suffix, attention_mark)` where `attention_mark` is Rich markup or `""`
  - `ConversationPane.attention_acked: int`
  - `AegisApp._blink_off: bool`, toggled every `_tick`

- [ ] **Step 1: Write the failing tests**

```python
"""The tab bar leads with the pending category instead of the ready dot."""

from aegis.tui.state import AgentState
from aegis.tui.themes import INK, aegis_colors
from aegis.tui.widgets import _TabCell

C = aegis_colors(INK)


def _label(state, attention_mark, active=False):
    cell = _TabCell(markup=True)
    seen = {}
    cell.update = lambda s: seen.setdefault("s", s)
    cell.render_tab(1, "h", "opus", state, False, active, None, attention_mark, C)
    return seen["s"]


def test_a_pending_category_replaces_the_ready_dot():
    out = _label(AgentState.ready, "[x]?[/]")
    assert out.lstrip().startswith("[x]?[/]")
    assert "●" not in out


def test_a_working_session_keeps_its_dot_whatever_the_category():
    out = _label(AgentState.working, "")
    assert "●" in out


def test_no_category_is_todays_tab():
    out = _label(AgentState.ready, "")
    assert "●" in out
```

Add a pilot test in the same file, modelled on `tests/test_tui.py` around line 230 (read it first), that:
1. builds the app the way `tests/test_tui.py` does,
2. sets the active pane's core `attention = "needs_input"`, `attention_seq = 1` on a second, inactive pane,
3. calls `app._refresh_tabbar()` and asserts that pane's item in `app.query_one(TabBar)._items` has a non-empty 8th field,
4. activates that pane with `app._activate(index)` and asserts the 8th field is now `""` and `pane.attention_acked == 1`.

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest -q tests/test_tabbar_attention.py`
Expected: FAIL (`render_tab` takes 8 positional arguments)

- [ ] **Step 3: Implement**

`_TabCell.render_tab(self, idx, handle, slug, state, unseen, active, suffix, attention_mark, colors)`:

```python
        lead = attention_mark if attention_mark and state is not AgentState.working else state.dot(colors)
        label = (
            f"{lead} {idx} {handle} "
            f"[{colors.accent}]·{slug}·[/]{sfx}{mark}"
        )
```

(`AgentState` is already imported in `widgets.py`; the `set_tabs` placeholder tuple at line 319 gains a trailing `""`).

In `ConversationPane.__init__`, next to `self.unseen = False`: `self.attention_acked = 0`.

In `AegisApp`, initialise `self._blink_off = False` where `_last_bell` is initialised. In `_tick`, at the top: `self._blink_off = not self._blink_off` and, if any pane's item would blink, call `self._refresh_tabbar()` (always calling it is acceptable: `TabBar._refresh_cells` repaints only changed items).

In `_refresh_tabbar`, build each item with:

```python
def _attention_mark(p, active: bool, colors, blink_off: bool) -> str:
    from aegis.attention import is_pending, mark

    core = getattr(p, "_core", None)
    category = getattr(core, "effective_attention", "") or ""
    seq = getattr(core, "attention_seq", 0)
    if active and hasattr(p, "attention_acked"):
        p.attention_acked = seq  # the tab in front is seen
    acked = getattr(p, "attention_acked", seq)
    if not is_pending(category, seq, acked):
        return ""
    return mark(category, colors, blink_off=blink_off and not active)
```

as a module-level helper in `app.py`, and append `_attention_mark(p, p.id == cs.current, self._palette, self._blink_off)` as the 8th field.

In `_activate`, after `pane.unseen = False`:

```python
        if (core := getattr(pane, "_core", None)) is not None and hasattr(pane, "attention_acked"):
            pane.attention_acked = getattr(core, "attention_seq", 0)
```

In `ConversationPane._sidebar_model`, replace `state_label=core.state.label,` with:

```python
            state_label=self._state_label(core),
```

and add:

```python
    def _state_label(self, core) -> str:
        from aegis.attention import LABELS, is_pending

        label = core.state.label
        category = getattr(core, "effective_attention", "") or ""
        if core.state is AgentState.ready and is_pending(
            category, getattr(core, "attention_seq", 0), self.attention_acked
        ):
            label = f"{label} · {LABELS[category]}"
        return label
```

(`AgentState` is already imported in `pane.py` at line 48.) The sidebar is shown for the active tab, which is acked by `_refresh_tabbar`, so in practice this shows `waiting`; keep it anyway, it is what the spec asks for and a second view may not be acked.

Update the two `render_tab` fakes in `tests/test_tui.py` (around lines 230 and 252) to accept the extra argument.

- [ ] **Step 4: Run to verify it passes**

Run: `uv run pytest -q tests/test_tabbar_attention.py tests/test_tui.py tests/test_sidebar_render.py`
Expected: all pass

- [ ] **Step 5: Commit**

```bash
uv run ruff format src/aegis/tui/widgets.py src/aegis/tui/app.py src/aegis/tui/pane.py
git add tests/test_tabbar_attention.py
git commit -F - -- src/aegis/tui/widgets.py src/aegis/tui/app.py src/aegis/tui/pane.py tests/test_tabbar_attention.py tests/test_tui.py <<'EOF'
feat(tui): tabs lead with a pending attention mark, cleared when seen

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
EOF
```

---

### Task 6: Docs, gate, and the real screen

**Files:**
- Modify: `CHANGELOG.md` (Unreleased, Added)
- Modify: `docs/usage.md` (the "Recaps and the loop judge" section)
- Modify: `docs/superpowers/specs/2026-09-17-aegis-turn-attention-design.md` (status line)
- Create: `.playground/fleet-render/shoot_attention.py` (workspace playground, not committed)

- [ ] **Step 1: CHANGELOG entry** under `## [Unreleased]` / `### Added`:

```markdown
- **Each turn says whether it needs you.** The end-of-turn recap classifies
  the turn as needs you, error, review, waiting or done. The model proposes
  the category; an error result, a queue or workflow worker, and a live
  monitor, queue callback or spawned session decide over it. Tabs lead with
  the category's mark until you open them (`?` blinks), the recap block names
  it, and anything but done is drawn even when the turn changed no file. The
  category survives a restart.
```

- [ ] **Step 2: usage.md.** In "Recaps and the loop judge", after the bullet about the one-line recap, add a paragraph with the five categories, their marks, the precedence of the hard signals, and that opening the tab clears the mark in that view.

- [ ] **Step 3: Gate.** Run `make test` verbatim, as its own command:

Run: `make test`
Expected: rc 0. Then `make check`; `ty` must stay at its current count (327 before this plan).

- [ ] **Step 4: See it.** Write `.playground/fleet-render/shoot_attention.py`, modelled on `.playground/fleet-render/shoot_f3.py` (read it; it mounts the app in-process and fakes the quota query). Build three panes whose cores have `attention` set to `needs_input`, `error` and `review` with `attention_seq = 1`, one active; mount a recap block with `attention="needs_input"` in the active pane; save the SVG and PNG. Read the PNG and compare it with `attention-both-screens.html` screen 1: marks lead the inactive tabs, the active tab shows no pending mark, the recap header reads `recap · ? needs you` with an accent border.

- [ ] **Step 5: Status and commit.** Set the spec's status line to `Implemented 2026-09-17, per docs/superpowers/plans/2026-09-17-aegis-turn-attention.md.` and commit:

```bash
git commit -F - -- CHANGELOG.md docs/usage.md docs/superpowers/specs/2026-09-17-aegis-turn-attention-design.md docs/superpowers/plans/2026-09-17-aegis-turn-attention.md <<'EOF'
docs(attention): changelog, usage, and the spec marked implemented

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
EOF
git push
```
