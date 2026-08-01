# Cutting a release (tag → CI → PyPI)

*When to reach for it: bumping the version and cutting a release of
`aegis-harness` — before you edit `pyproject.toml` or push a `vX.Y.Z`
tag. Covers the `uv.lock` gate that has failed the publish twice.*

Releases are **tag-driven**. Pushing a `v*` tag triggers
`.github/workflows/release.yml`, which sanity-checks the tag against
`pyproject.toml`, runs the hermetic suite behind `uv sync --locked`,
builds, and publishes to PyPI via trusted publishing (no stored token).

## The one that bites: `uv.lock` must be re-locked in the same bump

The workflow's test step runs **`uv sync --locked`**. That flag fails the
build if `uv.lock` is even slightly out of date. `uv.lock` pins the
project's *own* version, so **every version bump makes the lock stale** —
if you bump `pyproject.toml` and tag without touching `uv.lock`, the
release run dies at "Run hermetic tests" with:

```
error: The lockfile at `uv.lock` needs to be updated, but `--locked` was provided.
```

This has sunk both the v0.17.0 and v0.18.0 first attempts. **Bump the lock
in the same commit as `pyproject.toml`.**

### Do the lock edit surgically, not with `uv lock`

Running `uv lock` locally on zion rewrites the *entire* file — the local
uv is an older format and strips `upload-time` from every entry, producing
~2400 lines of churn. Instead, edit just the self-version line:

```toml
[[package]]
name = "aegis-harness"
version = "0.18.0"   # <-- match the new pyproject version
source = { editable = "." }
```

Then confirm the gate is satisfied — this must print nothing about needing
an update:

```bash
uv sync --locked
```

## The other one: `[Unreleased]` is routinely a fraction of what shipped

Sessions land features and write the changelog entry *only* for the thing they
were asked about. At v0.29.0 the `[Unreleased]` block held two entries; the tag
range held sixty commits, and `/btw`, `/fork`, the `generate()` seam, the
`pgrep` monitor guard, four perf wins and five user-visible fixes were all
absent. Release notes assembled from that block would have been a lie about
most of the release.

**Diff the commits against the block before you promote it**, never the other
way round:

```bash
git log --oneline vX.Y.Z..HEAD          # what actually shipped
git log vX.Y.Z..HEAD --format='=== %h %s%n%b' -- . ':!docs'   # the material
```

Read the commit *bodies* — this repo writes the reasoning and the measured
numbers there, so the changelog entry is mostly assembly, not authorship.

Two traps while assembling:

- **A reverted commit still shows in the range.** `perf(tui): halve the
  mounted transcript window` was in the log and `revert(tui): put N_MAX back
  to 300` three commits later; documenting the first would have shipped a
  changelog claiming behaviour the release does not have. Check the current
  value in the tree, not the commit that set it.
- **Intra-release fixes are not user-facing fixes.** A bug introduced and
  fixed between two tags never reached anyone. Leave it out of `### Fixed`.

Same sweep for the docs: grep `README.md docs/*.md` for each new command,
config key and MCP verb. At v0.29.0 none of `/btw`, `/fork`, `@peer`,
`text_generation:`, `aegis_fork` or `aegis_read_peer` appeared in any
user-facing doc — the features had shipped with only AGENTS.md entries.

## Release checklist

1. Clean tree, on `main`, not behind origin.
2. Move the CHANGELOG `## [Unreleased]` block to `## [vX.Y.Z] - YYYY-MM-DD`,
   add a fresh empty `## [Unreleased]` above it — **after** running the
   coverage diff above, and after closing any doc gaps it exposes.
3. Bump `version` in `pyproject.toml`.
4. **Bump the `aegis-harness` version line in `uv.lock`** (surgical edit),
   then `uv sync --locked` to verify.
5. Run the fast suite locally: `uv run python -m pytest -q -m "not live"`.
   It is expected to be green: the old "1-2 TUI/watchdog tests flake on
   the inotify limit" caveat was a leak plus two teardown races, fixed in
   0.25.0. A red run is a regression — do not re-roll it.
6. Commit `chore(release): vX.Y.Z`, push `main`.
7. `git tag -a vX.Y.Z -m "Release vX.Y.Z"` and `git push origin vX.Y.Z`.
8. Watch the run: `gh run watch $(gh run list --workflow=release.yml --limit 1 --json databaseId --jq '.[0].databaseId') --exit-status`.
9. `gh release create vX.Y.Z --generate-notes --title vX.Y.Z`.
10. Verify PyPI (index lags ~30s): `curl -s https://pypi.org/pypi/aegis-harness/json | jq -r .info.version`.

## Recovering a failed publish

If the run fails *before* the publish step (e.g. the `uv.lock` gate), nothing
reached PyPI — safe to re-point the tag. Fix the cause, commit, push `main`,
then re-cut the tag cleanly:

```bash
gh release delete vX.Y.Z --yes            # if you already created it
git push origin :refs/tags/vX.Y.Z         # delete remote tag
git tag -d vX.Y.Z && git tag -a vX.Y.Z -m "Release vX.Y.Z"
git push origin vX.Y.Z                     # re-triggers the workflow
```

PyPI does **not** allow re-uploading a version that already published, so if
the publish step itself succeeded you must bump to the next patch — never try
to overwrite an existing PyPI version.
