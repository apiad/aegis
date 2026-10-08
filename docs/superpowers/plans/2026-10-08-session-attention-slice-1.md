# Session attention, slice 1: status Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Every session carries one attention value (working, error, needs_you, waiting, review, done), shown as SVG badges on its tab, its Fleet card and the Fleet band, with the agent's own plan and turn report on the card and reply pills on the composer.

**Architecture:** The agent reports through two new agent operations, `plan.update` and `turn.end`, which append aegis records to the session's store. The fold turns those records, plus results, exits and sends, into a small `standing` dict that the session persists in its meta. A pure function in `attention.py` combines `standing` with live facts the registry holds (monitors, background tasks, held messages, queue tasks, child sessions) into the card fields. The browser only draws them.

**Tech Stack:** Python 3.13, pydantic, pytest (`make test`, browser tests with Playwright under `-m browser`), plain ES modules with no build step.

**Spec:** `docs/superpowers/specs/2026-10-08-session-attention-design.md`, slice 1 ("Status").

## Global Constraints

- Imports inside `src/aegis/` are relative; nothing imports `legacy/` (`tests/test_imports.py`).
- Nothing below the CLI calls `Path.cwd()` (`tests/test_no_cwd.py`).
- Boot reads meta files only, never stores (`tests/test_registry.py::test_boot_reads_no_store_when_every_meta_is_there`). Anything a card shows must be in the meta.
- Python decides, the browser draws: attention, labels' meaning, now/did, counts are computed in Python; the client maps a value to a symbol and a word.
- `plan.update`: whole list each call; `state` one of `pending`, `doing`, `done`; at most one `doing`; text cut to 120 characters; at most 30 items kept.
- `turn.end`: `attention` one of `needs_you`, `review`, `done`; `line` at most 140 characters; `replies` 0 to 3, each one line of at most 80 characters, refused otherwise.
- Attention precedence: working > error > needs_you > waiting > review > done. A queue worker's `needs_you` is `done`, and its replies are dropped.
- A person's own interrupt is not an error.
- Glyphs are inline SVG drawn in `currentColor`, the filled-badge style B; the knocked-out glyph uses `var(--knock)`, set to `var(--bg)`.
- Fleet order: "Needs you first" (default) or "Tab order", kept per browser in `localStorage` key `aegis.fleetOrder`.
- Reply pills sit on top of the message box; a click sends the text through `session.send`; all pills hide on any send.
- Stage named paths only, conventional commits, never amend (AGENTS.md). Work stays in `.claude/worktrees/session-attention` on branch `spec-session-attention`.
- Iterate on the test file each task touches. Leave the full suite to CI; run `make lint typecheck` and `rift check` before the PR.

## Review Focus

- A session that existed before this change boots with no `standing` in its meta: its card must read `done` with an empty plan, and nothing may crash. Pinned in Task 2.
- A turn the harness starts on its own (a background task waking Claude) and that ends without `turn_end` must not keep the previous turn's needs_you report. Pinned in Task 1, at the fold, because the fake claude cannot call `turn_end` and start a background task in one turn.
- A person pressing Esc ends a turn with an error result: the card must not turn red. Pinned in Task 1.
- An agent that calls `turn_end` twice in one turn: the last call wins. Pinned in Task 1.
- An agent that sends a reply over 80 characters, a reply with a newline, or four replies gets a refusal it can read, and the card is unchanged. Pinned in Task 4.

---

## File Structure

| File | Responsibility |
|---|---|
| `src/aegis/transcript/entries.py` (modify) | `Fold.standing`: plan, did, current report, turn error, derived from records |
| `src/aegis/session.py` (modify) | holds and persists `standing`, `report()`, publishes on change, `Host.status_changed`, `in_turn` |
| `src/aegis/attention.py` (create) | the pure rule: `card(standing, working=, worker=, waits=) -> dict` |
| `src/aegis/registry.py` (modify) | builds `standing` from the meta, `card()` adds attention fields, `_waits()`, `status_changed()` |
| `src/aegis/queues.py` (modify) | `cancel` refreshes the enqueuer's card |
| `src/aegis/agent_ops.py` (modify) | `plan.update` and `turn.end` operations |
| `src/aegis/mcp.py` (modify) | the primer paragraph |
| `src/aegis/client/js/glyphs.js` (create) | the SVG sprite, `glyph(kind)`, `LABEL` |
| `src/aegis/client/js/tabs.js` (modify) | a tab's mark from `attention` |
| `src/aegis/client/js/fleet.js` (modify) | card lines, groups, band counts by attention |
| `src/aegis/client/js/app.js` (modify) | order switch, sidebar plan and report, reply pills |
| `src/aegis/client/index.html` (modify) | order switch, sidebar plan and ask box, replies container |
| `src/aegis/client/css/base.css` (modify) | glyph, card, group, switch, plan, pill styles |
| `tests/test_fold.py`, `tests/test_session.py`, `tests/test_attention.py` (create), `tests/test_attention_e2e.py` (create), `tests/test_agents.py`, `tests/test_browser.py`, `tests/test_live.py` | tests |
| `DESIGN.md`, `changelog.d/171-session-attention.added.md`, the spec's status line | docs |

---

### Task 1: The fold derives `standing`

**Files:**
- Modify: `src/aegis/transcript/entries.py` (class `Fold`: `__init__`, `_own`, the `Result` branch of `_event`)
- Test: `tests/test_fold.py`

**Interfaces:**
- Produces: `EMPTY_STANDING: dict` (module constant) and `Fold.standing: dict` with keys `plan: list[dict]` (`{"text", "state"}`), `did: str`, `report: dict | None` (`{"attention", "line", "replies"}`), `turn_error: str`. `Fold.standing` is replaced by a new dict object whenever any key changes and is the same object otherwise, so a caller can compare identity.
- Consumes: store records `{"kind": "plan", "items": [...]}` and `{"kind": "turn_end", "attention", "line", "replies"}` (written by Task 4).

- [ ] **Step 1: Write the failing tests** (append to `tests/test_fold.py`)

```python
from aegis.transcript.entries import EMPTY_STANDING


def plan(*pairs):
    return [{"text": t, "state": s} for t, s in pairs]


def test_standing_starts_empty_and_is_the_same_object_until_it_changes():
    rec = Rec()
    rec.own("send", text="hi")
    rec.echo("hi")
    rec.text("hello")
    f, _ = run(rec)
    assert f.standing == EMPTY_STANDING


def test_a_plan_record_sets_the_plan_and_did_tracks_the_last_item_finished():
    rec = Rec()
    rec.own("plan", items=plan(("read", "doing"), ("fix", "pending")))
    f, _ = run(rec)
    assert f.standing["plan"] == plan(("read", "doing"), ("fix", "pending"))
    assert f.standing["did"] == ""
    before = f.standing
    f.apply(rec.own("plan", items=plan(("read", "done"), ("fix", "doing"))))
    assert f.standing is not before
    assert f.standing["did"] == "read"
    same = f.standing
    f.apply(rec.own("plan", items=plan(("read", "done"), ("fix", "doing"))))
    assert f.standing is same  # nothing changed, same object


def test_a_turn_end_lasts_until_the_next_send_or_a_later_turn_ends_without_one():
    rec = Rec()
    rec.own("send", text="go")
    rec.echo("go")
    rec.own("turn_end", attention="needs_you", line="Rebase or merge?", replies=["rebase"])
    rec.result()
    f, _ = run(rec)
    assert f.standing["report"] == {
        "attention": "needs_you",
        "line": "Rebase or merge?",
        "replies": ["rebase"],
    }
    # A turn Claude starts on its own and ends without turn_end drops it.
    f.apply(rec.text("the background task finished"))
    f.apply(rec.result())
    assert f.standing["report"] is None


def test_a_send_clears_the_report_at_once():
    rec = Rec()
    rec.own("turn_end", attention="review", line="Read the spec", replies=[])
    rec.result()
    f, _ = run(rec)
    assert f.standing["report"]["attention"] == "review"
    f.apply(rec.own("send", text="ok"))
    assert f.standing["report"] is None


def test_the_last_turn_end_of_a_turn_wins():
    rec = Rec()
    rec.own("turn_end", attention="done", line="first", replies=[])
    rec.own("turn_end", attention="needs_you", line="second", replies=[])
    rec.result()
    f, _ = run(rec)
    assert f.standing["report"]["line"] == "second"


def test_failures_set_turn_error_and_a_persons_interrupt_does_not():
    rec = Rec()
    rec.own("send", text="go")
    rec.result(is_error=True, subtype="error_during_execution")
    f, _ = run(rec)
    assert f.standing["turn_error"] == "turn failed (error_during_execution)"
    f.apply(rec.own("send", text="again"))
    assert f.standing["turn_error"] == ""
    f.apply(rec.own("interrupt"))
    f.apply(rec.result(is_error=True, subtype="error_during_execution"))
    assert f.standing["turn_error"] == ""
    f.apply(rec.own("exit", code=3, stderr_tail=[]))
    assert f.standing["turn_error"] == "claude exited with code 3"
    f.apply(rec.own("send", text="resume"))
    f.apply(rec.own("interrupt_timeout", after_s=10))
    assert f.standing["turn_error"] == "the interrupt went unanswered for 10s"
    f.apply(rec.result())
    assert f.standing["turn_error"] == ""
```

