# Plan timing Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Each plan item carries the work time it took, the plan carries its work and idle totals, and the Fleet card and session sidebar show them with a segmented progress bar and an ETA; the `doing` spinner turns only while the agent works.

**Architecture:** A pure module, `transcript/plan_clock.py`, accrues time onto the standing at the few moments the clock changes. The fold calls it on a plan record, when a turn opens, when a turn closes, and when a turn dies without a result. The standing gains `clock` and a `work_s` per item, which `attention.card` passes to the browser. A pure browser module, `plantime.js`, adds the time running since `clock.at`, computes the pace and the ETA, and formats durations. `fleet.js` and `app.js` draw them and refresh them on the existing one-second timer.

**Tech Stack:** Python 3.13, pytest, Playwright (headless Chromium) for browser tests, vanilla ES modules in `src/aegis/client/js/`, plain CSS in `src/aegis/client/css/base.css`.

**Spec:** `docs/superpowers/specs/2026-10-09-plan-timing-design.md` (issue #228).

## Global Constraints

- The agent reports nothing new: `plan.update`'s params and the `plan` record's shape stay `{text, state}`.
- Work: time inside a turn, and the gap after a turn that ended without a `turn_end` report, without failing and without an interrupt. Idle: the gap after any other turn.
- A turn that ends with no `Result` (`exit`, `stop`, `server_stopped`, `reset`) counts as work up to the record before the closing one, and as idle from there.
- Items are matched by exact text. A plan that shares no text with the previous one starts its clock from zero.
- `standing["clock"]` is `{"work_s", "idle_s", "at", "running"}` with `running` in `"work"`, `"idle"`; `None` before the first plan.
- The ETA is `max(0, (plan work_s / done) × (items not done) − doing item's work_s)`, shown only when at least one item is done.
- Durations read `<1m`, `6m`, `1h12m`. No seconds.
- A meta from before this change (no `clock`, no `work_s`) shows no times and does not crash.
- The spinner of the `doing` item turns only while the session's attention is `working`; `prefers-reduced-motion` keeps it still.
- The tab strip does not change. Idle is not on the card.
- Work in the worktree `.claude/worktrees/plan-timing` on branch `feat/plan-timing`. Stage named paths only. Conventional commits ending with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.

## Review Focus

- A meta from before this change boots to a card with `plan_clock` `None` and items without `work_s`; the browser draws the bar and the count, but no times and no ETA. Pinned in Task 3 and Task 4.
- The browser's clock is a little behind the server's (`now < clock.at`): no negative or `NaN` durations. Pinned in Task 4.
- The agent resends an identical plan many times in a turn: the standing keeps its identity, so the meta is not rewritten. Pinned in Task 2.
- A plan with no item `doing` (between items): the plan's work grows and no item's does. Pinned in Task 2.
- aegis restarts mid-turn (`server_stopped`): work stops at the turn's last record and idle runs from there. Pinned in Task 2.

---

### Task 1: The plan clock

**Files:**
- Create: `src/aegis/transcript/plan_clock.py`
- Test: `tests/test_plan_clock.py`

**Interfaces:**
- Produces:
  - `accrue(plan: list[dict], clock: dict | None, ts: float) -> tuple[list[dict], dict | None]`
  - `replan(old: list[dict], clock: dict | None, new: list[dict], ts: float, running: str) -> tuple[list[dict], dict]`
  - `switch(plan: list[dict], clock: dict | None, ts: float, running: str) -> tuple[list[dict], dict | None]`
  - Each returns the same objects it was given when nothing changed, because the fold compares standing by identity before publishing.

- [ ] **Step 1: Write the failing tests**

```python
"""The plan clock: work and idle time accrued from record timestamps."""

from aegis.transcript.plan_clock import accrue, replan, switch


def items(*pairs, work=None):
    work = work or [0.0] * len(pairs)
    return [{"text": t, "state": s, "work_s": w} for (t, s), w in zip(pairs, work)]


def clock(work_s=0.0, idle_s=0.0, at=1000.0, running="work"):
    return {"work_s": work_s, "idle_s": idle_s, "at": at, "running": running}


def test_no_clock_is_no_time():
    p = items(("read", "doing"))
    assert accrue(p, None, 2000.0) == (p, None)
    assert switch(p, None, 2000.0, "idle") == (p, None)


def test_work_accrues_to_the_plan_and_the_doing_item():
    p, c = accrue(items(("read", "done"), ("fix", "doing")), clock(), 1060.0)
    assert [i["work_s"] for i in p] == [0.0, 60.0]
    assert c == clock(work_s=60.0, at=1060.0)


def test_idle_accrues_to_the_plan_only():
    p0 = items(("fix", "doing"))
    p, c = accrue(p0, clock(running="idle"), 1300.0)
    assert p == p0
    assert c == clock(idle_s=300.0, at=1300.0, running="idle")


def test_work_with_nothing_doing_accrues_to_the_plan_only():
    p0 = items(("read", "done"), ("fix", "pending"), work=[30.0, 0.0])
    p, c = accrue(p0, clock(work_s=30.0), 1010.0)
    assert [i["work_s"] for i in p] == [30.0, 0.0]
    assert c["work_s"] == 40.0


def test_a_timestamp_before_at_adds_nothing():
    p, c = accrue(items(("fix", "doing")), clock(at=1000.0), 990.0)
    assert c["work_s"] == 0.0 and p[0]["work_s"] == 0.0


def test_the_first_plan_starts_the_clock():
    p, c = replan([], None, [{"text": "read", "state": "doing"}], 1000.0, "work")
    assert p == items(("read", "doing"))
    assert c == clock(at=1000.0)


def test_a_new_plan_keeps_each_items_time_by_its_text():
    old = items(("read", "doing"), ("fix", "pending"))
    new = [
        {"text": "read", "state": "done"},
        {"text": "fix", "state": "doing"},
        {"text": "ship", "state": "pending"},
    ]
    p, c = replan(old, clock(), new, 1040.0, "work")
    assert [(i["text"], i["state"], i["work_s"]) for i in p] == [
        ("read", "done", 40.0),
        ("fix", "doing", 0.0),
        ("ship", "pending", 0.0),
    ]
    assert c == clock(work_s=40.0, at=1040.0)


def test_a_plan_sharing_no_text_starts_from_zero():
    old = items(("read", "done"), work=[500.0])
    p, c = replan(
        old, clock(work_s=500.0, idle_s=90.0), [{"text": "other", "state": "doing"}], 1100.0, "work"
    )
    assert p == items(("other", "doing"))
    assert c == clock(at=1100.0)


def test_the_same_plan_again_changes_nothing():
    old, c0 = items(("read", "doing"), work=[5.0]), clock(work_s=5.0)
    p, c = replan(old, c0, [{"text": "read", "state": "doing"}], 1900.0, "work")
    assert p is old and c is c0


def test_switch_accrues_then_runs_the_new_class():
    p, c = switch(items(("fix", "doing")), clock(), 1020.0, "idle")
    assert p[0]["work_s"] == 20.0
    assert c == clock(work_s=20.0, at=1020.0, running="idle")


def test_switch_to_what_already_runs_changes_nothing():
    p0, c0 = items(("fix", "doing")), clock()
    p, c = switch(p0, c0, 1020.0, "work")
    assert p is p0 and c is c0
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest -q tests/test_plan_clock.py`
Expected: FAIL with `ModuleNotFoundError: No module named 'aegis.transcript.plan_clock'`

- [ ] **Step 3: Write the module**

```python
"""How long a plan has taken: work and idle time, from the store's timestamps.

The fold calls these only where the clock changes: a plan record, a turn
opening, a turn closing. Between them the clock runs, and the browser adds
``now - at`` to whatever is running, so the standing, and the meta that
persists it, do not change on every line of a turn (spec
2026-10-09-plan-timing-design.md).

``clock`` is ``{"work_s", "idle_s", "at", "running"}``: the totals accrued up
to ``at``, and what has run since (``"work"`` or ``"idle"``). ``None`` means
no plan yet, and no time is kept. Every function returns the objects it was
given when nothing changed, because the fold compares standing by identity.
"""


def accrue(
    plan: list[dict], clock: dict | None, ts: float
) -> tuple[list[dict], dict | None]:
    """Add the time from ``clock["at"]`` to ``ts`` to whatever is running."""
    if clock is None or ts <= clock["at"]:
        return plan, clock
    dt = ts - clock["at"]
    if clock["running"] == "idle":
        return plan, {**clock, "idle_s": round(clock["idle_s"] + dt, 1), "at": ts}
    plan = [
        {**i, "work_s": round(i.get("work_s", 0.0) + dt, 1)}
        if i["state"] == "doing"
        else i
        for i in plan
    ]
    return plan, {**clock, "work_s": round(clock["work_s"] + dt, 1), "at": ts}


def switch(
    plan: list[dict], clock: dict | None, ts: float, running: str
) -> tuple[list[dict], dict | None]:
    """Accrue up to ``ts``, then run ``running`` from there."""
    if clock is None or clock["running"] == running:
        return plan, clock
    plan, clock = accrue(plan, clock, ts)
    return plan, {**clock, "running": running}


def replan(
    old: list[dict], clock: dict | None, new: list[dict], ts: float, running: str
) -> tuple[list[dict], dict]:
    """A plan record. Items keep their time by text; a plan that shares no
    text with the last one is a new plan, and its clock starts at zero."""
    if clock is not None and [(i["text"], i["state"]) for i in old] == [
        (i["text"], i["state"]) for i in new
    ]:
        return old, clock
    old, clock = accrue(old, clock, ts)
    times = {i["text"]: i.get("work_s", 0.0) for i in old}
    if clock is None or not any(i["text"] in times for i in new):
        clock = {"work_s": 0.0, "idle_s": 0.0, "at": ts, "running": running}
        times = {}
    items = [
        {"text": i["text"], "state": i["state"], "work_s": times.get(i["text"], 0.0)}
        for i in new
    ]
    return items, clock
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest -q tests/test_plan_clock.py`
Expected: PASS, 11 tests.

- [ ] **Step 5: Commit**

```bash
git add src/aegis/transcript/plan_clock.py tests/test_plan_clock.py
git commit -F - <<'EOF'
feat(plan): a clock that accrues work and idle time onto a plan (#228)

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
EOF
```

---

### Task 2: The fold runs the clock

**Files:**
- Modify: `src/aegis/transcript/entries.py` (`EMPTY_STANDING` near line 90; `Fold.__init__` near line 132; `Fold.apply` near line 191; `_end_turn` near line 246; the `send` branch of `_own` near line 351; the `plan` branch of `_own` near line 379; the `Echo` branch of `_event` near line 642; the `Result` branch near line 828)
- Modify: `tests/test_fold.py` (the `Rec` helper, `test_a_plan_record_sets_the_plan_and_did_tracks_the_last_item_finished`, new tests)
- Modify: `tests/test_session.py:319` and `tests/test_session.py:327` (plan equality now includes `work_s`)

**Interfaces:**
- Consumes: `accrue`, `replan`, `switch` from Task 1.
- Produces: `Fold.standing["clock"]` (`dict | None`) and `work_s` on each `Fold.standing["plan"]` item; `EMPTY_STANDING["clock"] is None`.

- [ ] **Step 1: Let `Rec` move time forward**

In `tests/test_fold.py`, change `Rec` so a test can open a gap. Existing tests keep their timestamps, because `skew` starts at 0:

```python
class Rec:
    """Builds store records the way a session writes them."""

    def __init__(self):
        self.records = []
        self.skew = 0.0

    def _add(self, **kw):
        r = {"i": len(self.records), "ts": 1000.0 + len(self.records) + self.skew, **kw}
        self.records.append(r)
        return r

    def wait(self, s: float) -> None:
        """The next record comes ``s`` seconds later than it otherwise would."""
        self.skew += s
```

- [ ] **Step 2: Write the failing tests**

Append to `tests/test_fold.py`. Each record is one second after the last, plus any `wait`; the comments give each record's `ts`.

```python
def times(f):
    c = f.standing["clock"]
    return [i["work_s"] for i in f.standing["plan"]], (c["work_s"], c["idle_s"], c["at"], c["running"])


def test_no_plan_keeps_no_clock():
    r = Rec()
    r.own("send", text="go")
    r.echo("go")
    r.result()
    f, _ = run(r)
    assert f.standing["clock"] is None


def test_a_wait_on_a_monitor_is_work_for_the_doing_item():
    r = Rec()
    r.own("send", text="go")  # 1000
    r.echo("go")  # 1001
    r.own("plan", items=plan(("read", "doing"), ("fix", "pending")))  # 1002
    r.wait(100)
    r.result()  # 1103: no turn_end, so the gap is work
    r.wait(50)
    r.own("send", text="> from monitor:m1 · done")  # 1154
    r.echo("> from monitor:m1 · done")  # 1155
    r.own("plan", items=plan(("read", "done"), ("fix", "doing")))  # 1156
    f, _ = run(r)
    assert times(f) == ([154.0, 0.0], (154.0, 0.0, 1156.0, "work"))


@pytest.mark.parametrize("close", ["turn_end", "failed", "interrupted"])
def test_a_turn_that_hands_back_to_the_person_starts_idle(close):
    r = Rec()
    r.own("send", text="go")  # 1000
    r.echo("go")  # 1001
    r.own("plan", items=plan(("read", "doing"),))  # 1002
    if close == "turn_end":
        r.own("turn_end", attention="needs_you", line="ok?", replies=[])
    elif close == "interrupted":
        r.own("interrupt")
    else:
        r.text("trying")
    if close == "failed":
        r.result(is_error=True, subtype="error_during_execution")  # 1004
    else:
        r.result()  # 1004
    r.wait(600)
    r.own("send", text="yes")  # 1605
    f, _ = run(r)
    assert times(f) == ([2.0], (2.0, 601.0, 1605.0, "work"))


def test_work_with_nothing_doing_counts_for_the_plan_only():
    r = Rec()
    r.own("send", text="go")  # 1000
    r.echo("go")  # 1001
    r.own("plan", items=plan(("read", "doing"), ("fix", "pending")))  # 1002
    r.own("plan", items=plan(("read", "done"), ("fix", "pending")))  # 1003
    r.wait(10)
    r.own("plan", items=plan(("read", "done"), ("fix", "doing")))  # 1014
    f, _ = run(r)
    assert times(f) == ([1.0, 0.0], (12.0, 0.0, 1014.0, "work"))


def test_a_plan_with_no_text_in_common_starts_from_zero():
    r = Rec()
    r.own("send", text="go")  # 1000
    r.echo("go")  # 1001
    r.own("plan", items=plan(("read", "doing"),))  # 1002
    r.wait(30)
    r.own("plan", items=plan(("other", "doing"),))  # 1033
    f, _ = run(r)
    assert times(f) == ([0.0], (0.0, 0.0, 1033.0, "work"))


def test_a_turn_with_no_result_works_until_its_last_record():
    r = Rec()
    r.own("send", text="go")  # 1000
    r.echo("go")  # 1001
    r.own("plan", items=plan(("read", "doing"),))  # 1002
    r.text("reading")  # 1003
    r.wait(500)
    r.own("server_stopped")  # 1504
    r.own("send", text="again")  # 1505
    f, _ = run(r)
    assert times(f) == ([1.0], (1.0, 502.0, 1505.0, "work"))


def test_the_clock_survives_a_refold():
    r = Rec()
    r.own("send", text="go")
    r.echo("go")
    r.own("plan", items=plan(("read", "doing"), ("fix", "pending")))
    r.own("turn_end", attention="done", line="read it", replies=[])
    r.result()
    r.wait(40)
    r.own("send", text="next")
    f, _ = run(r)
    again, _ = run(r)
    assert again.standing == f.standing
```

Add `import pytest` at the top of `tests/test_fold.py` if it is not there.

In `test_a_plan_record_sets_the_plan_and_did_tracks_the_last_item_finished`, the first assertion now meets a `work_s` key. Replace it with:

```python
    assert [(i["text"], i["state"]) for i in f.standing["plan"]] == [
        ("read", "doing"),
        ("fix", "pending"),
    ]
```

Keep the rest of that test: its last assertion, `f.standing is same`, is the identity pin from the Review Focus.

- [ ] **Step 3: Run the tests to verify they fail**

Run: `uv run pytest -q tests/test_fold.py -k "clock or monitor or hands_back or nothing_doing or no_text or no_result or refold or plan_record"`
Expected: FAIL with `KeyError: 'clock'`.

- [ ] **Step 4: Wire the clock into the fold**

In `src/aegis/transcript/entries.py`:

Import the clock next to the other relative imports at the top of the file:

```python
from .plan_clock import replan, switch
```

Add `clock` to `EMPTY_STANDING`:

```python
EMPTY_STANDING: dict = {
    "plan": [],
    "did": "",
    "report": None,
    "turn_error": "",
    "last_message": "",
    "clock": None,
}
```

In `Fold.__init__`, after `self._turn_open = False`:

```python
        # The ts of the last record applied and of the one before it: a turn
        # that dies without a result worked until its last record.
        self._ts: float | None = None
        self._prev_ts: float | None = None
```

In `Fold.apply`, right after `i, ts = record["i"], record.get("ts")`:

```python
        self._prev_ts, self._ts = self._ts, ts
```

Add a helper next to `_stand`:

```python
    def _clock(self, ts: float | None, running: str) -> None:
        """Run ``running`` on the plan's clock from ``ts`` (plan_clock.py)."""
        if ts is None:
            return
        plan, clock = switch(self.standing["plan"], self.standing["clock"], ts, running)
        self._stand(plan=plan, clock=clock)
```

In `_end_turn`, before `self._turn_open = False`:

```python
        if self._turn_open:
            self._clock(self._prev_ts, "idle")
```

In the `send` branch of `_own`, after `self._turn_open = True`:

```python
            self._clock(ts, "work")
```

Replace the `plan` branch of `_own` with:

```python
        if kind == "plan":
            new = list(rec.get("items") or [])
            items, clock = replan(
                self.standing["plan"],
                self.standing["clock"],
                new,
                ts if ts is not None else 0.0,
                "work" if self._turn_open else "idle",
            )
            self._stand(
                plan=items,
                clock=clock,
                did=_did(self.standing["plan"], items, self.standing["did"]),
            )
            return []
```

In the `Echo` branch of `_event`, after `self._turn_open = True`:

```python
            self._clock(ts, "work")
```

In the `Result` branch, after `self._stand(report=report, turn_error=error)`:

```python
            # A turn that handed back to the person starts idle; one that ended
            # to wait on a monitor or a queue task keeps working (spec
            # 2026-10-09-plan-timing-design.md).
            self._clock(ts, "idle" if report is not None or error or interrupted else "work")
```

- [ ] **Step 5: Update the session tests that compare the plan exactly**

In `tests/test_session.py`, lines 319 and 327 compare the plan to `[{"text": "read", "state": "doing"}]`. Compare text and state only:

```python
    assert [(i["text"], i["state"]) for i in s.standing["plan"]] == [("read", "doing")]
```

```python
    assert [(i["text"], i["state"]) for i in meta["standing"]["plan"]] == [("read", "doing")]
```

Keep the line that rebuilds a session from `meta["standing"]` and compares the standings as it is: it now also pins that `clock` survives the meta.

- [ ] **Step 6: Run the tests to verify they pass**

Run: `uv run pytest -q tests/test_fold.py tests/test_session.py tests/test_plan_clock.py`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add src/aegis/transcript/entries.py tests/test_fold.py tests/test_session.py
git commit -F - <<'EOF'
feat(plan): the fold times each plan item and the plan's work and idle (#228)

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
EOF
```

---

### Task 3: The card carries the clock

**Files:**
- Modify: `src/aegis/attention.py` (`card()`, near line 66)
- Modify: `tests/test_attention.py`, `tests/test_attention_e2e.py`, `tests/test_registry.py` (`test_boot_takes_the_standing_from_the_meta`, near line 98)

**Interfaces:**
- Consumes: `standing["clock"]` and `work_s` on items from Task 2.
- Produces: card field `plan_clock: dict | None`. `plan` items carry `work_s` when the fold made them.

- [ ] **Step 1: Write the failing tests**

In `tests/test_attention.py`:

```python
def test_the_card_carries_the_plans_clock_and_none_without_one():
    clock = {"work_s": 60.0, "idle_s": 5.0, "at": 1000.0, "running": "work"}
    assert a(st(clock=clock))["plan_clock"] == clock
    assert a(st())["plan_clock"] is None
    assert a({"plan": [], "did": ""})["plan_clock"] is None  # a meta from before
```

In `tests/test_attention_e2e.py`, extend `test_the_plan_reaches_the_card`:

```python
    assert c["plan_clock"]["running"] in ("work", "idle")
    assert all(isinstance(i["work_s"], float) for i in c["plan"])
```

In `tests/test_registry.py`, at the end of `test_boot_takes_the_standing_from_the_meta` (its meta's standing has no `clock` and its item no `work_s`, like a meta from before this change), add:

```python
    assert s.wire()["plan_clock"] is None
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest -q tests/test_attention.py tests/test_attention_e2e.py tests/test_registry.py -k "clock or plan_reaches or standing_from_the_meta"`
Expected: FAIL with `KeyError: 'plan_clock'`.

- [ ] **Step 3: Pass the clock through**

In `src/aegis/attention.py`, in the dict `card()` returns, after `"plan_total": len(plan),`:

```python
        # Work and idle so far (transcript/plan_clock.py); the browser adds the
        # time running since "at", and computes the pace and the ETA.
        "plan_clock": standing.get("clock"),
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest -q tests/test_attention.py tests/test_attention_e2e.py tests/test_registry.py`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/aegis/attention.py tests/test_attention.py tests/test_attention_e2e.py tests/test_registry.py
git commit -F - <<'EOF'
feat(plan): the card carries the plan's clock (#228)

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
EOF
```

---

### Task 4: Live times, pace and ETA in the browser

**Files:**
- Create: `src/aegis/client/js/plantime.js`
- Test: `tests/test_browser.py` (new test near the plan tests, near line 1846)

**Interfaces:**
- Consumes: card fields `plan` (items with optional `work_s`), `plan_clock`.
- Produces:
  - `dur(s: number) -> string`: `"<1m"`, `"6m"`, `"1h12m"`.
  - `planTimes(m, now = Date.now() / 1000) -> {work, idle, items, left} | null`. `items` is each item's live work seconds, in plan order. `left` is the ETA in seconds, or `null` while no item is done. Returns `null` when `m.plan_clock` is missing.

- [ ] **Step 1: Write the failing test**

In `tests/test_browser.py`:

```python
def test_plan_times_add_the_running_clock_and_extrapolate_the_pace(server, page):
    page.goto(server.url)
    page.wait_for_selector("#a2[data-view=fleet]")
    got = page.evaluate(
        """async () => {
          const { dur, planTimes } = await import('/static/js/plantime.js');
          const plan = [
            { text: "a", state: "done", work_s: 300 },
            { text: "b", state: "doing", work_s: 60 },
            { text: "c", state: "pending", work_s: 0 },
          ];
          const at = 10000;
          const work = planTimes({ plan, plan_clock: { work_s: 400, idle_s: 50, at, running: "work" } }, at + 120);
          const idle = planTimes({ plan, plan_clock: { work_s: 400, idle_s: 50, at, running: "idle" } }, at + 120);
          const skew = planTimes({ plan, plan_clock: { work_s: 400, idle_s: 50, at, running: "work" } }, at - 30);
          const none = planTimes({ plan: [{ text: "a", state: "doing" }], plan_clock: { work_s: 0, idle_s: 0, at, running: "work" } }, at + 10);
          return {
            durs: [dur(0), dur(59), dur(60), dur(359), dur(3600), dur(4320)],
            work, idle, skew, none,
            old: planTimes({ plan }),
          };
        }"""
    )
    assert got["durs"] == ["<1m", "<1m", "1m", "5m", "1h0m", "1h12m"]
    # Working: 120 s more on the plan and on "b". Pace 520/1 = 520 s; 2 not done;
    # minus b's 180 s: 860 s left.
    assert got["work"] == {"work": 520, "idle": 50, "items": [300, 180, 0], "left": 860}
    # Idle: the work clock stands still, so the ETA does not move: 400 × 2 − 60.
    assert got["idle"] == {"work": 400, "idle": 170, "items": [300, 60, 0], "left": 740}
    # The browser's clock behind the server's: nothing negative.
    assert got["skew"] == {"work": 400, "idle": 50, "items": [300, 60, 0], "left": 740}
    assert got["none"]["left"] is None and got["none"]["items"] == [10]
    assert got["old"] is None
    assert page.errors == []
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run pytest -q tests/test_browser.py -k plan_times`
Expected: FAIL: the dynamic import of `/static/js/plantime.js` returns 404.

- [ ] **Step 3: Write the module**

```javascript
// How long a plan has taken and how long it has left. The fold accrues time
// only at a plan record or a turn boundary (transcript/plan_clock.py), so the
// time running since `plan_clock.at` is added here, against the browser's clock.

export function dur(s) {
  const m = Math.floor(s / 60);
  if (m < 1) return "<1m";
  if (m < 60) return `${m}m`;
  return `${Math.floor(m / 60)}h${m % 60}m`;
}

export function planTimes(m, now = Date.now() / 1000) {
  const c = m.plan_clock;
  if (!c) return null;
  const plan = m.plan || [];
  const run = Math.max(0, now - c.at);
  const working = c.running === "work" ? run : 0;
  const work = c.work_s + working;
  const idle = c.idle_s + (c.running === "idle" ? run : 0);
  const items = plan.map((i) => (i.work_s || 0) + (i.state === "doing" ? working : 0));
  const done = plan.filter((i) => i.state === "done").length;
  const doing = plan.findIndex((i) => i.state === "doing");
  const left = done ? Math.max(0, (work / done) * (plan.length - done) - (doing < 0 ? 0 : items[doing])) : null;
  return { work, idle, items, left };
}
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `uv run pytest -q tests/test_browser.py -k plan_times`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/aegis/client/js/plantime.js tests/test_browser.py
git commit -F - <<'EOF'
feat(plan): live plan times, pace and ETA in the browser (#228)

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
EOF
```

---

### Task 5: The Fleet card draws the bar, the ETA and the current item's time

**Files:**
- Modify: `src/aegis/client/js/fleet.js` (`card()` near line 109 and line 126, `planRow` near line 134, new export `tickPlan`)
- Modify: `src/aegis/client/js/app.js` (the import from `./fleet.js` at line 14; the one-second `setInterval` near line 736)
- Modify: `src/aegis/client/css/base.css` (near line 481, `.card .ft .prog`)
- Test: `tests/test_browser.py`

**Interfaces:**
- Consumes: `dur`, `planTimes` from Task 4; card fields `plan`, `plan_done`, `plan_total`, `plan_now`, `attention`.
- Produces: `tickPlan(cardEl: Element, m: object) -> void` exported from `fleet.js`. The card's DOM: `.ft .prog` holds `.pbar` (one `<i>` per item, class `done`, `doing` or none; `.pbar.live` while working) and `.pt` (the text `4/8 · ~12m`); `.pl .now .t` holds ` · 6m`.

- [ ] **Step 1: Write the failing test**

In `tests/test_browser.py`:

```python
def test_the_card_shows_the_plan_bar_count_eta_and_the_current_items_time(server, page):
    page.goto(server.url)
    page.wait_for_selector("#a2[data-view=fleet]")
    sid = spawn(page, "hello")
    plan = [
        {"text": "read", "state": "done"},
        {"text": "fix", "state": "doing"},
        {"text": "ship", "state": "pending"},
    ]
    page.fill("#input", f"/mcp plan_update {json.dumps({'items': plan})}")
    page.press("#input", "Enter")
    turns_done(page, 2)
    page.click("#tab-fleet")
    card = f"#cards .card[data-id='{sid}']"
    page.wait_for_selector(f"{card} .ft .prog .pbar")
    assert page.eval_on_selector_all(f"{card} .pbar i", "is => is.map(i => i.className)") == [
        "done",
        "doing",
        "",
    ]
    text = "e => e.textContent"  # inner_text would trim the leading space
    assert page.eval_on_selector(f"{card} .ft .prog .pt", text) == "1/3 · ~<1m"
    assert page.eval_on_selector(f"{card} .pl .now .t", text) == " · <1m"
    # Not working: the doing segment holds still.
    assert page.eval_on_selector(f"{card} .pbar i.doing", "i => getComputedStyle(i).animationName") == "none"
    assert page.errors == []


def test_a_card_from_before_plan_times_draws_the_bar_without_times(server, page):
    page.goto(server.url)
    page.wait_for_selector("#a2[data-view=fleet]")
    got = page.evaluate(
        """async () => {
          const { tickPlan } = await import('/static/js/fleet.js');
          const c = document.createElement('div');
          c.innerHTML = '<div class="pl"><div class="now"><span class="t"></span></div></div><div class="ft"><span class="prog"><span class="pt"></span></span></div>';
          tickPlan(c, { plan: [{ text: 'a', state: 'done' }, { text: 'b', state: 'doing' }], plan_done: 1, plan_total: 2 });
          return [c.querySelector('.pt').textContent, c.querySelector('.t').textContent];
        }"""
    )
    assert got == ["1/2", ""]
    assert page.errors == []
```

The `sid` returned by `spawn()` is the key the card's `data-id` carries; the existing test near line 1840 uses the same selector shape.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest -q tests/test_browser.py -k "plan_bar or before_plan_times"`
Expected: FAIL: no `.pbar` on the card, and `tickPlan` is not exported.

- [ ] **Step 3: Draw the bar and the times**

In `src/aegis/client/js/fleet.js`, add to the imports:

```javascript
import { dur, planTimes } from "./plantime.js";
```

In `card()`, replace the plan block:

```javascript
  if (m.plan_now || m.plan_did) {
    const pl = el("div", "pl");
    if (m.plan_now) pl.append(planRow("now", m.plan_now, "now"));
    if (m.plan_did) pl.append(planRow("did", m.plan_did, ""));
    parts.push(pl);
  }
```

with:

```javascript
  if (m.plan_now || m.plan_did) {
    const pl = el("div", "pl");
    if (m.plan_now) pl.append(planRow("now", m.plan_now, "now", el("span", "t")));
    if (m.plan_did) pl.append(planRow("did", m.plan_did, ""));
    parts.push(pl);
  }
```

and replace the footer line

```javascript
  if (m.plan_total) ft.append(el("span", "prog", `plan ${m.plan_done}/${m.plan_total}`));
```

with:

```javascript
  if (m.plan_total) ft.append(planProg(m));
```

After `c.append(...parts);` and before the click listener, add:

```javascript
  tickPlan(c, m);
```

Replace `planRow` and add the two helpers after it:

```javascript
function planRow(key, text, cls, tail) {
  const d = el("div", cls);
  const body = el("span", null, text);
  if (tail) body.append(tail);
  d.append(el("span", "k", key), body);
  return d;
}

// One segment per item: done filled, doing pulsing while the agent works.
function planProg(m) {
  const bar = el("span", `pbar${m.attention === "working" ? " live" : ""}`);
  for (const i of m.plan || []) bar.append(el("i", i.state === "pending" ? "" : i.state));
  const prog = el("span", "prog");
  prog.append(bar, el("span", "pt"));
  return prog;
}

// The parts of a card that move with the clock: the count and ETA, and the
// current item's time. Called when the card is drawn and every second after.
export function tickPlan(c, m) {
  const t = planTimes(m);
  const pt = c.querySelector(".ft .prog .pt");
  if (pt) pt.textContent = `${m.plan_done}/${m.plan_total}${t && t.left != null ? ` · ~${dur(t.left)}` : ""}`;
  const now = c.querySelector(".pl .now .t");
  const doing = (m.plan || []).findIndex((i) => i.state === "doing");
  if (now) now.textContent = t && doing >= 0 ? ` · ${dur(t.items[doing])}` : "";
}
```

In `src/aegis/client/js/app.js`, add `tickPlan` to the import from `./fleet.js` on line 14, and in the one-second `setInterval`, inside the loop over `.card`:

```javascript
    if (m) {
      c.querySelector(".when").textContent = ago(m.last_activity);
      tickPlan(c, m);
    }
```

replacing the existing `if (m) c.querySelector(".when").textContent = ago(m.last_activity);`.

In `src/aegis/client/css/base.css`, after the `#a2 .card .ft .prog{color:var(--faint)}` line:

```css
#a2 .card .ft .prog{display:inline-flex;align-items:center;gap:6px}
#a2 .pbar{display:inline-flex;gap:1px;width:48px;height:6px}
#a2 .pbar i{flex:1;min-width:0;background:var(--rule);border-radius:1px}
#a2 .pbar i.done{background:var(--ok)}#a2 .pbar i.doing{background:var(--accent)}
#a2 .pbar.live i.doing{animation:a2pulse 1.4s ease-in-out infinite}
@media (prefers-reduced-motion:reduce){#a2 .pbar.live i.doing{animation:none}}
```

The `now` row's text cell already ellipsizes (`.card .pl div span:last-child`); the time sits inside that span, so a long item text cuts the time off with it. That is accepted: the full time is in the sidebar.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest -q tests/test_browser.py -k "plan_bar or before_plan_times or reply_pills or question_marks"`
Expected: PASS. The last two are existing tests that read the card; they must stay green.

- [ ] **Step 5: Look at it**

Start a server from the worktree, `uv run aegis serve --port 8765` in a scratch directory with its own `.aegis.yaml` (see the memory note: aegis config walks up to Workspace, so start it outside it), open it in a browser, give a session a plan and look at the card. Stop it by port: `fuser -k 8765/tcp`.

- [ ] **Step 6: Commit**

```bash
git add src/aegis/client/js/fleet.js src/aegis/client/js/app.js src/aegis/client/css/base.css tests/test_browser.py
git commit -F - <<'EOF'
feat(plan): the Fleet card shows a plan bar, the ETA and the current item's time (#228)

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
EOF
```

---

### Task 6: The sidebar shows totals and per-item times, and the spinner turns while working

**Files:**
- Modify: `src/aegis/client/index.html:125` (the plan heading gets an id)
- Modify: `src/aegis/client/js/app.js` (`renderMeta`, the plan block near line 684; the one-second `setInterval` near line 736)
- Modify: `src/aegis/client/css/base.css:494` and `:496`
- Test: `tests/test_browser.py`

**Interfaces:**
- Consumes: `dur`, `planTimes` from Task 4.
- Produces: `drawPlan(s)` in `app.js` (module-private). DOM: `#s-plan-h` reads `Plan 1/3 · <1m work · <1m idle · ~<1m left`; each `#s-plan > div` has a third `span.t` holding the item's time, empty for pending items; `#s-plan.live` while the session's attention is `working`.

- [ ] **Step 1: Write the failing tests**

In `tests/test_browser.py`:

```python
def test_the_sidebar_shows_plan_totals_and_each_items_time(server, page):
    page.goto(server.url)
    page.wait_for_selector("#a2[data-view=fleet]")
    spawn(page, "hello")
    plan = [
        {"text": "read", "state": "done"},
        {"text": "fix", "state": "doing"},
        {"text": "ship", "state": "pending"},
    ]
    page.fill("#input", f"/mcp plan_update {json.dumps({'items': plan})}")
    page.press("#input", "Enter")
    turns_done(page, 2)
    page.wait_for_selector("#s-plan-sec:not([hidden]) >> text=ship")
    assert page.inner_text("#s-plan-h") == "Plan 1/3 · <1m work · <1m idle · ~<1m left"
    assert page.eval_on_selector_all("#s-plan > div .t", "ts => ts.map(t => t.textContent)") == [
        "<1m",
        "<1m",
        "",
    ]
    assert page.errors == []


def test_the_doing_spinner_turns_only_while_the_agent_works(server, page):
    page.goto(server.url)
    page.wait_for_selector("#a2[data-view=fleet]")
    spawn(page, "hello")
    plan = [{"text": "read", "state": "done"}, {"text": "fix", "state": "doing"}]
    page.fill("#input", f"/mcp plan_update {json.dumps({'items': plan})}")
    page.press("#input", "Enter")
    turns_done(page, 2)
    spin = "#s-plan > div.doing svg.ic"
    page.wait_for_selector(spin)
    assert page.eval_on_selector(spin, "m => getComputedStyle(m).animationName") == "none"
    page.fill("#input", "/sleep 3")
    page.press("#input", "Enter")
    page.wait_for_selector("#s-plan.live")
    assert page.eval_on_selector(spin, "m => getComputedStyle(m).animationName") == "a2spin"
    turns_done(page, 3)
    page.wait_for_selector("#s-plan:not(.live)")
    assert page.eval_on_selector(spin, "m => getComputedStyle(m).animationName") == "none"
    assert page.errors == []
```

The existing `test_reply_pills_send_their_text_and_all_disappear` asserts the spinner is `none` after its turn ends; leave it as it is, it stays true.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest -q tests/test_browser.py -k "sidebar_shows_plan or spinner_turns"`
Expected: FAIL: no `#s-plan-h`, and the spinner is `none` while working.

- [ ] **Step 3: Draw the sidebar plan**

In `src/aegis/client/index.html` line 125, give the heading an id:

```html
      <div class="sec" id="s-plan-sec" hidden><h4 id="s-plan-h">Plan</h4><div class="plan" id="s-plan"></div></div>
```

In `src/aegis/client/js/app.js`, add `import { dur, planTimes } from "./plantime.js";` with the other imports, and replace the plan block of `renderMeta`:

```javascript
  const plan = s.plan || [];
  $("s-plan-sec").hidden = !plan.length;
  const mark = { done: "done", doing: "working", pending: "waiting" };
  $("s-plan").replaceChildren(
    ...plan.map((i) => {
      const d = document.createElement("div");
      d.className = i.state;
      d.append(glyph(mark[i.state] || "waiting"), span("", i.text));
      return d;
    }),
  );
```

with:

```javascript
  drawPlan(s);
```

and add the function after `renderMeta`:

```javascript
// The plan in the sidebar: totals in the heading, each item's time, and the
// doing spinner turning only while the agent works. Redrawn every second.
function drawPlan(s) {
  const plan = s.plan || [];
  $("s-plan-sec").hidden = !plan.length;
  const t = planTimes(s);
  const done = plan.filter((i) => i.state === "done").length;
  let head = `Plan ${done}/${plan.length}`;
  if (t) head += ` · ${dur(t.work)} work · ${dur(t.idle)} idle`;
  if (t && t.left != null) head += ` · ~${dur(t.left)} left`;
  $("s-plan-h").textContent = head;
  $("s-plan").classList.toggle("live", s.attention === "working");
  const mark = { done: "done", doing: "working", pending: "waiting" };
  $("s-plan").replaceChildren(
    ...plan.map((i, k) => {
      const d = document.createElement("div");
      d.className = i.state;
      d.append(glyph(mark[i.state] || "waiting"), span("", i.text), span("t", t && i.state !== "pending" ? dur(t.items[k]) : ""));
      return d;
    }),
  );
}
```

`span(cls, text)` is the helper `renderMeta` already uses; check its argument order where it is defined before relying on it.

In the one-second `setInterval`, add:

```javascript
  if (root.dataset.view === "session" && shown) {
    const s = sessions.get(shown);
    if (s && (s.plan || []).length) drawPlan(s);
  }
```

Replacing the children every second restarts the spinner's CSS animation from 0°. Check this in Step 5. If the spinner visibly jumps, update the `.t` cells and the heading in place instead of calling `drawPlan`, and keep `drawPlan` for `renderMeta` only.

In `src/aegis/client/css/base.css`, change line 494's grid to three columns and line 496's freeze to the non-live case:

```css
#a2 .plan div{display:grid;grid-template-columns:16px 1fr auto;gap:6px;color:var(--muted)}
```

```css
#a2 .plan .ic{width:12px;height:12px;margin-top:3px}#a2 .plan:not(.live) .ic.work{animation:none}
#a2 .plan .t{color:var(--faint);font-family:var(--font-chrome);font-size:11.5px}
```

The global `prefers-reduced-motion` rule at line 470 already stops every `.ic.work`.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest -q tests/test_browser.py -k "sidebar_shows_plan or spinner_turns or reply_pills"`
Expected: PASS.

- [ ] **Step 5: Look at it**

Against a server started from the worktree, as in Task 5: give a session a plan, send `/sleep 20` to the fake agent or let a real agent work, and watch the sidebar for a minute. The spinner must turn without jumping, and the item's time must reach `1m`.

- [ ] **Step 6: Commit**

```bash
git add src/aegis/client/index.html src/aegis/client/js/app.js src/aegis/client/css/base.css tests/test_browser.py
git commit -F - <<'EOF'
feat(plan): the sidebar shows plan totals and item times; the spinner turns while working (#228)

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
EOF
```

---

### Task 7: Docs, release note, gates and PR

**Files:**
- Modify: `DESIGN.md` (the paragraph "A session's attention is decided in Python", near line 88)
- Modify: `docs/superpowers/specs/2026-10-09-plan-timing-design.md` (status line)
- Create: `changelog.d/228-plan-timing.added.md`

- [ ] **Step 1: DESIGN.md**

After the sentence ending "No model reads a transcript to guess what a turn meant." add:

```markdown
The fold also times the plan from the records' `ts`: work is time inside a turn
and after a turn that did not hand back to the person, idle is the rest, and
each item gets the work done while it was `doing`. It accrues only at a plan
record and at turn boundaries (`transcript/plan_clock.py`); the browser adds the
time running since, so a turn's lines do not rewrite the meta.
```

- [ ] **Step 2: Release note**

`changelog.d/228-plan-timing.added.md`:

```markdown
- **The plan says how long each item took and how much work is left.** A
  Fleet card shows a segmented bar, the count and an estimate of the work
  left (`▰▰▰▰▱▱▱▱ 4/8 · ~12m`), and the current item's time on its `now`
  row. The sidebar adds the plan's work and idle time and each item's time,
  and the spinner on the item in progress turns only while the agent works.
  Waiting on a monitor or a queue task counts as work; waiting on you is idle
  and never enters the estimate. Everything comes from timestamps aegis
  already stored; the agent reports nothing new.
```

- [ ] **Step 3: Spec status**

In the spec, replace the status line with `**Status: implemented, 2026-10-09** (issue #228), following docs/superpowers/plans/2026-10-09-plan-timing.md.` (Use the date it lands.)

- [ ] **Step 4: Gates**

Run each and read its own exit code, never through a pipe:

```bash
uv sync
make format-check
make lint-docs
make changelog-check
make test
make bench
```

Expected: each exits 0. `make bench` prints a table; keep it for the PR body.

- [ ] **Step 5: Commit, push and open the PR**

```bash
git add DESIGN.md docs/superpowers/specs/2026-10-09-plan-timing-design.md changelog.d/228-plan-timing.added.md
git commit -F - <<'EOF'
docs(plan): plan timing in DESIGN.md, the spec's status and a release note (#228)

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
EOF
git push
gh pr create --title "The plan times each item and estimates the work left (#228)" --body-file <body>
```

The PR body says what was measured, the `make bench` table, the decision on how a gap is classified (by whether the turn handed back to the person) and what was left out (history-based ETA, ETA to the agent, reworded items). It ends with `🤖 Generated with [Claude Code](https://claude.com/claude-code)` and `Closes #228`.

- [ ] **Step 6: Alex's smoke test**

Tell Alex the branch is ready to try with `AEGIS_REF=feat/plan-timing aegis-dev`. Done means he has run it.
