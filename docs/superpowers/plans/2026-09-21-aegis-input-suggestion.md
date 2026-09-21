# Suggested reply + operator block — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** The input box offers the operator's likely next message as dim
placeholder text, completed on Tab; and the operator's own message lands in the
transcript as a Markdown panel instead of a single tinted line.

**Architecture:** The suggestion rides the existing end-of-turn recap as a fifth
field on `StandingRecap` — no second model call. It reaches the view over a new
`on_suggestion` observer that fires independently of the recap's draw gate,
because the turns that end on a question are exactly the turns whose recap is
not drawn. In the TUI it is the `GrowingInput` widget's `placeholder`, which
Textual already renders dim and already clears on the first keystroke.

**Tech Stack:** Python 3.13, pydantic, Textual 8.2.6, Rich, pytest, uv.

**Spec:** `docs/superpowers/specs/2026-09-21-aegis-input-suggestion-design.md`

## Global Constraints

- Python 3.13+, `uv` only, never pip.
- The gate is `make test` verbatim (`-n auto -m "not slow" --max-unmarked-duration=3`). Bare `pytest` skips the duration budget and goes green on a test that `make test` fails.
- Shared checkout: stage named paths, `git commit -- <paths>`, never `git add -A`, never `--amend`.
- Conventional commits, English, one logical change each.
- `suggestion` is one line, at most 12 words: a Textual placeholder renders on the first row and truncates.
- The recap's existing draw gate (`should_draw_recap`) does not change. Only a new, separate notification is added.
- No second model call. The suggestion is a field on the call that already fires every turn.
- The aegis web client is out of scope entirely; it is being retired.

---

### Task 1: The fifth field and the prompt

**Files:**
- Modify: `src/aegis/recap/__init__.py` (`StandingRecap`, `Recap`, `SYSTEM`, `_one`)
- Test: `tests/test_recap_suggestion.py` (create)

**Interfaces:**
- Produces: `StandingRecap.suggestion: str`; `Recap.suggestion: str` (default `""`); module constant `SUGGESTION_RULE: str` appended to `SYSTEM`.

- [ ] **Step 1: Write the failing test**

```python
"""The recap's fifth field: a draft of the operator's own next message."""

from aegis.recap import SYSTEM, Recap, StandingRecap


def test_schema_carries_a_suggestion_field():
    r = StandingRecap(
        task="t", outcome="o", next="n", attention="needs_input", suggestion="yes, do it"
    )
    assert r.suggestion == "yes, do it"


def test_suggestion_defaults_to_empty():
    r = StandingRecap(task="t", outcome="o", next="", attention="done")
    assert r.suggestion == ""


def test_recap_dataclass_carries_the_suggestion():
    assert Recap().suggestion == ""
    assert Recap(suggestion="ship it").suggestion == "ship it"


def test_prompt_tells_the_model_to_write_as_the_operator():
    # The rule that makes the field a draft reply rather than a fourth summary.
    assert "first person" in SYSTEM
    assert "12 words" in SYSTEM
    # Empty is the default answer; a wrong suggestion costs more than none.
    assert "Leave it empty" in SYSTEM
```

- [ ] **Step 2: Run it and watch it fail**

Run: `uv run pytest tests/test_recap_suggestion.py -q`
Expected: FAIL — `StandingRecap` has no `suggestion`, `Recap` has no `suggestion`, `SYSTEM` lacks the rule.

- [ ] **Step 3: Add the field to both models**

In `src/aegis/recap/__init__.py`, add to `StandingRecap` after `next`:

```python
    suggestion: str = Field(
        default="",
        description="A draft of the OPERATOR's own next message, written as "
        "if they typed it: first person, their language, their register. "
        "Empty when there is no obvious next message.",
    )
```

Add to the `Recap` dataclass, after `next: str = ""`:

```python
    # A draft of the operator's next message, for the input box. Empty is
    # the common case and the correct one.
    suggestion: str = ""
```

- [ ] **Step 4: Add the prompt rule**

In the same file, after the `SYSTEM` string, add:

```python
# The suggestion speaks in the operator's voice, which is the opposite of
# everything above it — so it is spelled out as its own paragraph rather
# than folded into SYSTEM's sentence about the other fields. Its only
# calibration material is the `user:` lines the window already carries.
SUGGESTION_RULE = (
    " SUGGESTION: `suggestion` is a draft of the operator's own next "
    "message, written as if they typed it — first person, their language, "
    "their register, their length. Copy how the `user:` lines in the window "
    "actually sound: if they are short and blunt, be short and blunt. "
    "Unlike the other fields it may name a file, a command or a number, "
    "because the operator's messages do. At most 12 words, one line. Leave "
    "it empty unless the next message is genuinely obvious: a question was "
    "asked, a choice was offered, work was presented for approval, or the "
    "session is one plain step from continuing. An empty suggestion is the "
    "correct answer most of the time."
)

SYSTEM = SYSTEM + SUGGESTION_RULE
```

- [ ] **Step 5: Carry the value out of `_one`**

In `_one`, the success return adds one line:

```python
    return Recap(
        line=v.outcome,
        task=v.task,
        next=v.next,
        suggestion=v.suggestion,
        attention=v.attention,
        ...
    )
```

- [ ] **Step 6: Run the test and the recap suite**

Run: `uv run pytest tests/test_recap_suggestion.py tests/test_recap_unified.py tests/test_recap_generate.py tests/test_recap_command.py -q`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add tests/test_recap_suggestion.py
git commit -F - -- src/aegis/recap/__init__.py tests/test_recap_suggestion.py <<'EOF'
feat(recap): a fifth field drafting the operator's next message

