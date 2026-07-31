# `@peer` Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans
> to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax.

**Goal:** Type `@lucid-knuth is this right?` in any pane; an idle peer
answers from a teaser of your conversation; the answer lands as a real turn
in its transcript and a transient block in yours.

**Architecture:** `@handle …` is **sugar for a slash command** — the one
departure from the spec, and it collapses most of the projected build.
`classify_input` rewrites `@foo bar` → `/peer foo bar`, so the existing
dispatch path, `CommandResult.effect` channel, palette, and web seam all
work unchanged. The only genuinely new machinery is
`session_send_and_await` on `SessionManager`.

**Tech Stack:** Python 3.13+, `uv run pytest`, Textual 8.x.

**Spec:** `docs/superpowers/specs/2026-07-31-aegis-at-mention-peer-ask-design.md`

## Global Constraints

- TDD: failing test first, minimal implementation, commit per logical unit.
- `uv run python -m pytest -q -m "not live"` must be green before every commit.
  Never pipe the gate — read its rc directly.
- Best-effort by contract: every failure returns `PeerAnswer(ok=False, error=…)`.
  A peer ask must never disturb the conversation it sits beside.
- The source pane's session log is **never** appended to. Assert against the
  file on disk, not the pane.
- Target must be live **and** `state == "ready"`. The guard is on the target,
  never the source — `/peer` is legal while the source is mid-turn.

---

### Task 1 (VS1): `session_send_and_await` + `/peer` end to end — ✅ *shipped*

The walking skeleton. No teaser, no `aegis_read_peer`, no `--cc`, no `@`
sugar — `/peer <handle> <question>` typed literally, answered, rendered.

**Files:**
- Create: `src/aegis/peer/__init__.py` (`PeerAnswer`, `peer_ask`)
- Modify: `src/aegis/core/session.py` (`await_next_reply`)
- Modify: `src/aegis/core/manager.py` (`session_send_and_await`, `peer_ask`)
- Modify: `src/aegis/mcp/bridge.py` (`AppBridge.peer_ask`)
- Modify: `src/aegis/tui/app.py` (`peer_ask` delegating to the manager)
- Modify: `src/aegis/tui/remote_manager.py` (explicit refusal)
- Modify: `src/aegis/commands/builtins/core.py` (`_peer` + registration)
- Modify: `src/aegis/render.py` (`render_peer_answer`)
- Modify: `src/aegis/tui/pane.py` (`peer_answer` effect branch)
- Test: `tests/test_peer_ask.py`, `tests/test_peer_command.py`

**Interfaces:**
- Produces:
  - `PeerAnswer(answer, target, header, model, duration_ms, cost_usd, ok, error)`
    — frozen dataclass, `asdict`-able (the web seam ships `effect` as JSON,
    so the effect payload is a plain dict, never the dataclass).
  - `PeerAnswer.footer` → `"target · 12.4s · $0.0312"`.
  - `AgentSession.await_next_reply(timeout) -> str` — register a one-shot
    event observer, accumulate `AssistantText.text`, resolve on `Result`.
  - `SessionManager.session_send_and_await(handle, prompt, timeout) -> str`
  - `AppBridge.peer_ask(from_handle, target, prompt) -> PeerAnswer`
- Consumes: `AgentSession.deliver` (`core/session.py:254`), `Delivery`,
  `InboxMessage` / `now_iso` / `sender_agent` (`queue/schema.py`),
  `render.coalesce_chunks` (`render.py:109`).

- [x] **Step 1: Write failing tests for `PeerAnswer` + refusal matrix**

```python
# tests/test_peer_ask.py
def test_footer_names_target_and_price():
    a = PeerAnswer(answer="yes", target="lucid-knuth",
                   model="opus", duration_ms=12400, cost_usd=0.0312)
    assert a.footer == "lucid-knuth · opus · 12.4s · $0.0312"

async def test_refuses_unknown_target(mgr):
    a = await mgr.peer_ask("alpha", "nobody", "hi")
    assert not a.ok and "unknown" in a.error

async def test_refuses_self():
    a = await mgr.peer_ask("alpha", "alpha", "hi")
    assert not a.ok and "itself" in a.error

async def test_refuses_busy_target_and_names_enqueue(mgr_busy):
    a = await mgr_busy.peer_ask("alpha", "beta", "hi")
    assert not a.ok and "mid-turn" in a.error and "/enqueue" in a.error
```