- [ ] **Step 2: Run them to see them fail**

Run: `uv run pytest -q tests/test_fold.py -k "standing or turn_end or report or turn_error"`
Expected: FAIL with `ImportError: cannot import name 'EMPTY_STANDING'`.

- [ ] **Step 3: Implement**

In `src/aegis/transcript/entries.py`, after `_entry`:

```python
# What a session stands on between turns, for its card: the agent's plan, the
# item it finished last, its report on the turn that ended (turn_end), and why
# that turn failed. Replaced as a whole when it changes, so a session compares
# identity to know whether to publish.
EMPTY_STANDING: dict = {"plan": [], "did": "", "report": None, "turn_error": ""}


def _did(old: list[dict], new: list[dict], prev: str) -> str:
    """The item finished most recently: one that became done in this update, or
    the previous one while it is still done, or the last done item."""
    was = {i["text"] for i in old if i["state"] == "done"}
    fresh = [i["text"] for i in new if i["state"] == "done" and i["text"] not in was]
    if fresh:
        return fresh[-1]
    done = [i["text"] for i in new if i["state"] == "done"]
    if prev in done:
        return prev
    return done[-1] if done else ""
```

In `Fold.__init__` add:

```python
        self.standing: dict = EMPTY_STANDING
        self._turns = 0  # results seen
        self._report_turn = -1  # self._turns when the current report was made
```

Add a method to `Fold` (under `# -- helpers`):

```python
    def _stand(self, **changes: Any) -> None:
        if any(self.standing.get(k) != v for k, v in changes.items()):
            self.standing = {**self.standing, **changes}
```

In `_own`, change the `send` branch so it clears the report and the error before it returns:

```python
        if kind == "send":
            self._stand(report=None, turn_error="")
            pid = f"pending:{i}"
            ...  # unchanged
```

In `_own`, before `if kind == "interrupt":` add:

```python
        if kind == "plan":
            items = list(rec.get("items") or [])
            self._stand(plan=items, did=_did(self.standing["plan"], items, self.standing["did"]))
            return []
        if kind == "turn_end":
            self._report_turn = self._turns
            self._stand(
                report={
                    "attention": rec.get("attention"),
                    "line": rec.get("line") or "",
                    "replies": list(rec.get("replies") or []),
                }
            )
            return []
```

In the `interrupt_timeout` branch, compute the summary once and record it:

```python
        if kind == "interrupt_timeout":
            line = f"the interrupt went unanswered for {rec.get('after_s', 10):g}s"
            self._stand(turn_error=line)
            return self._upsert(
                _entry(f"e{i}", "error", "err", ts, d.ERROR_GLYPH, summary=line)
            )
```

In the `exit` branch, before the `return`:

```python
            self._stand(turn_error=f"claude exited with code {rec.get('code')}")
```

In `_event`'s `Result` branch, right after `interrupted, self._interrupted = self._interrupted, False`:

```python
            self._turns += 1
            report = self.standing["report"]
            if report is not None and self._report_turn < self._turns - 1:
                report = None  # made in an earlier turn; this one ended without one
            error = ""
            if ev.is_error and not interrupted:
                error = f"turn failed ({ev.subtype or 'error'})"
            self._stand(report=report, turn_error=error)
```

- [ ] **Step 4: Run them to see them pass**

Run: `uv run pytest -q tests/test_fold.py`
Expected: all pass, including the existing fold tests.

- [ ] **Step 5: Commit**

```bash
git add src/aegis/transcript/entries.py tests/test_fold.py
git commit -m "feat(fold): a session's standing: plan, did, turn report, turn error (#171)"
```

---

### Task 2: The session holds, persists and publishes `standing`

**Files:**
- Modify: `src/aegis/session.py` (`_NOW`, `Host`, `Session.__init__`, `meta`, `wire`, `_record`, `_set`, `_on_line`, new `report`, new `in_turn`)
- Modify: `src/aegis/registry.py` (the `Session(...)` built from a meta, around line 124)
- Test: `tests/test_session.py`

**Interfaces:**
- Consumes: `Fold.standing`, `EMPTY_STANDING` (Task 1).
- Produces: `Session.standing: dict`; `Session.report(record: dict) -> None`; `Session.in_turn: bool` (status working or flushing held messages); `Host.status_changed(session) -> None` (no-op default); meta key `"standing"`. `Session.wire()` no longer carries `"standing"` itself; the registry's card adds the derived fields (Task 4).

- [ ] **Step 1: Write the failing tests** (append to `tests/test_session.py`, which already has the `Harness` class and a `fake_claude` fixture from conftest)

```python
async def test_a_report_is_published_at_once_and_survives_a_rebuild(tmp_path, fake_claude):
    h = Harness(tmp_path, fake_claude)
    s = h.session
    await s.start()
    s.report({"kind": "plan", "items": [{"text": "read", "state": "doing"}]})
    assert s.standing["plan"] == [{"text": "read", "state": "doing"}]
    last = [op["upsert"] for ch, ops in h.published if ch == "sessions" for op in ops][-1]
    assert "standing" not in last  # the card carries derived fields, not the dict
    await s.shutdown()
    meta = h.metas.read_all()[0][0]
    assert meta["standing"]["plan"] == [{"text": "read", "state": "doing"}]
    again = h.make(standing=meta["standing"])
    assert again.standing == meta["standing"]


async def test_a_session_from_an_old_meta_has_an_empty_standing(tmp_path, fake_claude):
    from aegis.transcript.entries import EMPTY_STANDING

    h = Harness(tmp_path, fake_claude)
    assert h.session.standing == EMPTY_STANDING


async def test_a_status_change_tells_the_host(tmp_path, fake_claude):
    from aegis.session import Host

    seen: list[str] = []

    class Spy(Host):
        def status_changed(self, session):
            seen.append(session.status)

    h = Harness(tmp_path, fake_claude, host=Spy())
    await h.session.start()
    await h.session.send("hello")
    await until(lambda: h.session.status == "idle" and "working" in seen, what="a turn")
    assert seen[:3] == ["idle", "working", "idle"]
    await h.session.shutdown()
```

- [ ] **Step 2: Run them to see them fail**

Run: `uv run pytest -q tests/test_session.py -k "report or old_meta or status_change"`
Expected: FAIL with `AttributeError: 'Session' object has no attribute 'report'` (and `standing`).

- [ ] **Step 3: Implement**

In `src/aegis/session.py`:

```python
from .transcript.entries import EMPTY_STANDING, Fold, fold_records
```

```python
_NOW = ("status", "handle", "title", "model_id", "standing")
```

In `class Host`, add:

```python
    def status_changed(self, session: "Session") -> None:
        """The session's status changed: a parent waiting on it re-derives."""
```

