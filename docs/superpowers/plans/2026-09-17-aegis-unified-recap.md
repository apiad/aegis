# Unified Recap Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** One recap schema (`task`, `outcome`, `next`, `attention`) and one abstract prompt for the turn recap, the mid-turn recap and `/recap`, differing only in window, shown in the transcript, `/recap`, F10 and F3.

**Architecture:** `aegis/recap/__init__.py` keeps its three entry points (`recap_turn`, `recap_in_flight`, `recap_session` and their `_for` resolvers) but they share one pydantic schema, one system prompt (plus a one-sentence mid-turn addition) and a `previous_task` hint. The `Recap` dataclass keeps `line` as the storage name of the outcome (persisted `RecapNote`s already use `line`) and gains `task` and `next`; `building`, `done`, `doing`, `remaining` go away. The session stores the last `task`/`next`, persists them in `RecapNote`, restores them, and passes the last `task` back as the hint.

**Tech Stack:** Python 3.13, pydantic, Rich, Textual, pytest (`make test`).

**Spec:** `docs/superpowers/specs/2026-09-17-aegis-unified-recap-design.md`

## Global Constraints

- Schema field descriptions and the system prompt are copied verbatim from the spec and Task 1.
- Windows: turn `dict(max_turns=3, budget_tokens=3_000, item_chars=300)`; mid-turn unchanged `dict(max_turns=2, budget_tokens=2_500, item_chars=240)`; session `dict(max_turns=8, budget_tokens=8_000, item_chars=300)`.
- `Recap.line` holds the outcome everywhere; no second outcome field.
- Language is not detected in code.
- Hermetic tests never reach the real `claude -p`; patch `aegis.core.session.recap_for` / `recap_in_flight_for` in any test that runs a turn with a roster.
- Shared checkout: `git commit -F - -- <paths>`, never `git add -A`, `--amend`, `git stash`; `uv run ruff format` only on `src/` files.
- A task runs only its own test files; the controller runs `make test`.

---

### Task 1: One schema, one prompt, three windows

**Files:**
- Modify: `src/aegis/recap/__init__.py`
- Modify: `tests/test_recap_generate.py`, `tests/test_recap_attention_schema.py`, `tests/test_fleet_recap.py`, `tests/test_session_attention.py` (parity test only)
- Test: `tests/test_recap_unified.py`

**Interfaces:**
- Produces:
  - `class StandingRecap(BaseModel)` with `task: str`, `outcome: str`, `next: str`, `attention: Literal["needs_input","error","review","waiting","done"]`
  - `Recap` dataclass fields: `line` (the outcome), `task`, `next`, `attention`, `header`, `model`, `duration_ms`, `cost_usd`, `ok`, `error`
  - `Recap.text` → `line` for a turn or mid-turn recap; for `/recap` callers, `Recap.block` → markdown list `- **task:** …\n- **outcome:** …\n- **next:** …` (skipping empty fields)
  - `recap_turn(*, replay, facts, driver, agent, cwd, previous_task: str = "")`, same `previous_task` keyword on `recap_session`, `recap_in_flight`, `recap_for`, `recap_in_flight_for`
  - constants `TURN_WINDOW`, `IN_FLIGHT_WINDOW`, `SESSION_WINDOW`, `SYSTEM`, `IN_FLIGHT_ADDENDUM`
  - `TurnRecap`, `SessionRecap`, `FleetRecap` are deleted

- [ ] **Step 1: Write the failing tests** (`tests/test_recap_unified.py`)

