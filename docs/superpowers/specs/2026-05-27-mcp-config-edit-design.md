---
title: MCP Config-Edit Surface
date: 2026-05-27
status: draft
---

# MCP Config-Edit Surface

## Motivation

Aegis already exposes the `.aegis.yaml` mutation surface through the
`aegis config …` CLI (`aegis.cli_config` → `aegis.config.edit`). That
lets a human operator add agents, register queues, drop plugin dirs,
toggle schedules — all through a single comment-preserving, atomic,
fail-loud writer.

Agents running under the aegis MCP server can't reach that surface.
They can `Edit .aegis.yaml` directly through the harness's filesystem
tools, but that path skips the validator, races with the on-disk
atomic write, and never tells the running `aegis serve` process that
anything changed.

This spec adds an MCP layer that lets spawned agents mutate the same
config through the same `aegis.config.edit` helpers the CLI uses, with
hot-registration on the live `QueueManager` / agent map / plugin
loader for the additive paths.

## Scope

**In scope (v1):** add / remove agents, add / remove queues, add /
remove plugin dirs, set / toggle schedule enabled. Reads of agents,
queues, schedules, and the full config snapshot.

**Out of scope:** `set_telegram` (the human-↔-agent transport is
operator territory), `set_default_agent` (boot behavior — no
runtime use case for an agent to mutate it), fully-live removes,
dry-run / proposal mode, groups / remotes / agent-group config (no
`aegis.config.edit` helpers yet for those — separate spec when
needed).

## Tool surface

All tools live on the existing FastMCP server under
`src/aegis/mcp/server.py`, alongside `aegis_enqueue` /
`aegis_handoff` / `aegis_meta`. Every spawned harness sees them
uniformly — no per-agent gate (agents already have `Edit` over the
whole workspace; the validated MCP path is strictly safer than the
file-edit path they could otherwise take).

### Read tools (4)

| Tool                          | Returns                                                                                  |
| ----------------------------- | ---------------------------------------------------------------------------------------- |
| `aegis_config_show`           | Full parsed `AegisConfig` as JSON. Telegram block has `token` redacted to `"<set>"` / `"<unset>"`. |
| `aegis_config_list_agents`    | `[{slug, harness, model, effort, permission}, …]`                                        |
| `aegis_config_list_queues`    | `[{name, agent, max_parallel, budgets}, …]`                                              |
| `aegis_config_list_schedules` | `[{name, cron, enabled, lifecycle, workflow, payload}, …]`                               |

### Write tools (8)

All wrap a single `aegis.config.edit` helper plus, where applicable,
an in-process live-registration step. All return:

```json
{"ok": true, "live": true | false, "restart_required_for": []}
```

`"live": true` means the change is in effect inside the running
`aegis serve` immediately. `"restart_required_for"` lists the
subsystems where the YAML is updated but the running process still
sees the prior state. For v1 it'll be `["queues"]` or `["agents"]` or
`["plugins"]` for the remove paths.

| Tool                                              | Helper                          | Live?           |
| ------------------------------------------------- | ------------------------------- | --------------- |
| `aegis_config_add_agent(slug, harness, model, effort?, permission?)` | `edit.add_agent`                | yes (agent map) |
| `aegis_config_remove_agent(slug)`                 | `edit.remove_agent`             | no              |
| `aegis_config_add_queue(name, agent, max_parallel, budgets?)`        | `edit.add_queue`                | yes (`QueueManager.register_queue`) |
| `aegis_config_remove_queue(name)`                 | `edit.remove_queue`             | no              |
| `aegis_config_add_plugin_dir(path)`               | `edit.add_plugin_dir`           | yes (re-`import_plugins`) |
| `aegis_config_remove_plugin_dir(path)`            | `edit.remove_plugin_dir`        | no              |
| `aegis_config_set_schedule_enabled(name, enabled)` | `edit.set_schedule_enabled`    | yes (already hot-reloads via `ReloadWatcher`) |
| `aegis_config_toggle_schedule_enabled(name)`      | `edit.toggle_schedule_enabled`  | yes (same)      |

### Conventions

- Every tool resolves the project root via `find_project_root()`. No
  `root` parameter — no per-call escape hatch out of the discovered
  project tree.
- Validation failures (unknown harness, duplicate slug, queue
  referencing missing agent, etc.) raise the same `ConfigError` /
  `ValueError` the CLI raises today. The MCP server converts those
  into tool errors — the agent sees the same wording the human does
  at `aegis config …`.
- Concurrency: a single `asyncio.Lock` on the MCP runtime serializes
  *all* write tools. `_atomic_write` already gives on-disk atomicity;
  the lock prevents two agents from racing on the in-memory
  live-registration step. Reads are not gated.

## Live registration

The MCP server today carries an `AppBridge` reference (`SessionInfo`
+ `aegis.mcp.bridge`). It's the protocol the server consumes for
spawn / handoff / enqueue. The same bridge gains three new methods:

```
class AppBridge(Protocol):
    ...
    def register_agent(self, slug: str, agent: Agent) -> None: ...
    def register_queue(self, queue: Queue) -> None: ...
    def reload_plugins(self) -> None: ...
```