Rides the call that already fires every turn: its window already carries
the `user:` lines that are the only calibration material for "what would
the operator type".

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
EOF
```

---

### Task 2: The calibration probe

This task runs **before** any UI is built, because the prompt from Task 1 is a
starting point and is expected to move.

**Files:**
- Create: `/home/apiad/Workspace/.playground/reply-suggestion-probe/probe.py`
- Create (generated): `results.json`, `results.md` in the same directory

**Interfaces:**
- Consumes: `aegis.recap.recap_turn`, `Recap.suggestion` from Task 1.
- Produces: nothing importable. Its output is a reading.

- [ ] **Step 1: Write the probe**

The probe walks real session logs, finds every point where a turn ended and the
operator's next message followed, generates a recap from the prefix **only**,
and writes the suggestion beside what the operator actually typed.

```python
"""Does the fifth field draft what Alex would actually have typed?

Walks real logs. At each (turn ended -> operator typed next) boundary it
runs the SHIPPED recap_turn on the prefix only, then puts `suggestion`
beside the real next message. `task` and `outcome` ride along so
contamination of the other fields is visible rather than inferred.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

from aegis.btw import generation_agent
from aegis.config import load_config
from aegis.digest.models import TurnFacts
from aegis.drivers import get_driver
from aegis.events import Result, UserMessage
from aegis.recap import recap_turn
from aegis.state.session_log import replay_events

ROOT = Path("/home/apiad/Workspace")
STATE = ROOT / ".aegis" / "state"
OUT = Path(__file__).resolve().parent
MAX_CASES = 40

# Messages aegis itself put in the log: queue callbacks, handoffs, monitor
# wakes. They are not Alex typing, so they are not ground truth.
SUBSTRATE = ("> from ", "[queued]", "[handoff]", "[monitor]")


def boundaries(events):
    """(prefix_end_idx, real_next_text) for each turn-end -> operator-typed."""
    out = []
    for i, ev in enumerate(events):
        if not isinstance(ev, Result):
            continue
        for j in range(i + 1, len(events)):
            nxt = events[j]
            if isinstance(nxt, Result):
                break
            if isinstance(nxt, UserMessage):
                text = nxt.text.strip()
                if text and not text.startswith(SUBSTRATE) and not text.startswith("/"):
                    out.append((i + 1, text))
                break
    return out


def pick():
    cases = []
    logs = sorted(STATE.glob("sessions/2026-*.jsonl")) or sorted(
        STATE.glob("sessions/*.jsonl")
    )
    for p in reversed(logs):
        try:
            r = replay_events(STATE, p.stem)
        except Exception:
            continue
        for end, real in boundaries(r.events):
            cases.append((p.stem, end, real, r.events[:end]))
        if len(cases) >= MAX_CASES:
            break
    return cases[:MAX_CASES]


async def main():
    agents, _ = load_config(ROOT)
    agent, unset = generation_agent(None, agents, ROOT)
    assert not unset, "no text_generation profile configured"
    driver = get_driver(agent.harness)
    cases = pick()
    sem = asyncio.Semaphore(4)

    async def one(log, end, real, prefix):
        async with sem:
            rec = await recap_turn(
                replay=prefix,
                facts=TurnFacts(),
                driver=driver,
                agent=agent,
                cwd=str(ROOT),
            )
        return dict(
            log=log,
            at=end,
            real=real,
            suggestion=rec.suggestion,
            task=rec.task,
            outcome=rec.line,
            attention=rec.attention,
            cost=rec.cost_usd,
            error=rec.error,
        )

    rows = await asyncio.gather(*(one(*c) for c in cases))
    (OUT / "results.json").write_text(json.dumps(rows, ensure_ascii=False, indent=2))

    offered = [r for r in rows if r["suggestion"].strip()]
    total = sum(r["cost"] or 0 for r in rows)
    lines = [
        f"# Reply suggestion — {len(rows)} boundaries, "
        f"{len(offered)} offered a suggestion, ${total:.4f}\n",
    ]
    for r in rows:
        lines += [
            f"## {r['log']} @ {r['at']} · {r['attention']}",
            f"- SUGGESTED: {r['suggestion'] or '(none)'}",
            f"- REAL:      {r['real'][:300]}",
            f"- task:      {r['task']}",
            f"- outcome:   {r['outcome']}",
            *([f"- ERROR: {r['error']}"] if r["error"] else []),
            "",
        ]
    (OUT / "results.md").write_text("\n".join(lines))
    print(f"{len(rows)} boundaries · {len(offered)} suggested · ${total:.4f}")


asyncio.run(main())
```

- [ ] **Step 2: Sanity-check case selection without spending anything**

Run, from `repos/aegis`:

```bash
uv run python -c "
import sys; sys.path.insert(0, '/home/apiad/Workspace/.playground/reply-suggestion-probe')
from probe import pick
cs = pick()
print(len(cs), 'cases')
for log, end, real, prefix in cs[:5]:
    print('--', log, end, '|', real[:80].replace(chr(10), ' '))
"
```

Expected: a double-digit number of cases, and the `real` lines read like Alex
typing, not like queue notices. If they don't, widen `SUBSTRATE` and re-run.
This step costs nothing; do not skip it, because a probe that spent $0.60 on
queue callbacks is a probe that has to be run twice.

- [ ] **Step 3: Run the probe**

```bash
cd /home/apiad/Workspace/.playground/reply-suggestion-probe && uv run --project /home/apiad/Workspace/repos/aegis python probe.py
```

Expected: `N boundaries · M suggested · $0.xx`, and `results.md` on disk.

- [ ] **Step 4: Read the results and answer three questions in writing**

Append a `## Reading` section to `results.md` answering, in order:

1. **Would the operator have pressed Tab?** Judged by reading, not by a
   similarity score. A differently-worded suggestion can be right; a
   word-identical one can be wrong. Count them.
2. **How often did it offer something when the honest answer was none?** This
   is the failure that makes the feature annoying rather than merely useless.
3. **Did `outcome` drift back toward inventory style?** Compare against the
   `outcome` lines in `.playground/recap-abstract-probe/results-real.md`, which
   were generated by the same function before the fifth field existed.

- [ ] **Step 5: Tune the prompt if the reading says to, then re-run**

If either failure rate is unacceptable, edit `SUGGESTION_RULE` in
`src/aegis/recap/__init__.py` and repeat steps 3–4. Record each iteration as a
numbered section in `results.md` — the prompt that ships is whichever one the
reading justifies, and the record is how that choice stays auditable.

If `outcome` degraded, the spec's fallback applies: split `suggestion` into its
own call and pay the second $0.016. That is a change to Task 1 and this plan,
so stop and say so rather than improvising it.

- [ ] **Step 6: Commit the tuned prompt, if it changed**

```bash
git commit -F - -- src/aegis/recap/__init__.py <<'EOF'
fix(recap): tune the suggestion rule against real transcripts

Measured over N turn boundaries in .playground/reply-suggestion-probe/.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
EOF
```

The playground is gitignored, so `results.md` is not committed. Quote the
numbers in the commit message instead.

---

### Task 3: Persist it, and get it out of the session

**Files:**
- Modify: `src/aegis/events.py` (`RecapNote`)
- Modify: `src/aegis/core/session.py` (`__init__`, `_emit_state`, `add_suggestion_observer`, `remove_suggestion_observer`, `_emit_suggestion`, `_run_recap`, `_persist_recap`)
- Test: `tests/test_recap_suggestion_flow.py` (create)

**Interfaces:**
- Consumes: `Recap.suggestion` from Task 1.
- Produces: `RecapNote.suggestion: str = ""`; `AgentSession.suggestion: str`;
  `AgentSession.add_suggestion_observer(cb)` / `remove_suggestion_observer(cb)`,
  where `cb(session, suggestion: str)` — called with `""` to clear.

- [ ] **Step 1: Write the failing test**

```python
"""The suggestion reaches a view on every turn, including the quiet ones."""

import pytest

from aegis.events import RecapNote
from aegis.recap import Recap


def test_recapnote_carries_a_suggestion_and_old_records_decode():
    assert RecapNote(line="x").suggestion == ""
    assert RecapNote(line="x", suggestion="go on").suggestion == "go on"


@pytest.mark.asyncio
async def test_suggestion_emitted_even_when_the_recap_block_is_not_drawn(
    session_with_stub_recap,
):
    """A turn that moved nothing is exactly the turn that ended on a
    question, so its suggestion must still arrive."""
    seen = []
    s = session_with_stub_recap(
        Recap(line="asked about the gate", task="t", suggestion="one call", ok=True)
    )
    s.add_suggestion_observer(lambda _s, text: seen.append(text))
    await s.run_recap_for_test(draw=False)
    assert seen == ["one call"]


@pytest.mark.asyncio
async def test_a_new_turn_clears_the_suggestion(session_with_stub_recap):
    seen = []
    s = session_with_stub_recap(Recap(line="x", suggestion="one call", ok=True))
    s.add_suggestion_observer(lambda _s, text: seen.append(text))
    await s.run_recap_for_test(draw=False)
    s.start_turn_for_test()
    assert seen == ["one call", ""]
    assert s.suggestion == ""
```

The fixture `session_with_stub_recap` and the two `*_for_test` helpers do not
exist yet. Build them in `tests/conftest.py` following whatever the existing
recap tests already do to drive a session without a live harness — read
`tests/test_recap_unified.py` and `tests/conftest.py` first and reuse their
machinery rather than inventing a second way to fake a session. If those tests
patch `recap_for` directly, do that instead of adding a fixture, and rewrite
these three tests to match. **The test shape is negotiable; the three
behaviours are not.**

- [ ] **Step 2: Run it and watch it fail**

Run: `uv run pytest tests/test_recap_suggestion_flow.py -q`
Expected: FAIL — `RecapNote` has no `suggestion`, session has no observer API.

- [ ] **Step 3: Add the field to `RecapNote`**

In `src/aegis/events.py`, add after `next: str = ""`:

```python
    suggestion: str = ""
```

and extend the docstring's last sentence to name it alongside `task` and
`next`, since it has the same older-records-read-as-empty property.

- [ ] **Step 4: Add the observer list and the emit**

In `AgentSession.__init__`, beside `self._recap_observers`:

```python
        # The operator's drafted next message, for the input box. Separate
        # from the recap observers on purpose: the recap's draw gate keeps
        # a conversation of pure questions free of repeated blocks, and a
        # turn that moved nothing is exactly the turn that ended on a
        # question — so the suggestion must arrive where the block does not.
        self.suggestion = ""
        self._suggestion_observers: list = []
```

Methods beside `add_recap_observer`:

```python
    def add_suggestion_observer(self, cb) -> None:
        """Subscribe to the drafted next message. ``cb(session, text)``;
        ``text`` is ``""`` when the suggestion is cleared."""
        self._suggestion_observers.append(cb)

    def remove_suggestion_observer(self, cb) -> None:
        """Unsubscribe. Idempotent."""
        with contextlib.suppress(ValueError):
            self._suggestion_observers.remove(cb)

    def _emit_suggestion(self, text: str) -> None:
        if text == self.suggestion:
            return
        self.suggestion = text
        for cb in list(self._suggestion_observers):
            try:
                cb(self, text)
            except Exception:  # noqa: BLE001
                log.exception("suggestion observer raised")
```

- [ ] **Step 5: Clear it when a turn starts**

In `_emit_state`, inside the `if state is AgentState.working and was is not AgentState.working:` branch, after `self._fleet_last_started = None`:

```python
            # A suggestion drafted for the turn before last is worse than
            # none — the same reasoning that cancels an in-flight recap.
            self._emit_suggestion("")
```

- [ ] **Step 6: Fire it from `_run_recap`, and persist it**

In `_run_recap`, the early-return identity guard already returns before the
draw. Emit the suggestion **before** that guard, right after the `if not
recap.ok or not recap.line: return` line:

```python
        # Before the identity guard and before the draw gate: a repeated
        # recap line still carries a fresh suggestion, and a turn that
        # moved nothing is the one most likely to want one.
        if not resumed:
            self._emit_suggestion(recap.suggestion.strip())
```

Thread it through both `_persist_recap` call sites and the method itself:

```python
    def _persist_recap(
        self,
        line: str,
        attention: str,
        *,
        task: str = "",
        next: str = "",
        suggestion: str = "",
    ) -> None:
```

passing `suggestion=suggestion` into the `RecapNote(...)` construction, and
`suggestion=recap.suggestion` at both call sites.

- [ ] **Step 7: Run the tests**

Run: `uv run pytest tests/test_recap_suggestion_flow.py tests/test_recap_unified.py tests/test_session_recap_fields.py tests/test_recap_attention_schema.py -q`
Expected: PASS.

- [ ] **Step 8: Commit**

```bash
git add tests/test_recap_suggestion_flow.py
git commit -F - -- src/aegis/events.py src/aegis/core/session.py tests/test_recap_suggestion_flow.py tests/conftest.py <<'EOF'
feat(session): emit the drafted reply past the recap's draw gate

The draw gate keeps a conversation of pure questions free of repeated
transcript blocks, and it stays. But a turn that moved nothing is exactly
the turn that ended on a question, so the suggestion rides its own
observer and arrives regardless. A new turn clears it.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
EOF
```

---

### Task 4: Ghost text and Tab in the input widget

**Files:**
- Modify: `src/aegis/tui/widgets.py` (`GrowingInput`)
- Test: `tests/test_growing_input_suggestion.py` (create)

**Interfaces:**
- Produces: `GrowingInput.suggestion` property (get/set, `str`);
  `GrowingInput.PLACEHOLDER: str` class constant holding `"type a message…"`.

- [ ] **Step 1: Write the failing test**

```python
"""The suggestion is the widget's placeholder — dim, and gone on a keystroke."""