```python
"""One recap schema and one prompt; only the window changes."""

import pytest

from aegis.digest.models import TurnFacts
from aegis.recap import (
    IN_FLIGHT_WINDOW,
    SESSION_WINDOW,
    SYSTEM,
    TURN_WINDOW,
    Recap,
    StandingRecap,
    recap_in_flight,
    recap_session,
    recap_turn,
)

V = StandingRecap(task="Ship the Help view", outcome="Fixed the last rendering bugs.",
                  next="Final build.", attention="waiting")


class _Gen:
    def __init__(self, value):
        self.value, self.model, self.duration_ms, self.cost_usd = value, "haiku", 1, 0.01


class _Driver:
    def __init__(self):
        self.calls = []

    async def generate_detailed(self, agent, cwd, schema, system, *rest):
        self.calls.append((schema, system, rest))
        return _Gen(V)


@pytest.mark.parametrize("fn", [recap_turn, recap_session, recap_in_flight])
async def test_every_caller_uses_the_one_schema_and_prompt(fn):
    d = _Driver()
    got = await fn(replay=[], facts=TurnFacts(), driver=d, agent=None, cwd="/tmp")
    schema, system, _rest = d.calls[0]
    assert schema is StandingRecap
    assert system.startswith(SYSTEM)
    assert (got.task, got.line, got.next, got.attention) == (
        "Ship the Help view", "Fixed the last rendering bugs.", "Final build.", "waiting")


async def test_the_mid_turn_call_asks_for_the_present():
    d = _Driver()
    await recap_in_flight(replay=[], facts=TurnFacts(), driver=d, agent=None, cwd="/tmp")
    assert "still running" in d.calls[0][1]


async def test_the_previous_task_is_passed_as_a_hint():
    d = _Driver()
    await recap_turn(replay=[], facts=TurnFacts(), driver=d, agent=None, cwd="/tmp",
                     previous_task="Ship the Help view")
    assert any("Previous task: Ship the Help view" in part for part in d.calls[0][2])


async def test_no_previous_task_sends_no_hint():
    d = _Driver()
    await recap_turn(replay=[], facts=TurnFacts(), driver=d, agent=None, cwd="/tmp")
    assert not any("Previous task" in part for part in d.calls[0][2])


def test_the_prompt_forbids_the_inventory():
    for word in ("files", "commits", "hashes", "task ids", "test counts", "tool calls"):
        assert word in SYSTEM
    assert "Name files and counts" not in SYSTEM


def test_the_windows():
    assert TURN_WINDOW == dict(max_turns=3, budget_tokens=3_000, item_chars=300)
    assert IN_FLIGHT_WINDOW == dict(max_turns=2, budget_tokens=2_500, item_chars=240)
    assert SESSION_WINDOW == dict(max_turns=8, budget_tokens=8_000, item_chars=300)


def test_text_is_the_outcome_and_block_lists_the_three_fields():
    r = Recap(task="T", line="O", next="", ok=True)
    assert r.text == "O"
    assert r.block == "- **task:** T\n- **outcome:** O"
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest -q tests/test_recap_unified.py`
Expected: FAIL (`cannot import name 'StandingRecap'`)

- [ ] **Step 3: Implement.** Replace the three schemas, the three system prompts and the `Recap` fields in `src/aegis/recap/__init__.py`:

```python
TURN_WINDOW = dict(max_turns=3, budget_tokens=3_000, item_chars=300)
IN_FLIGHT_WINDOW = dict(max_turns=2, budget_tokens=2_500, item_chars=240)
SESSION_WINDOW = dict(max_turns=8, budget_tokens=8_000, item_chars=300)


class StandingRecap(BaseModel):
    task: str = Field(
        description="What the session is working toward, as a goal a person "
        "would name in one short phrase. Not a file, not a command."
    )
    outcome: str = Field(
        description="ONE sentence, at most 20 words: what was just solved, "
        "decided, delivered or learned, at the level of the goal. No file "
        "names, hashes, task ids, test counts or tool names unless that name "
        "is the subject itself."
    )
    next: str = Field(
        description="ONE short sentence: what is left, or what the session is "
        "waiting for. Empty if nothing."
    )
    attention: Literal["needs_input", "error", "review", "waiting", "done"] = Field(
        description="needs_input: the turn ended on a question or a decision "
        "for the operator. error: something failed. review: it presents "
        "something for the operator to read, without waiting on it. waiting: "
        "it waits on a monitor, a queue, a subagent or CI, not on the "
        "operator. done: it reports finished work and needs nothing."
    )


SYSTEM = (
    "You tell an operator, glancing at a dashboard, where a coding agent's "
    "session stands. Speak at the level of intent and outcome: the problem "
    "being solved, what got solved or decided, what is left. Never list "
    "files, commits, hashes, task ids, ports, test counts, finding counts or "
    "tool calls, and avoid numbers unless the number is the point (a budget "
    "that ran out, a deadline). The FACTS block is only a guard against "
    "claiming work that did not happen; do not report its contents. Prefer "
    "what actually happened over what the agent said it would do. "
    "LANGUAGE: write every field in the language of the operator's own "
    "messages (the lines marked as the user), even when the agent answers in "
    "another language. `task` names the goal, never a state like waiting. "
    "`outcome` is at most 20 words. No preamble, no praise. A question to the "
    "operator is needs_input even when the turn also landed work."
)

IN_FLIGHT_ADDENDUM = (
    " The turn is still running: `outcome` is what it is doing right now, "
    "in the present tense."
)
```

`Recap`: fields `line: str = ""` (comment: the outcome; named `line` because persisted `RecapNote`s already are), `task: str = ""`, `next: str = ""`, `attention: str = ""`, then `header`, `model`, `duration_ms`, `cost_usd`, `ok`, `error`. `text` returns `self.line`. New property:

```python
    @property
    def block(self) -> str:
        """`/recap`'s body: a markdown list, because Rich's Markdown joins
        single newlines into one paragraph."""
        return "\n".join(
            f"- **{name}:** {value}"
            for name, value in (("task", self.task), ("outcome", self.line), ("next", self.next))
            if value
        )
```

