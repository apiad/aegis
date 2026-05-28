---
title: Aegis plugin substrate — hooks, tools, and the plugin registry
date: 2026-05-28
status: draft
---

# Aegis plugin substrate — hooks, tools, and the plugin registry

## Context

Today an aegis user who wants to add behavior chooses among at least three parallel substrates:

- A `@workflow`-decorated Python function in `.aegis/plugins/*.py`, dispatched via CLI (`aegis workflow run`) or MCP (`aegis_run_workflow`).
- A Telegram chat command registered in the internal Python `COMMANDS` registry.
- A schedule entry in `.aegis.yaml` that wraps a workflow with a cron expression.

There is no "skill" substrate: the equivalent of Claude Code's auto-loaded markdown skills is unavailable when an aegis session is driven by Claude Code itself (the harness handles it natively but only inside that harness), and unavailable entirely when the session is driven by OpenCode, Gemini, or Codex.

This design adds two new substrate primitives — **hooks** and **tools** — alongside the existing workflow primitive, and reframes "shipped extensions" as user-installable plugins. The result: a single, harness-agnostic substrate that lets users (and aegis itself, via a single shipped plugin) compose skill-like, memory-like, knowhow-like behavior without aegis having to bake any of those concepts into the core.

A canonical `skill-system` plugin demonstrates the substrate by reproducing Claude Code's skill-selection behavior on any harness aegis drives.

## Thesis

Aegis ships the substrate. Everything semantic — skills, memory systems, knowhow surfaces, briefing injectors — lives in user-space plugins built on top. There is no distinction at runtime between aegis-authored plugins and user-authored plugins; both are folders under `.aegis/plugins/`, both auto-import via the same machinery, both are visible, editable, and removable by the user.

## Three substrate primitives

| Primitive | Triggered by | Contract shape | Status |
| --- | --- | --- | --- |
| **Workflow** | User / agent (via MCP) / scheduler | Async orchestration; may spawn workers, drain inboxes, log to JSONL; MCP-callable via the umbrella `aegis_run_workflow(name=...)` | Already shipped |
| **Hook** | Harness lifecycle events | Typed event → typed return; one mutator event (`pre_turn`) + three observer events; ≤5s wall-clock budget; declaration-order composition | New |
| **Tool** | Agent (via MCP) | First-class MCP tool with its own name and typed schema; synchronous request/response; deterministic; no orchestration | New |

The three are non-overlapping in *who triggers them* and *what shape the contract has*. A plugin is a Python package that registers any mix of the three plus declares the folder and config conventions it expects.

## Hook contract

### Events shipped in v1 — Tier A only

| Event | Mutator? | Payload | Notes |
| --- | --- | --- | --- |
| `pre_turn` | Yes | `PreTurnContext` | Fires before the user message reaches the harness subprocess. Return value may modify the turn. |
| `post_turn` | No | `PostTurnEvent` | Fires after the harness emits its final assistant message of the turn. Return value ignored. |
| `session_start` | No | `SessionStartEvent` | Fires once after the session is spawned. |
| `session_end` | No | `SessionEndEvent` | Fires once when the session is being closed. |

Deferred to a Tier B follow-up: `pre_tool_use`, `post_tool_use`, `on_error`, `on_interrupt`, `on_handoff`, `on_enqueue`. Each requires harness-specific normalization work and isn't needed to ship the canonical plugin.

### `pre_turn` — the only mutator

```python
from aegis.hooks import hook, PreTurnContext, PreTurnResult

@hook("pre_turn")
async def skill_selector(ctx: PreTurnContext) -> PreTurnResult | None:
    """Inject a menu of available skills as system context."""
    skills = load_skills_index(ctx.project_root)
    if not skills:
        return None
    menu = render_menu(skills)
    return PreTurnResult(prepend_system=menu)
```

**`PreTurnContext` (frozen dataclass, read-only):**

| Field | Type | Notes |
| --- | --- | --- |
| `session` | `SessionHandle` | Read-only handle: `handle`, `agent_profile`, `harness`. |
| `user_message` | `str` | The message about to be sent to the harness. |
| `history` | `tuple[Turn, ...]` | Prior turns of the session, oldest first. |
| `project_root` | `Path` | Resolved by `find_project_root()`. |
| `prior_results` | `tuple[PreTurnResult, ...]` | Results already produced this turn by earlier-declared hooks. Empty for the first hook. |