from aegis.tui.widgets import GrowingInput


def make() -> GrowingInput:
    return GrowingInput(placeholder=GrowingInput.PLACEHOLDER)


def test_setting_a_suggestion_becomes_the_placeholder():
    w = make()
    w.suggestion = "one call, fifth field"
    assert w.placeholder == "one call, fifth field"


def test_clearing_restores_the_default_placeholder():
    w = make()
    w.suggestion = "one call"
    w.suggestion = ""
    assert w.placeholder == GrowingInput.PLACEHOLDER


def test_a_suggestion_is_refused_while_the_box_has_text():
    """Nothing is inserted into the document, so a non-empty box must not
    have its hint replaced under the operator's cursor."""
    w = make()
    w.value = "half a sentence"
    w.suggestion = "one call"
    assert w.suggestion == ""
    assert w.placeholder == GrowingInput.PLACEHOLDER


def test_accept_fills_the_box_and_consumes_the_suggestion():
    w = make()
    w.suggestion = "one call, fifth field"
    assert w.accept_suggestion() is True
    assert w.value == "one call, fifth field"
    assert w.suggestion == ""
    assert w.placeholder == GrowingInput.PLACEHOLDER


def test_accept_is_a_no_op_with_nothing_to_accept():
    w = make()
    assert w.accept_suggestion() is False
    assert w.value == ""


