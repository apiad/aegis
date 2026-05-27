---
title: "aegis config — CLI surface + TUI panel"
date: 2026-05-27
status: armed
---

# Overview

Replace `aegis init` with two coordinated surfaces for `.aegis.yaml`
authorship + maintenance:

1. **`aegis config` — scriptable CLI subcommands.** Idempotent
   add/remove/show verbs for agents, queues, telegram, default-agent,
   and plugin-dirs. No interactive prompt loop. Writes through
   ruamel.yaml so existing comments and key order are preserved.

2. **TUI ConfigPanel — interactive surface.** New tab type reachable
   via `Ctrl+,` mid-session, *and* mounted automatically when `aegis`
   launches in a directory with no `.aegis.yaml` (instead of refusing
   to start). Writes through the same helpers, so the on-disk file
   matches what the panel rendered.

The existing `aegis schedule …` and `aegis remote …` verbs stay where
they are — they carry their own subcommand surfaces (`push`, `logs`,
`enable`, etc.) and we don't fold them under `aegis config`.

Watchdog reload (already used by the scheduler) is extended to cover
agents, queues, telegram, and plugin_dirs — so a `aegis config agent
add …` from a side terminal lands in the running serve within ~200ms,
no restart needed. Running sessions keep their bound profile until
closed; new `/new <agent>` spawns see the updated set.

`aegis init` is retired.

# Goals

- One scriptable verb (`aegis config`) covers every authorable section
  of `.aegis.yaml`. Each subcommand is idempotent, fails loud on bad
  input, and writes a comment-preserving diff.
- One interactive surface (the TUI ConfigPanel) covers the same
  matrix, with form-based input and inline validation.
- Mid-session reconfiguration works without restart.
- Empty-directory bootstrap is friendly: `aegis` → ConfigPanel → save
  → tab switches to a real session.

# Non-goals

- Schedules and remotes stay under their own verbs.
- The ConfigPanel does not edit YAML files other than `.aegis.yaml`
  (overlays under `.aegis/{agents,queues,…}/` remain hand-edited).
- No "wizard mode" — neither CLI nor TUI prompts a multi-step Q&A.
  The TUI panel shows everything at once; the CLI takes flags.

# CLI surface

```
aegis config show [--json]
aegis config agent list
aegis config agent add <slug> --provider <claude-code|gemini|opencode>
                              --model <str>
                              [--effort <low|medium|high|max>]
                              [--permission <read|write|full|auto>]
aegis config agent remove <slug>
aegis config queue list
aegis config queue add <name> --agent <slug>
                              --max-parallel <N>
                              [--budget <spec> ...]    # repeatable
aegis config queue remove <name>
aegis config telegram show
aegis config telegram set [--token <s> | --clear-token]
                          [--chat-id <n> | --clear-chat-id]
                          [--auto-prompt <s> | --clear-auto-prompt]
aegis config default-agent <slug>
aegis config plugin-dir list
aegis config plugin-dir add <path>
aegis config plugin-dir remove <path>
```

`--budget` spec: `"<constraint>:<limit>:<window>"`, e.g.
`"usd:1.00:1h"` or `"output_tokens:500000:1h"`. Repeatable; replaces
the queue's existing budget list.

If no `.aegis.yaml` exists, the first writing subcommand creates a
minimal one. `aegis config show` on an empty directory exits non-zero
with a pointer to `aegis config agent add`.

All writing verbs validate against the YAML loader's invariants
(unknown agent ref, missing default_agent, bad permission/effort
value, etc.) and fail loud — the file is *not* mutated when
validation fails.

# TUI ConfigPanel

New tab type living at `src/aegis/tui/config_panel.py`. Layout:

```
┌────────────────────────────────────────────────────────────┐
│  Default agent: [ default ▾ ]                              │
├────────────────────────────────────────────────────────────┤
│  AGENTS                                              [+Add]│
│  ┌──────────────────────────────────────────────────────┐  │
│  │ slug      provider     model      effort  perm  [─]  │  │
│  │ default   claude-code  opus       high    auto  [─]  │  │
│  │ fast      gemini       gemini-3…  —       full  [─]  │  │
│  └──────────────────────────────────────────────────────┘  │
├────────────────────────────────────────────────────────────┤
│  QUEUES                                              [+Add]│
│  ┌──────────────────────────────────────────────────────┐  │
│  │ name    agent     max_parallel  budgets    [─]       │  │
│  │ impl    default   2             $1/1h…     [─]       │  │
│  │ review  fast      2             —          [─]       │  │
│  └──────────────────────────────────────────────────────┘  │
├────────────────────────────────────────────────────────────┤
│  TELEGRAM                                                  │
│    token:     ******** (from AEGIS_TELEGRAM_TOKEN env)     │
│    chat_id:   123456789                          [Edit]    │
│    auto_prompt: "Be concise…"                              │
├────────────────────────────────────────────────────────────┤
│  PLUGIN DIRS                                         [+Add]│
│    .aegis/plugins                                  [─]     │
└────────────────────────────────────────────────────────────┘
```

