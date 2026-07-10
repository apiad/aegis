# Aegis Spawn Tool Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add `aegis_spawn` — an MCP tool that lets an agent create a genuine independent peer agent with an opening prompt, fire-and-forget, recording `spawned_by` provenance.

**Architecture:** Thread two optional kwargs (`opening_prompt`, `spawned_by`) through the existing `AppBridge.spawn` seam and both implementers (`SessionManager`, TUI `AegisApp`), which already have all the spawn machinery. Store `spawned_by` as an attribute on the spawned `AgentSession`, surface it via `SessionInfo`, and expose a new `aegis_spawn` MCP tool.

**Tech Stack:** Python 3.13+, `uv`, pytest (`uv run python -m pytest`), FastMCP, Textual 8.x.

## Global Constraints

- Python 3.13+.
- Package manager is `uv` (`uv run python -m pytest`, `uv pip install -e .`), never bare pip.
- TDD: failing test first, then minimal implementation, commit per logical unit.
- Run the fast hermetic suite with `uv run python -m pytest -q -m "not live"`. Never `-k "not live"` (substring-matches unrelated names).
- Spec: `docs/superpowers/specs/2026-07-10-aegis-spawn-tool-design.md`.
- `spawned_by` defaults to `None` (a boot session or a queue/group worker has no spawner).
- Feedback from spawned agents rides the existing inbox — do NOT add any new delivery path.

---

### Task 1: `SessionInfo.spawned_by` + `SessionManager` spawn provenance

Extend the headless spawn path (used by `aegis serve` / web) to accept an opening prompt and a spawner, store the spawner on the session, and surface it in `SessionInfo`.

**Files:**
- Modify: `src/aegis/mcp/bridge.py` (SessionInfo dataclass; AppBridge.spawn signature)
- Modify: `src/aegis/core/manager.py` (`_sync_spawn`, `spawn`, `list_sessions`)
- Test: `tests/test_core_manager.py`

**Interfaces:**
- Produces: `SessionInfo(handle, agent_slug, state, active, unseen, spawned_by: str | None = None)`.
- Produces: `SessionManager.spawn(profile, *, handle=None, opening_prompt=None, spawned_by=None) -> str`.
- Produces: spawned `AgentSession` carries attribute `.spawned_by: str | None`.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_core_manager.py` (the file already has `make_mgr()` and imports `asyncio`, `pytest`). The `FakeHarness.send` in that file is a no-op, so Task 1 verifies **provenance** (the new behavior); opening-prompt *delivery* is verified in Task 2's TUI test, whose `FakeSession` records `.sent`.

```python
@pytest.mark.asyncio
async def test_spawn_records_spawned_by_and_surfaces_it():
    m = make_mgr()
    h = await m.spawn("default", handle="child-one",
                      opening_prompt="do the thing", spawned_by="parent-x")
    sess = m.get(h)
    assert sess is not None
    assert sess.spawned_by == "parent-x"
    info = next(i for i in m.list_sessions() if i.handle == h)
    assert info.spawned_by == "parent-x"


@pytest.mark.asyncio
async def test_boot_session_has_no_spawned_by():
    m = make_mgr()
    s = m._sync_spawn("default")
    info = next(i for i in m.list_sessions() if i.handle == s.handle)
    assert info.spawned_by is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run python -m pytest tests/test_core_manager.py::test_spawn_records_spawned_by_and_surfaces_it -v`
Expected: FAIL — `spawn()` got an unexpected keyword argument `spawned_by` (or `AttributeError: spawned_by`).

- [ ] **Step 3: Add `spawned_by` to `SessionInfo`**

In `src/aegis/mcp/bridge.py`, extend the dataclass:

```python
@dataclass(frozen=True)
class SessionInfo:
    handle: str
    agent_slug: str
    state: str          # AgentState.value: "ready" | "working" | "error"
    active: bool
    unseen: bool
    spawned_by: str | None = None