`Session.__init__` gains the keyword `standing: dict | None = None` (after `worker`) and sets:

```python
        # The fold's view of the agent's plan and last report; persisted so a
        # card at boot needs no store (DESIGN.md, boot reads meta files).
        self.standing: dict = standing or EMPTY_STANDING
```

In `meta()`, add `"standing": self.standing,` after `"worker"`. In `wire()`, after `m.pop("held")`, add `m.pop("standing")`.

Add, next to `busy`:

```python
    @property
    def in_turn(self) -> bool:
        """Mid-turn, or starting one to deliver held messages."""
        return self.status == "working" or self._flushing
```

Add, next to `record_file`:

```python
    def report(self, record: dict) -> None:
        """A plan or a turn report from the agent (agent_ops)."""
        self._record(record)
```

In `_record`, after `self._publish(self.channel, ops)`:

```python
        if fold.standing is not self.standing:
            self._set(standing=fold.standing)
```

In `_set`, after `self.last_status = self.status`:

```python
            self._host.status_changed(self)
```

(the line goes inside the existing `if "status" in changes:` block.)

In `_on_line`, track whether the open tasks changed and publish if so. Before the loop: `tasks = len(self.open_tasks)`. After `if changes: self._set(**changes)`:

```python
        if len(self.open_tasks) != tasks:
            self._publish_now()  # waiting on a background task shows on the card
```

In `src/aegis/registry.py`, in the `Session(...)` built from a meta, add after `worker=meta.get("worker"),`:

```python
            standing=meta.get("standing"),
```

- [ ] **Step 4: Run them to see them pass**

Run: `uv run pytest -q tests/test_session.py tests/test_registry.py`
Expected: all pass, including `test_boot_reads_no_store_when_every_meta_is_there`.

- [ ] **Step 5: Commit**

```bash
git add src/aegis/session.py src/aegis/registry.py tests/test_session.py
git commit -m "feat(session): hold, persist and publish the fold's standing (#171)"
```

---

### Task 3: The attention rule

**Files:**
- Create: `src/aegis/attention.py`
- Test: `tests/test_attention.py`

**Interfaces:**
- Consumes: the `standing` shape (Task 1).
- Produces: `card(standing: dict, *, working: bool, worker: bool, waits: list[str]) -> dict` returning exactly the keys `attention` (one of `working`, `error`, `needs_you`, `waiting`, `review`, `done`), `attention_line: str`, `replies: list[str]`, `waiting_on: str`, `plan: list[dict]`, `plan_now: str`, `plan_did: str`, `plan_done: int`, `plan_total: int`.

- [ ] **Step 1: Write the failing tests** (`tests/test_attention.py`)

```python
import pytest

from aegis.attention import card
from aegis.transcript.entries import EMPTY_STANDING


def st(**kw):
    return {**EMPTY_STANDING, **kw}


def rep(attention, line="a line", replies=()):
    return {"attention": attention, "line": line, "replies": list(replies)}


def a(standing, working=False, worker=False, waits=()):
    return card(standing, working=working, worker=worker, waits=list(waits))


@pytest.mark.parametrize(
    "standing, kw, want",
    [
        (st(report=rep("needs_you"), turn_error="boom"), {"working": True}, "working"),
        (st(report=rep("needs_you"), turn_error="boom"), {}, "error"),
        (st(report=rep("needs_you")), {"waits": ["1 monitor"]}, "needs_you"),
        (st(report=rep("review")), {"waits": ["1 monitor"]}, "waiting"),
        (st(report=rep("review")), {}, "review"),
        (st(report=rep("done")), {}, "done"),
        (st(), {}, "done"),
        (st(report=rep("needs_you")), {"worker": True}, "done"),
    ],
)
def test_the_first_rule_that_holds_wins(standing, kw, want):
    assert a(standing, **kw)["attention"] == want


def test_the_line_and_replies_follow_the_report():
    c = a(st(report=rep("needs_you", "Rebase or merge?", ["rebase", "merge"])))
    assert c["attention_line"] == "Rebase or merge?"
    assert c["replies"] == ["rebase", "merge"]
    assert a(st(report=rep("done", "Shipped", ["thanks"])))["attention_line"] == ""
    assert a(st(report=rep("done", "Shipped", ["thanks"])))["replies"] == ["thanks"]
    err = a(st(report=rep("needs_you", "q", ["x"]), turn_error="claude exited with code 3"))
    assert err["attention_line"] == "claude exited with code 3"
    assert err["replies"] == []
    assert a(st(report=rep("needs_you", "q", ["x"])), worker=True)["replies"] == []
    assert a(st(report=rep("needs_you", "q", ["x"])), working=True)["replies"] == []


def test_waiting_names_what_it_waits_on():
    c = a(st(), waits=["1 monitor", "2 background tasks"])
    assert c["waiting_on"] == "1 monitor, 2 background tasks"
    assert a(st(), working=True, waits=["1 monitor"])["waiting_on"] == ""


def test_the_plan_gives_now_did_and_counts():
    plan = [
        {"text": "read", "state": "done"},
        {"text": "fix", "state": "doing"},
        {"text": "ship", "state": "pending"},
    ]
    c = a(st(plan=plan, did="read"))
    assert (c["plan_now"], c["plan_did"], c["plan_done"], c["plan_total"]) == ("fix", "read", 1, 3)
    assert c["plan"] == plan
```

- [ ] **Step 2: Run them to see them fail**

Run: `uv run pytest -q tests/test_attention.py`
Expected: FAIL with `ModuleNotFoundError: No module named 'aegis.attention'`.

- [ ] **Step 3: Implement** (`src/aegis/attention.py`)

```python
"""What a session needs from a person, decided once, in Python.

Facts decide what they can: a running turn is working, a failed one is an
error, a live monitor or task is waiting. The agent's own `turn_end` decides
what only it knows: whether its last message asked something (needs_you),
presented something to read (review), or finished (done). No model reads the
transcript to guess. The first rule that holds wins:

    working > error > needs_you > waiting > review > done

A queue worker's needs_you is done: its answer goes to whoever enqueued it,
and nobody reads its tab, so its replies are dropped too.
"""

from __future__ import annotations


def card(standing: dict, *, working: bool, worker: bool, waits: list[str]) -> dict:
    report = standing.get("report")
    said = report["attention"] if report else "done"
    if worker and said == "needs_you":
        said = "done"
    error = standing.get("turn_error") or ""
    if working:
        kind = "working"
    elif error:
        kind = "error"
    elif said == "needs_you":
        kind = "needs_you"
    elif waits:
        kind = "waiting"
    else:
        kind = said
    if kind == "error":
        line = error
    elif kind in ("needs_you", "review") and report:
        line = report["line"]
    else:
        line = ""
    shows_report = kind in ("needs_you", "review", "done") and report and not worker
    plan = standing.get("plan") or []
    return {
        "attention": kind,
        "attention_line": line,
        "replies": list(report["replies"]) if shows_report else [],
        "waiting_on": ", ".join(waits) if kind == "waiting" else "",
        "plan": plan,
        "plan_now": next((i["text"] for i in plan if i["state"] == "doing"), ""),
        "plan_did": standing.get("did") or "",
        "plan_done": sum(1 for i in plan if i["state"] == "done"),
        "plan_total": len(plan),
    }
```

- [ ] **Step 4: Run them to see them pass**

