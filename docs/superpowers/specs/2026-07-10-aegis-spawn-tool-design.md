# Aegis Spawn Tool — Design

**Status:** approved (brainstormed 2026-07-10)
**Primitive:** `aegis_spawn` — genuine peer-agent spawn from inside an agent.

## Why

Agents can already delegate three ways, and none of them is "create a real
new agent and walk away":

- **Harness subagents** (`Task`) — ephemeral, owned by the harness turn, invisible
  to the substrate, die with the turn.
- **Queue workers** (`aegis_enqueue`) — spawned by the substrate, wrapped in
  FIFO + max-parallel + callback ceremony, conceptually a job not a peer.
- **Groups** (`aegis_group_spawn`) — genuine sessions, but coupled to a group
  and its broadcast/wait choreography.

What's missing is the plain case: *one agent decides another top-level agent
should exist, gives it an opening prompt, and lets it run.* Fire-and-forget,
no queue, no group, no callback. If the spawner wants results, it asks for
them later through the existing inbox.

The machinery already exists — `SessionManager._sync_spawn(slug,
opening_prompt, handle)` creates exactly this. This spec exposes it as a
first-class MCP tool without the group ceremony.

## End state

An agent calls:

```
aegis_spawn(agent="reviewer", prompt="Audit src/aegis/queue/ for races.",
            from_handle="lucid-knuth")
→ {"handle": "civic-codd"}
```

`civic-codd` boots as a normal top-level session — its own TUI tab / web
session — runs the prompt as its first turn, and lives independently of
`lucid-knuth`. The substrate records that `lucid-knuth` spawned it
(`spawned_by`), surfaced in `aegis_list_sessions`. No lifecycle coupling:
when `lucid-knuth` finishes or is closed, `civic-codd` is untouched.

## Surface

```python
aegis_spawn(agent: str, prompt: str, from_handle: str,
            slug: str | None = None) -> {"handle": str}
```

- `agent` — profile name; must resolve in the loaded `.aegis.yaml` `agents:`.
  Unknown profile → error (same failure shape as `aegis_group_spawn`).
- `prompt` — delivered as the new agent's first user-message turn.
- `from_handle` — the spawner's own handle; recorded as `spawned_by`.
- `slug` — desired handle for the new agent. Omitted → auto-generated
  (`generate_name`, the existing `adjective-surname` generator). Collision
  with a live handle → error.
- **Returns** `{"handle": <new handle>}`. No wait, no callback, no result.

**"One or several"** = call it N times. No batch parameter — that is the
no-ceremony point. N calls give N handles, each independent.

## Mechanics

Thin layer over existing code. The work:

1. **Extend the spawn seam.** `AppBridge.spawn` currently is
   `spawn(profile, *, handle=None) -> str`. Add two optional keyword args:
   `opening_prompt: str | None = None` and `spawned_by: str | None = None`.
   Both `AppBridge` implementers implement them:
   - `SessionManager.spawn` — forward straight to `_sync_spawn`, which already
     accepts `opening_prompt`; thread `spawned_by` onto the session record.
   - `AegisApp.spawn` (TUI) — same, but it must also **mount the pane** for the
     new session (the TUI's existing spawn path already does this; extend it to
     pass `opening_prompt` through so the first turn fires on mount).
2. **New MCP tool** `aegis_spawn` in `mcp/server.py`, delegating to
   `bridge.spawn(agent, handle=slug, opening_prompt=prompt,
   spawned_by=from_handle)`.
3. **Provenance.** Add `spawned_by: str | None` to `SessionInfo`. Populate it
   on spawn. Surface it in `aegis_list_sessions` output so a dashboard / an
   agent can see who begat whom.
4. **Feedback path — reuse the inbox, add nothing.** The `aegis_spawn`
   docstring instructs the spawner: to get results back, either tell the child
   in its `prompt` to `aegis_handoff` you when done, or `aegis_handoff` the
   child yourself later. Callbacks, handoffs, and queue results already ride
   one tagged inbox channel; spawn adds no new delivery mechanism.

## Docstring (teaches the agent)

The tool docstring must make the peer-vs-subagent distinction explicit:

> Create a **new independent top-level agent** and hand it an opening prompt.
> Unlike a harness subagent (the `Task` tool), this agent is a real peer: it
> gets its own handle and session, appears as its own tab, and keeps running
> after you finish — you are only its midwife, not its owner.
>
> Fire-and-forget: this returns immediately with the new handle and does **not**
> wait for or collect the agent's output. If you want results back, either tell
> the new agent *in its prompt* to `aegis_handoff` you when it's done, or
> `aegis_handoff` it yourself later to pull its state. Use `aegis_list_sessions`
> to see agents you've spawned (they carry `spawned_by`).

## Non-goals (v1)

- No batch parameter (call N times).
- No lifecycle coupling (spawner death never cascades).
- No auto-notification when a spawned agent finishes.
- No capability changes: the child gets whatever its profile's driver already
  grants. A spawned `claude` has aegis-MCP injected per-invocation and can
  itself spawn/handoff; `gemini`/`opencode` children cannot call back, exactly
  as queue workers behave today.

## Testing strategy

Hermetic, mirroring `tests/test_tui.py` and the groups tests:

- `SessionManager.spawn(opening_prompt=…, spawned_by=…)` creates a session
  whose first `send` is the prompt and whose `SessionInfo.spawned_by` is set.
- `aegis_spawn` MCP tool round-trips: returns a handle, the session exists,
  the opening prompt was delivered, unknown profile errors, slug collision
  errors.
- TUI: `aegis_spawn` mounts a new pane and fires the opening turn; the spawner
  pane keeps focus (reuses the background-finish focus discipline).
- `aegis_list_sessions` surfaces `spawned_by`.
- One live round-trip against a real `claude` subprocess (`@pytest.mark.live`):
  spawn a child, child reports back via `aegis_handoff`.

## References

- `src/aegis/core/manager.py` — `_sync_spawn`, `spawn`.
- `src/aegis/mcp/bridge.py` — `AppBridge`, `SessionInfo`.
- `src/aegis/mcp/server.py` — `aegis_group_spawn` (the closest existing tool).
- `src/aegis/groups/wiring.py` — how group spawn builds on `SessionManager.spawn`.