def test_submitting_clears_a_stale_suggestion():
    w = make()
    w.suggestion = "one call"
    w._record_history("something else")
    assert w.suggestion == ""
```

- [ ] **Step 2: Run it and watch it fail**

Run: `uv run pytest tests/test_growing_input_suggestion.py -q`
Expected: FAIL — no `PLACEHOLDER`, no `suggestion`, no `accept_suggestion`.

- [ ] **Step 3: Implement it**

In `GrowingInput`, add the class constant beside `MAX_LINES`:

```python
    PLACEHOLDER = "type a message…"
```

In `__init__`, after `self.key_interceptor = None`:

```python
        # The drafted next message, shown as the placeholder so Textual's
        # own dimming and its clear-on-keystroke do the work. Nothing is
        # inserted into the document, so history recall, the palette's key
        # interceptor and the voice lock are all untouched.
        self._suggestion = ""
        self._base_placeholder = placeholder or self.PLACEHOLDER
```

Then:

```python
    @property
    def suggestion(self) -> str:
        return self._suggestion

    @suggestion.setter
    def suggestion(self, text: str) -> None:
        # Only into an empty box: the placeholder is invisible behind text,
        # so accepting one on Tab would replace what the operator was
        # halfway through typing.
        text = (text or "").strip() if not self.text.strip() else ""
        self._suggestion = text
        self.placeholder = text or self._base_placeholder

    def accept_suggestion(self) -> bool:
        """Fill the box with the suggestion. False when there was none."""
        text = self._suggestion
        if not text:
            return False
        self.suggestion = ""
        self.value = text
        self.move_cursor(self.document.end)
        return True
