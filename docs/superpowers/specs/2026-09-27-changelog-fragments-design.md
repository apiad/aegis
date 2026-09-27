# Release notes as fragments

**Status:** implemented (2026-09-27)
**Spec:** `docs/superpowers/specs/2026-09-27-changelog-fragments-design.md`
**Issue:** #10

## The gap

`CHANGELOG.md` was the only file in the repository that conflicted on a merge,
and it conflicted on every one. Three PRs landed on 2026-09-27 — #6, #7, #9 —
and `git merge-tree` reported exactly one conflicting path each time:

```
CONFLICT (content): Merge conflict in CHANGELOG.md
```

Nothing else collided. Not `queue/manager.py`, which two of the three edited.
Not `tui/app.py`. Not the Makefile, not the workflow file.

It is structural rather than bad luck. Every change appends to the top of the
same `### Changed` or `### Fixed` list under `## [Unreleased]`, so two agents
working in parallel write adjacent lines inside one hunk, and adjacent lines in
one hunk is the single thing a three-way merge cannot resolve. With several
agents working at once — which is what this repo is for — that is a rebase per
PR, forever.

There was a second failure in the same file, hit the same afternoon. Inserting
an entry at "the first `### Fixed`" produced a **second** `### Changed` section
under one release, because that release already had one further down. Placing a
single bullet correctly requires reading the whole release section, and a wrong
placement is invisible in review.

## The change

A change adds a **file**, not a line:

```
changelog.d/
  10-changelog-fragments.changed.md
  8-make-check-runs-the-tests.fixed.md
```

`<slug>.<category>.md`. Two PRs never touch the same bytes, so git has nothing
to merge. The category is in the filename, so placement is not a judgement and
a duplicate heading is not expressible.

`python -m aegis.changelog` has three verbs, matching the three moments it gets
used: `check` (every fragment parses), `preview` (collated output to stdout,
writes nothing), `apply --version x.y.z` (writes `CHANGELOG.md`, deletes the
fragments). Only `apply` writes, and only a releaser runs it.

## Decisions

**No `towncrier`.** Collation is a group-by and a concatenation — about 120
lines including the docstrings that explain why each rule exists. This repo
already prefers a script it can read to a dependency it cannot, and the
`uv.lock` gate has failed a release twice over dependency drift
(`know-how/releasing.md`), so a dependency that buys a group-by is a bad trade.

**An unknown category is refused, never skipped.** A silently-dropped fragment
is a change missing from the release notes, which is the one failure the
mechanism exists to prevent. `FragmentError` names the offending file and lists
the real categories.

**`changelog-check` blocks; `typecheck` does not.** The two advisory/blocking
decisions landed a day apart and look inconsistent, so: typecheck is advisory
because it has a 235-item backlog and an alpha checker (#8). The fragment check
is a filename parse over a handful of files with no backlog and no false
positives, so it blocks from the start. A gate is advisory when its list is
long, not as a matter of taste.

**Existing entries are never moved.** Fragments govern changes from here on.
The collator appends inside a section that already exists and slots a new
section into category order *around* the ones already there. Whatever was
hand-written above stays where its author put it, and collating an empty
directory returns the file byte for byte, so running it twice is not a diff.

## Where it does not apply

- **The `## [Unreleased]` section as it stands today.** It is not rewritten. The
  first release after this collates fragments alongside what is already there.
- **Releases.** `apply --version` is a deliberate, manual step in the release
  procedure, not something CI does. Automating a commit into `CHANGELOG.md` from
  a workflow buys little and adds a way for a release to rewrite history.

## Implementation

- `aegis.changelog` — `parse_fragment_name`, `read_fragments`, `collate`. Pure:
  text and a directory in, text out, which is what makes the ordering rules
  testable without a git repository.
- `aegis.changelog.__main__` — the three verbs.
- `make changelog-check` in `make check` and in CI; `make changelog` previews.
- `tests/test_changelog_fragments.py` — 17 tests. Two of them pin the failures
  that motivated this: a second `### Changed` under one release, and a new
  section landing out of category order beside an existing one.
