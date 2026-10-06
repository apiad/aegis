# Changelog fragments

One file per change. `CHANGELOG.md` is written at release time by collating
these; do not edit its `## [Unreleased]` section by hand any more.

## Why

`CHANGELOG.md` was the only file that conflicted on every PR. Every change
appended to the top of the same list, so two agents working in parallel wrote
adjacent lines in one hunk — the one thing git cannot merge. Three PRs in one
afternoon, three conflicts, and nothing else in the tree collided once.

A fragment is its own file, so two PRs never touch the same bytes.

## Writing one

Name it `<slug>.<category>.md`. The slug is free-form and never rendered —
by convention the issue number and a few words, so the directory reads as a
list of what is landing:

```
10-changelog-fragments.changed.md
8-make-check-runs-the-tests.fixed.md
```

Categories: `added`, `changed`, `deprecated`, `removed`, `fixed`,
`performance`, `security`. Anything else is refused by name rather than
skipped, because a fragment that is silently dropped is an entry missing from
the release notes, which is the failure this exists to prevent.

The body is the bullet exactly as it should appear, in this project's house
style — bold lead sentence naming the change, then what was wrong and what it
cost:

```markdown
- **A cached module registers no workflow.** Registration is an import side
  effect, so `import_module` on an already-imported module registered nothing
  and `register_builtins` returned as if it had succeeded.
```

Multiple bullets in one file are fine when they are one change. Separate
concerns get separate files.

## Commands

```bash
make changelog-check      # every fragment parses; runs in CI and in make check
make changelog            # preview the collated CHANGELOG, writing nothing
```

At release, the releaser folds them into a version section and deletes them:

```bash
uv run python scripts/changelog.py apply --version X.Y.Z
```

See `know-how/releasing.md`.