Run: `uv run pytest -q tests/test_attention.py`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/aegis/attention.py tests/test_attention.py
git commit -m "feat(attention): the rule that decides what a session needs from you (#171)"
```

---

### Task 4: The agent's tools, the card fields and the primer

**Files:**
- Modify: `src/aegis/agent_ops.py` (models and two operations)
- Modify: `src/aegis/registry.py` (`card`, new `_waits`, new `status_changed`)
- Modify: `src/aegis/queues.py` (`cancel`)
- Modify: `src/aegis/mcp.py` (`PRIMER`)
- Modify: `tests/test_agents.py` (the tool-name set)
- Create: `tests/test_attention_e2e.py`

**Interfaces:**
- Consumes: `Session.report`, `Session.standing`, `Session.in_turn`, `Host.status_changed` (Task 2); `attention.card` (Task 3).
- Produces: operations `plan.update` (`items: list[{text, state}]`) and `turn.end` (`attention`, `line`, `replies`), served as MCP tools `plan_update` and `turn_end`; the `sessions` channel card gains every key `attention.card` returns.

- [ ] **Step 1: Write the failing tests** (`tests/test_attention_e2e.py`; add `"plan_update"` and `"turn_end"` to the set in `test_the_tools_are_named_after_their_operations_and_take_no_handle` in `tests/test_agents.py`)

```python
"""Attention end to end: the fake claude calls the real tools over /mcp."""

import json

import pytest

from aegis.ops import OpError

from .conftest import until
from .test_agents import CONFIG, World, mcp, turn


@pytest.fixture
async def world(tmp_path, fake_claude):
    (tmp_path / ".aegis.yaml").write_text(CONFIG)
    w = await World(tmp_path, fake_claude).start()
    yield w
    await w.stop()


async def test_a_question_turn_needs_you_and_carries_its_line_and_replies(world):
    a = await world.spawn()
    await turn(
        a,
        mcp(
            "turn_end",
            attention="needs_you",
            line="Rebase or merge?",
            replies=["rebase onto main", "merge main into it"],
        ),
    )
    c = a.wire()
    assert c["attention"] == "needs_you"
    assert c["attention_line"] == "Rebase or merge?"
    assert c["replies"] == ["rebase onto main", "merge main into it"]
    await turn(a, "rebase onto main")
    assert a.wire()["attention"] == "done" and a.wire()["replies"] == []


async def test_the_plan_reaches_the_card(world):
    a = await world.spawn()
    items = [{"text": "read", "state": "done"}, {"text": "fix", "state": "doing"}]
    await turn(a, mcp("plan_update", items=items))
    c = a.wire()
    assert (c["plan_now"], c["plan_did"], c["plan_done"], c["plan_total"]) == ("fix", "read", 1, 2)


async def test_a_live_monitor_is_waiting_and_cancelling_it_is_done(world):
    a = await world.spawn()
    said = await turn(
        a,
        mcp("monitor_start", description="never", done="false", progress=None, interval_s=60),
    )
    assert a.wire()["attention"] == "waiting"
    assert a.wire()["waiting_on"] == "1 monitor"
    mid = json.loads(said.removeprefix("mcp ok: "))["monitor_id"]
    await world.app.registry.call("monitor.cancel", {"monitor_id": mid})
    assert a.wire()["attention"] == "done"


async def test_a_dead_process_is_an_error_until_the_next_send(world):
    a = await world.spawn()
    await a.send("/exit 3")
    await until(lambda: a.status == "stopped", timeout=8, what="the exit")
    c = a.wire()
    assert c["attention"] == "error"
    assert c["attention_line"] == "claude exited with code 3"
    await turn(a, "hello again")
    assert a.wire()["attention"] == "done"


async def test_a_parent_waits_on_a_working_child(world):
    a = await world.spawn()
    said = await turn(a, mcp("session_spawn", agent="opus", prompt="/sleep 3"))
    child = world.session(json.loads(said.removeprefix("mcp ok: "))["log_id"])
    await until(lambda: child.status == "working", timeout=8, what="the child working")
    assert a.wire()["attention"] == "waiting" and a.wire()["waiting_on"] == "1 session"
    await until(lambda: child.status == "idle", timeout=10, what="the child done")
    assert a.wire()["attention"] == "done"


@pytest.mark.parametrize(
    "args",
    [
        {"attention": "needs_you", "line": "q", "replies": ["x" * 81]},
        {"attention": "needs_you", "line": "q", "replies": ["one\ntwo"]},
        {"attention": "needs_you", "line": "q", "replies": ["a", "b", "c", "d"]},
        {"attention": "waiting", "line": "q", "replies": []},
        {"attention": "done", "line": "x" * 141, "replies": []},
    ],
)
async def test_bad_reports_are_refused_and_change_nothing(world, args):
    from aegis.ops import Caller

    a = await world.spawn()
    with pytest.raises(OpError) as e:
        await world.app.registry.call("turn.end", args, Caller("agent", a.log_id))
    assert e.value.code == "bad_params"
    assert a.wire()["attention"] == "done" and a.wire()["attention_line"] == ""


async def test_two_items_doing_is_refused_and_long_plans_are_cut(world):
    from aegis.ops import Caller

    a = await world.spawn()
    me = Caller("agent", a.log_id)
    with pytest.raises(OpError):
        await world.app.registry.call(
            "plan.update",
            {"items": [{"text": "a", "state": "doing"}, {"text": "b", "state": "doing"}]},
            me,
        )
    items = [{"text": "t" * 200, "state": "pending"} for _ in range(40)]
    await world.app.registry.call("plan.update", {"items": items}, me)
    plan = a.wire()["plan"]
    assert len(plan) == 30 and len(plan[0]["text"]) == 120
```

- [ ] **Step 2: Run them to see them fail**

Run: `uv run pytest -q tests/test_attention_e2e.py tests/test_agents.py::test_the_tools_are_named_after_their_operations_and_take_no_handle`
Expected: FAIL (`KeyError: 'attention'` on the card, `unknown_op` for `turn.end`, and the tool-name set).

- [ ] **Step 3: Implement the operations** (`src/aegis/agent_ops.py`)

Imports:

```python
from typing import TYPE_CHECKING, Annotated, Literal

from pydantic import BaseModel, Field, model_validator
```

Models, after `FileSend`:

```python
class PlanItem(_Strict):
    text: str = Field(min_length=1)
    state: Literal["pending", "doing", "done"]


class PlanUpdate(_Strict):
    items: list[PlanItem] = Field(
        description="The whole plan, every time, in order. Mark one item `doing` "
        "while you work on it and `done` when it is finished."
    )

    @model_validator(mode="after")
    def _one_doing(self) -> "PlanUpdate":
        if sum(1 for i in self.items if i.state == "doing") > 1:
            raise ValueError("mark one item `doing` at a time")
        return self


Reply = Annotated[str, Field(min_length=1, max_length=80, pattern=r"^[^\n]+$")]


class TurnEnd(_Strict):
    attention: Literal["needs_you", "review", "done"] = Field(
        description="needs_you: your message asks the person something. review: it "
        "presents something for them to read. done: it reports finished work."
    )
    line: str = Field(
        min_length=1,
        max_length=140,
        description="One sentence: the question they must answer, what to read, or what got done.",
    )
    replies: list[Reply] = Field(
        default_factory=list,
        max_length=3,
        description="Up to three messages the person might send next, written as they "
        "would type them: their language, lowercase, no final period. Only when you "
        "laid out options or wait for a go-ahead; empty otherwise.",
    )
```

Operations, after `file_send`:

```python
    @r.op("plan.update", PlanUpdate, agent=True)
    async def plan_update(p: PlanUpdate, caller):
        """Keep your plan where the person can see it: on your tab's card and in
        its sidebar. Send the whole list each time."""
        s = own(caller)
        items = [{"text": i.text[:120], "state": i.state} for i in p.items[:30]]
        s.report({"kind": "plan", "items": items})
        done = sum(1 for i in items if i["state"] == "done")
        return f"plan saved: {done} of {len(items)} done"

    @r.op("turn.end", TurnEnd, agent=True)
    async def turn_end(p: TurnEnd, caller):
        """Call this as the last thing before you hand the turn back to the person,
        not when you end a turn to wait on a monitor or a queue task. It sets the
        mark on your tab and the line on your card."""
        s = own(caller)
        s.report(
            {"kind": "turn_end", "attention": p.attention, "line": p.line, "replies": p.replies}
        )
        return "noted"