`_one(system, *, replay, facts, driver, agent, cwd, window_opts, previous_task="", on_driver=None)`: always passes `StandingRecap`; after the conversation and `render_facts(facts)` instructions, appends `f"Previous task: {previous_task} — keep it unless the goal changed."` when `previous_task` is not empty; maps `line=v.outcome, task=v.task, next=v.next, attention=v.attention`. `recap_turn` uses `SYSTEM` + `TURN_WINDOW`; `recap_session` uses `SYSTEM` + `SESSION_WINDOW`; `recap_in_flight` uses `SYSTEM + IN_FLIGHT_ADDENDUM` + `IN_FLIGHT_WINDOW`. `recap_for` and `recap_in_flight_for` accept `previous_task: str = ""` and forward it. Update the module docstring: one schema, one prompt, three windows, and why (spec).

- [ ] **Step 4: Update the existing tests** that construct the deleted schemas or read deleted fields: `tests/test_recap_generate.py` (use `StandingRecap(...)` and assert `line`/`task`/`next`), `tests/test_recap_attention_schema.py` (`TurnRecap` → `StandingRecap`, the prompt assertion now checks `SYSTEM`), `tests/test_fleet_recap.py` (`FleetRecap`/`done`/`doing` → `StandingRecap`/`line`), and the parity test in `tests/test_session_attention.py` (`TurnRecap` → `StandingRecap`). Keep each test's intent.

- [ ] **Step 5: Run**

Run: `uv run pytest -q tests/test_recap_unified.py tests/test_recap_generate.py tests/test_recap_attention_schema.py tests/test_fleet_recap.py tests/test_session_attention.py`
Expected: all pass

- [ ] **Step 6: Commit** — `feat(recap): one schema and one abstract prompt, three windows`

---

### Task 2: The session keeps task and next, and hands the task back

**Files:**
- Modify: `src/aegis/events.py` (`RecapNote`), `src/aegis/state/event_codec.py`
- Modify: `src/aegis/core/session.py` (`__init__`, `_run_recap`, `_persist_recap`, `rehydrate_card`, `_run_fleet_recap`)
- Modify: `src/aegis/core/manager.py` (`recap`), `src/aegis/tui/app.py` (`recap`)
- Test: `tests/test_session_recap_fields.py`; update `tests/test_fleet_card_restore.py`, `tests/test_fleet_recap_session.py` if they read removed fields

**Interfaces:**
- Consumes: Task 1 (`Recap.task`, `Recap.next`, `previous_task=`)
- Produces:
  - `RecapNote(line: str, attention: str = "done", task: str = "", next: str = "")`, codec round-trips all four, old records decode `task`/`next` as `""`
  - `AgentSession._last_recap_task: str`, `AgentSession._last_recap_next: str` (restored by `rehydrate_card`)
  - `_run_recap` and `_run_fleet_recap` pass `previous_task=self._last_recap_task`; `_run_recap` sets `_last_recap_task`/`_last_recap_next` from the recap and persists them; the identity guard compares `line`, `task` and `category`
  - `SessionManager.recap(handle, *, session_scope=True)` and `AegisApp.recap(...)` pass `previous_task` from the session (`getattr(s, "_last_recap_task", "")`)

- [ ] **Step 1: Write the failing tests** (`tests/test_session_recap_fields.py`): build a brain session the way `tests/test_session_attention.py::_brain` does; patch `aegis.core.session.recap_for` with a fake that records its kwargs and returns `Recap(line="Fixed it.", task="Ship Help", next="Build.", attention="done", ok=True)`; run one turn (`send_and_wait`, await `_recap_task`) and assert: `_last_recap_task == "Ship Help"`, `_last_recap_next == "Build."`, the log's last `RecapNote` has `task="Ship Help"` and `next="Build."`; run a second turn and assert the fake received `previous_task="Ship Help"`. A restore test: `rehydrate_card([Result(...), RecapNote(line="x", attention="done", task="T", next="N")], [1.0, 2.0])` sets `_last_recap_task == "T"` and `_last_recap_next == "N"`. A codec test: `decode_event({"t": "RecapNote", "line": "x"}) == RecapNote(line="x")`. A mid-turn test: patch `aegis.core.session.recap_in_flight_for` with a fake that records kwargs, set `_last_recap_task = "Ship Help"`, call `await s._run_fleet_recap()` and assert `previous_task == "Ship Help"` (build `digest.build` as `tests/test_fleet_recap_session.py` does).

- [ ] **Step 2: Run to verify it fails** — `uv run pytest -q tests/test_session_recap_fields.py`

