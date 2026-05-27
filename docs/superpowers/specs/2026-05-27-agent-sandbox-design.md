# Agent Sandbox Design

**Date:** 2026-05-27
**Status:** Draft

## Overview

Aegis-level sandboxing provides configurable isolation for individual agent
profiles. All sandbox features are opt-in — the default is current behavior
(no restrictions). Three independent primitives can be combined freely:

1. **Worktree isolation** — agent spawns into a dedicated git worktree
2. **Filesystem partitioning** — declarative read-only / hidden path enforcement
3. **Network isolation** — outbound network blocked

Implementation backend: **bubblewrap (`bwrap`)** for filesystem and network
isolation (Linux-only, v1 and likely forever). Worktrees use native `git
worktree add`.

## Config Schema

Sandbox config lives under `sandbox:` in an agent profile. Omitting the block
entirely means no restrictions.

```yaml
agents:
  my-implementor:
    harness: claude-code
    model: claude-sonnet-4-6
    sandbox:
      worktree: true          # spawn into a fresh git worktree
      network: false          # block all outbound network (--unshare-net)
      filesystem:
        readwrite:
          - "."               # paths the agent may write to (relative to repo root)
        hidden:               # shadowed with empty tmpfs — agent cannot see these
          - ".env"
          - ".git"
```

**Rules:**
- All three keys (`worktree`, `network`, `filesystem`) are optional and
  independent — any combination is valid.
- `filesystem.readwrite` is the explicit write allowlist. Everything else on
  the filesystem is bind-mounted read-only by default (via `--ro-bind / /`).
  There is no explicit `readonly:` key — unlisted paths are implicitly readonly.
- `hidden` paths are shadowed with an empty `tmpfs` — the agent sees a blank
  directory or missing path rather than an error.
- Paths under `filesystem` are relative to the repo root (cwd at spawn time).
  `~` expands to the user's home directory for credential paths.

## Architecture

### SandboxContext

A new `SandboxContext` class is created from the agent profile's sandbox
config before the driver spawns its subprocess. It is driver-agnostic — it
wraps any harness command as a bwrap prefix.

```
SandboxConfig (from agent profile)
    │
    ▼
SandboxContext.setup(cwd)
    ├── git worktree add --detach <tmp-path>   [if worktree: true]
    └── build bwrap argv prefix                [if filesystem or network set]
    │
    ▼
HarnessDriver.session(agent, effective_cwd, mcp_url, handle)
    └── spawns: bwrap [sandbox args] -- <harness_cmd> [harness_args]
    │
    ▼
SandboxInit event emitted → {worktree_path, base_branch, handle_id}
```

### bwrap invocation

For filesystem isolation the bwrap prefix follows this pattern:

```
bwrap
  --ro-bind / /                      # root read-only
  --dev /dev
  --proc /proc
  --tmpfs /tmp
  --bind <rw_path> <rw_path>         # one per readwrite entry
  --tmpfs <hidden_path>              # one per hidden entry
  [--unshare-net]                    # if network: false
  -- <harness_cmd> <harness_args>
```

`SandboxContext` builds this argv list; the driver prepends it to its own
command without any other changes to driver code.

### Integration point

The single integration point is in the session spawner (the function that
calls `driver.session()`). Before calling the driver, if `agent.sandbox` is
set:

1. Instantiate `SandboxContext(agent.sandbox)`
2. Call `effective_cwd = await context.setup(cwd)`
3. Wrap `driver.build_argv(...)` output: `context.wrap_argv(harness_argv)`
4. Pass `effective_cwd` and wrapped argv to the driver's session constructor

No changes to any existing driver (`ClaudeDriver`, `GeminiDriver`,
`OpenCodeDriver`) are needed.

## Worktree Lifecycle

### Creation

On spawn with `worktree: true`:
- Aegis runs `git worktree add --detach <repo_root>/.aegis/worktrees/<uuid>`
  from the repo root