- [x] **Step 2: Run to verify failure** — `uv run python -m pytest tests/test_peer_ask.py -q`
- [x] **Step 3: Implement `PeerAnswer` + `peer_ask` guards**
- [x] **Step 4: Write failing test for `await_next_reply`**

```python
async def test_await_next_reply_returns_final_text(fake_session):
    fut = asyncio.create_task(fake_session.await_next_reply(timeout=5))
    fake_session.emit(AssistantText(text="part one "))
    fake_session.emit(AssistantText(text="part two"))
    fake_session.emit(Result(duration_ms=10, is_error=False))
    assert await fut == "part one part two"

async def test_observer_detaches_after_one_reply(fake_session):
    # a second turn must not resolve the same future twice
```

- [x] **Step 5–7: Implement, verify green, commit**
- [x] **Step 8: Failing test — the source log is not appended to**

```python
async def test_source_transcript_untouched(tmp_project):
    before = source_log_path.read_bytes()
    await mgr.peer_ask("alpha", "beta", "hi")
    assert source_log_path.read_bytes() == before
```

- [x] **Step 9: `/peer` command + `render_peer_answer` + pane branch**

Registration mirrors `/btw` (`commands/builtins/core.py:344`):

```python
SlashCommand("peer", "ask an idle peer, from where you're standing",
             "/peer <handle> <question>", _peer,
             spec=ArgSpec(positionals=(
                 Arg("handle", completer=_session_choices),
                 Arg("prompt", required=False, greedy=True))))
```

- [x] **Step 10: Full suite green, commit**

---

### Task 2 (VS2): `@handle` sugar + palette — ✅ *shipped* (TUI gate outstanding, see Task 5)

**Files:**
- Modify: `src/aegis/commands/__init__.py` (`classify_input`, `complete`)
- Modify: `src/aegis/tui/pane.py:1361` (widen the `/` gate to `("/", "@")`)
- Test: `tests/test_peer_at_sugar.py`

**Interfaces:**
- Consumes: `Completion` (`commands/__init__.py:118`), `SessionInfo.state`
  (`mcp/bridge.py:8`), `bridge.list_sessions()`.

- [x] **Step 1: Failing tests for `classify_input`**

```python
def test_at_handle_rewrites_to_peer_command():
    assert classify_input("@foo is this right?") == ("command", "/peer foo is this right?")

def test_double_at_escapes_to_literal_message():
    assert classify_input("@@foo") == ("message", "@foo")

def test_bare_at_is_a_plain_message():
    assert classify_input("@") == ("message", "@")

def test_at_with_no_question_still_routes():
    assert classify_input("@foo") == ("command", "/peer foo")
```

- [x] **Step 2–3: Implement `classify_input`, verify**
- [ ] **Step 4: widen the pane gate** — `tui/pane.py:1361`
      `startswith("/")` → `startswith(("/", "@"))`. **NOT DONE**: that file
      is under `btw-rendering`'s exclusive claim while it restructures
      `on_growing_input_submitted`; the change is handed over, not made.
      Until it lands, `@foo hi` is delivered to the current agent as
      literal text in the TUI (the web seam has no gate and already works).
- [x] **Step 5: Failing tests for `@` completion**

```python
def test_at_completes_live_handles_with_busy_detail(bridge):
    items = complete("@luc", bridge).items
    assert items[0].insert == "@lucid-knuth "
    assert "busy" in items[1].detail        # a working peer is marked
```

- [x] **Step 6–8: Implement, verify green, commit**

---

### Task 3 (VS3): the teaser + `sender_operator_at` + the real prompt — ✅ *shipped*

**Files:**
- Modify: `src/aegis/peer/__init__.py` (`compose`, teaser assembly)
- Modify: `src/aegis/queue/schema.py` (`sender_operator_at`)
- Test: `tests/test_peer_teaser.py`

**Interfaces:**
- Consumes: `btw.window.assemble(replay, *, max_turns, budget_tokens,
  item_chars) -> Window` (`btw/window.py:125`) with `.text` / `.header`;
  `state.session_log.replay_events(state_dir, log_id)`;
  `SessionManager.get(handle).log_id`.
- Produces: `sender_operator_at(handle) -> "operator@<handle>"`.