- [ ] **Step 3: Implement** per Interfaces. In `rehydrate_card`, a stale note (a Result after it) restores neither task nor next.

- [ ] **Step 4: Run** — `uv run pytest -q tests/test_session_recap_fields.py tests/test_session_attention.py tests/test_fleet_card_restore.py tests/test_fleet_recap_session.py tests/test_recap_gate.py tests/test_session_generation_config.py tests/test_recap_command.py`

- [ ] **Step 5: Commit** — `feat(core): the session keeps the recap's task and next and hands the task back`

---

### Task 3: Every surface shows the new fields

**Files:**
- Modify: `src/aegis/render.py` (`render_recap`), `src/aegis/commands/builtins/core.py` (`_recap`)
- Modify: `src/aegis/fleet/models.py` (`CardView.task`, `CardView.next`), `src/aegis/fleet/snapshot.py` (`_card`), `src/aegis/fleet/render.py` (`render_detail`, `render_item`), `src/aegis/tui/pane.py` (`_sidebar_model` `now_line`)
- Test: `tests/test_recap_surfaces.py`; update `tests/test_recap_command.py`, `tests/test_render_recap_attention.py`, `tests/test_fleet_v2_panes.py`, `tests/test_fleet_watching.py` where they read removed fields

**Interfaces:**
- Consumes: Tasks 1–2
- Produces:
  - `render_recap(recap, colors, *, session: bool = False)`: body is `Markdown(recap.block)` when `session`, else `recap.text` followed by a muted `recap.task` line when `task` is set
  - `/recap` returns `CommandResult(True, recap.block, recap.footer, effect={"kind": "recap", "recap": asdict(recap), "session": True})`, and the pane's `/recap` effect branch (`src/aegis/tui/pane.py` ~1777, which builds `Recap(**eff["recap"])`) calls `_put_recap(recap, session=True)`; `ConversationPane._put_recap(recap, *, session: bool = False)` (~2455) passes `session` to `render_recap`, and the automatic recap keeps `session=False`
  - `CardView.task: str = ""`, `CardView.next: str = ""`, filled from `_last_recap_task`/`_last_recap_next`; `doing` now reads `fleet_recap.line`
  - `render_detail`: a `TASK` section before `NOW` (body `card.task`) and a `NEXT` section after `DID` (body `card.next`), each omitted when empty
  - F3 `now_line` reads `fleet_recap.line`

- [ ] **Step 1: Write the failing tests** (`tests/test_recap_surfaces.py`): the turn recap block contains the outcome and the task, the task on its own line after the outcome; `render_recap(..., session=True)` shows `task:`, `outcome:`, `next:` labels; `/recap` via the command handler (model it on `tests/test_recap_command.py`) returns `recap.block` as its text; `render_detail` of a card with `task` and `next` orders `TASK < NOW < DID < NEXT` and omits both when empty; `build_snapshot` of a session with `_last_recap_task`/`_last_recap_next` and `fleet_recap=Recap(line="Running the suite.", ok=True)` gives `task`, `next` and `doing == "Running the suite."`; the sidebar model's `now_line` reads `fleet_recap.line` (model it on `tests/test_fleet_watching.py::test_a_delivered_recap_refreshes_the_now_line`).

- [ ] **Step 2: Run to verify it fails** — `uv run pytest -q tests/test_recap_surfaces.py`

- [ ] **Step 3: Implement** per Interfaces.

- [ ] **Step 4: Run** — `uv run pytest -q tests/test_recap_surfaces.py tests/test_recap_command.py tests/test_render_recap_attention.py tests/test_fleet_v2_panes.py tests/test_fleet_watching.py tests/test_fleet_snapshot.py tests/test_sidebar_render.py`

- [ ] **Step 5: Commit** — `feat(recap): the transcript, /recap, F10 and F3 show task, outcome and next`

---

### Task 4: Docs, gate, and the recap seen live

- [ ] **Step 1:** CHANGELOG (Unreleased, Changed): the recap now says what the session is working on and what just got solved, not which files it touched; one format for the turn recap, the `now` line and `/recap`; `/recap` reads the last eight turns, not the whole conversation. `docs/usage.md` "Recaps and the loop judge" and the F10 detail paragraph: task/outcome/next, the windows.
- [ ] **Step 2:** `make test` verbatim; `make check` with `ty` at its pre-plan count.
- [ ] **Step 3:** Re-run `.playground/recap-abstract-probe/probe.py` rewritten to call the real `aegis.recap.recap_turn` and `recap_session` (not its own schema), on the same 20 cases plus 4 `/recap`; compare with `results.md`; the lines must stay at goal level (no file names, hashes, ids or counts) and `task` must stay stable within a session.
- [ ] **Step 4:** Spec status → implemented; commit docs; push.