```

In `_record_history`, as the first statement of the body:

```python
        self.suggestion = ""
```

In `_on_key`, after the `key_interceptor` block and before the `enter` branch:

```python
        if event.key == "tab" and self._suggestion and not self.text.strip():
            event.stop()
            event.prevent_default()
            self.accept_suggestion()
            return
```

- [ ] **Step 4: Run the tests**

Run: `uv run pytest tests/test_growing_input_suggestion.py -q`
Expected: PASS.

- [ ] **Step 5: Prove the Tab interception cannot silently stop working**

The gate here is easy to write and worthless if it cannot fail. Temporarily
change the `_on_key` condition to `if False and ...`, run the suite, and
confirm a test goes red. Then put it back.

Run: `uv run pytest tests/test_growing_input_suggestion.py tests/test_pane_input_state_outline.py -q`
Expected: PASS after restoring.

If no test went red while the branch was disabled, the suite is not covering
the key path — add a test that drives `_on_key` with a `tab` event before
moving on.

- [ ] **Step 6: Commit**

```bash
git add tests/test_growing_input_suggestion.py
git commit -F - -- src/aegis/tui/widgets.py tests/test_growing_input_suggestion.py <<'EOF'
feat(tui): the drafted reply as dim placeholder, accepted on Tab

The placeholder is the whole mechanism: Textual already renders it dim
and already clears it on the first keystroke, so "dimmed in the box" and
"a keystroke overwrites it" cannot drift from the widget's own behaviour.
Nothing enters the document, so history recall and the palette keep
working without knowing it exists.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
EOF
```

---

### Task 5: Wire the pane to the session

**Files:**
- Modify: `src/aegis/tui/pane.py` (observer subscribe near line 973, release list near line 1245, new `_on_suggestion`)
- Test: `tests/test_pane_suggestion.py` (create)

**Interfaces:**
- Consumes: `AgentSession.add_suggestion_observer` (Task 3), `GrowingInput.suggestion` (Task 4).

- [ ] **Step 1: Write the failing test**

```python
"""The pane hands the session's drafted reply to its input box."""

from aegis.tui.widgets import GrowingInput


class _Input:
    def __init__(self):
        self.suggestion = ""


class _Pane:
    """Only the two collaborators _on_suggestion touches."""

    def __init__(self):
        self._inp = _Input()

    def input_widget(self):
        return self._inp


def test_on_suggestion_sets_the_widget(monkeypatch):
    from aegis.tui.pane import ConversationPane

    p = _Pane()
    ConversationPane._on_suggestion(p, None, "one call, fifth field")
    assert p._inp.suggestion == "one call, fifth field"


def test_on_suggestion_survives_a_pruned_pane():
    """The observer fires off Textual's dispatch; a torn-down pane must not
    raise into the session's emit loop."""
    from aegis.tui.pane import ConversationPane

    class _Gone:
        def input_widget(self):
            raise RuntimeError("pruned")

    ConversationPane._on_suggestion(_Gone(), None, "x")  # must not raise


def test_growing_input_is_the_real_target():
    # Guards the duck-typed test above against the property being renamed.
    assert isinstance(GrowingInput.suggestion, property)
```

- [ ] **Step 2: Run it and watch it fail**

Run: `uv run pytest tests/test_pane_suggestion.py -q`
Expected: FAIL — `ConversationPane` has no `_on_suggestion`.

- [ ] **Step 3: Implement**

In `pane.py`, beside the existing recap subscription (around line 973):

```python
        if hasattr(self._core, "add_suggestion_observer"):
            self._core.add_suggestion_observer(self._on_suggestion)
```

Add the handler beside `_on_recap`:

```python
    def _on_suggestion(self, _core, text: str) -> None:
        """Observer slot for the drafted next message. Best-effort: this
        runs off Textual's dispatch, so a pruned pane logs rather than
        raising into the session's emit loop."""
        try:
            self.input_widget().suggestion = text
        except Exception:  # noqa: BLE001
            import logging

            logging.getLogger(__name__).debug("no input box for the suggestion")