```

- [ ] **Step 4: Implement the card** (`src/aegis/registry.py`)

```python
from . import attention
```

Replace `card` and add two methods next to it:

```python
    def card(self, session: Session) -> dict:
        return {
            "monitors": self.monitors.card(session.log_id)
            if self.monitors is not None
            else [],
            **attention.card(
                session.standing,
                working=session.in_turn,
                worker=bool(session.worker),
                waits=self._waits(session),
            ),
        }

    def _waits(self, s: Session) -> list[str]:
        """What a session waits on that is not a person, one phrase each."""
        n_mon = len(self.monitors.of(s.log_id)) if self.monitors is not None else 0
        n_task = (
            sum(
                1
                for t in self.queues.tasks.values()
                if t.enqueuer == s.log_id
                and t.callback
                and t.status in ("pending", "running")
            )
            if self.queues is not None
            else 0
        )
        n_kid = sum(
            1
            for o in self.sessions.values()
            if o.spec.spawned_by == s.log_id and o.status == "working"
        )
        counts = (
            (n_mon, "monitor"),
            (len(s.open_tasks), "background task"),
            (len(s.held), "held message"),
            (n_task, "queue task"),
            (n_kid, "session"),
        )
        return [f"{n} {noun}{'' if n == 1 else 's'}" for n, noun in counts if n]

    def status_changed(self, session: Session) -> None:
        parent = session.spec.spawned_by
        if parent and parent in self.sessions:
            self.refresh_card(parent)
```

In `src/aegis/queues.py`, at the end of `cancel`, before `await self.dispatch()`:

```python
        if t.enqueuer:
            self._registry.refresh_card(t.enqueuer)
```

- [ ] **Step 5: Implement the primer** (`src/aegis/mcp.py`)

Insert before the paragraph that starts `If you are a queue worker`:

```
Keep the person informed through two aegis tools. For any work that is not \
obvious, keep a plan with plan_update: send the whole list each time, mark one \
item `doing` while you work on it and `done` when it is finished. When you hand \
the turn back to the person, call turn_end first: `needs_you` with the question \
they must answer, `review` with what they should read, or `done` with what got \
done, in one sentence. Do not call turn_end when you end your turn to wait on a \
monitor or a queue task.

turn_end also takes up to three `replies`: messages the person might send next, \
written as they would type them, in the language they write to you in, \
lowercase and without a final period. Offer them when you laid out options, or \
when you proposed one thing and wait for a go-ahead (then a reply is their way \
of saying yes). Leave them empty when you asked an open question with many \
possible answers, or when you report finished work. An empty list is better than \
a wrong guess.
```

(`PRIMER` goes through `str.format`; the text has no braces.)

- [ ] **Step 6: Run them to see them pass**

Run: `uv run pytest -q tests/test_attention_e2e.py tests/test_agents.py tests/test_registry.py`
Expected: PASS. If `test_a_parent_waits_on_a_working_child` sees `waiting` stay after the child idles, check that `Session._set` calls `status_changed` inside the `"status" in changes` block (Task 2).

- [ ] **Step 7: Commit**

```bash
git add src/aegis/agent_ops.py src/aegis/registry.py src/aegis/queues.py src/aegis/mcp.py tests/test_agents.py tests/test_attention_e2e.py
git commit -m "feat(agents): plan_update and turn_end, and the card's attention fields (#171)"
```

---

### Task 5: Glyphs, tab marks, Fleet cards, band and order switch

**Files:**
- Create: `src/aegis/client/js/glyphs.js`
- Modify: `src/aegis/client/js/tabs.js`, `src/aegis/client/js/fleet.js`, `src/aegis/client/js/app.js`, `src/aegis/client/index.html`, `src/aegis/client/css/base.css`
- Test: `tests/test_browser.py`

**Interfaces:**
- Consumes: card keys `attention`, `attention_line`, `waiting_on`, `plan_now`, `plan_did`, `plan_done`, `plan_total` (Task 4).
- Produces: `glyphs.js` exports `installGlyphs()`, `glyph(attention) -> SVGElement`, `LABEL: Record<string,string>`; `fleet.js` `renderCards(box, metas, onOpen, order)` and `patchCard(box, m, onOpen, order) -> boolean` (false means "regroup: call renderCards").

- [ ] **Step 1: Write the failing browser tests** (append to `tests/test_browser.py`)

```python
def report(pg, **args) -> None:
    pg.fill("#input", f"/mcp turn_end {json.dumps(args)}")
    pg.press("#input", "Enter")


def test_a_question_marks_the_tab_card_and_band_and_the_fleet_groups_it(server, page):
    page.goto(server.url)
    page.wait_for_selector("#a2[data-view=fleet]")
    spawn(page, "hello")
    first = page.evaluate("location.hash.slice(3)")
    spawn(page, "hello")
    report(page, attention="needs_you", line="Rebase or merge?", replies=[])
    turns_done(page, 2)
    page.wait_for_selector(".tab.on svg.ic use[href='#g-need']", state="attached")
    page.click("#tab-fleet")
    page.wait_for_selector(".card .ask >> text=Rebase or merge?")
    assert page.inner_text(".grp-h >> nth=0") == "Needs you"
    cards = page.eval_on_selector_all(".card", "cs => cs.map(c => c.dataset.id)")
    assert cards[-1] == first  # the done session sits below the one that needs you
    assert "need you" in page.inner_text("#band-counts")
    page.click(".seg button[data-order=tabs]")
    page.reload()
    page.wait_for_selector("#a2[data-view=fleet]")
    assert page.eval_on_selector_all(".card", "cs => cs.map(c => c.dataset.id)")[0] == first
    assert page.locator(".grp-h").count() == 0
    assert page.errors == []
```

- [ ] **Step 2: Run it to see it fail**

Run: `uv run pytest -q -m browser tests/test_browser.py -k "question_marks"`
Expected: FAIL waiting for `.tab.on svg.ic use[href='#g-need']`.

- [ ] **Step 3: Create `src/aegis/client/js/glyphs.js`**

```javascript
// The status badges, as one inline SVG sprite: Unicode marks render differently
// in each font. Python decides a session's attention; this only maps it to a
// symbol and a word. Drawn in currentColor; the knocked-out glyph inside a badge
// uses --knock, which base.css sets to the theme's background.

const NS = "http://www.w3.org/2000/svg";
const K = 'style="stroke:var(--knock)" fill="none" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"';

const SPRITE = `<defs>
<symbol id="g-need" viewBox="0 0 16 16"><rect x="1" y="1" width="14" height="14" rx="4" fill="currentColor"/><path d="M6.2 5.9a1.8 1.8 0 1 1 2.5 1.65c-.5.2-.7.55-.7 1.05" ${K}/><circle cx="8" cy="11.1" r="1" style="fill:var(--knock)"/></symbol>
<symbol id="g-err" viewBox="0 0 16 16"><circle cx="8" cy="8" r="7" fill="currentColor"/><path d="M5.8 5.8l4.4 4.4M10.2 5.8l-4.4 4.4" ${K}/></symbol>
<symbol id="g-rev" viewBox="0 0 16 16"><rect x="1" y="1" width="14" height="14" rx="4" fill="currentColor"/><path d="M3.4 8s1.8-3 4.6-3 4.6 3 4.6 3-1.8 3-4.6 3S3.4 8 3.4 8z" style="fill:var(--knock)"/><circle cx="8" cy="8" r="1.3" fill="currentColor"/></symbol>
<symbol id="g-wait" viewBox="0 0 16 16"><circle cx="8" cy="8" r="7" fill="currentColor" opacity=".28"/><path d="M8 4.4V8l2.4 1.6" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"/></symbol>
<symbol id="g-done" viewBox="0 0 16 16"><circle cx="8" cy="8" r="7" fill="currentColor"/><path d="M5 8.3l2 2 4-4.3" ${K}/></symbol>
<symbol id="g-work" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round"><circle cx="8" cy="8" r="5.4" opacity=".25"/><path d="M8 2.6a5.4 5.4 0 0 1 5.4 5.4"/></symbol>
</defs>`;

const SYMBOL = { working: "work", needs_you: "need", error: "err", review: "rev", waiting: "wait", done: "done" };

