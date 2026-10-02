# aegis

aegis is a meta-harness. It runs coding-agent CLIs (Claude Code, Gemini CLI,
OpenCode) and its own native lovelaice agent as subprocesses, and adds a control
plane above them: multiplexed sessions, inboxes, queues, workflows, schedules,
groups, file claims, execution hosts, and an MCP server every spawned agent talks
to. It is published on PyPI as `aegis-harness`, and it is the harness Alex and his
agents use for multi-agent work in this workspace, from a TUI locally and from a
browser over a remote link.

**Read this file, then DESIGN.md, then the know-how doc for the job in front of
you.** This file changes when aegis's goals change. Nothing in it should be made
false by a commit that adds a module, a tool or a test.

## What done means

A change is done when:

1. `make check` passes;
2. it has been exercised the way a user reaches it: in the TUI, or in a browser
   through `aegis web`, attached to a daemon started after the change, or with
   `aegis bench` for any claim about speed;
3. a user-visible change has a `changelog.d/` fragment, and a new command, driver, tool or
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
| `changelog.d/*.md` | one release note per change, awaiting the next release | any user-visible change |
| `CHANGELOG.md` | what shipped | a release collates the fragments |
| GitHub issues | what is still to do: every defect, feature and idea; the `v1.0` label marks the 1.0 scope, `idea` a direction not yet decided | work is found, decided or lands |
| `know-how/` | how to do one job | a procedure changes |
| `Makefile`, `.rift.yaml`, tests | every mechanical check | a gate is added or dropped |
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

Two gates are not what they look like:

- **`typecheck` is advisory.** `ty` is 0.0.29, has never been configured here,
  and reports a few hundred diagnostics on code that works. It runs, its count
  is printed, and it does not fail `make check` — because a stage that always
  fails gates nothing, and this one used to abort the run *before the tests*,
  so the gate had never once executed the suite. Drive the number down with
  `make typecheck` and promote it to blocking when the list is empty. Issue #8.
- **`lint-docs` is the one gate CI cannot run.** `rift` is private and not on
  PyPI, so a runner cannot install it. Run it locally before opening the PR.

Release notes are **fragments**, not edits to `CHANGELOG.md`: drop a file in
`changelog.d/` named `<slug>.<category>.md` and a release collates them. That
file was the only one in the repo that conflicted on a merge, and it conflicted
on every one. `changelog.d/README.md` has the format; `make changelog-check`
blocks, because it is a filename parse with no backlog rather than a 235-item
one. Issue #10.

CI runs `format-check`, `lint`, `changelog-check`, the suite, and `typecheck`
non-blocking. It does not run `make check` itself: that target's `format` stage
rewrites files, and a runner has to fail on drift rather than fix it.

Commits follow the workspace convention: conventional commits, English, one logical
change. This is a shared checkout, so stage and commit named paths only
(`git commit -- <paths>`) and never amend.