- [x] **Step 1: Failing test — the teaser costs no model call**

```python
async def test_teaser_makes_no_generate_call(mgr, spy_driver):
    await mgr.peer_ask("alpha", "beta", "hi")
    assert spy_driver.generate_calls == 0   # the whole cost argument
```

- [x] **Step 2: Failing test — the composed body carries place, not author**

```python
def test_prompt_frames_the_operator_not_the_source_agent():
    body = compose(source="alpha", slug="claude", window=w, prompt="q")
    assert "operator typed this" in body
    assert "aegis_read_peer(\"alpha\")" in body
    assert "Do not start long work" in body

def test_header_is_carried_verbatim():
    assert w.header in compose(...)      # the legible-boundary property
```

- [x] **Step 3: Failing test — an unreadable log degrades, never fails**

```python
async def test_missing_transcript_sends_without_teaser(mgr_no_log):
    a = await mgr_no_log.peer_ask("alpha", "beta", "hi")
    assert a.ok and "no transcript" in a.header
```

- [x] **Step 4–6: Implement, verify green, commit**

---

### Task 4 (VS4): `aegis_read_peer` + `--cc` — ✅ *shipped*

**Files:**
- Modify: `src/aegis/peer/__init__.py` (`read_peer`)
- Modify: `src/aegis/mcp/bridge.py`, `src/aegis/core/manager.py`,
  `src/aegis/tui/app.py`, `src/aegis/tui/remote_manager.py` (`read_peer`)
- Modify: `src/aegis/mcp/server.py` (`aegis_read_peer` tool + briefing line)
- Modify: `src/aegis/commands/builtins/core.py` (`Flag("cc", takes_value=False)`)
- Test: `tests/test_peer_read.py`, `tests/test_mcp_bridge.py`

**Interfaces:**
- Produces: `read_peer(handle, turns=12) -> {text, header, ok, error}`.

- [x] **Step 1: Failing test — `read_peer` windows a live peer's log**
- [x] **Step 2: Failing test — refuses a closed/unknown handle by name**
- [x] **Step 3: Implement + register the MCP tool, verify**
- [x] **Step 4: Failing tests for `--cc`**

```python
async def test_cc_delivers_answer_into_the_source(mgr):
    await mgr.peer_ask("alpha", "beta", "q", cc=True)
    assert "answer" in delivered_to("alpha")

async def test_bare_ask_does_not_deliver_to_source(mgr):
    await mgr.peer_ask("alpha", "beta", "q")
    assert delivered_to("alpha") == []
```

- [x] **Step 5: Bridge conformance across all three implementations**
- [x] **Step 6: Full suite green, commit**

---

### Task 5: docs + one live test — 🔶 *docs done; live test + TUI wiring outstanding*

- [ ] Live test behind the `live` marker: real `claude` peer, real turn,
      answer captured, source log byte-identical afterwards.
- [ ] `AGENTS.md` layout entry for `src/aegis/peer/`.
- [ ] `CHANGELOG.md` entry.
- [ ] `TASKS.md`: mark the arc, record what shipped and what deferred
      (multicast, clickable mentions in agent output, closed-session reads,
      web block treatment).
- [ ] Update the spec's status header and note the `/peer`-sugar departure.

## Self-Review

**Spec coverage.** Idle-only guard → T1. Legal-while-source-is-mid-turn →
T1 (guard reads the target only). Palette busy/idle → T2. Teaser + honest
header → T3. Provenance-of-place + `sender_operator_at` → T3.
Answer-don't-embark clause → T3. Real turn in B / transient block in A →
T1. `--cc` at send time → T4. `aegis_read_peer` → T4. Failure matrix → T1
and T3. Deferred items are recorded in T5, not built.

**Departure from spec:** `@handle` is sugar for `/peer`, so there is no new
input route in either seam — `classify_input` rewrites and the existing
dispatch path carries it. The spec's "a `peer_ask` route in the two input
seams" is therefore not built; the pane's `/`-gate widens by four
characters and the web seam needs no change at all. `--cc` stays a flag
(`@@` is the literal-`@` escape).

**Type consistency:** `PeerAnswer` fields are used identically in T1, T3,
T4. `peer_ask(from_handle, target, prompt, cc=False)` keeps that signature
from T1 through T4 (`cc` defaults false and is only honoured from T4).