export const LABEL = {
  working: "working",
  needs_you: "needs you",
  error: "error",
  review: "review",
  waiting: "waiting",
  done: "done",
};

export function installGlyphs() {
  if (document.getElementById("glyphs")) return;
  const svg = document.createElementNS(NS, "svg");
  svg.id = "glyphs";
  svg.setAttribute("width", "0");
  svg.setAttribute("height", "0");
  svg.setAttribute("aria-hidden", "true");
  svg.style.position = "absolute";
  svg.innerHTML = SPRITE;
  document.body.prepend(svg);
}

export function glyph(attention) {
  const kind = SYMBOL[attention] || "done";
  const svg = document.createElementNS(NS, "svg");
  svg.setAttribute("class", `ic ${kind}`);
  const use = document.createElementNS(NS, "use");
  use.setAttribute("href", `#g-${kind}`);
  svg.append(use);
  return svg;
}
```

- [ ] **Step 4: Draw the tab mark** (`src/aegis/client/js/tabs.js`)

```javascript
import { glyph, LABEL } from "./glyphs.js";
```

In `tab()`, replace the `dot` span with the glyph and the title text:

```javascript
  t.title = `${m.handle}: ${m.title || "untitled"} (${LABEL[m.attention] || m.state})`;
  const mark = glyph(m.attention);
  ...
  t.append(mark, name, handle);
```

(`dotClass` stays exported: the band and the archive still use it for host rows.)

- [ ] **Step 5: Draw cards, groups and the band** (`src/aegis/client/js/fleet.js`)

```javascript
import { glyph, LABEL } from "./glyphs.js";

// The cards a person must act on, shown first when the Fleet is ordered by need.
const NEEDS = new Set(["needs_you", "error", "review"]);
const group = (m) => (NEEDS.has(m.attention) ? "needs" : "rest");

export function renderCards(box, metas, onOpen, order = "attention") {
  if (!metas.length) {
    box.replaceChildren(el("div", "empty", "No open sessions. Start one with +."));
    return;
  }
  if (order !== "attention") {
    box.replaceChildren(...metas.map((m) => card(m, onOpen)));
    return;
  }
  const out = [];
  const needs = metas.filter((m) => group(m) === "needs");
  const rest = metas.filter((m) => group(m) === "rest");
  if (needs.length) out.push(el("div", "grp-h", "Needs you"), ...needs.map((m) => card(m, onOpen)));
  if (rest.length) out.push(el("div", "grp-h", needs.length ? "Everything else" : "Sessions"), ...rest.map((m) => card(m, onOpen)));
  box.replaceChildren(...out);
}

// One session's card redrawn where it stands. False when its group changed and
// the caller must regroup with renderCards.
export function patchCard(box, m, onOpen, order = "attention") {
  const old = box.querySelector(`.card[data-id="${CSS.escape(m.log_id)}"]`);
  if (!old) return true;
  if (order === "attention" && old.dataset.group !== group(m)) return false;
  old.replaceWith(card(m, onOpen));
  return true;
}

function card(m, onOpen) {
  const c = el("div", `card ${m.state} at-${m.attention}`);
  c.dataset.id = m.log_id;
  c.dataset.group = group(m);
  const hd = el("div", "hd");
  hd.append(glyph(m.attention), el("span", "h", m.handle));
  if (m.worker) hd.append(el("span", "badge", `worker · ${m.worker.queue}`));
  const label = m.attention === "waiting" && m.waiting_on ? `waiting · ${m.waiting_on}` : LABEL[m.attention] || m.state;
  hd.append(el("span", `s at-${m.attention}`, label));
  const parts = [hd, el("div", "ttl", m.title || "untitled")];
  if (m.attention_line) parts.push(el("div", `ask at-${m.attention}`, m.attention_line));
  const sub = el("div", "ln");
  sub.append(el("b", null, `${m.agent}${(m.overridden || []).length ? "*" : ""}`), document.createTextNode(`${m.model} · ${cwdTail(m.cwd)}`));
  parts.push(sub);
  if (m.plan_now || m.plan_did) {
    const pl = el("div", "pl");
    if (m.plan_now) pl.append(planRow("now", m.plan_now, "now"));
    if (m.plan_did) pl.append(planRow("did", m.plan_did, ""));
    parts.push(pl);
  }
  if (m.attention === "working") parts.push(el("div", "act", m.activity || ""));
  const pct = m.context_window && m.context_tokens ? Math.min(100, Math.round((100 * m.context_tokens) / m.context_window)) : 0;
  const bar = el("div", "bar thin");
  const fill = el("i");
  fill.style.width = `${pct}%`;
  bar.append(fill);
  const ft = el("div", "ft");
  ft.append(el("span", "when", ago(m.last_activity)), el("span", null, money(m.cost_usd)));
  const mons = (m.monitors || []).length;
  if (mons) ft.append(el("span", "mons", `${mons} monitor${mons > 1 ? "s" : ""}`));
  if (m.plan_total) ft.append(el("span", "prog", `plan ${m.plan_done}/${m.plan_total}`));
  ft.append(el("span", "ctx", `${pct}%`));
  parts.push(bar, ft);
  c.append(...parts);
  c.addEventListener("click", () => onOpen(m.log_id));
  return c;
}

function planRow(key, text, cls) {
  const d = el("div", cls);
  d.append(el("span", "k", key), el("span", null, text));
  return d;
}
```

Replace `STATE_ORDER` and the counting in `renderBand`:

```javascript
const ATTENTION_ORDER = ["needs_you", "error", "review", "working", "waiting", "done"];
```

```javascript
  const counts = new Map();
  for (const m of metas) counts.set(m.attention, (counts.get(m.attention) || 0) + 1);
  const rows = ATTENTION_ORDER.filter((a) => counts.get(a)).map((a) => {
    const d = el("div");
    d.append(glyph(a), el("b", null, String(counts.get(a))), document.createTextNode(a === "needs_you" ? "need you" : LABEL[a]));
    return d;
  });
```

(`dotClass` is no longer imported by `fleet.js` if nothing else in it uses it; remove the import if so.)

- [ ] **Step 6: The order switch and wiring** (`index.html`, `app.js`)

In `index.html`, between `</section>` of the band and `<section class="cards" ...>`:

```html
    <div class="order"><h3>Sessions</h3>
      <div class="seg" id="fleet-order">
        <button data-order="attention">Needs you first</button>
        <button data-order="tabs">Tab order</button>
      </div>
    </div>
```

In `app.js`:

```javascript
import { installGlyphs } from "./glyphs.js";
```

near the other top-level state:

```javascript
installGlyphs();
// How the Fleet orders its cards: this browser's choice, like the tab order.
let fleetOrder = localStorage.getItem("aegis.fleetOrder") || "attention";
function markOrder() {
  for (const b of document.querySelectorAll("#fleet-order button")) b.classList.toggle("on", b.dataset.order === fleetOrder);
}
markOrder();
for (const b of document.querySelectorAll("#fleet-order button"))
  b.addEventListener("click", () => {
    fleetOrder = b.dataset.order;
    localStorage.setItem("aegis.fleetOrder", fleetOrder);
    markOrder();
    render();
  });
```

In `render()`: `renderCards($("cards"), ordered, openSession, fleetOrder);`

In `flushSessions()`, replace the fleet branch's loop:

```javascript
  if (r.view === "fleet") {
    const regroup = [...ids].some((id) => !patchCard($("cards"), sessions.get(id), openSession, fleetOrder));
    if (regroup) renderCards($("cards"), ordered, openSession, fleetOrder);
    fleetMark(false);
    drawBand();
  }
```

`fleetItems()` already selects `#cards .card`, so `.grp-h` headers do not enter the j/k walk.

- [ ] **Step 7: Styles** (append to `src/aegis/client/css/base.css`)