**`PreTurnResult` (frozen dataclass, all fields optional):**

| Field | Type | Notes |
| --- | --- | --- |
| `prepend_system` | `str \| None` | Text appended to the system prompt for this turn only. Multiple hooks concatenate in declaration order, separated by blank lines. |
| `rewrite_user` | `str \| None` | Replaces `ctx.user_message`. Conflicts across hooks fail-loud at composition time. |
| `block` | `str \| None` | Cancels the turn; the string is the reason surfaced to the user via the front-end. First non-None wins; remaining hooks for this turn are skipped. |
| `extend_history` | `tuple[Turn, ...] \| None` | Synthetic prior turns spliced into the session history for this turn only. Concatenates in declaration order. |

Composition rules:

1. **`block` short-circuits.** The first hook returning a non-None `block` ends composition; later hooks for that event don't run.
2. **`rewrite_user` is exclusive.** Two hooks both returning non-None `rewrite_user` is a fail-loud error at composition time. Pick one.
3. **`prepend_system` and `extend_history` accumulate** in declaration order.

### Observers

`post_turn`, `session_start`, `session_end` receive their typed event payload, return nothing useful, can perform side effects (write files, post Telegram, enqueue follow-up workflows), and have the same 5s wall-clock cap.

### Ordering, timeouts, exceptions

- **Order.** Within a single event, hooks fire in declaration order — the order their plugin modules were imported by the auto-importer. Plugin folders are iterated lexically by name; files inside a plugin are iterated lexically by relative path. Stable, observable, and matches the existing `@workflow` registration order.
- **Timeout.** Each hook invocation is capped at 5s wall-clock. On timeout, the hook is hard-killed, an error is logged to `.aegis/state/hooks/<plugin>/<hook>.jsonl`, and composition continues as if the hook returned `None`.
- **Exceptions.** A hook raising an exception is logged-and-skipped by default — composition continues, the user-visible turn proceeds. A hook may opt into fail-loud with `@hook("pre_turn", strict=True)`; a strict hook raising blocks the turn with the exception's string in `PreTurnResult.block`.

### Hook registration

```python
from aegis.hooks import hook

@hook("pre_turn", strict=False)        # strict optional, default False
async def my_hook(ctx): ...
```

