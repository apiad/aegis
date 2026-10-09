# Agents see other sessions and wait on them: Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `session_list` carries each session's attention card and plan, and a new `monitor_sessions` tool lets an agent wait, without polling, until a set of other sessions has finished, waking it `ok`, or `blocked` as soon as one needs the person.

**Architecture:** `session_list` copies fourteen fields from `Session.wire()`, which already merges the attention card. A session monitor is an ordinary `Monitor` with a non-empty `sessions` list. `Monitors._watch` sends it to a new loop that reads each watched session's card through `Registry.card` every `interval_s`, instead of running bash. The browser's monitor row and card draw "1 of 2" and a Sessions section when `sessions` is present.

**Tech Stack:** Python 3.13, pydantic, fastmcp (via `aegis.ops` registry), pytest + pytest-asyncio, the fake claude in `tests/fake_claude.py`, Playwright for browser tests, plain ES modules in `src/aegis/client/js/`.

**Spec:** `docs/superpowers/specs/2026-10-09-agents-see-aegis-design.md`, part 1 (issue #234). Part 2 (telemetry, #235) is a separate plan.

## Global Constraints

- Read and advise only: no tool here pauses, closes, throttles or changes another session.
- Linked servers' entries in `session_list` stay cut to handle, server and state (`far_sessions` unchanged).
- `monitor_start`'s schema does not change: `progress` stays required-but-nullable (#165).
- Watched sessions are kept by log id; the handle shown is the current one.
- Classification: finished = `done`, `review` or closed; blocked = `needs_you` or `error`; running = `working` or `waiting`.
- Outcomes: `ok` when all are finished, `blocked` as soon as any is blocked, `timeout` otherwise.
- `monitor_sessions` defaults: `interval_s=10` (1 to 3600), `timeout_s=4*3600` (up to 7 days), 1 to 20 sessions.
- English everywhere; conventional commits; stage named paths only (`git commit -- <paths>`), never amend; work in this worktree (`.claude/worktrees/agents-see-aegis`, branch `agents-see-aegis`).
- Tests that take 1.5 s or more are marked `@pytest.mark.slow`; `make test` fails an unmarked test over 3 s (`--max-unmarked-duration=3`).

## Review Focus

1. **A watched session renamed after arming**: the monitor still follows it (by log id) and the card and wake use the new handle. Test in Task 3.
2. **A watched session closed after arming**: counts as finished, shows as `closed`, and the wake names it. Test in Task 3.
3. **Every watched session already finished when the monitor is armed**: the `ok` wake still reaches the owner, after the owner's own turn ends, not lost and not delivered mid-turn. Test in Task 3.
4. **The same handle listed twice**: watched once, so the tally reads "0 of 1" and not "0 of 2". Test in Task 3.
5. **An agent listing its own handle**: refused with `not_yourself`, since its own wait would keep it `waiting` and it would never finish. Test in Task 3.

Not covered by a test, on purpose: two agents waiting on each other both stay `waiting` until the timeout. The timeout is the guard.

---

### Task 1: `session_list` carries the card and the plan

**Files:**
- Modify: `src/aegis/agent_ops.py` (`session_list`, around line 437; a module-level `LISTED` tuple next to `FAR_STATES`)
- Test: `tests/test_attention_e2e.py`

**Interfaces:**
- Consumes: `Session.wire()` (already includes `model`, `cost_usd`, `context_tokens`, `context_window`, `last_activity` and every key of `attention.card()`).
- Produces: each local `session_list` entry has keys `handle, title, state, cwd, worker, you` plus every name in `LISTED`.

- [ ] **Step 1: Write the failing test** (append to `tests/test_attention_e2e.py`)

```python
async def test_session_list_shows_another_sessions_card_and_plan(world):
    """One agent follows another's plan and state (#234)."""
    a, b = await world.spawn(), await world.spawn()
    items = [{"text": "read", "state": "done"}, {"text": "fix", "state": "doing"}]
    await turn(b, mcp("plan_update", items=items))
    await turn(b, mcp("turn_end", attention="needs_you", line="Rebase or merge?"))
    said = await turn(a, mcp("session_list"))
    listed = {x["handle"]: x for x in json.loads(said.removeprefix("mcp ok: "))}
    seen = listed[b.handle]
    card = b.wire()
    for key in LISTED:
        assert seen[key] == card[key], key
    assert (seen["attention"], seen["attention_line"]) == ("needs_you", "Rebase or merge?")
    assert (seen["plan_now"], seen["plan_done"], seen["plan_total"]) == ("fix", 1, 2)
    for left_out in ("replies", "mark", "blink", "monitors", "unread"):
        assert left_out not in seen
```

and add to the imports at the top of the file:

```python
from aegis.agent_ops import LISTED
```

- [ ] **Step 2: Run it to verify it fails**

Run: `uv run pytest tests/test_attention_e2e.py::test_session_list_shows_another_sessions_card_and_plan -q`
Expected: FAIL with `ImportError: cannot import name 'LISTED'`.

- [ ] **Step 3: Implement**

In `src/aegis/agent_ops.py`, after the `FAR_ERRORS` dict:

```python
# What session_list adds from a session's card, so one agent can follow
# another's plan and state. replies, mark and blink describe the person's tab,
# not the session, and stay out.
LISTED = (
    "model",
    "cost_usd",
    "context_tokens",
    "context_window",
    "last_activity",
    "attention",
    "attention_line",
    "waiting_on",
    "plan",
    "plan_now",
    "plan_did",
    "plan_done",
    "plan_total",
    "plan_clock",
)
```

Replace the body of `session_list`:

```python
    @r.op("session.list", NoArgs, agent=True)
    async def session_list(_, caller):
        """The open sessions on this server, each with its state, what it needs
        (attention and its line, what it waits on), its plan and what it is
        doing now, its model and spend; then each linked server's by handle and
        state alone, as `handle@server`: reach one with peer_handoff."""
        out = []
        for s in reg.open_sessions():
            w = s.wire()
            out.append(
                {
                    "handle": s.handle,
                    "title": s.title,
                    "state": s.status,
                    "cwd": str(s.spec.cwd),
                    "worker": s.worker,
                    "you": caller.log_id == s.log_id,
                    **{k: w.get(k) for k in LISTED},
                }
            )
        if caller.link is None:  # a link asking is not relayed further
            out += await far_sessions()
        return out
```

- [ ] **Step 4: Run the new test and the existing session_list tests**

Run: `uv run pytest tests/test_attention_e2e.py tests/test_agents.py -q -k "session_list or own_session or card_and_plan" && uv run pytest tests/test_links.py -q -k session_list`
Expected: PASS. `test_session_list_shows_far_handles_and_states_only` must still pass unchanged.

- [ ] **Step 5: Commit**

```bash
git commit -m "feat(agents): session_list carries each session's card and plan (#234)" -- src/aegis/agent_ops.py tests/test_attention_e2e.py
```

---

### Task 2: a monitor can watch sessions

**Files:**
- Modify: `src/aegis/monitors.py` (constants and `classify` after `READINGS_KEPT`; `Monitor` gains `sessions`; `Monitor.card`; `Monitors._watch`; new `Monitors._read_sessions`, `Monitors._watch_sessions`; module function `_said`; module docstring)
- Test: `tests/test_monitors.py`

**Interfaces:**
- Consumes: `Registry.sessions: dict[str, Session]`, `Registry.card(session) -> dict` with keys `attention`, `attention_line`; `Session.handle`.
- Produces:
  - `classify(attention: str) -> str` returning `"finished" | "blocked" | "running"`.
  - `Monitor.sessions: list[dict]`, each `{"log_id", "handle"}` when armed and `{"log_id", "handle", "attention", "state", "line"}` after the first read. Empty for a bash monitor.
  - `Monitor.card()["sessions"]`: `None` for a bash monitor, else a list of `{"handle", "attention", "state", "line"}`. `card()["checks"]` is `[]` for a session monitor.
  - `Monitors.start(owner, cwd, description=..., done="", sessions=[{"log_id", "handle"}, ...], interval_s=..., timeout_s=...)` arms one.

- [ ] **Step 1: Write the failing tests** (append to `tests/test_monitors.py`; extend its import to `from aegis.monitors import Monitor, classify, eta, verdict` and add `from dataclasses import asdict`)

```python
@pytest.mark.parametrize(
    ("attention", "state"),
    [
        ("done", "finished"),
        ("review", "finished"),
        ("closed", "finished"),
        ("needs_you", "blocked"),
        ("error", "blocked"),
        ("working", "running"),
        ("waiting", "running"),
    ],
)
def test_each_attention_classifies_for_a_session_wait(attention, state):
    assert classify(attention) == state


def test_a_session_monitors_card_lists_its_sessions_and_no_checks():
    row = {"handle": "ada-lovelace", "attention": "working", "state": "running", "line": ""}
    m = Monitor(
        id="m", owner="o", description="d", done="", cwd="/",
        sessions=[{"log_id": "x", **row}],
    )
    c = m.card()
    assert c["checks"] == [] and c["sessions"] == [row] and not c["broken"]
    bash = Monitor(id="m", owner="o", description="d", done="false", cwd="/")
    assert bash.card()["sessions"] is None


def test_a_session_monitor_round_trips_through_its_saved_form():
    m = Monitor(
        id="m", owner="o", description="d", done="", cwd="/",
        sessions=[{"log_id": "x", "handle": "ada-lovelace"}],
    )
    assert Monitor(**asdict(m)) == m
```

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run pytest tests/test_monitors.py -q`
Expected: FAIL with `ImportError: cannot import name 'classify'`.

- [ ] **Step 3: Implement in `src/aegis/monitors.py`**

Add to the module docstring, after its first paragraph:

```
A monitor can watch sessions instead of running bash (``monitor_sessions``):
it reads each one's attention card every interval and ends ``ok`` when all
have finished, or ``blocked`` as soon as one needs the person or failed, so a
waiting agent can say what holds it up instead of sitting out the timeout.
Sessions are kept by log id, so a rename does not lose one.
```

After `READINGS_KEPT = 100`:

```python
# What a watched session's attention means to a monitor waiting on it. A
# closed session is finished: its work is over, whatever its last turn said.
FINISHED = frozenset({"done", "review", "closed"})
BLOCKED = frozenset({"needs_you", "error"})


def classify(attention: str) -> str:
    """finished, blocked or running, for a monitor waiting on a session."""
    if attention in FINISHED:
        return "finished"
    if attention in BLOCKED:
        return "blocked"
    return "running"
```

In `class Monitor`, after `checks`:

```python
    # Watched sessions, for a monitor that waits on sessions instead of bash.
    sessions: list[dict] = field(default_factory=list)
```

In `Monitor.card`, build `checks` only for a bash monitor and add `sessions`:

```python
    def card(self) -> dict:
        due = eta(self.started_at, self.readings)
        checks = []
        if not self.sessions:
            for kind in ("done", "progress", "fail"):
                cmd = getattr(self, kind)
                checks.append(
                    {"kind": kind, "cmd": cmd, **(self.checks.get(kind) or {})}
                )
        return {
            "id": self.id,
            "description": self.description,
            "progress": self.last_progress,
            "cwd": self.cwd,
            "started_at": self.started_at,
            "interval_s": self.interval_s,
            "timeout_s": self.timeout_s,
            "readings": self.readings,
            "eta_at": due[0] if due else None,
            "eta_basis": due[1] if due else None,
            "checks": checks,
            "sessions": [
                {k: r.get(k, "") for k in ("handle", "attention", "state", "line")}
                for r in self.sessions
            ]
            or None,
            "broken": any(c.get("bad") for c in checks),
        }
```

In `Monitors._watch`, first thing inside the `try:`:

```python
            if m.sessions:
                await self._watch_sessions(m)
                return
```

Add these two methods to `Monitors`, after `_watch`:

```python
    def _read_sessions(self, m: Monitor) -> list[dict]:
        rows = []
        for r in m.sessions:
            s = self._registry.sessions.get(r["log_id"])
            if s is None:
                attention, handle, line = "closed", r["handle"], ""
            else:
                c = self._registry.card(s)
                attention, handle, line = c["attention"], s.handle, c["attention_line"]
            rows.append(
                {
                    "log_id": r["log_id"],
                    "handle": handle,
                    "attention": attention,
                    "state": classify(attention),
                    "line": line,
                }
            )
        return rows

    async def _watch_sessions(self, m: Monitor) -> None:
        while True:
            if time.time() - m.started_at > m.timeout_s:
                await self._end(
                    m, "timeout", f"it ran out of time after {_elapsed(m.timeout_s)}"
                )
                return
            rows = self._read_sessions(m)
            if rows != m.sessions:
                m.sessions = rows
                done = sum(1 for r in rows if r["state"] == "finished")
                m.last_progress = round(100 * done / len(rows))
                m.read(time.time(), m.last_progress)
                self._publish(m)
            blocked = [r for r in rows if r["state"] == "blocked"]
            if blocked:
                await self._end(m, "blocked", "; ".join(map(_said, blocked)))
                return
            if all(r["state"] == "finished" for r in rows):
                ended = ", ".join(f"{r['handle']} {r['attention']}" for r in rows)
                await self._end(m, "ok", f"every session finished: {ended}")
                return
            await asyncio.sleep(m.interval_s)
```

Add next to `_last_line`:

```python
def _said(row: dict) -> str:
    """A blocked session in a wake: who, why, and its own line if it gave one."""
    why = "needs you" if row["attention"] == "needs_you" else "hit an error"
    return f"{row['handle']} {why}" + (f": {row['line']}" if row["line"] else "")
```

- [ ] **Step 4: Run the monitor tests**

Run: `uv run pytest tests/test_monitors.py -q`
Expected: PASS, the new tests and the eight old ones.

- [ ] **Step 5: Commit**

```bash
git commit -m "feat(monitors): a monitor can wait on sessions instead of bash (#234)" -- src/aegis/monitors.py tests/test_monitors.py
```

---

### Task 3: the `monitor_sessions` tool

**Files:**
- Modify: `src/aegis/agent_ops.py` (new `MonitorSessions` model after `MonitorStart`; new op after `monitor_start`)
- Test: `tests/test_agents.py` (a new `# -- waiting on sessions` section after the monitors section)

**Interfaces:**
- Consumes: `Monitors.start(..., done="", sessions=[{"log_id", "handle"}])` and `Monitor.card()["sessions"]` from Task 2; the local helpers `own`, `target` and `roster` inside `register_agent_ops`.
- Produces: the agent tool `monitor_sessions(description: str, sessions: list[str], interval_s: float = 10, timeout_s: float = 14400)`, returning `{"monitor_id", "other_live_monitors"}`; errors `not_yourself`, `no_session`, `archived`, `not_across_links`.

- [ ] **Step 1: Write the failing tests** (in `tests/test_agents.py`, after the last monitor test)

```python
# -- waiting on sessions ------------------------------------------------------------
def wait_on(*handles: str, **kw) -> str:
    return mcp(
        "monitor_sessions",
        description="the others",
        sessions=list(handles),
        interval_s=1,
        **kw,
    )


async def hold(s) -> str:
    """Keep ``s`` running: a live monitor of its own makes it ``waiting``."""
    said = await turn(
        s,
        mcp("monitor_start", description="hold", done="false", progress=None, interval_s=60),
    )
    return json.loads(said.removeprefix("mcp ok: "))["monitor_id"]


async def test_waiting_on_sessions_wakes_ok_once_every_one_finished(world):
    a, b, c = await world.spawn(), await world.spawn(), await world.spawn()
    held = await hold(b)
    assert b.wire()["attention"] == "waiting"
    said = await turn(a, wait_on(b.handle, c.handle))
    assert said.startswith("mcp ok: ")
    await until(
        lambda: a.wire()["monitors"] and a.wire()["monitors"][0]["progress"] == 50,
        what="1 of 2 finished",
    )
    (m,) = a.wire()["monitors"]
    assert [(r["handle"], r["state"]) for r in m["sessions"]] == [
        (b.handle, "running"),
        (c.handle, "finished"),
    ]
    assert m["checks"] == []
    assert inbox(a) == []
    await turn(b, mcp("monitor_cancel", monitor_id=held))
    await until(lambda: inbox(a), timeout=5, what="the wake")
    (wake,) = inbox(a)
    assert " · ok · " in wake["title"]
    assert f"{b.handle} done" in wake["md"] and f"{c.handle} done" in wake["md"]
    assert a.wire()["monitors"] == []


async def test_a_session_that_needs_the_person_ends_the_wait_blocked(world):
    a, b = await world.spawn(), await world.spawn()
    await hold(b)
    await turn(a, wait_on(b.handle))
    await until(
        lambda: a.wire()["monitors"]
        and a.wire()["monitors"][0]["sessions"][0]["state"] == "running",
        what="b running",
    )
    await turn(b, mcp("turn_end", attention="needs_you", line="Rebase or merge?"))
    await until(lambda: inbox(a), timeout=5, what="the blocked wake")
    (wake,) = inbox(a)
    assert " · blocked · " in wake["title"]
    assert f"{b.handle} needs you: Rebase or merge?" in wake["md"]


async def test_sessions_already_finished_wake_the_waiter_after_its_turn(world):
    """Review focus 3: nothing to wait for still answers, once the turn ends."""
    a, b = await world.spawn(), await world.spawn()
    said = await turn(a, wait_on(b.handle))
    assert said.startswith("mcp ok: ")
    await until(lambda: inbox(a), timeout=5, what="the wake")
    assert " · ok · " in inbox(a)[0]["title"]


async def test_a_watched_session_is_followed_through_a_rename_and_a_close(world):
    """Review focus 1, 2 and 4: kept by log id, listed once, closed is finished."""
    a, b = await world.spawn(), await world.spawn()
    await hold(b)
    await turn(a, wait_on(b.handle, b.handle))
    (m,) = a.wire()["monitors"]
    assert len(m["sessions"]) == 1
    await turn(b, mcp("session_rename", handle="renamed-peer"))
    await until(
        lambda: a.wire()["monitors"][0]["sessions"][0]["handle"] == "renamed-peer",
        timeout=3,
        what="the new handle on the card",
    )
    await world.app.registry.call("session.close", {"log_id": b.log_id})
    await until(lambda: inbox(a), timeout=5, what="the wake")
    (wake,) = inbox(a)
    assert " · ok · " in wake["title"] and "renamed-peer closed" in wake["md"]


async def test_waiting_on_sessions_refuses_itself_unknown_far_and_archived(world):
    """Review focus 5, and the addresses target() already refuses."""
    a, b = await world.spawn(), await world.spawn()
    cases = (
        ([a.handle], "not_yourself"),
        (["no-such-one"], "no_session"),
        ([f"{b.handle}@far"], "not_across_links"),
    )
    for handles, code in cases:
        said = await turn(a, wait_on(*handles))
        assert said.startswith(f"mcp error: {code}"), said
    await world.app.registry.call("session.close", {"log_id": b.log_id})
    said = await turn(a, wait_on(b.handle))
    assert said.startswith("mcp error: archived"), said
    said = await turn(a, mcp("monitor_sessions", description="x", sessions=[]))
    assert said.startswith("mcp error"), said
    assert a.wire()["monitors"] == []


@pytest.mark.slow  # a restart
async def test_a_session_monitor_survives_a_restart(world):
    a, b = await world.spawn(), await world.spawn()
    await hold(b)
    await turn(a, wait_on(b.handle))
    await world.restart()
    (m,) = world.session(a.log_id).wire()["monitors"]
    assert m["sessions"][0]["handle"] == b.handle
```

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run pytest tests/test_agents.py -q -k "sessions or needs_the_person or rename_and_a_close"`
Expected: FAIL; the fake claude prints `mcp error` for an unknown tool `monitor_sessions`.

- [ ] **Step 3: Implement in `src/aegis/agent_ops.py`**

After `class MonitorStart`:

```python
class MonitorSessions(_Strict):
    description: str = Field(
        min_length=1,
        description="What is being waited on, shown on the card and in the wake.",
    )
    sessions: list[str] = Field(
        min_length=1,
        max_length=20,
        description="Handles of open sessions on this server, from session_list.",
    )
    interval_s: float = Field(10, ge=1, le=3600)
    # Other sessions' work runs longer than a build.
    timeout_s: float = Field(4 * 3600, gt=0, le=7 * 86400)
```

After the `monitor_start` op:

```python
    @r.op("monitor.sessions", MonitorSessions, agent=True)
    async def monitor_sessions(p: MonitorSessions, caller):
        """Wait for other sessions on this server to finish, without polling.
        You are woken `ok` when every one has finished (its last turn ended done
        or for review, or it was closed), `blocked` as soon as one needs the
        person or hit an error, naming it, and `timeout` otherwise. Pick the
        handles from session_list. Returns at once; end your turn after calling
        it. monitor_cancel stops it."""
        s = own(caller)
        picked: dict[str, dict] = {}
        for handle in p.sessions:
            t = target(handle)
            if t.log_id == s.log_id:
                raise OpError(
                    "not_yourself",
                    "a session cannot wait for itself: its own wait keeps it waiting",
                )
            picked.setdefault(t.log_id, {"log_id": t.log_id, "handle": t.handle})
        m = app.monitors.start(
            s.log_id,
            str(s.spec.cwd),
            description=p.description,
            done="",
            sessions=list(picked.values()),
            interval_s=p.interval_s,
            timeout_s=p.timeout_s,
        )
        return {"monitor_id": m.id, "other_live_monitors": roster(s.log_id, m.id)}
```

Add `"monitor_sessions"` to the set of tool names in `test_the_tools_are_named_after_their_operations_and_take_no_handle`.

- [ ] **Step 4: Run the new tests and the whole monitor and agents files**

Run: `uv run pytest tests/test_agents.py tests/test_attention_e2e.py tests/test_monitors.py -q -m "not slow" && uv run pytest tests/test_agents.py -q -k survives_a_restart`
Expected: PASS. If an unmarked test exceeds 3 s, find out why before marking it slow: every wait here polls at 1 s.

- [ ] **Step 5: Break it on purpose**

In `classify`, temporarily move `"review"` from `FINISHED` to `BLOCKED`; run `uv run pytest tests/test_monitors.py -q`; confirm the parametrized `review` case fails. Then remove `"closed"` from `FINISHED` and confirm `test_a_watched_session_is_followed_through_a_rename_and_a_close` fails. Restore both and re-run until green.

- [ ] **Step 6: Commit**

```bash
git commit -m "feat(agents): monitor_sessions waits on other sessions (#234)" -- src/aegis/agent_ops.py tests/test_agents.py
```

---

### Task 4: the monitor row and card draw sessions

**Files:**
- Modify: `src/aegis/client/js/monitors.js`
- Test: `tests/test_browser.py` (after `test_a_monitor_whose_check_cannot_run_is_marked_and_its_card_says_why`)

**Interfaces:**
- Consumes: the card's `sessions` (Task 2): `null`, or a list of `{handle, attention, state, line}`; `progress` as finished over listed, in percent.
- Produces: the row reads `N of M · <age>`; the card's big number reads `N of M`, and a "Sessions" section with one `.ck` per session (`.ck.bad` when blocked) replaces "Checks".

- [ ] **Step 1: Write the failing browser test**

```python
def test_a_monitor_on_sessions_counts_them_in_its_row_and_card(server, page):
    page.goto(server.url)
    page.wait_for_selector("#a2[data-view=fleet]")
    busy = spawn(page)
    page.fill("#input", "/sleep 30")
    page.press("#input", "Enter")
    page.wait_for_selector(".row.tool")
    idle = spawn(page)
    spawn(page)
    args = {
        "description": "wait for the others",
        "sessions": [busy, idle],
        "interval_s": 1,
    }
    page.fill("#input", f"/mcp monitor_sessions {json.dumps(args)}")
    page.press("#input", "Enter")
    page.wait_for_selector("#s-monitors .mon >> text=1 of 2")
    page.hover("#s-monitors .mon")
    page.wait_for_selector("#mcard.show")
    card = page.inner_text("#mcard")
    assert "Sessions" in card and "Checks" not in card
    assert "working" in card and "done" in card
    assert "1 of 2" in page.inner_text("#mcard .big")
    assert page.errors == []
```

`spawn()` returns the log id, and `monitor_sessions` resolves a log id as it resolves a handle (`target()` tries `reg.sessions.get(handle)` second).

- [ ] **Step 2: Run it to verify it fails**

Run: `uv run pytest tests/test_browser.py -q -k monitor_on_sessions`
Expected: FAIL on `text=1 of 2` (the row shows `50%`), or on `page.errors` from `hasProgressCmd` reading `.cmd` of `undefined`.

- [ ] **Step 3: Implement in `src/aegis/client/js/monitors.js`**

Replace `hasProgressCmd` and add `tally` below it:

```js
const hasProgressCmd = (m) => !!m.checks.find((c) => c.kind === "progress")?.cmd;
// A monitor on sessions counts them instead of a percent: "1 of 2".
const tally = (m) => `${m.sessions.filter((r) => r.state === "finished").length} of ${m.sessions.length}`;
```

First line of `rowRight`'s chain becomes:

```js
  if (m.sessions) span.replaceChildren(tally(m), tail(age(nowS() - m.started_at)));
  else if (m.broken) span.replaceChildren("check fails");
```

(the rest of the chain unchanged).

Add after `function check(c)`:

```js
// -- one watched session: its handle and what it last said ---------------------
function watched(r) {
  return h(
    "div",
    `ck${r.state === "blocked" ? " bad" : ""}`,
    h("div", "h", h("span", "k", r.handle), h("span", "res", (r.attention || "not read yet").replace("_", " "))),
    r.line && h("div", "err", r.line),
  );
}
```

In `card(m, live)`, replace the `left` constant:

```js
  const left = m.sessions
    ? h("span", "p", tally(m))
    : measured(m)
      ? h("span", "p", String(m.progress), h("small", null, "%"))
      : h("span", "p unk", !hasProgressCmd(m) ? "no progress command" : m.broken ? "no reading" : "no reading yet");
```

and the `checks` constant:

```js
  const checks = m.sessions
    ? h("div", "checks", h("h5", null, "Sessions"), ...m.sessions.map(watched))
    : h("div", "checks", h("h5", null, "Checks"), ...m.checks.map(check));
```

- [ ] **Step 4: Run the browser monitor tests**

Run: `uv run pytest tests/test_browser.py -q -k monitor`
Expected: PASS, the new test and the four existing monitor tests. Then `uv run pytest tests/test_client_rules.py -q` (PASS).

- [ ] **Step 5: Commit**

```bash
git commit -m "feat(web): a monitor on sessions shows N of M and its sessions (#234)" -- src/aegis/client/js/monitors.js tests/test_browser.py
```

---

### Task 5: the primer, DESIGN.md, the spec status and the changelog

**Files:**
- Modify: `src/aegis/mcp.py` (the primer paragraph that starts "To wait on a long process")
- Modify: `DESIGN.md` (the paragraph "**Agents change only what they created.**")
- Modify: `docs/superpowers/specs/2026-10-09-agents-see-aegis-design.md` (status line)
- Create: `changelog.d/234-agents-see-sessions.added.md`

- [ ] **Step 1: Primer.** After the sentence ending "Pass null only when nothing can be counted." in `src/aegis/mcp.py`, add (keep the file's `\` line continuations):

```
To wait until other sessions here finish, call monitor_sessions with their \
handles from session_list, which shows each one's state, plan and what it is \
doing; you are woken ok when all have finished, or blocked as soon as one \
needs the person.
```

Run: `uv run pytest tests/test_meta.py tests/test_agents.py -q -m "not slow" -k "meta or primer"`
Expected: PASS. If a test pins the primer's text, update it to include the new sentence.

- [ ] **Step 2: DESIGN.md.** After the sentence "People can do anything." in the paragraph "**Agents change only what they created.**", add:

```
Reading includes waiting: `monitor_sessions` watches other sessions' attention
cards and wakes its owner when all have finished or one is blocked on the
person, and `session_list` shows each card and plan. Neither changes the
sessions it watches.
```

- [ ] **Step 3: Spec status.** Replace the spec's status paragraph with:

```
**Status: part 1 implemented, 2026-10-09** (issue #234), following
`docs/superpowers/plans/2026-10-09-agents-see-sessions.md`. Part 2 (issue
#235) is designed and has no plan yet. Designed with Alex in a brainstorm,
text only.
```

- [ ] **Step 4: Changelog fragment** `changelog.d/234-agents-see-sessions.added.md`:

```
- **Agents see other sessions and wait on them.** `session_list` now carries
  each session's attention and its line, what it waits on, its plan with what
  it is doing now, its model and its spend. The new `monitor_sessions` tool
  waits until a list of sessions has finished and wakes the agent `ok`, or
  `blocked` as soon as one needs you, so "when the une-tools sessions finish,
  cut a release" works without polling. Its card in the sidebar reads
  "1 of 2" and lists each session (#234).
```

Run: `make changelog-check && make lint-docs`
Expected: both exit 0.

- [ ] **Step 5: Commit**

```bash
git commit -m "docs: primer, DESIGN.md and changelog for waiting on sessions (#234)" -- src/aegis/mcp.py DESIGN.md docs/superpowers/specs/2026-10-09-agents-see-aegis-design.md changelog.d/234-agents-see-sessions.added.md
```

---

### Task 6: a real Claude Code session, the gates, and the PR

**Files:**
- Test: `tests/test_live.py` (after `test_real_claude_arms_a_monitor_through_the_endpoint_and_is_woken`)

- [ ] **Step 1: Write the live test**

```python
async def test_real_claude_waits_on_another_session_and_is_woken(tmp_path: Path):
    """The primer's monitor_sessions paragraph, followed by a real model."""
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
    app = App(
        make_roots(tmp_path, None),
        claude_bin=claude,
        base_url=f"http://127.0.0.1:{port}",
    )
    server = uvicorn.Server(
        uvicorn.Config(
            build_web(app, "t", {f"127.0.0.1:{port}"}), port=port, log_level="warning"
        )
    )
    task = asyncio.create_task(server.serve())
    await until(lambda: server.started, timeout=10, what="uvicorn")
    try:
        r = await app.registry.call("session.spawn", {"agent": "haiku"})
        busy = app.sessions.sessions[r["log_id"]]
        r = await app.registry.call("session.spawn", {"agent": "haiku"})
        s = app.sessions.sessions[r["log_id"]]
        await busy.send("Run the shell command `sleep 25`, then reply with the word SLEPT.")
        await until(lambda: busy.status == "working", timeout=60, what="busy working")
        await s.send(
            f"Wait, using aegis, until the session {busy.handle} has finished, "
            "then reply with the single word FINISHED."
        )
        await until(
            lambda: any(m.sessions for m in app.monitors.of(s.log_id)),
            timeout=120,
            what="a session monitor armed by Claude",
        )
        await until(
            lambda: any(
                "FINISHED" in (e.get("md") or "")
                for e in s.entries()
                if e["kind"] == "prose"
            ),
            timeout=180,
            what="the wake and Claude's answer",
        )
        calls = [e["title"] for e in s.entries() if e["kind"] == "tool"]
        assert "monitor_sessions" in calls
    finally:
        server.should_exit = True
        await asyncio.wait_for(task, 30)
```

- [ ] **Step 2: Run the live suite**

Run: `make test-live`
Expected: PASS, the new test included. It spends a few cents of Haiku. If Haiku does not reach for `monitor_sessions` in 2 runs of 3, run the test on `SONNET` as `test_real_claude_names_a_bash_call...` does, and say so in the PR body.

- [ ] **Step 3: Commit the live test**

```bash
git commit -m "test(live): a real session waits on another with monitor_sessions (#234)" -- tests/test_live.py
```

- [ ] **Step 4: The gates.** Run each and read its exit code directly, never through a pipe:

```bash
make format-check
make check
make bench
```

Expected: all exit 0. `make check` includes the browser tests. Keep `make bench`'s table for the PR body.

- [ ] **Step 5: Exercise it in a browser against a fresh server.** Start `aegis serve` from this worktree on a free port with a throwaway state root (never against Workspace's live state; see the workspace memory "aegis config walks up to Workspace"). Open three tabs: in one run `/sleep 60`, in the second ask the agent to wait for the first with `monitor_sessions`, and check that its sidebar row reads "0 of 1", that the card lists the session as `working`, and that the wake arrives when the sleep ends. Note what you saw in the PR body.

- [ ] **Step 6: Push and open the PR**

```bash
git push -u origin agents-see-aegis
gh pr create --title "Agents see other sessions and wait on them (#234)" --body-file <body.md>
```

The body says: closes #234; what was measured (the `make bench` table; the live test's model and pass count); what was tried and rejected (a `sessions` field on `monitor_start`, rejected because of #165); what is left out (part 2, #235). It ends with `🤖 Generated with [Claude Code](https://claude.com/claude-code)`.

- [ ] **Step 7: Hand it to Alex for the smoke test.** Ask Alex to run the branch with `AEGIS_REF=agents-see-aegis aegis-dev` and try his own case: "when the une-tools sessions finish, cut a release". The PR merges only on green CI and after he has run it.