```css
/* attention: glyphs, cards, groups, the order switch (js/glyphs.js) */
#a2{--knock:var(--bg)}
#a2 .ic{width:15px;height:15px;flex:none;display:inline-block;vertical-align:-3px}
#a2 .ic.need{color:var(--accent)}#a2 .ic.err{color:var(--err)}#a2 .ic.rev{color:var(--strong)}
#a2 .ic.wait{color:var(--muted)}#a2 .ic.done{color:var(--ok)}#a2 .ic.work{color:var(--accent);animation:a2spin 1.1s linear infinite}
@keyframes a2spin{to{transform:rotate(360deg)}}
@keyframes a2blink{to{visibility:hidden}}
#a2 .tab:not(.on) .ic.need{animation:a2blink 1.2s steps(2,start) infinite}
@media (prefers-reduced-motion:reduce){#a2 .ic.work,#a2 .tab .ic.need{animation:none}}
#a2 .card.at-needs_you{border-color:var(--accent);box-shadow:inset 3px 0 0 var(--accent)}
#a2 .card.at-error{border-color:var(--err);box-shadow:inset 3px 0 0 var(--err)}
#a2 .card .hd .s.at-needs_you{color:var(--accent)}#a2 .card .hd .s.at-error{color:var(--err)}
#a2 .card .ask{font-size:13px;color:var(--strong);line-height:1.4}
#a2 .card .ask.at-error{color:var(--err)}
#a2 .card .pl{display:grid;gap:3px;font-size:12px;font-family:var(--font-chrome)}
#a2 .card .pl div{display:grid;grid-template-columns:44px 1fr;gap:6px;color:var(--muted);overflow:hidden}
#a2 .card .pl div span:last-child{overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
#a2 .card .pl .k{color:var(--faint)}
#a2 .card .pl .now span:last-child{color:var(--strong)}
#a2 .card .ft .prog{color:var(--faint)}
#a2 .grp-h{grid-column:1/-1;font-family:var(--font-head);font-size:12px;color:var(--faint);margin-top:4px}
#a2 .order{display:flex;align-items:center;justify-content:space-between;padding:14px 20px 0}
#a2 .order h3{margin:0;font-family:var(--font-head);font-weight:500;font-size:15px;color:var(--strong)}
#a2 .seg{display:inline-flex;border:1px solid var(--rule);border-radius:var(--r);overflow:hidden;font-family:var(--font-chrome);font-size:11.5px}
#a2 .seg button{background:none;border:none;padding:4px 10px;color:var(--muted);cursor:pointer}
#a2 .seg button+button{border-left:1px solid var(--rule)}
#a2 .seg button.on{background:var(--accent-soft);color:var(--strong)}
#a2 .band .counts .ic{width:14px;height:14px}
```

- [ ] **Step 8: Run the browser tests that touch tabs and the Fleet**

Run: `uv run pytest -q -m browser tests/test_browser.py -k "question_marks or fleet or tabs or patch_redraws or alt_brackets"`
Expected: PASS. `test_a_sessions_patch_redraws_only_its_own_tab_and_card` must still pass: a patch that keeps a card in its group replaces only that card.

- [ ] **Step 9: Commit**

```bash
git add src/aegis/client/js/glyphs.js src/aegis/client/js/tabs.js src/aegis/client/js/fleet.js src/aegis/client/js/app.js src/aegis/client/index.html src/aegis/client/css/base.css tests/test_browser.py
git commit -m "feat(client): attention badges on tabs, cards and the band; order the Fleet by need (#171)"
```

---

### Task 6: Sidebar plan and report, and reply pills

**Files:**
- Modify: `src/aegis/client/index.html`, `src/aegis/client/js/app.js` (`renderMeta`, `send`), `src/aegis/client/css/base.css`
- Test: `tests/test_browser.py`

**Interfaces:**
- Consumes: card keys `attention`, `attention_line`, `replies`, `plan` (Task 4); `glyph`, `LABEL` (Task 5).
- Produces: `sendText(text)` in `app.js`, used by Enter and by a pill.

- [ ] **Step 1: Write the failing browser test** (append to `tests/test_browser.py`)

```python
def test_reply_pills_send_their_text_and_all_disappear(server, page):
    page.goto(server.url)
    page.wait_for_selector("#a2[data-view=fleet]")
    spawn(page, "hello")
    plan = [{"text": "read", "state": "done"}, {"text": "fix", "state": "doing"}]
    page.fill("#input", f"/mcp plan_update {json.dumps({'items': plan})}")
    page.press("#input", "Enter")
    turns_done(page, 2)
    page.wait_for_selector("#s-plan-sec:not([hidden]) >> text=fix")
    report(page, attention="needs_you", line="Rebase or merge?", replies=["rebase onto main", "merge main into it"])
    turns_done(page, 3)
    page.wait_for_selector("#s-ask:not([hidden]) >> text=Rebase or merge?")
    page.wait_for_selector("#replies:not([hidden]) .rp >> text=merge main into it")
    page.click("#replies .rp >> text=rebase onto main")
    page.wait_for_selector("#replies", state="hidden")
    turns_done(page, 4)
    assert "rebase onto main" in page.inner_text(".row.user >> nth=-1")
    assert page.locator("#replies .rp").count() == 0 or page.is_hidden("#replies")
    assert page.errors == []
```

- [ ] **Step 2: Run it to see it fail**

Run: `uv run pytest -q -m browser tests/test_browser.py -k "reply_pills"`
Expected: FAIL waiting for `#s-plan-sec`.

- [ ] **Step 3: Markup** (`index.html`)

In the Session section, after the `#s-cwd` row:

```html
        <div class="askbox" id="s-ask" hidden></div>
```

After the Session section:

```html
      <div class="sec" id="s-plan-sec" hidden><h4>Plan</h4><div class="plan" id="s-plan"></div></div>
```

In `.composer`, before `<div class="box">`:

```html
        <div class="replies" id="replies" hidden></div>
```

- [ ] **Step 4: Drawing and sending** (`app.js`)

```javascript
import { glyph, installGlyphs, LABEL } from "./glyphs.js";
```

In `renderMeta(s)`, replace the two `s-status` lines:

```javascript
  $("s-status").replaceChildren(glyph(s.attention), document.createTextNode(` ${LABEL[s.attention] || s.state}`));
  $("s-status").className = `st at-${s.attention}`;
  $("s-ask").hidden = !s.attention_line;
  $("s-ask").textContent = s.attention_line || "";
  $("s-ask").className = `askbox at-${s.attention}`;
  const plan = s.plan || [];
  $("s-plan-sec").hidden = !plan.length;
  const mark = { done: "✓", doing: "◐", pending: "○" };
  $("s-plan").replaceChildren(
    ...plan.map((i) => {
      const d = document.createElement("div");
      d.className = i.state;
      d.append(span("pm", mark[i.state] || ""), span("", i.text));
      return d;
    }),
  );
  drawReplies(s);
```

(`span(cls, text)` already exists in `app.js`.)

Add next to `send`:

```javascript
// The agent's suggested next messages, from its turn_end. Redrawn only when they
// change: renderMeta runs on every patch of the open session.
function drawReplies(s) {
  const box = $("replies");
  const replies = s.state === "working" ? [] : s.replies || [];
  const key = JSON.stringify([s.log_id, replies]);
  if (box.dataset.key === key) return;
  box.dataset.key = key;
  box.hidden = !replies.length;
  box.replaceChildren(
    span("lbl", "reply"),
    ...replies.map((text) => {
      const b = document.createElement("button");
      b.className = "rp";
      b.textContent = text;
      b.addEventListener("click", () => sendText(text));
      return b;
    }),
  );
}

async function sendText(text) {
  const s = focused();
  if (!text || !s) return false;
  $("send-error").textContent = "";
  $("replies").hidden = true; // any send answers the turn the pills belonged to
  try {
    await conn.call("session.send", { log_id: s.log_id, text });
    transcript.toBottom();
    return true;
  } catch (e) {
    $("send-error").textContent = e.message;
    $("replies").hidden = false;
    return false;
  }
}
```

Rewrite `send()` on top of it:

```javascript
async function send() {
  const s = focused();
  const text = input.value.trim();
  if (!text || !s) return;
  if (await sendText(text)) {
    input.value = "";
    localStorage.removeItem(`aegis.draft.${s.log_id}`);
    autosize();
  }
}
```

