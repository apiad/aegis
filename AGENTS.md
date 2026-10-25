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

1. the tests that cover the change pass locally, and nothing more: run the
   test files you touched and their neighbours while you iterate, plus
   `make lint-docs` when a doc or help page changed. The whole suite is CI's
   job, not yours;
2. CI is green on the PR. It runs the whole hermetic suite, slow and browser
   tests included, with format, lint, typecheck, rift and the changelog check,
   and compares the bench of the PR's base and head in its job summary. Copy
   that table into the PR body: CI warns on a regression and never fails on
   one, so a reader has to look at it;
3. it has been exercised in a browser against an `aegis serve` started after the
   change, and, for a change to how agents use aegis, by the `make test-live`
   case that covers it, run on its own (`-k`), not the whole live suite;
4. DESIGN.md describes the code as it now is;
5. a user-visible change has a `changelog.d/` fragment and updates or adds its
   help page under `src/aegis/help/`, and a change to how the pieces fit has
   its spec under `docs/superpowers/specs/`, with a status that matches the
   code;
6. before a release, Alex has smoke-tested that version himself. Offer him the
   guided smoke test then, not per PR: the build under test runs through
   `bin/aegis-dev` beside his own aegis, and an agent inside it walks him
   through what changed since the last tag, simplest first, asking after each
   step whether it works. File what the tour finds as issues unless the fix on
   main is trivial. Every check above is a proxy; his hands on the build are the
   gate, and a release he has not run is not done.

The full local gates, `make check`, `make test-slow` and `make bench`, run once
before a release, not per PR. The order is the order
of what each gate protects: a fast local loop while you work, CI before a merge
to `main`, the full gates and the smoke test before a release.

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
| `src/aegis/help/` | how to use aegis, for agents and people, shipped in the package | a tool, command, CLI subcommand or config key changes |
| `changelog.d/*.md` | one release note per change, awaiting the next release | any user-visible change |
| `CHANGELOG.md` | what shipped | a release collates the fragments |
| the pinned `vision` issue | where aegis is going now: the goal, the current milestone, and one meta-issue per subsystem with the work under it, in order | a milestone closes, a release ships, a merge changes the picture |
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
4. **Get it green, then ask.** The author works its PR until every check
   passes, not only the required ones, then asks the person whether to merge
   and stops. It merges unasked only when the person said so up front, or the
   plan or workflow they approved says so. The smoke test waits for the
   release, unless the person asks for one. Check the merge landed. `gh pr merge` run from
   inside a worktree prints `fatal: 'main' is already used by worktree` after
   the merge has already succeeded on GitHub; read `gh pr view <n> --json state`
   rather than believing the error. Once the issue closes, read the pinned
   vision issue and its sub-issues and say whether the tree still holds:
   close a finished meta-issue, reorder, attach new work; propose to the
   person anything that changes the milestones, and at a release, an update
   of the whole vision.
5. **End the PR with screenshots that prove it.** Shoot the changed screens in
   the real client, at desktop and at phone width (390 px), and a terminal
   capture of the command and its output when nothing on screen changed. Push
   them to `<issue>/` on the orphan `pr-shots` branch, which never merges, link
   their raw URLs in the PR body, and send them to the person; a PR without
   them is not done.

The PR body carries what the reviewer cannot re-derive: what was measured, what
was tried and rejected, and what was deliberately left out. Wrong guesses you
eliminated are worth writing down — they stop the next agent repeating them.

## Working here

`make check` runs every gate on the fast lane; `make test` is that lane alone, to
iterate on; `make test-slow` runs the tests both skip, browser tests included.
`make know-how` prints the procedure docs, one `when:` line each; read the ones
that match the task. Use `uv`, never pip. Python 3.13 or newer.

`make lint-docs` runs rift on `.rift.yaml`, the help pages against the code
among its rules. CI runs the same rift, pinned to a tag, so a doc rule fails a
PR like a test does.

Every icon the client draws is a symbol in the sprite in
`src/aegis/client/js/glyphs.js`, drawn in the theme: no emoji, no icon-like
Unicode marks, no SVG written into a page. A new icon is a new symbol and a
new row on its help page, `src/aegis/help/icons.md`; `tests/test_glyphs.py`
fails a change that misses either.

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