`SessionManager` (`src/aegis/core/manager.py`) implements them by
mutating its internal `_agents: dict[str, Agent]` / handing the new
`Queue` to its `QueueManager` / calling `import_plugins(cfg)` against
the freshly re-loaded `AegisConfig`. `AegisApp` (TUI) implements the
same methods by forwarding to its `SessionManager`.

The MCP tool's order of operations:

1. Acquire the runtime's write lock.
2. Call the `aegis.config.edit` helper. On failure, release + raise —
   no live mutation happens.
3. For the *additive* tools: build the resulting `Agent` / `Queue`
   object from the now-on-disk YAML (re-load via `load_config`) and
   call the corresponding `bridge.register_*`. Failure here is
   exceptional (the YAML was just validated) but if it happens, log
   loudly and surface the error — the file is already updated, so the
   tool returns `{"ok": true, "live": false, "restart_required_for":
   [...]}` with the failure reason in a `note` field.
4. For the *removal* tools: skip the bridge call entirely. Return
   `live: false` with `restart_required_for: [<subsystem>]`.
5. Release the lock.

## Listing semantics

Reads are not gated. They re-parse `.aegis.yaml` via
`load_config`; because every write goes through `_atomic_write`
(tempfile + rename), the on-disk file is always well-formed.
`list_agents` / `list_queues` return the **on-disk YAML** view, not
the running registry, so an agent that just called `add_queue` sees
the new entry whether it landed live or not. (For agents that need
to confirm liveness, the write tool's `"live"` field is the
authoritative signal.)

## Error handling

| Failure                              | Surface                                                                       |
| ------------------------------------ | ----------------------------------------------------------------------------- |
| No `.aegis.yaml` in cwd or ancestors | `ConfigError` with the same wording as `_resolve_root` raises in the CLI path. |
| Duplicate slug / queue name           | `ValueError` from `edit.add_*`, surfaced as a tool error.                     |
| Unknown harness on `add_agent`        | `ValueError` from the `Agent` pydantic validator.                             |
| Queue references missing agent        | `ConfigError` from `_validate_and_dump`'s full re-parse.                      |
| Live-registration fails after write   | Tool returns `{"ok": true, "live": false, "note": "<reason>"}`; loud log.     |

## Implementation seams

- **`src/aegis/mcp/server.py`** — eight new `@mcp.tool()` writes, four
  reads. Each is ~10–20 lines: lock acquire, helper call, optional
  bridge call, return dict.
- **`src/aegis/mcp/bridge.py`** — protocol gains `register_agent`,
  `register_queue`, `reload_plugins`.
- **`src/aegis/core/manager.py`** — `SessionManager` implements the
  three new bridge methods.
- **`src/aegis/tui/app.py`** — `AegisApp` forwards the new methods to
  `SessionManager`.
- **`src/aegis/queue/manager.py`** — already has the runtime queue
  table; `register_queue(queue)` becomes a public method (currently
  the table is built at boot).
- **`src/aegis/config/yaml_loader.py`** — `import_plugins(cfg)`
  already exists and is idempotent (re-importing a module that was
  already loaded is a no-op for `@workflow` registration since the
  decorator is keyed by function name); no change needed.
- **No change** to `aegis.config.edit` — every helper it exposes is
  already shaped for this consumer.

## Testing

- **Unit:** one test per write tool, covering the happy path + the
  representative failure (duplicate slug, missing referenced agent,
  no project root). Use the same `tmp_path` `.aegis.yaml` fixture
  pattern from `tests/test_config_edit.py`.
- **Live-registration:** `SessionManager` test that calls
  `register_agent` / `register_queue` and asserts the next
  `_sync_spawn` / `aegis_enqueue` sees the new entry. No new harness
  subprocess needed — the existing `FakeHarness` fixture covers it.
- **Concurrency:** one test that fires two `add_queue` calls in
  parallel against the same runtime, asserts both end up in the YAML
  and the live registry, and the on-disk file is well-formed (the
  lock serializes the writes; `_atomic_write` guarantees no torn
  state).
- **MCP integration:** one `tests/test_mcp_live.py`-style test
  (marked `live`) that spawns a real `claude -p` worker, has it call
  `aegis_config_add_queue` followed by `aegis_enqueue` to the new
  queue, and confirms the worker round-trips. Skipped when `claude`
  is off PATH.

## Migration & back-compat

- Pure addition. No breaking changes to existing CLI or MCP surfaces.
- The `AppBridge` protocol gains three methods; existing test fakes
  that implement the protocol need to add them (default-noop is fine
  for tests that don't exercise config edits).

## Open questions deferred

- **Live removes.** Removing a queue with in-flight tasks: drain,
  hard-cancel, or refuse? Same question for an agent that's
  currently being used by a worker. Punted to a v2 spec.
- **Telegram + default_agent.** Could land in v2 if a use case shows
  up, but no concrete one today.
- **Groups / remotes / agent-groups.** Need `aegis.config.edit`
  helpers first; separate spec.
- **Dry-run / proposal mode.** An `aegis_config_propose_*` family
  that returns the resulting YAML diff without writing. Worth doing
  if agents start over-editing and need a review step; punt until
  observed.
