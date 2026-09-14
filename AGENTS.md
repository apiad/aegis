# aegis

aegis is a meta-harness. It runs coding-agent CLIs (Claude Code, Gemini CLI,
OpenCode) and its own native lovelaice agent as subprocesses, and adds a control
plane above them: multiplexed sessions, inboxes, queues, workflows, schedules,
groups, file claims, execution hosts, and an MCP server every spawned agent talks
to. It is published on PyPI as `aegis-harness`, and it is the harness Alex and his
agents use for multi-agent work in this workspace, from a TUI locally and from an
installable web client over a flaky remote link.

**Read this file, then DESIGN.md, then the know-how doc for the job in front of
you.** This file changes when aegis's goals change. Nothing in it should be made
false by a commit that adds a module, a tool or a test.

## What done means

A change is done when:

1. `make check` passes;
2. it has been exercised the way a user reaches it: in the TUI or the web client,
   attached to a daemon started after the change, or with `aegis bench` for any
   claim about speed;
3. a user-visible change has a CHANGELOG entry, and a new command, driver, tool or
   config key is documented under `docs/`;
4. a change to how the pieces fit has its spec under `docs/superpowers/specs/`,
   with a status that matches the code.

Green tests against a daemon that booted before the change prove nothing about the
change.

## Where everything lives

Each place changes at a different rate. Put a fact in the one that matches what
would make it false.

| | holds | changes when |
|---|---|---|
| `AGENTS.md` | what aegis is, who it is for, what done means | the goals change |
| `DESIGN.md` | the process model, the rules that span modules, what is linted and what a reader judges | the architecture changes |
| module docstrings | the rules of one module, with their reasons | that module changes |
| `docs/` | the user-facing reference published with mkdocs | a user-visible surface changes |
| `docs/superpowers/` | why each feature is shaped the way it is | a feature is designed |
| `CHANGELOG.md` | what shipped | a release |
| `know-how/` | how to do one job | a procedure changes |
| `Makefile`, `.rift.yaml`, tests | every mechanical check | a gate is added or dropped |
| the code | everything else | constantly |

Nothing derivable is written down: module tours, command lists and counts are one
`aegis --help` or one file away. Nothing mechanical is restated: the Makefile and
`.rift.yaml` carry each check and its reason.

## Working here

`make check` runs every gate; `make test` is the fast lane to iterate on.
`make know-how` prints the procedure docs, one `when:` line each; read the ones
that match the task. Use `uv`, never pip. Python 3.13 or newer.

Commits follow the workspace convention: conventional commits, English, one logical
change. This is a shared checkout, so stage and commit named paths only
(`git commit -- <paths>`) and never amend.