[+Add] opens a modal with the same fields as the equivalent CLI
verb. Validation errors render inline; success closes the modal,
re-reads the file, and re-renders the table.

Keybinding `Ctrl+,` toggles the panel from any other tab. If the
panel is already mounted, focus moves to it; otherwise a new instance
is mounted and added to the tabbar with handle `config`.

# Boot path

`aegis` resolves the project root via `find_project_root`. Today it
exits with "No .aegis.yaml found…" if none exists. New path:

1. Launch the TUI as normal.
2. Skip session bootstrap (no `agents` dict yet → no `default_agent`
   spawnable).
3. Mount one ConfigPanel as the only tab. Status bar shows "no
   .aegis.yaml — add an agent to get started".
4. When the user saves a config with at least one agent + a
   default_agent, the watchdog reload picks it up, the app re-binds
   `SessionManager` with the new agents dict, and a `/new` from the
   panel (or the keybinding) becomes available.

# Edit machinery

`src/aegis/config/edit.py` already exists and uses ruamel for
`set_schedule_enabled` / `toggle_schedule_enabled`. Extend with:

- `add_agent(root, slug, provider, model, *, effort=None, permission=None)`
- `remove_agent(root, slug)`
- `add_queue(root, name, agent, max_parallel, *, budgets=None)`
- `remove_queue(root, name)`
- `set_telegram(root, *, token=Sentinel, chat_id=Sentinel,
                 auto_prompt=Sentinel)`
  (Sentinel distinguishes "leave alone" from "set to None / clear".)
- `set_default_agent(root, slug)`
- `add_plugin_dir(root, path)`
- `remove_plugin_dir(root, path)`

Each helper:
1. Reads `.aegis.yaml` with ruamel.YAML(), preserving comments.
   Creates the file if missing.
2. Mutates the in-memory document.
3. Validates the result by passing through `yaml_loader.load_config`
   on the temp body — raises `ConfigError` and leaves the original
   file untouched if validation fails.
4. Writes atomically (write to `<file>.tmp`, fsync, rename).

# Reload extension

Today `aegis.scheduler.reload.ReloadWatcher` watches the YAML file
and calls a callback that swaps the scheduler's schedule table. We
extend that:

- Watcher fires a generic `on_config_change(new_cfg: AegisConfig)`
  callback.
- `aegis serve` installs a callback that:
  - Updates `SessionManager.agents` + `.default_agent` in place.
  - Swaps `QueueManager.queues` (existing in-flight tasks finish with
    their original binding; pending tasks re-bind to the new spec).
  - Updates the Telegram frontend's known agent slugs.
  - Reloads plugin_dirs (idempotent re-import).
- The TUI's app gets the same callback, refreshes the ConfigPanel if
  it's mounted, and re-renders any session-spawn affordances.

Running session state (in-flight turns, mounted panes, conversation
history) is never touched. Only the next-spawn surface changes.

# Validation

Every writing verb (CLI or TUI) runs the result through the full YAML
loader before persisting. Concretely:

- Default agent must exist in agents.
- Queue.agent must reference a declared agent.
- max_parallel must be >= 1.
- permission ∈ {read, write, full, auto}.
- effort ∈ {low, medium, high, max}, and only valid for claude-code.
- Budget specs parse cleanly via `parse_budgets`.

Failures echo the loader's error message verbatim; the on-disk file
is unchanged.

# Migration

- `aegis init` command is removed. The wizard's interactive Q&A
  goes; `init_wizard.py` retains `ProviderSpec`, `_PROVIDER_CATALOG`,
  `detect_providers`, and `_next_slug` (used by the panel + by
  `aegis config agent add` for the "auto-suggest slug" path).
- `aegis init` references in docs (`install.md`, `configuration.md`)
  are rewritten to point at `aegis config agent add` and the TUI
  ConfigPanel.

# Out of scope (for v1)

- Editing schedules / remotes inside the panel (the existing
  subcommand surfaces are richer; folding them in is a separate
  effort if Alex asks).
- Multi-environment configs (env var-driven overrides beyond the
  existing `AEGIS_TELEGRAM_TOKEN`).
- A config-validation `aegis config check` verb. Loading
  `.aegis.yaml` already validates fail-loud at every aegis subcommand
  boot; an explicit verb is redundant.