```

Add to the release list around line 1245:

```python
            ("remove_suggestion_observer", self._on_suggestion),
```

- [ ] **Step 4: Run the tests**

Run: `uv run pytest tests/test_pane_suggestion.py tests/test_pane_observer_release.py -q`
Expected: PASS. `test_pane_observer_release.py` is the one that proves a
detached view stops paying; if it asserts on an exact observer count, update it.

- [ ] **Step 5: Commit**

```bash
git add tests/test_pane_suggestion.py
git commit -F - -- src/aegis/tui/pane.py tests/test_pane_suggestion.py <<'EOF'
feat(tui): hand the session's drafted reply to the pane's input box

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
EOF
```

---

### Task 6: The operator's message as a panel

**Files:**
- Modify: `src/aegis/render.py` (`render_user_line` → `render_user_block`, `_USER_BOX`, `render_event`)
- Modify: `src/aegis/tui/pane.py:2340`, `src/aegis/tui/pane.py:2468`
- Test: `tests/test_render_event.py:392-415` (modify)

**Interfaces:**
- Produces: `render_user_block(text: str, colors, width: int | None = None) -> Panel`. `render_user_line` is removed, not deprecated — three call sites and two tests, all in this repo.

- [ ] **Step 1: Rewrite the two existing tests to describe a panel**

Replace `test_render_user_line_has_accent_prefix_and_bg` and
`test_render_user_line_no_width_not_padded` in `tests/test_render_event.py`
with:

```python
def test_render_user_block_is_a_panel_on_the_user_background():
    from rich.panel import Panel

    block = render_user_block("hello", C, width=40)
    assert isinstance(block, Panel)
    assert C.user_bg in str(block.style)


def test_render_user_block_renders_markdown():
    from rich.console import Console

    out = Console(width=40, record=True)
    out.print(render_user_block("- one\n- two", C))
    text = out.export_text()
    # A list renders as a list, not as two lines starting with a hyphen.
    assert "•" in text


def test_render_user_block_is_not_the_aside_surface():
    """An aside means 'in the transcript but not the conversation'. The
    operator's message IS the conversation and must not read as a /btw
    note."""
    block = render_user_block("hello", C)
    assert C.panel not in str(block.style)
```

Update the line-415 assertion (`as_text(render_user_line(...))`) to call
`render_user_block` and compare rendered text rather than a `Text` object.

- [ ] **Step 2: Run them and watch them fail**

Run: `uv run pytest tests/test_render_event.py -q -k user`
Expected: FAIL — `render_user_block` is not defined.

- [ ] **Step 3: Implement**

In `src/aegis/render.py`, beside `_ASIDE_BOX`:

```python
# A heavier left edge than the aside's, in the user's own accent. The two
# blocks share proportions so they read as siblings; they must not share a
# surface, because an aside means "not the conversation" and the
# operator's message is the conversation.
_USER_BOX = box.Box("    \n┃   \n    \n┃   \n    \n    \n┃   \n    \n")
```

Replace `render_user_line` with:

```python
def render_user_block(text: str, colors, width: int | None = None) -> Panel:
    """The operator's own message: an accent `›` header over a Markdown body.

    Markdown, because a prompt is prose and carries fenced blocks, lists and
    backticks. The cost is that `snake_case` loses its underscores and
    `**/*.py` turns bold; that trade was taken deliberately (see the
    2026-09-21 input-suggestion spec), so a mangled glob is a known cost,
    not a bug.

    ``width`` is accepted and ignored — the panel expands to its container,
    which is what the old single-line band had to fake with padding.
    """
    header = Text("›", style=f"bold {colors.user}")
    return Panel(
        Group(header, Markdown(text)),
        box=_USER_BOX,
        border_style=colors.user,
        style=f"on {colors.user_bg}",
        padding=(0, 1),
        expand=True,
    )
