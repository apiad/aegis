# aegis

aegis is a web-native workplace for coding agents. `aegis serve` runs Claude Code,
OpenCode and Codex sessions on one machine and serves them to browser tabs: a Fleet view, shared tabs,
a transcript per session, and an archive. Agents in it get aegis's tools over MCP:
monitors, queues of workers, handoffs, reading a peer, and an inbox that delivers
what those produce. It is published on PyPI as `aegis-harness`, and it is the
workplace Alex and his agents use for multi-agent work in this workspace.

Until 2.0 aegis was a terminal app. That tree lives under `legacy/` as reference:
not packaged, not tested, not launchable. Read it to learn how something was done;
never import it. Its design is `legacy/DESIGN.md`.

**Read this file, then DESIGN.md, then the know-how doc for the job in front of
you.** This file changes when aegis's goals change. Nothing in it should be made
false by a commit that adds a module, a tool or a test.

## What done means

A change is done when:

1. `make check` passes, browser tests included;
2. it has been exercised in a browser against an `aegis serve` started after the
   change, and, for a change to how agents use aegis, by a real Claude Code
   session (`make test-live`);
3. `make bench` has run and its table is in the PR body. CI reports a regression
   as a warning and never fails on one, so a reader has to look at it;
4. DESIGN.md describes the code as it now is;
5. a user-visible change has a `changelog.d/` fragment, and a change to how the
   pieces fit has its spec under `docs/superpowers/specs/`, with a status that
   matches the code;
6. Alex has smoke-tested that version himself through `bin/aegis-dev`
   (`AEGIS_REF=<branch> aegis-dev`), ideally before the PR opens and always
   before a release. Every check above is a proxy; his hands on the build are
   the gate, and a merged PR he has not run is not done.

Green tests against a server that booted before the change prove nothing about
the change.

## Where everything lives

Each place changes at a different rate. Put a fact in the one that matches what
would make it false.

| | holds | changes when |
|---|---|---|
| `AGENTS.md` | what aegis is, who it is for, what done means | the goals change |
| `DESIGN.md` | the process model and the rules that span modules | the architecture changes |
| module docstrings | the rules of one module, with their reasons | that module changes |
| `docs/superpowers/` | the vision and one spec and plan per slice: why each part is shaped the way it is | a feature is designed |
| `changelog.d/*.md` | one release note per change, awaiting the next release | any user-visible change |
| `CHANGELOG.md` | what shipped | a release collates the fragments |
| GitHub issues | what is still to do: defects, features, ideas (`idea` marks a direction not yet decided) | work is found, decided or lands |
| `know-how/` | how to do one job | a procedure changes |
| `Makefile`, `.rift.yaml`, tests | every mechanical check | a gate is added or dropped |
| `legacy/` | the TUI-era tree, its tests, docs and know-how, as reference | never |
| the code | everything else | constantly |

Nothing derivable is written down: module tours, command lists and counts are one
`aegis --help` or one file away. Nothing mechanical is restated: the Makefile and
`.rift.yaml` carry each check and its reason.

## How work lands here

**Issue, then worktree, then PR that CI passes before it merges.** Every
non-trivial change, by every agent. Not a suggestion — this is the default, and
an agent that commits straight to `main` has skipped the only step that catches
what a local run cannot.

1. **File the issue first.** State what is wrong with evidence, not a plan: the
   reproduction, the numbers, the file and line. An issue whose claims were never
   measured is a guess with a URL. It is also where the analysis lives after the
   branch is deleted.
2. **Work in a worktree**, not in the shared checkout. Other agents and Alex are
   editing the same tree at the same time; `.claude/worktrees/` keeps your edits,
   your index and your branch to yourself. Branch from `origin/main`, never from
   another PR's branch — a stacked PR is closed automatically when its base merges.
3. **Open a PR and let CI judge it.** A green local `make test` is not a green
   run: the runner differs, and it has already caught what no laptop did (Typer
   colours `--help` only when `GITHUB_ACTIONS` is set, see #7). If CI is red,
   find out whether `main` is red the same way *before* concluding it is yours —
   `gh run view <id> --log-failed` on both, and diff the failure lists.
4. **Merge only on green**, and check the merge landed. `gh pr merge` run from
   inside a worktree prints `fatal: 'main' is already used by worktree` after
   the merge has already succeeded on GitHub; read `gh pr view <n> --json state`
   rather than believing the error.

The PR body carries what the reviewer cannot re-derive: what was measured, what
was tried and rejected, and what was deliberately left out. Wrong guesses you
eliminated are worth writing down — they stop the next agent repeating them.

## Working here

`make check` runs every gate; `make test` is the fast lane to iterate on.
`make know-how` prints the procedure docs, one `when:` line each; read the ones
that match the task. Use `uv`, never pip. Python 3.13 or newer.

`lint-docs` is the one gate CI cannot run: `rift` is private and not on PyPI.
Run it locally before opening the PR.

Release notes are **fragments**, not edits to `CHANGELOG.md`: drop a file in
`changelog.d/` named `<slug>.<category>.md` and a release collates them
(`changelog.d/README.md` has the format; `make changelog-check` blocks).

The tests run the real app: the fake claude in `tests/fake_claude.py` speaks
stream-json and calls the real `/mcp` with its own token, and the browser tests
drive headless Chromium against a real `aegis serve`. Prefer a test of that shape
to a unit test of a seam.

Commits follow the workspace convention: conventional commits, English, one logical
change. This is a shared checkout, so stage and commit named paths only
(`git commit -- <paths>`) and never amend.