The decorator registers the function at module import time. Duplicate names across plugins fail-loud at registration. The decorator records the source plugin (derived from the module's path under `plugin_dirs`) so error logs can name the culprit.

## Tool contract

### Decorator

```python
from aegis.tools import tool

@tool
async def load_skill(name: str) -> str:
    """Load the full body of a registered skill.

    Args:
        name: skill name as listed in the pre-turn menu.

    Returns:
        the skill's markdown body.
    """
    path = aegis_project_root() / ".aegis/skills" / f"{name}.md"
    return path.read_text(encoding="utf-8")
```

### Schema generation

Auto-generated from type hints + docstring via FastMCP, which is already in the aegis stack (`src/aegis/mcp/server.py`):

- Tool name defaults to function name; override with `@tool(name="...")`.
- Tool description = the docstring's first paragraph.
- Argument descriptions = the `Args:` section.
- Argument types = Python type hints; FastMCP maps to JSON Schema.
- Return type = the type hint on the return; serialized as the tool's output schema.

### Registration

Same `.aegis/plugins/<name>/*.py` auto-import path as workflows and hooks. A plugin file may freely mix `@workflow`, `@hook`, and `@tool` declarations.

Name collisions — between two `@tool`s or between a `@tool` and an aegis built-in MCP tool (`aegis_enqueue`, `aegis_handoff`, `aegis_run_workflow`, `aegis_list_sessions`, etc.) — fail-loud at registration. Same posture as duplicate workflow names today.

### Scope

All registered `@tool`s are available to every spawned session in v1. Per-agent-profile tool scoping (e.g. `agents.researcher.tools: [load_skill]`) is deferred — it's a config knob, not a primitive shape question.

### Timeout, logging

- **Timeout.** Each call is capped at 30s wall-clock by default; configurable per-tool via `@tool(timeout=...)`. On timeout the call is hard-killed and the tool returns a structured `ToolTimeout` error visible to the agent.
- **Logging.** Each invocation appends one JSONL record to `.aegis/state/tools/<plugin>/<tool>.jsonl` (one record per call: timestamp, agent handle, args, success/error, duration). Mirrors the existing `state/workflows/` pattern.

### Sync vs async

Both sync and async function bodies are supported (FastMCP handles either). The convention in shipped plugins is async-by-default; sync is fine for trivial file reads.

### Tool ≠ workflow

A workflow exposed via MCP is reached as `aegis_run_workflow(name="X", kwargs=...)` — one umbrella tool that takes a workflow name argument. A `@tool` is its own first-class MCP tool with its own name, typed schema, and direct visibility in the agent's tool list. Use a workflow when the operation is orchestrative (spawn workers, await drains, chain steps); use a tool when the operation is a synchronous capability the agent should be able to reach for directly.

## Plugin substrate

### Folder layout

```
.aegis/plugins/
  skill-system/
    plugin.toml
    skill_system.py           # @hook + @tool registrations
    _install.py               # optional — runs on `aegis plugin install`
    _uninstall.py             # optional — runs on `aegis plugin uninstall`
    _lib.py                   # optional — internal helpers, not auto-imported
    templates/
      skills/
        README.md             # materialized into .aegis/skills/ on install
  my_local_plugin/
    plugin.toml
    handlers.py
    nested/
      more_handlers.py        # auto-imported (full recursion)
  my_single_file.py           # legacy single-file plugin — still works
```

### Auto-import rules

The existing auto-importer scans every `*.py` under each `plugin_dirs` entry. This design extends it as follows:

- **Full recursion.** Walk into every subdirectory of each `plugin_dirs` entry without depth limit. Each plugin organizes its internal structure however it likes.
- **Skip underscore-prefixed files.** Any `.py` whose basename starts with `_` is skipped by the auto-importer at any depth (`_install.py`, `_uninstall.py`, `_lib.py`, `_internal/_helpers.py`). These files exist for plugin-internal use, not for substrate registration.
- **Skip underscore-prefixed directories.** Any directory whose basename starts with `_` is skipped entirely. Lets plugins keep vendor or cache folders out of the auto-import sweep without per-file workarounds.
- **Existing single-file plugins keep working.** `.aegis/plugins/my_thing.py` (no enclosing folder) is unchanged.

### Plugin manifest — `plugin.toml`

```toml
[plugin]
name           = "skill-system"
version        = "0.1.0"
description    = "Inject relevant skill descriptions pre-turn; agent calls load_skill on demand."
requires_aegis = ">=0.15"

[default_config]
# Merged into .aegis.yaml under `plugins.skill-system` on install.
# Only consulted at install time; runtime config lives in .aegis.yaml.
folder = ".aegis/skills/"
top_k  = 3

[[templates]]
src = "templates/skills/"
dst = ".aegis/skills/"
```

The manifest is the single source of truth for plugin metadata. Aegis reads only the fields listed above; unknown keys are preserved across `aegis plugin update` operations.

## Install / update / uninstall lifecycle

### `aegis plugin install <name>`

1. **Resolve.** Walk configured `plugin_registries` (see below) in order; first hit wins. Ambiguity across registries fails with the candidate list.
2. **Fetch.** Pull the plugin folder from the registry via `git archive` over HTTPS (or equivalent tarball download).
3. **Copy.** Place files under `.aegis/plugins/<name>/`. If the folder already exists, refuse unless `--force` is passed.
4. **Merge config.** If the manifest declares `[default_config]`, prompt to merge under `.aegis.yaml`'s `plugins.<name>` namespace (`--yes` skips the prompt; runs as if the user confirmed). Comment-preserving merge via `aegis.config.edit` (ruamel-backed).
5. **Materialize templates.** For each `[[templates]]` entry, copy `src` → `dst` if `dst` doesn't already exist; never overwrite.
6. **Run `_install.py::install(ctx)`** if present.
7. **Record install state.** Append/update an entry in `.aegis/plugins.lock` (see Lockfile below).

If step 6 raises, the installer rolls back steps 3–5, surfaces the traceback, and exits non-zero. Step 4 is *not* rolled back automatically — config merges are user-visible and Alex should keep the partial state to inspect.

### `_install.py` contract

```python
# .aegis/plugins/skill-system/_install.py
from aegis.plugins import InstallContext


def install(ctx: InstallContext) -> None:
    """Runs after files are copied and templates are materialized.

    Plugin author owns idempotency and failure modes.
    """
    if not ctx.skills_dir.exists():
        ctx.skills_dir.mkdir(parents=True, exist_ok=True)
    ctx.console.print(f"[green]skill-system[/] ready at {ctx.skills_dir}")
```

**`InstallContext` (frozen dataclass):**

| Field | Type | Notes |
| --- | --- | --- |
| `project_root` | `Path` | Parent of `.aegis.yaml`. |
| `aegis_dir` | `Path` | `<project_root>/.aegis/`. |
| `plugin_dir` | `Path` | `<aegis_dir>/plugins/<name>/`. |
| `config` | `AegisConfig` | Parsed `.aegis.yaml`. Mutable — changes are written back to disk on a clean return. |
| `console` | `rich.Console` | Pre-baked for terminal output; respects no-color and quiet flags from the CLI. |
| `confirm(q: str, default: bool) -> bool` | callable | Prompts the user; returns `default` automatically when the installer was invoked with `--yes`. |

Both `install` and `uninstall` are optional. Aegis tolerates missing `_install.py` (no setup needed) and missing `uninstall` (no teardown needed).

### `aegis plugin uninstall <name>`

1. **Run `_uninstall.py::uninstall(ctx)`** if present. Exceptions log + continue — the user wants the plugin gone.
2. **Delete** `.aegis/plugins/<name>/`.
3. **Strip** the plugin's `[default_config]` from `.aegis.yaml`'s `plugins.<name>` namespace. The user's hand-edits to that namespace are preserved (only keys present in the manifest's `default_config` are removed).
4. **Leave user data alone.** `.aegis/skills/`, materialized templates, state files — untouched. The plugin's author decides in `uninstall(ctx)` whether to clean state; aegis defaults to keeping it.
5. **Update the lockfile.**