```

- [ ] **Step 4: Thread `opening_prompt` + `spawned_by` through `SessionManager`**

In `src/aegis/core/manager.py`, update `_sync_spawn` to stamp the spawner on the session, and widen `spawn`:

```python
    def _sync_spawn(self, slug: str | None = None, *,
                    opening_prompt: str | None = None,
                    handle: str | None = None,
                    spawned_by: str | None = None) -> AgentSession:
        slug = slug or self._default_agent
        if slug not in self._agents:
            raise KeyError(slug)
        agent = self._agents[slug]
        h = handle or generate_name({s.handle for s in self._sessions})
        url = self._mcp.url if self._mcp is not None else ""
        s = AgentSession(self._make_session(agent, url, h),
                         agent, slug, h,
                         inbox=self._inbox,
                         opening_prompt=opening_prompt)
        s.spawned_by = spawned_by
        if self._inbox is not None:
            self._inbox.bind_session(h, s)
        self._sessions.append(s)
        if self._persist_dir is not None:
            from aegis.state.session_log import make_session_log_observer
            s.add_event_observer(make_session_log_observer(self._persist_dir, h))
        self._touch(h)
        if opening_prompt is not None:
            asyncio.create_task(s.send(opening_prompt))
        return s

    async def spawn(self, profile: str, *,
                    handle: str | None = None,
                    opening_prompt: str | None = None,
                    spawned_by: str | None = None) -> str:
        """AppBridge-shaped async spawn. Returns the new handle."""
        sess = self._sync_spawn(profile, handle=handle,
                                opening_prompt=opening_prompt,
                                spawned_by=spawned_by)
        return sess.handle
```

Update `list_sessions` to surface it:

```python
    def list_sessions(self) -> list[SessionInfo]:
        top = self._mru[0] if self._mru else None
        return [
            SessionInfo(handle=s.handle, agent_slug=s.agent_slug,
                        state=s.state.value, active=(s.handle == top),
                        unseen=False,
                        spawned_by=getattr(s, "spawned_by", None))
            for s in self._sessions
        ]
```

- [ ] **Step 5: Update the `AppBridge.spawn` protocol signature**

In `src/aegis/mcp/bridge.py`, widen the protocol method so both implementers share one shape:

```python
    async def spawn(self, profile: str, *,
                    handle: str | None = None,
                    opening_prompt: str | None = None,
                    spawned_by: str | None = None) -> str: ...
```

- [ ] **Step 6: Run test to verify it passes**

Run: `uv run python -m pytest tests/test_core_manager.py -q`
Expected: PASS (new tests + existing manager tests still green).

- [ ] **Step 7: Commit**

```bash
git add src/aegis/mcp/bridge.py src/aegis/core/manager.py tests/test_core_manager.py
git commit -m "feat(core): SessionManager.spawn takes opening_prompt + spawned_by; SessionInfo carries provenance"
```

---

### Task 2: TUI `AegisApp.spawn` provenance + opening prompt

The TUI is the second `AppBridge` implementer. Its `_spawn`/`_SessionManagerAdapter.spawn` already handle `opening_prompt`; the AppBridge `spawn` method just drops it. Thread both kwargs through and stamp `spawned_by` on the pane's `_core`.

**Files:**
- Modify: `src/aegis/tui/app.py` (`AegisApp.spawn`, `_SessionManagerAdapter.spawn`, `list_sessions`)
- Test: `tests/test_tui.py`

**Interfaces:**
- Consumes: `SessionInfo.spawned_by` (Task 1).
- Produces: `AegisApp.spawn(profile, *, handle=None, opening_prompt=None, spawned_by=None) -> str` mounts a pane, fires the opening turn, records provenance.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_tui.py` (helpers `_app`, `_factory`, `FakeSession`, `Input` are already imported at the top of the file):

```python
@pytest.mark.asyncio
async def test_appbridge_spawn_delivers_prompt_and_records_spawner():
    app = _app(_factory(FakeSession(), FakeSession()))
    async with app.run_test() as pilot:
        parent = app._panes[0]
        handle = await app.spawn("default", handle="child-one",
                                 opening_prompt="go audit",
                                 spawned_by=parent.handle)
        await pilot.pause()
        await pilot.pause()
        child = next(p for p in app._panes
                     if getattr(p, "handle", None) == "child-one")
        assert child._core.session.sent == ["go audit"]
        info = next(i for i in app.list_sessions() if i.handle == handle)
        assert info.spawned_by == parent.handle
        # spawn must not steal focus from the parent tab
        assert app.focused is not child._core  # sanity: child isn't grabbed
```

