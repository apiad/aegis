- **Release notes are written as fragments, one file per change.** `CHANGELOG.md`
  was the only file that conflicted on every PR: each change appended to the top
  of the same list under `## [Unreleased]`, so two agents working in parallel
  wrote adjacent lines in one hunk, and three PRs in one afternoon produced three
  conflicts while nothing else in the tree collided once. A change now drops a
  file in `changelog.d/` named `<slug>.<category>.md`, so two PRs never touch the
  same bytes, and `python -m aegis.changelog apply --version x.y.z` collates them
  into a release section at release time. The category lives in the filename,
  which also removes the second failure this file had — placing a bullet meant
  reading the whole release section, and inserting at the first matching heading
  had already produced two `### Changed` blocks under one release. An unknown
  category is refused by name rather than skipped, because a fragment nobody
  renders is an entry missing from the release notes.
