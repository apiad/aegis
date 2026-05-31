# Plugins

Plugins extend aegis without forking it. Three composable primitive shapes,
auto-imported from disk, installable from anywhere over `gh:` registry URLs,
and with a lifecycle that gives them a controlled foothold in the
project's `.aegis.yaml`.

The plugin substrate is what lets `skill-system` ship Claude-Code-style
skill selection on any harness, and what lets `memory-system` ship
persistent memory with periodic dreaming — both as drop-in packages, not
patches to the core.

## The three primitives

A plugin is a Python module (or package) that registers any combination
of three decorated functions. Each primitive maps to a different
extension surface in the runtime.

### `@hook(event)` — fires on harness lifecycle events

```python
from aegis.hooks import hook, PreTurnContext, PreTurnResult


@hook("pre_turn")
async def inject_context(ctx: PreTurnContext) -> PreTurnResult | None:
    """Modify the user message before it reaches the harness."""
    if "remind me" in ctx.user_message:
        return PreTurnResult(prepend_system="Be terse and bullet-formatted.")
    return None
```

Four events fire from `AgentSession` at well-defined points:

| Event             | Mutator?  | Fires                                    | Receives                |
|-------------------|-----------|------------------------------------------|-------------------------|
| `pre_turn`        | **yes**   | before every turn                        | `PreTurnContext`        |
| `post_turn`       | observer  | after every turn finishes                | `PostTurnEvent`         |
| `session_start`   | observer  | once on session open                     | `SessionStartEvent`     |
| `session_end`     | observer  | once on session close                    | `SessionEndEvent`       |

Only `pre_turn` is a mutator: its `PreTurnResult` can prepend a system
message, rewrite the user message, block the turn entirely, or extend
the history. The other three are observers — useful for logging,
metrics, side-channel writes — but their return value is ignored.

Hooks compose deterministically by registration order. The composer
sums `prepend_system` results, applies `rewrite_user` last-wins, and
short-circuits on `block`. Each invocation is timeout-wrapped (10s
default) and JSONL-logged under `.aegis/state/hooks/`. An exception in
a non-strict hook is logged-and-skipped; `@hook("pre_turn", strict=True)`
escalates to a hard block on the turn.

### `@tool` — first-class MCP tools the agent can call

```python
from aegis.tools import tool


@tool(timeout=5.0)
async def lookup_definition(term: str) -> dict:
    """Look up a term in the project glossary.

    Args:
        term: the term to look up.

    Returns:
        a dict with keys `term`, `definition`, `source`.
    """
    ...
```