`FakeSession.sent` is the list the fake records `send()` calls into (see the `FakeSession` class near the top of `tests/test_tui.py`); `child._core.session` is the underlying fake harness. Adjust the attribute path if the pane wraps the fake differently.

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run python -m pytest tests/test_tui.py::test_appbridge_spawn_delivers_prompt_and_records_spawner -v`
Expected: FAIL — `spawn()` got an unexpected keyword argument `opening_prompt`.

- [ ] **Step 3: Thread kwargs through `AegisApp.spawn` and the adapter**

In `src/aegis/tui/app.py`, update the AppBridge `spawn` (around line 842):

```python
    async def spawn(self, profile: str, *,
                    handle: str | None = None,
                    opening_prompt: str | None = None,
                    spawned_by: str | None = None) -> str:
        """AppBridge-shaped: spawn a long-lived agent as a TUI pane."""
        sm_adapter = _SessionManagerAdapter(self)
        sess = sm_adapter.spawn(profile, handle=handle,
                                opening_prompt=opening_prompt,
                                spawned_by=spawned_by)
        return sess.handle
```

Update `_SessionManagerAdapter.spawn` to accept + stamp `spawned_by` (it already accepts `opening_prompt`):

```python
    def spawn(self, slug: str, *,
              opening_prompt: str | None = None,
              handle: str | None = None,
              spawned_by: str | None = None):
        agent = self._app._agents[slug]
        h = handle or generate_name({p.handle for p in self._app._panes})
        pane = ConversationPane(
            self._app._make_session(agent, self._app._mcp.url, h), agent,
            slug, h, self._app._palette, digest=self._app.queue_digest,
            state_dir_path=self._app._state_dir)
        pane._core.spawned_by = spawned_by
        self._app._panes.append(pane)
        self._app.inbox_router.bind_session(h, pane._core)
        self._app.run_worker(
            self._mount_and_kick(pane, opening_prompt),
            group=f"queue-spawn-{h}", exclusive=False)
        return pane._core
```

- [ ] **Step 4: Surface `spawned_by` in the TUI `list_sessions`**

In `src/aegis/tui/app.py` `AegisApp.list_sessions` (around line 794):

```python
    def list_sessions(self) -> list[SessionInfo]:
        active = self._active
        return [
            SessionInfo(handle=p.handle, agent_slug=p.agent_slug,
                        state=p.state.value, active=(p is active),
                        unseen=p.unseen,
                        spawned_by=getattr(p._core, "spawned_by", None))
            for p in self._panes
            if isinstance(p, ConversationPane)
        ]
```

- [ ] **Step 5: Run test to verify it passes**

Run: `uv run python -m pytest tests/test_tui.py -q`
Expected: PASS (new test + existing TUI suite green).

- [ ] **Step 6: Commit**

```bash
git add src/aegis/tui/app.py tests/test_tui.py
git commit -m "feat(tui): AegisApp.spawn threads opening_prompt + spawned_by, surfaced in list_sessions"
```

---

### Task 3: `aegis_spawn` MCP tool

Expose the seam as a first-class MCP tool with a docstring that teaches the peer-vs-subagent distinction and the inbox feedback path.

**Files:**
- Modify: `src/aegis/mcp/server.py` (new `aegis_spawn` tool)
- Test: `tests/test_mcp_server.py`

**Interfaces:**
- Consumes: `AppBridge.spawn(profile, *, handle, opening_prompt, spawned_by)` (Tasks 1–2).
- Produces: MCP tool `aegis_spawn(agent, prompt, from_handle, slug=None) -> {"handle": str}`.

- [ ] **Step 1: Write the failing test**

In `tests/test_mcp_server.py`, first extend `FakeBridge.spawn` to record the call and honor the new kwargs, then add a tool test. Replace `FakeBridge.spawn` (lines ~43-44) with:

```python
    async def spawn(self, profile, *, handle=None,
                    opening_prompt=None, spawned_by=None):
        self.spawned = {"profile": profile, "handle": handle,
                        "opening_prompt": opening_prompt,
                        "spawned_by": spawned_by}
        return handle or "auto-handle"
```

Add the test:

```python
@pytest.mark.asyncio
async def test_aegis_spawn_creates_peer():
    br = FakeBridge()
    srv = build_server(br)
    out = await _call(srv, "aegis_spawn", agent="default",
                      prompt="do the thing", from_handle="parent-x",
                      slug="child-one")
    assert out == {"handle": "child-one"}
    assert br.spawned == {"profile": "default", "handle": "child-one",
                          "opening_prompt": "do the thing",
                          "spawned_by": "parent-x"}


