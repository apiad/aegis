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

## Foundational principle — the core is a Python library, everything else sits on top

Before the five ideas, one architectural commitment that colors all of
them: **aegis's core is a Python library, not a server**. The server,
the TUI, the PWA, the wire format, the HTTP remote plane — all of these
sit *on top of* the library and are themselves consumers of it, not
peers to it.

The test: another Python application (a dark factory, a research
pipeline, a Jupyter notebook doing ad-hoc orchestration, an embedded
agent inside somebody else's product) should be able to `import aegis`
and use everything aegis can do — spawn agents, register plugins, run
workflows, dispatch queues, hot-load a plugin authored inside the
calling process — without ever running `aegis serve`, without opening a
socket, without dealing with the FastMCP HTTP surface, without a wire
format. Zero network. Zero subprocess of the aegis server itself
(agent subprocesses still exist, obviously — that's the harness model).

The server is a *frontend* to the library, one of several. So is the
TUI. So is the PWA. So is the HTTP remote plane. Each frontend
translates its own interaction model into library calls; nothing lives
only in the frontend.

Consequences that shape everything below:

- **Plugin registration is a Python API call.** `aegis.plugins.install(path)`,
  `aegis.plugins.enable(name)`, `aegis.plugins.attach(module)` must all
  be callable from library code, not only through a CLI or a server
  request. The CLI is a thin argparse over these; the server is a thin
  HTTP wrapper.
- **UI declaration is Python-native.** A plugin declaring a panel does
  so with library calls, and if no frontend is attached those panels
  simply don't render — the plugin still works headless. (Idea 5's UI
  abstraction fits here: the abstraction is a library-level object
  graph; the frontends are its interpreters.)
- **The MCP surface is a projection of the library, not a source of
  truth.** Whatever is registered as an MCP tool must also be callable
  as a library function; MCP is the shape aegis presents *to agents*,
  not aegis's only public surface.
- **Config is a Python object.** YAML is a serialization of it, not the
  canonical form. A library user can build a config in code and skip
  YAML entirely.
- **State lives on disk in the library's format.** The server does not
  own persistence; the library does. Two processes (server + embedded
  library user) pointing at the same `.aegis/` directory should not
  corrupt each other — file locking / SQLite / append-only JSONL, but
  the answer is at the library level.

This principle is not new — aegis today already ships as a Python
package — but it needs to be made *load-bearing*. Today several
capabilities are wired at the CLI or server layer (some argparse
subcommands do work that belongs in the library; some MCP tools have
logic that isn't reachable except through MCP). Making the library the
sole owner of every capability is a refactor, and it precedes most of
the ideas below because those ideas all assume library-level
extensibility.

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

## Test targets — five forks that measure the architecture

The plugin-first thesis is falsifiable: if the core is really thin and
the plugin vocabulary really rich, it should be *easy* to reshape aegis
into products that look nothing like a coding harness. Five concrete
targets to measure difficulty against — added 2026-08-23 as follow-up.

Each is a hypothetical bundle: a specific plugin set + config that
produces a working product on top of the aegis core. The measurement is
not "is it built" — it's "how much code does the plugin set need, and
how much of it belongs in aegis core vs. in the plugin".

1. **The default: coding harness.** Baseline. Bundled plugins for
   queues, workflows, canvases, terminals, groups, scheduler, Claude
   and ACP drivers, memory system, skill system. This is what ships as
   `aegis` today, just repackaged.

2. **Research harness.** A plugin set for literature review and
   synthesis: source-puller plugin (wrapping `bin/pull-source` +
   Firecrawl), corpus-index plugin (semantic search over pulled
   sources), citation-graph panel, note-linking UI, distill workflow.
   No terminals, no PTYs. The MCP surface is source-shaped, not
   file-shaped.

3. **Creative writing / storytelling harness.** A plugin set for
   long-form narrative: chapter/scene structure panel, character-sheet
   canvas, timeline panel, prose-linter workflow, POV-switch skill. The
   default agent persona is a collaborator, not an implementer. No
   bash, no tests, no git integration.

4. **LaTeX paper harness.** A plugin set for academic paper drafting:
   LaTeX source panel, live PDF viewer panel, auto-compile-on-save
   workflow (LaTeX → PDF → refresh), citation manager against BibTeX,
   equation editor. The main loop is edit-compile-view rather than
   edit-run-test. A CI-like plugin that keeps the PDF fresh in the
   background.

5. **Dark factory.** A plugin set for autonomous multi-agent code
   production, meant to run without a human in the seat: agent-to-agent
   handoff-heavy workflows, PR-open / PR-review / PR-merge automation,
   scheduled cycles (spawn N workers, dispatch tickets, harvest PRs,
   deliver diffs), Telegram/webhook notifications on blockers. No TUI
   required — headless `aegis serve` with the web surface for
   inspection. Different key bindings, different status bar (queue
   throughput instead of context gauge), different default profile.

The architecture is well-shaped if each of these bundles is on the
order of hundreds of lines of plugin code, not thousands, and if none
of them require touching aegis core. The architecture is *not*
well-shaped if any of them exposes a missing extension point that
forces a core change.

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