The decorator registers the function in the plugin's tool registry.
At session start, every registered `@tool` is added to the FastMCP
server the spawned agent connects to. Schema is auto-generated from
type hints + docstring (FastMCP's standard behavior). Reserved names
(every built-in `aegis_*` tool) are guarded — collisions raise at
import time, not at first call.

Per-tool defaults: 30s timeout, sync-or-async tolerated. Both flavors
run through `aegis.tools.runner`, which wraps with try/except and
emits a JSONL record per invocation to `.aegis/state/tools/`.

### `@workflow` — orchestrated procedures

```python
from aegis.workflow import workflow


@workflow
async def daily_review(engine, *, lookback_days: int = 1) -> dict:
    """Run a daily branch review across last-day commits."""
    diff = await engine.bash(f"git log --since='{lookback_days} day' --oneline")
    notes = await engine.delegate("reviewer", payload=f"Review: {diff}")
    return {"notes": notes}
```

Workflows are the top of the substrate stack. Plain async Python
functions whose first parameter is `engine: WorkflowEngine`, with
access to:

- `engine.delegate(queue, payload)` — enqueue a one-shot worker, await the result.
- `engine.spawn(profile, …)` / `engine.send(handle, …)` / `engine.drain(…)` / `engine.close(handle)` — long-lived agent lifecycle.
- `engine.bash(cmd)` — async shell.
- `engine.log(record)` — stderr + JSONL under `.aegis/state/workflows/`.
- `engine.spawn_group(name, profiles)` / `engine.broadcast(…)` / `engine.wait_all(…)` — groups primitive composition.
- `engine.caller_handle` — whoever invoked via MCP `aegis_run_workflow` (or `None` from the CLI).

Workflows can be invoked from the CLI (`aegis workflow run <name>`),
from any agent over MCP (`aegis_run_workflow(name=…, kwargs=…)`), or
from the scheduler via a `schedules:` entry that names the workflow.

## Plugin layout on disk

A plugin lives in its own directory. Minimum layout:

```
my-plugin/
  plugin.toml             # manifest
  my_plugin.py            # the module; @hook / @tool / @workflow live here
  _install.py             # optional — runs at install time
  _uninstall.py           # optional — runs at uninstall time
```

The directory may contain whatever supporting files you want (templates,
data, sub-modules). The plugin loader **recurses** into the directory and
auto-imports every `*.py` file under it, **except** files and
directories whose name starts with `_`. That convention lets
`_install.py` / `_uninstall.py` (which are *not* meant to be
auto-imported — they run only at install/uninstall time) coexist with
the module(s) the runtime should load.

### `plugin.toml` manifest

```toml
[plugin]
name           = "my-plugin"
version        = "0.1.0"
description    = "Short, single-line description shown in `aegis plugin list`."
requires_aegis = ">=0.15"

[default_config]
some_knob = "default-value"
```

`name` and `version` are required. `requires_aegis` is a SemVer
constraint checked at install. `[default_config]` is an arbitrary
table whose contents are handed to `_install.py` so plugins can
read defaults without baking them into Python literals.

### `_install.py` and `_uninstall.py`

Both are optional. Each exports a function that takes a single
`InstallContext` argument:

```python
# _install.py
from aegis.plugins import InstallContext


def install(ctx: InstallContext) -> None:
    # ctx.project_root  — the user's project root (cwd of `aegis plugin install`)
    # ctx.aegis_dir     — the project root, again (the dir holding .aegis.yaml)
    # ctx.plugin_dir    — where the plugin landed on disk
    # ctx.plugin_name   — manifest name
    # ctx.manifest      — full parsed plugin.toml as a dict
    # ctx.config        — the live AegisConfig object
    # ctx.console       — rich.Console for printing; may be None in headless mode
    # ctx.confirm(question, *, default) — interactive y/n with --yes-mode fallthrough
    ...
```

Use it for:

- creating directory trees the plugin needs at runtime,
- writing stub files the user is meant to edit,
- adding agent profiles or other sections to `.aegis.yaml` via the
  comment-preserving `aegis.config.edit` helpers,
- dropping a schedule overlay via `aegis.scheduler.push.write_atomic`
  so cron entries actually register (a bare YAML append to
  `.aegis.yaml` persists but does not activate),
- asking the user a small number of yes/no questions through
  `ctx.confirm` (which honors `--yes` mode for non-interactive
  installs).

`_uninstall.py::uninstall(ctx)` is the mirror. Strip whatever
`_install.py` added; leave user data alone by default; ask
`ctx.confirm(..., default=False)` if the plugin's data dir might be
worth preserving.

## Discovery — `plugin_dirs:`

The runtime imports every `*.py` under each directory listed in
`.aegis.yaml`'s `plugin_dirs:` section, recursively, on session start.
Default value: `.aegis/plugins/`.

```yaml
plugin_dirs:
  - .aegis/plugins
  - .aegis/extra-plugins
```

Plugins installed via `aegis plugin install` land under
`.aegis/plugins/<plugin-name>/`. You can also drop a development
plugin folder anywhere and add it to `plugin_dirs` by hand (or via
`aegis config plugin-dir add <path>`) — useful while iterating before
shipping.

The recursive-import-with-underscore-skip rule means a plugin can
freely organize its module across multiple files, keep helpers in
subpackages, and stash install-time scripts as `_install.py` /
`_uninstall.py` without the runtime trying to import them.

## Install lifecycle

The end-to-end install flow:

```bash
aegis plugin install <name> [--from <source>] [--yes] [--force]
```

`<source>` resolution, in order:

1. **`--from gh:owner/repo[@ref][#path]`** — `git archive` HTTPS fetch of
   the named subpath. Works for any GitHub-hosted plugin repo. Ref
   defaults to `main`; path defaults to `plugins/`.
2. **`--from file:///abs/path`** or **`--from /abs/path`** — copy from a
   local directory. Handy for development.
3. **No `--from`** — resolve against the configured registries (default:
   `gh:apiad/aegis#plugins/`, the aegis repo's own plugins folder).

The installer:

1. Resolves the source, copies the plugin into `.aegis/plugins/<name>/`
   (rolls back on failure — partial installs do not survive).
2. Parses `plugin.toml`, checks the `requires_aegis` SemVer.
3. Runs `_install.py::install(ctx)` if present.
4. Writes a lockfile entry to `.aegis/plugins.lock` recording the
   resolved source, version, and install timestamp.

`aegis plugin uninstall <name>`:

1. Runs `_uninstall.py::uninstall(ctx)` if present.
2. Removes the plugin's directory and its lockfile entry.

`aegis plugin list / show / update / search` round out the CLI
surface. The substrate is observable enough that you can audit what
landed where without leaving the terminal.

## Two canonical plugins

The aegis repo ships two plugins at its own root, both under
`plugins/`. They serve as worked examples of the substrate.

### `skill-system` — Claude-Code-style skill selection on any harness

A `pre_turn` hook injects a numbered menu of skills (parsed from
`.aegis/skills/*.md` Claude-Code-compatible files) as system context.
A `@tool` exposes `load_skill(name)` so the agent pulls the full body
when relevant.

End-to-end: ~100 lines of Python.

```bash
aegis plugin install skill-system --from gh:apiad/aegis#plugins/skill-system
```

Source: [`plugins/skill-system/`](https://github.com/apiad/aegis/tree/main/plugins/skill-system).

### `memory-system` — Hermes-inspired persistent memory with periodic dreaming

Exercises every primitive shape end-to-end.

**Hooks:**

- `pre_turn` on turn 0 — injects `SOUL.md` (persona) + `USER.md`
  (identity) + the `MEMORY.md` index (one-line teasers) + a judgment
  primer ("save a memory when…").
- `pre_turn` on turn ≥ 1 — scores memory entries against the user
  message (keyword + 24h recency boost) and injects the top-5
  name+description teasers, capped at 1,000 words. The agent calls
  `memory_read(slug)` if it wants a body.
- `session_start` — observer only; best-effort log line.

**Tools:** `memory_add`, `memory_replace`, `memory_remove`,
`memory_search`, `memory_read`.

**Workflow:** `dream` — three-stage consolidate-plus-synthesize pass
over the last `lookback_days` of session transcripts in
`.aegis/state/sessions/`.

1. Fan out a `dreamer`-profile subagent per session file (parallel).
   Each returns structured JSON with proposed memory entries and
   observations.
2. One consolidator subagent reads current entries + proposals, emits
   an action plan (add/replace/remove). The workflow applies it.
3. One synthesizer subagent writes a narrative dream log to
   `.aegis/memory/dreams/dream-YYYY-MM-DD.md`.

**Install** asks once whether to schedule the dream daily at 03:00.
If accepted, drops an overlay at `.aegis/schedules/memory-dream.yaml`
via `aegis.scheduler.push.write_atomic` (which is how the running
scheduler actually picks it up — a bare YAML append doesn't activate
cron).

```bash
aegis plugin install memory-system --from gh:apiad/aegis#plugins/memory-system
```

Source: [`plugins/memory-system/`](https://github.com/apiad/aegis/tree/main/plugins/memory-system).

## Testing a plugin

The skill-system and memory-system test suites both follow the same
pattern: manually import the plugin module via `importlib.util` so
the decorators fire, reset the relevant registries between tests, and
operate against `tmp_path` for filesystem effects. See
`tests/test_skill_system.py` and `tests/test_memory_*.py` for working
templates.

Hermetic tests for hooks use `aegis.hooks.contexts.PreTurnContext`
directly with a synthetic `SessionHandle`. Tests for tools rely on
the registry reset helpers in `aegis.tools.decorator`. Tests for
workflows use a fake `WorkflowEngine` with a scripted `delegate(...)`
that returns canned JSON — no real harness required.

## What's not in v1

Per the v1 plugin substrate spec, the following are deferred until a
concrete plugin demands them:

- **Tier B hook events** — `pre_tool_use`, `post_tool_use`,
  `on_error`, `on_interrupt`, `on_handoff`, `on_enqueue`. Each
  requires harness-specific normalization work and isn't needed yet.
- **Per-agent-profile tool scoping** — `agents.<name>.tools: [tool_a,
  tool_b]` to filter which agents see which tools. Config knob, not a
  primitive shape question.
- **Plugin-version constraints** between plugins (`my-plugin requires
  skill-system>=0.2`) and inter-plugin dependencies.
- **Tier B substrate-events bus** — `on_handoff`, `on_enqueue` as
  aegis-internal events rather than harness-level ones. Needs its own
  taxonomy.

The substrate's first job is to be small and clear enough that two
non-trivial plugins ship on top of it. That box is checked. The
deferred list reopens when a real plugin asks.