@pytest.mark.asyncio
async def test_aegis_spawn_auto_handle():
    br = FakeBridge()
    srv = build_server(br)
    out = await _call(srv, "aegis_spawn", agent="default",
                      prompt="hi", from_handle="parent-x")
    assert out == {"handle": "auto-handle"}
```

Also add `"aegis_spawn"` to the expected set in `test_build_server_registers_all_aegis_tools`.

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run python -m pytest tests/test_mcp_server.py::test_aegis_spawn_creates_peer -v`
Expected: FAIL — no tool named `aegis_spawn`.

- [ ] **Step 3: Register the tool**

In `src/aegis/mcp/server.py`, add alongside the other bridge tools (near `aegis_handoff` / `aegis_group_spawn`):

```python
    @server.tool
    async def aegis_spawn(agent: str, prompt: str, from_handle: str,
                          slug: str | None = None) -> dict:
        """Create a NEW INDEPENDENT top-level agent and hand it an opening
        prompt. Unlike a harness subagent (the `Task` tool), this agent is a
        real peer: it gets its own handle and session, appears as its own tab,
        and keeps running after you finish — you are only its midwife, not its
        owner.

        Fire-and-forget: returns immediately with the new handle and does NOT
        wait for or collect the agent's output. To get results back, either
        tell the new agent *in its prompt* to `aegis_handoff` you when done, or
        `aegis_handoff` it yourself later. `aegis_list_sessions` shows agents
        you spawned (they carry `spawned_by`).

        Args:
            agent: profile name from the loaded .aegis.yaml `agents:`.
            prompt: delivered as the new agent's first user-message turn.
            from_handle: your own aegis handle (recorded as `spawned_by`).
            slug: desired handle for the new agent; auto-generated if omitted.
        """
        handle = await bridge.spawn(agent, handle=slug,
                                    opening_prompt=prompt,
                                    spawned_by=from_handle)
        return {"handle": handle}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run python -m pytest tests/test_mcp_server.py -q`
Expected: PASS (both new tests + the updated registry-set test).

- [ ] **Step 5: Commit**

```bash
git add src/aegis/mcp/server.py tests/test_mcp_server.py
git commit -m "feat(mcp): aegis_spawn — genuine fire-and-forget peer-agent spawn"
```

---

### Task 4: Documentation + full-suite gate

Record the tool in AGENTS.md's MCP surface and gate the whole hermetic suite.

**Files:**
- Modify: `AGENTS.md` (the `src/aegis/mcp/` bullet listing the tool surface)

- [ ] **Step 1: Add `aegis_spawn` to the AGENTS.md MCP tool list**

In `AGENTS.md`, in the `src/aegis/mcp/` layout bullet, add `aegis_spawn` to the enumerated inter-agent tools (next to `aegis_handoff`), with a half-line: "`aegis_spawn` (genuine fire-and-forget peer spawn — new top-level agent + opening prompt + `spawned_by` provenance)".

- [ ] **Step 2: Run the full hermetic suite**

Run: `uv run python -m pytest -q -m "not live"`
Expected: PASS. (Per the known inotify flakiness on zion, if 1–2 unrelated TUI/watchdog tests flake, re-run them in isolation to confirm they pass; the spawn-related tests must be green.)

- [ ] **Step 3: Commit**

```bash
git add AGENTS.md
git commit -m "docs(agents): note aegis_spawn in the MCP tool surface"
```

---

## Optional follow-up (not required for v1)

A live round-trip against a real `claude` subprocess (`@pytest.mark.live` in `tests/test_mcp_live.py`): spawn a child with a prompt that tells it to `aegis_handoff` the parent, assert the parent's inbox receives the child's report. Add only if the hermetic coverage feels thin in practice.

## Self-Review

- **Spec coverage:** surface (`aegis_spawn` + returns handle) → Task 3; `opening_prompt` delivery → Tasks 1–2; `spawned_by` provenance on `SessionInfo` + `list_sessions` → Tasks 1–2; docstring teaches peer-vs-subagent + inbox feedback → Task 3; "call N times" (no batch) → honored (single-agent signature); non-goals (no lifecycle coupling, no new delivery path) → respected. Covered.
- **Type consistency:** `spawn(..., opening_prompt=None, spawned_by=None)` identical across the protocol, `SessionManager`, `AegisApp`, and the adapter; `SessionInfo.spawned_by: str | None = None` used uniformly; tool returns `{"handle": str}` matching test assertions.
- **Placeholder scan:** none — every step carries concrete code/commands.