- The worktree is checked out at the current HEAD (detached)
- Effective cwd for the agent session is the new worktree path
- Aegis emits a `SandboxInit` event immediately after `SystemInit`:

```python
@dataclass
class SandboxInit(Event):
    handle_id: str        # opaque ID for MCP tool calls
    worktree_path: str    # absolute path on disk
    base_branch: str      # branch at time of spawn
    base_commit: str      # SHA at time of spawn
```

### Caller responsibility

The caller (workflow, queue orchestrator, parent agent) receives the
`SandboxInit` event and owns the worktree handle from that point. Aegis does
not auto-merge or auto-discard on session close.

### MCP tools

Three MCP tools are registered on the shared plane and available to any agent
in the session (not just the sandboxed one):

**`aegis_worktree_diff(handle_id)`**
Returns a structured summary of changes in the worktree relative to its base:
```json
{
  "files_changed": 4,
  "insertions": 120,
  "deletions": 18,
  "files": [
    {"path": "src/foo.py", "status": "modified", "+": 40, "-": 5},
    ...
  ]
}
```

**`aegis_worktree_merge(handle_id, strategy?)`**
Attempts to merge the worktree branch into its `base_branch` (recorded at spawn
time).
- `strategy`: `"merge"` (default) or `"rebase"`
- On success: `{"ok": true, "commit": "<sha>"}`
- On conflict: `{"ok": false, "conflicts": ["src/foo.py", ...]}`
Does not auto-resolve conflicts. Caller decides next step.

**`aegis_worktree_discard(handle_id)`**
Removes the worktree directory and unregisters it from git. Irreversible.

### Typical workflow pattern

```
orchestrator spawns implementor (worktree: true)
    → receives SandboxInit {handle_id, ...}

implementor works, commits to worktree branch

orchestrator calls aegis_worktree_diff(handle_id)
    → reviews changes

[optional] orchestrator spawns reviewer agent
    → reviewer calls aegis_worktree_diff, approves/rejects

orchestrator calls aegis_worktree_merge(handle_id)
    → on conflict: sends to resolver agent or discards
    → on success: worktree is merged, orchestrator calls aegis_worktree_discard
```

## Orphan Recovery

Aegis persists active worktree handles to a local state file
(`~/.aegis/worktrees.json`). If the Aegis process dies before teardown, the
worktrees remain as valid git worktrees on disk. A CLI command `aegis worktree
gc` lists and optionally removes orphaned worktrees (those in the state file
whose associated session no longer exists).

## Failure Modes

| Scenario | Behavior |
|---|---|
| `bwrap` not installed | Spawn fails immediately with actionable error: "requires bubblewrap — `apt install bubblewrap`" |
| Not a git repo (`worktree: true`) | Spawn fails: "worktree isolation requires a git repository" |
| `git worktree add` fails | Spawn fails with git's error message forwarded |
| Agent crashes inside sandbox | Worktree stays alive; caller can inspect via `aegis_worktree_diff` or discard |
| `aegis_worktree_merge` conflict | Returns structured conflict list; no auto-resolution |
| Aegis dies with live worktrees | Worktrees orphaned on disk; `aegis worktree gc` cleans up |

## Non-Goals (v1)

- **macOS support** — bwrap is Linux-only. Seatbelt could back a v2 macOS
  implementation but is not planned.
- **Per-command approval** — sandboxing is per-session, not per shell command.
  A separate approval primitive (Codex-style) would complement this but is a
  different feature.
- **Docker backend** — decided against; bwrap is the right tool for
  unprivileged process sandboxing without a daemon.
- **Auto-merge on agent exit** — caller always decides.
- **Nested sandboxes** — a sandboxed agent spawning another sandboxed agent is
  deferred.
- **Worktree auto-push / PR creation** — the merge tools land changes locally;
  pushing to remote is the caller's responsibility.