```

In `render_event`, change the `UserMessage` branch to call
`render_user_block(text, colors)`.

- [ ] **Step 4: Update the two pane call sites**

`pane.py:2340` and `pane.py:2468`: change `render_user_line` to
`render_user_block` (both already pass `width`, which is now ignored but
harmless — leave the argument, so the call sites stay uniform with the other
width-taking renderers). Update the import at `pane.py:43`.

- [ ] **Step 5: Run the render and pane suites**

Run: `uv run pytest tests/test_render_event.py tests/test_pane_replay.py tests/test_pane_hot_path.py tests/test_pane_reflow_cost.py -q`
Expected: PASS. `test_pane_reflow_cost.py` is the one that would catch a panel
costing more to lay out than a `Text` — if it fails, that is a real finding,
not a test to relax.

- [ ] **Step 6: Commit**

```bash
git commit -F - -- src/aegis/render.py src/aegis/tui/pane.py tests/test_render_event.py <<'EOF'
feat(render): the operator's message as a Markdown panel

It was the plainest block on screen: one tinted line whose band stopped
where the text stopped, while the recap — which is not the conversation —
got a panel and full Markdown. Same proportions as the recap, its own
colours and box, deliberately not the aside surface.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
EOF
```

---

### Task 7: See it working, then document it

**Files:**
- Modify: `CHANGELOG.md`
- Modify: `docs/usage.md` (the keys the operator presses)
- Modify: `docs/superpowers/specs/2026-09-21-aegis-input-suggestion-design.md` (status header)
- Modify: `docs/superpowers/plans/2026-09-21-aegis-input-suggestion.md` (check the boxes)

- [ ] **Step 1: Run the full gate**

Run: `make check`
Expected: green. Not `pytest` on its own — `make test` adds
`--max-unmarked-duration=3`, which a bare run skips.

- [ ] **Step 2: Exercise it the way a user reaches it**

Green tests against a daemon that booted before the change prove nothing.
Restart the daemon so it is running this code, open a session in the TUI, ask
it a question, and confirm with your own eyes:

1. the reply lands as a bordered panel with a `›` header, not a tinted line;
2. when the turn ends on a question, dim text appears in the empty input box;
3. Tab fills the box with it;
4. typing any other key makes it vanish;
5. starting a new turn clears a stale one.

Write down which of the five you saw. If a step could not be checked, say so
in those words rather than rounding up to "working".

- [ ] **Step 3: CHANGELOG entry**

Under the unreleased heading, in the repo's existing voice:

```markdown
- The input box drafts your next message. When a turn ends on a question or
  an obvious next step, the reply appears as dim text in the empty box; Tab
  accepts it, any other key overwrites it. It rides the end-of-turn recap as
  a fifth field, so it costs no extra call.
- Your own message renders as a Markdown panel in the transcript instead of
  a single tinted line — fenced blocks, lists and backticks now render.
```

- [ ] **Step 4: Document the key**

In `docs/usage.md`, wherever the input box's keys are listed (`enter`,
`alt+enter`, `shift+enter`), add `tab` — accepts the drafted reply when the
box is empty, focus-next otherwise.

- [ ] **Step 5: Flip the spec status and check this plan's boxes**

The spec header moves from `designed 2026-09-21, not yet implemented` to
`implemented <date>, per docs/superpowers/plans/2026-09-21-aegis-input-suggestion.md`,
in the same commit as the checked boxes. A stale status header misleads the
next `/workon`.

- [ ] **Step 6: Run `rift check`, then commit and push**

```bash
rift check
git commit -F - -- CHANGELOG.md docs/usage.md \
  docs/superpowers/specs/2026-09-21-aegis-input-suggestion-design.md \
  docs/superpowers/plans/2026-09-21-aegis-input-suggestion.md <<'EOF'
docs: the drafted reply and the operator's block

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
EOF
git push
```

---

## Self-review

**Spec coverage.** Fifth field and prompt → Task 1. Persistence and the split
emit gate → Task 3. Clear on turn start → Task 3 step 5. Placeholder and Tab →
Task 4. Pane wiring → Task 5. `render_user_block` → Task 6. Calibration probe
and its three questions → Task 2. The spec's "out of scope" items appear
nowhere, which is correct.

**Names used consistently across tasks.** `StandingRecap.suggestion`,
`Recap.suggestion`, `SUGGESTION_RULE`, `RecapNote.suggestion`,
`AgentSession.suggestion`, `add_suggestion_observer` /
`remove_suggestion_observer` / `_emit_suggestion`, `GrowingInput.suggestion` /
`accept_suggestion` / `PLACEHOLDER`, `ConversationPane._on_suggestion`,
`render_user_block`, `_USER_BOX`.

**Known soft spot.** Task 3's test fixture is written against machinery that
may not exist in `tests/conftest.py`. The task says so explicitly and names the
three behaviours as the fixed part. That is the one place an executor has to
read neighbouring tests before writing code.