The sidebar's glyph and the pill drawing for the `#replies` box are the only new uses; the Plan glyphs ✓ ◐ ○ in the sidebar list stay text, since they sit in running text at the theme's UI font. If they render badly in Ink's JetBrains Mono during the browser check, replace them with `glyph("done")` / `glyph("working")` / `glyph("waiting")`.

- [ ] **Step 5: Styles** (append to `base.css`)

```css
#a2 .askbox{font-size:12.5px;color:var(--strong);background:var(--accent-soft);border-left:2px solid var(--accent);padding:6px 8px;border-radius:4px;margin-top:6px}
#a2 .askbox.at-error{background:var(--err-bg);border-left-color:var(--err);color:var(--err)}
#a2 .st .ic{width:13px;height:13px}
#a2 .plan{display:grid;gap:5px;font-size:12.5px}
#a2 .plan div{display:grid;grid-template-columns:16px 1fr;gap:6px;color:var(--muted)}
#a2 .plan .done{color:var(--faint)}#a2 .plan .done .pm{color:var(--ok)}
#a2 .plan .doing{color:var(--strong)}#a2 .plan .doing .pm{color:var(--accent)}
#a2 .replies{display:flex;flex-wrap:wrap;gap:8px;align-items:center;margin:0 0 9px 2px}
#a2 .replies .lbl{font-family:var(--font-chrome);font-size:11px;color:var(--faint);margin-right:2px}
#a2 .rp{font-family:var(--font-ui);font-size:13px;color:var(--strong);background:var(--surface);border:1px solid var(--rule);border-radius:999px;padding:5px 13px;cursor:pointer;line-height:1.3;text-align:left}
#a2 .rp:hover{border-color:var(--accent);background:var(--accent-soft)}
#a2 .col:has(#replies:not([hidden])) .jump{bottom:160px}
```

- [ ] **Step 6: Run the touched browser tests**

Run: `uv run pytest -q -m browser tests/test_browser.py -k "reply_pills or send_sits or message_box or composer or pending"`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add src/aegis/client/index.html src/aegis/client/js/app.js src/aegis/client/css/base.css tests/test_browser.py
git commit -m "feat(client): the plan and the agent's report in the sidebar, reply pills on the composer (#171)"
```

---

### Task 7: Live check, docs, bench and the PR

**Files:**
- Modify: `tests/test_live.py`, `DESIGN.md`, `docs/superpowers/specs/2026-10-08-session-attention-design.md` (status line)
- Create: `changelog.d/171-session-attention.added.md`

**Interfaces:**
- Consumes: everything above.

- [ ] **Step 1: Write the live test** (append to `tests/test_live.py`; it follows `test_real_claude_gives_a_countable_wait_a_progress_command`)

```python
async def test_real_claude_reports_its_turns_with_turn_end(tmp_path: Path):
    """A real Haiku primed by aegis calls turn_end: needs_you with replies after
    laying out two options, done without needs_you after finished work (#171)."""
    import asyncio

    import uvicorn

    from aegis.app import App
    from aegis.roots import make_roots
    from aegis.web import build_web

    from .test_agents import _free_port

    claude = shutil.which("claude")
    assert claude, "claude is not on PATH"
    (tmp_path / ".aegis.yaml").write_text(
        f"agents:\n  haiku: {{harness: claude-code, model: {HAIKU}, effort: low, permission: full}}\n"
    )
    port = _free_port()
    app = App(make_roots(tmp_path, None), claude_bin=claude, base_url=f"http://127.0.0.1:{port}")
    server = uvicorn.Server(
        uvicorn.Config(build_web(app, "t", {f"127.0.0.1:{port}"}), port=port, log_level="warning")
    )
    task = asyncio.create_task(server.serve())
    await until(lambda: server.started, timeout=10, what="uvicorn")
    try:
        r = await app.registry.call("session.spawn", {"agent": "haiku"})
        s = app.sessions.sessions[r["log_id"]]
        await s.send(
            "I need to bring a feature branch up to date with main. Lay out the two "
            "usual ways in two lines and ask me which one I want. Do not run anything."
        )
        await until(lambda: s.status == "idle" and s.cost_usd, timeout=120, what="the question turn")
        c = s.wire()
        assert c["attention"] == "needs_you", c
        assert 1 <= len(c["replies"]) <= 3, c
        await s.send(f"Create the file {tmp_path / 'done.txt'} containing ok, then tell me it is done.")
        await until(lambda: s.status == "idle" and (tmp_path / "done.txt").exists(), timeout=120, what="the work turn")
        assert s.wire()["attention"] == "done", s.wire()
    finally:
        server.should_exit = True
        await asyncio.wait_for(task, 30)
```

- [ ] **Step 2: Run it** (spends a few cents of Haiku)

Run: `uv run pytest -q --run-live -m live tests/test_live.py -k turn_end`
Expected: PASS. Record pass or fail in the PR body. A failure here is a priming problem: adjust the primer paragraph (Task 4, Step 5) and the `TurnEnd` field descriptions, rerun, and report how many runs passed out of how many.

- [ ] **Step 3: DESIGN.md**

After the paragraph that starts `**A session's card is published when it changes**`, add:

```markdown
**A session's attention is decided in Python, from facts and the agent's own
reports.** Working, error and waiting are facts the server holds: the turn, the
fold's last failure, live monitors, background tasks, held messages, queue tasks
and child sessions. Whether a finished turn asked the person something, showed
them something to read, or just finished, only the agent knows, and it says so
with `turn_end`; its plan comes from `plan_update`. Both are aegis records in the
store, so the fold derives a session's `standing` and a refold gives the same
card; the meta keeps it, so boot still reads no store. `attention.py` holds the
precedence. No model reads a transcript to guess what a turn meant.
```

- [ ] **Step 4: Changelog fragment** (`changelog.d/171-session-attention.added.md`)

```markdown
- **Every session says what it needs from you.** Tabs, Fleet cards and the Fleet
  band carry a badge: working, needs you, error, review, waiting or done. Agents
  report their own plan (`plan_update`) and how their turn ended (`turn_end`),
  with up to three suggested replies that show as pills on the message box and
  send on click. Error and waiting come from what the server already knows:
  failed turns, live monitors, background tasks, queue tasks and child
  sessions. The Fleet can put the sessions that need you first, or keep your tab
  order.
```

- [ ] **Step 5: Spec status**

Change the spec's first status line to:

```markdown
**Status: slice 1 implemented, 2026-10-08** (issue #171), following
`docs/superpowers/plans/2026-10-08-session-attention-slice-1.md`. Slices 2 and 3
are designed, not built.
```

(keep the rest of that paragraph as it is.)

- [ ] **Step 6: Gates CI cannot or should not be trusted alone for**

Run, each on its own, reading the exit code directly:

```bash
make format
make lint
make typecheck
rift check
make changelog-check
```

Expected: each exits 0. Then restart a real server and look at it: start `uv run aegis serve --root /tmp/aegis-attn --port 8799` in a clean directory holding a copy of the test `CONFIG`, open it, send `/mcp turn_end {...}` through a session running the fake claude (`--claude tests/fake_claude.py`), and check the tab badge, the card, the band, the switch and a pill click in all three themes.

- [ ] **Step 7: Bench**

Run: `make bench`
Expected: a table; copy it into the PR body. The fold now builds a dict on plan, turn_end, send, exit and result records only; the per-line cost should not move.

- [ ] **Step 8: Commit and open the PR**

```bash
git add tests/test_live.py DESIGN.md changelog.d/171-session-attention.added.md docs/superpowers/specs/2026-10-08-session-attention-design.md
git commit -m "docs: attention in DESIGN.md, the release note, slice 1 implemented (#171)"
git push
gh pr create --title "Session attention, slice 1: what each session needs from you (#171)" --body-file <body.md>
```

The PR body carries: what was measured (live test runs, bench table), what was left for slices 2 and 3, and that done and review badges stay until slice 2 adds read tracking.
