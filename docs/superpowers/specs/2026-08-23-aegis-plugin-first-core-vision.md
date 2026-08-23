# Aegis as a plugin-first core — vision

> **Status:** vision doc, captured from a voice note on 2026-08-23 after
> comparing aegis with DeepSeek Harness (DSH). Not a plan yet; not a spec.
> The purpose is to write down the destination so future work can point at
> it. See `TASKS.md` for the pointer.

## The seed

DSH's headline architectural claim is *everything is a plugin*, and unlike
most claims of the kind, they mean it: LLM adapters, tools, session
persistence, the agent loop, shells, sandboxes, sub-agent providers, even
the UI panels are plugins on top of a ~1000-LOC Cordis kernel. Plugins can
hot-load and hot-unload without restarting the process.

Aegis today is the opposite: workflows, queues, scheduler jobs, canvases,
terminals, groups — all these coordination primitives live as native
subsystems inside `src/aegis/`, and the plugin surface (`@hook`, `@tool`,
`@workflow`) is a shallow overlay that can add behavior but cannot replace
or extend the substrate. Installing a plugin requires a restart of `aegis
serve` or the TUI.

This document sketches the destination if we take DSH's tesis seriously
inside aegis's world (Python, subprocess-per-agent, protocol-above-CLI).
Five ideas, ordered roughly by leverage.

---

## Idea 1 — Vertical plugin extension: turn native subsystems into plugins

Today the plugin API is horizontal — it *adds* hooks, tools, workflows on
top of a fixed substrate. The ambition is to make it vertical: **plugins
can define new subsystems**, and most of what today is native aegis
becomes a bundled-but-swappable plugin.

Concretely, we want the plugin API to be powerful enough that all of
these can be defined by a plugin instead of the aegis core:

- Workflows and the workflow engine itself
- Queues (definition, dispatch policy, lifecycle log)
- Scheduler jobs and the trigger manager
- Canvases (shared markdown blackboards)
- Shared terminals (OSC-133 PTYs)
- Groups (broadcast-and-gather primitives)
- Harness drivers themselves (`ClaudeDriver`, `AcpDriver`,
  `LovelaiceDriver`, future `CodexDriver`, etc.) as swappable plugins
- UI surfaces — plugins declare panels, status-bar segments, commands,
  keybindings, config tabs

What stays in the aegis core is minimal:

- The `HarnessDriver` abstraction (the *interface*, not the concrete
  drivers).
- The MCP wiring — aegis owns the FastMCP server and the per-session
  injection contract, but *what tools go into the MCP surface* is
  declared by plugins.
- The UI abstraction (see Idea 5) — aegis owns the abstract widgets and
  the TUI/web bindings, plugins declare panels on top.
- Session lifecycle, JSONL persistence, event stream.
- The plugin loader/runtime itself.

**The shippable outcome.** Two builds of aegis are trivially possible:

1. **Aegis coding harness** — core + a canonical set of bundled plugins
   (queues, workflows, canvases, terminals, groups, scheduler, Claude
   driver, ACP driver, memory system, skill system). This is what we ship
   by default and what most users install.
2. **Aegis clean base** — core alone, plus whatever plugins someone else
   picks. Someone who wants to build a non-coding harness on top of
   aegis (a research assistant, a writing companion, a devops orchestrator,
   an agent for a totally different problem class) picks a different
   plugin set and gets a different product.

The plugin vocabulary has to become dramatically richer than today's
`@hook / @tool / @workflow`. Plugins must be able to:

- Declare event types (new inbox message kinds, new lifecycle events).
- Declare persistence backends.
- Declare notifier providers.
- Declare UI components (panels, status-bar segments, commands, tabs).
- Extend the MCP surface (add tools, add resources, add prompts).
- Compose with each other via dependency declarations.

---

## Idea 2 — Runtime plugin lifecycle: install/uninstall/enable/disable live

Today installing a plugin means `aegis plugin install ...` followed by a
restart. The goal is to make every plugin operation live:

- Install a plugin into a running `aegis serve` process — takes effect
  on the next turn or immediately, depending on the plugin.
- Uninstall / disable — the plugin's contributions (hooks, tools,
  panels, commands, MCP entries) unregister cleanly; the plugin's fiber
  disposes; state persists to disk in case of reinstall.
- Enable / disable per-session — a plugin can be armed globally but
  toggled off for one particular agent tab.
- Add a queue, delete a queue, add a skill, add a command, add a
  workflow — all of it in runtime, all of it hot-reloaded, no restart.

For coding agents this unlocks the tight loop: **an agent writes a
plugin during a turn, attaches it, and the next turn (or the same
session) has the new capability**. That's the key enabler for Idea 3.

This is technically demanding — Python's import system does not hot-reload
gracefully by default, and effect cleanup (unregistering everything a
plugin contributed) needs a disciplined effect-tracking model. DSH gets
this from Cordis's fiber model + `ctx.effect()` returning disposers; the
Python equivalent needs to be designed. Not a small piece of work.

---

## Idea 3 — Agent-authored plugins in-session

Idea 2 unlocks Idea 3: **the agent can extend aegis while working**.

Concretely, during a session:

- The agent decides it needs a visualization panel it doesn't have.
- The agent writes a plugin — a Python file registering a UI panel,
  maybe a helper tool, maybe a workflow.
- The agent calls an aegis MCP tool (say, `aegis_install_plugin`)
  pointing at the file it just wrote.
- The plugin loads. The panel appears in the TUI and the web view. The
  agent continues working, now using the panel it just built.

For this to be usable by the agent, it needs a **meta-skill** that
explains how aegis works — a bundled skill that surfaces:

- The plugin authoring API (via MCP documentation entries).
- The available substrate primitives (queues, workflows, canvases,
  terminals, groups, UI widgets).
- The current runtime state — what plugins are loaded, what tools are
  registered, what UI panels are visible.
- Per-plugin documentation for any plugin currently attached (fed via
  MCP as the plugin loads).

The agent, at any moment, can query MCP for "what is my environment
right now?" and get a complete, accurate answer. That is what makes
autonomous plugin authoring realistic instead of aspirational.

---

## Idea 4 — Plugin registry (aegis-hub)

Once plugins can do all of the above, distribution matters. Today
`aegis plugin install --from gh:owner/repo#plugins/name` resolves URLs
directly — good enough for the 2 canonical in-tree plugins, insufficient
if plugins are the primary extensibility.

The vision:

- **Default store** — the canonical aegis-hub (a repo listing plugins with
  a manifest: names, versions, checksums, install URLs, descriptions,
  aegis version constraints).
- **Additional stores** — register new stores by URL; each store publishes
  its own manifest. Enterprise deployments, private teams, personal
  collections all work the same way.
- **CLI browse + install** — `aegis plugin search <term>` hits every
  registered store; `aegis plugin install <name>` resolves against the
  first store that has it (with `--store` disambiguator).
- **Publish** — a plugin author with their own repo can register it in
  aegis-hub (PR to the canonical hub manifest) or run a private store.

Nothing fancy — no server-side machinery, no accounts, no ratings. Just a
list of manifests, resolvable offline once cached, publishable by opening
a PR. The bar is: someone who wants to publish an aegis plugin needs
about the same amount of ceremony as publishing an npm package.

---

## Idea 5 — UI abstraction that decouples TUI and web

Today the TUI (Textual) and the PWA are co-equal but not co-authored:
`tui/pane.py` (123KB) and the web frontend implement the same
conversation-pane concept twice, and adding a new panel means writing
Textual widget code plus React component code plus wiring both.

The vision: **an atomic, composable UI abstraction that plugins target
in ~5 lines of Python**, with two concrete backends (TUI, web) that the
core ships. Composable primitives — editor, input box, status bar
segment, panel, list, table, form — declared in Python; automatically
functional in TUI and web without plugin-specific frontend code.

Rough API sketch:

```python
from aegis.ui import panel, text_input, button, status_segment

@panel("my-plugin.settings", title="My Plugin")
def render(state):
    with panel.column():
        text_input("api_key", label="API key", value=state.api_key)
        button("Save", on_click=state.save)
```

Aegis binds `text_input` to a Textual `Input` widget in TUI mode and to
an HTML `<input>` in web mode. The plugin author writes it once.

The abstraction has to be genuinely atomic — no leaking Textual concepts
into the API surface (no `Reactive[str]`, no CSS selectors), no leaking
web concepts either (no HTML strings, no CSS). It's a bounded vocabulary
of widgets with well-defined behavior, and both backends implement the
same behavior with their native machinery.

This is the largest of the five ideas by engineering cost. It's also the
enabler for meaningful plugin ecosystem — without it, most third-party
plugins would be forced to either skip UI entirely or write two separate
frontends.

---

## Non-goals of this vision

To keep the destination honest:

- **Not a Cordis port.** The Python subprocess-per-agent model aegis
  chose is not compatible with Cordis's TypeScript in-memory fiber
  model. The *pattern* transfers; the runtime does not.
- **Not web-first.** The TUI stays a first-class surface. Any UI
  abstraction has to bind TUI as fully as web.
- **Not multi-provider LLM at the aegis level.** The harness driver
  still delegates model selection to the underlying CLI. Aegis does not
  reimplement Anthropic / OpenAI / Google SDKs.
- **Not a sync engine.** aegis remains local-first / single-operator /
  single-machine (plus SSH hosts). Plugin state is on-disk; sharing
  across machines is out of scope for this vision.

## Non-transferable knowledge from DSH

Things DSH does that we should *not* adopt:

- **Cordis-level DI + fiber lifecycle.** The complexity budget is too
  high for Python + subprocess-per-agent. Adopt disposal discipline;
  don't adopt Cordis.
- **YAML with `!!js` expressions.** Config should stay declarative
  Python-friendly YAML; embedded expression languages are power we
  don't need at that cost.
- **Snapshot browser tests.** Great for a public product; disproportionate
  cost for an operator tool with a small user base.

## Sequencing (rough)

Not a plan — a hint. Roughly in order of dependency:

1. Design the effect-tracking + disposal model (needed by Ideas 1, 2, 3).
2. Design the UI abstraction (Idea 5) — this is the largest single
   subsystem and blocks meaningful third-party plugins.
3. Refactor one native subsystem to be a plugin as a proof point (queues
   or scheduler). Keep it bundled by default.
4. Runtime install/uninstall/enable/disable (Idea 2). Depends on 1.
5. Meta-skill + MCP environment introspection (Idea 3). Depends on 2.
6. Plugin registry / aegis-hub (Idea 4). Depends on 1-2 being stable
   enough that plugins can be authored against a durable contract.
7. Migrate remaining native subsystems to bundled plugins as bandwidth
   allows.

Each of these is weeks-to-months of work. This is a multi-quarter
direction, not a sprint plan.

## Origin

Voice note from Alex on 2026-08-23, dictated after reading the
DeepSeek Harness vs. aegis comparison report at
`.playground/deepseek-vs-aegis/report.pdf` (workspace-local, not
committed). The comparison identified DSH's plugin-first architecture,
runtime lifecycle, and rich event model as the most valuable patterns
to steal; this vision extends those observations into aegis-native
form.