### `aegis plugin update [name]`

Re-fetch the plugin from its recorded source registry and replace. If the user has edited any installed file (detected by SHA mismatch against the lockfile's recorded file hashes), refuse and print the diff; user resolves by hand (delete the local edit, or pin to the current commit via `aegis plugin pin <name>`).

`aegis plugin update` with no name updates every installed plugin.

### Lockfile — `.aegis/plugins.lock`

```toml
[[plugins]]
name      = "skill-system"
version   = "0.1.0"
registry  = "gh:apiad/aegis"
path      = "plugins/skill-system"
commit    = "f3a2e9c..."
installed = "2026-05-28T12:34:56Z"
file_hashes = { "skill_system.py" = "sha256:...", "plugin.toml" = "sha256:...", ... }
```

Gitignored by default — it records local install state, not project intent. Teams that want to share plugin installs commit the lockfile by hand.

### Trust boundary

`_install.py` and runtime plugin code are arbitrary Python — by design. The act of `aegis plugin install` is the trust gate; sandboxing what runs afterward would be theater. On first install from a registry not listed in the default-trusted set, aegis prints a one-line notice: `Installing from <registry-url> — first time this registry is used.`

## Plugin registry

### Topology

A plugin registry is a folder convention, not a service. Any URL pointing to a tree where each subfolder is a plugin (each containing a `plugin.toml`) qualifies.

**Default registry: `gh:apiad/aegis#plugins/`** — a `plugins/` directory at the root of the aegis repo itself. This means:

- Plugin updates don't require an aegis version bump. A user on aegis v0.15 today can install tomorrow's `skill-system` revision the moment it merges to `main`.
- v1 ships one repo, not two. Once the ecosystem grows we can split into `apiad/aegis-plugins` by changing the default URL; the lockfile records source so existing installs keep working.

### Configuration

```yaml
# .aegis.yaml
plugin_registries:
  - gh:apiad/aegis#plugins/                       # default, can be omitted
  - gh:<some-user>/<some-repo>#aegis/plugins/     # third-party
  - file:///home/me/local-plugins/                 # filesystem registry
```

### Discovery commands

- `aegis plugin list` — installed plugins (reads lockfile).
- `aegis plugin search <query>` — walks every registered registry, reads each `plugin.toml`, ranks by name + description match.
- `aegis plugin show <name>` — reads the named plugin's `plugin.toml` from its source registry (or from the lockfile if installed).

## The canonical `skill-system` plugin

### Behaviour

A user drops Claude-Code-compatible skill markdown files into `.aegis/skills/`:

```markdown
---
name: brainstorming
description: Use before any creative work — features, components, behavior changes. Explores user intent, requirements, and design before implementation.
---

(skill body in markdown)
```

When the user sends a message, the plugin's `pre_turn` hook injects a menu like:

```
Available skills:

1. brainstorming — Use before any creative work — features, components, behavior changes. Explores user intent, requirements, and design before implementation.
2. using-workspace-locks — Use before doing non-trivial work that writes files in the Workspace — declares lock scope so concurrent sessions don't clobber each other.
3. ...

If any are relevant to your task, call `load_skill(name)` to load the full body before proceeding.
```

The agent reads the menu, decides which skills apply, and calls `load_skill(name)`. The tool returns the body. The agent follows the skill.

This is the same loop Claude Code runs natively for its own `Skill` tool — but here the loop is implemented as a 50-line plugin on top of aegis primitives, runs on any harness aegis drives (subject to MCP injection reach — see Assumptions), and is fully visible and editable by the user.

### Implementation sketch

```python
# .aegis/plugins/skill-system/skill_system.py
from pathlib import Path
import yaml

from aegis.hooks import hook, PreTurnContext, PreTurnResult
from aegis.tools import tool


def _load_index(project_root: Path) -> list[tuple[str, str]]:
    folder = project_root / ".aegis/skills"
    if not folder.exists():
        return []
    out = []
    for path in sorted(folder.glob("*.md")):
        text = path.read_text(encoding="utf-8")
        front, _, _ = text.partition("\n---\n")
        if not front.startswith("---\n"):
            continue
        meta = yaml.safe_load(front[4:])
        name = meta.get("name") or path.stem
        desc = meta.get("description", "")
        out.append((name, desc))
    return out


@hook("pre_turn")
async def inject_menu(ctx: PreTurnContext) -> PreTurnResult | None:
    skills = _load_index(ctx.project_root)
    if not skills:
        return None
    lines = ["Available skills:\n"]
    for i, (name, desc) in enumerate(skills, 1):
        lines.append(f"{i}. {name} — {desc}")
    lines.append(
        "\nIf any are relevant to your task, call "
        "`load_skill(name)` to load the full body before proceeding."
    )
    return PreTurnResult(prepend_system="\n".join(lines))


@tool
async def load_skill(name: str) -> str:
    """Load the full body of a registered skill.

    Args:
        name: skill name as listed in the pre-turn menu.

    Returns:
        the skill's markdown body (the content after the YAML frontmatter).
    """
    from aegis.workflow import current_project_root
    path = current_project_root() / ".aegis/skills" / f"{name}.md"
    text = path.read_text(encoding="utf-8")
    _, _, body = text.partition("\n---\n")
    return body.lstrip()
```

### `_install.py` for `skill-system`

The folder and the README come from the manifest's `[[templates]]` entry — the
installer copies `templates/skills/` to `.aegis/skills/` automatically. The
`_install.py` for this plugin only confirms the installation visibly:

```python
from aegis.plugins import InstallContext


def install(ctx: InstallContext) -> None:
    skills_dir = ctx.aegis_dir / "skills"
    n = sum(1 for _ in skills_dir.glob("*.md")) if skills_dir.exists() else 0
    ctx.console.print(
        f"[green]skill-system[/] ready — {n} skill file(s) at {skills_dir}/"
    )
```

A plugin author would reach for `_install.py` when setup needs imperative logic
— validating an external dependency, prompting for credentials, registering with
an OS service. Plain folder/file scaffolding belongs in `[[templates]]`.

## Implicit assumptions worth surfacing

1. **MCP injection reach is the cross-harness frontier.** Today aegis MCP is injected only for Claude sessions per-invocation via `--mcp-config` + `--strict-mcp-config`. Gemini and OpenCode workers in v1 don't see aegis MCP. The `skill-system` plugin (and any plugin relying on `@tool`) works end-to-end on Claude harness on day one. Reach to other harnesses depends on the existing harness roadmap (see `vault/Atlas/Architecture/2026-05-25-aegis-harness-roadmap.md`); this design does not change that timeline.
2. **The aegis AGENTS.md still states specs/plans are HTML.** This is stale per the current global rule (Markdown-first, HTML companion only on explicit request). A `/maintain-know-how aegis` pass will reconcile after this design lands.

## Deferred — call-outs for future work

| Deferred | Why | When to revisit |
| --- | --- | --- |
| Tier B hook events (`pre_tool_use`, `post_tool_use`, `on_error`, `on_interrupt`, `on_handoff`, `on_enqueue`) | Each requires harness-specific normalization; not needed for the canonical plugin. | When a real plugin proves it needs one. |
| Python package dependencies (`plugin.toml [dependencies] python = [...]`, shared `.aegis/_vendor/`) | Adds a resolver + vendor dir; v1 plugins survive on aegis's existing dep stack. | First time a desired plugin needs an external Python package not already shipped. |
| Inter-plugin dependencies (`plugin.toml [dependencies] plugins = [...]`, recursive resolver) | v1 ships one plugin; recursive resolver with no consumers is premature. | When the second plugin authored wants to compose on top of the first. |
| Plugin-version constraints (`skill-system>=0.2`) | Subsumed by the deferred deps story; same revisit trigger. | Same as above. |
| Per-agent-profile tool scoping (`agents.researcher.tools: [load_skill]`) | A config knob, not a primitive shape question. | First time a real session needs to hide a tool. |
| Three-way merge on `aegis plugin update` conflicts | Refuse-and-surface is safer for v1; merge adds complexity for the rare case of edited installed plugins. | When local plugin edits become routine. |
| Claude-Code skills auto-import plugin (`~/.claude/skills/` → `.aegis/skills/`) | One-line `cp` substitutes for v1. | When the friction of manual copy is felt. |
| Tier B "substrate events" bus (`on_handoff`, `on_enqueue`) | Aegis-internal events live outside the harness-events bus; they need their own taxonomy. | When a plugin wants to react to substrate state changes. |

## Acceptance criteria for v1

A v1 ship is the following, in order:

1. **`aegis.hooks`** package: `@hook` decorator, the four Tier A events, `PreTurnContext` + `PreTurnResult` dataclasses, observer event payloads, declaration-order composition with the rules above, 5s timeout, log-and-skip exception handling with `strict=` opt-in, JSONL logging to `.aegis/state/hooks/<plugin>/<hook>.jsonl`.
2. **`aegis.tools`** package: `@tool` decorator, FastMCP integration (registration into the live MCP runtime), name-collision fail-loud, 30s default timeout, JSONL logging to `.aegis/state/tools/<plugin>/<tool>.jsonl`.
3. **Plugin loader** changes: full recursion into `plugin_dirs` entries; skip `_*.py` files and `_*` directories at any depth; existing single-file plugins keep working.
4. **`aegis plugin` CLI subapp**: `install`, `update`, `uninstall`, `list`, `search`, `show`, `pin`. Registry resolution against configured `plugin_registries` with `gh:apiad/aegis#plugins/` as the default.
5. **`aegis.plugins` package**: `InstallContext`, the install/uninstall machinery, lockfile management, `_install.py` / `_uninstall.py` invocation.
6. **Manifest schema validator**: reads `plugin.toml`, enforces required fields, preserves unknown keys.
7. **Canonical `skill-system` plugin** shipped at `repos/aegis/plugins/skill-system/` in the aegis repo, importable via `aegis plugin install skill-system` against the default registry.
8. **Tests**:
   - Unit: hook composition rules (block short-circuits, rewrite-user conflicts fail-loud, prepend-system concatenates), tool registration, install context behaviour, manifest parsing.
   - Integration: a fixture plugin registered in a temporary `plugin_dirs` entry exercises `@workflow` + `@hook` + `@tool` in one file. The `skill-system` plugin loaded against a fixture `.aegis/skills/` produces the expected pre-turn menu and `load_skill` returns the expected body.
   - Live (marked, opt-in): a real `claude` subprocess driven by aegis, with `skill-system` installed, calls `load_skill` and the assistant message reflects skill content.

A v1 ship does *not* include any deferred item from the table above. The acceptance gate is the canonical plugin running end-to-end on a Claude harness session and a clean uninstall returning the plugin folder to its pre-install state.
