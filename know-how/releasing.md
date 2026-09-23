---
when: bumping the version and cutting a release of aegis-harness, before editing pyproject.toml or pushing a vX.Y.Z tag; covers the uv.lock gate that has failed the publish twice
---

# Cutting a release (tag → CI → PyPI)

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
uv sync --inexact --locked --extra voice --group dev --group docs
```

### `uv sync --locked` on its own strips the venv

A bare `uv sync` resolves to *exactly* the locked default set, so it
**uninstalls** everything outside it — at v0.39.0 that was 37 packages,
taking the `voice` extra (`harpio`, `sounddevice`) and the whole `docs`
group with it. The step passes, the gate is satisfied, and `mkdocs` and
push-to-talk are gone until someone notices.

`--inexact` is what stops it: verify the lock without touching anything
the lock does not mention. If you have already run the bare form, put the
venv back with the same flags and check by module name, not distribution
name — `harpio` imports as `harp` and `mkdocs-material` as `material`, so
`import harpio` fails on a perfectly good install and sends you chasing a
problem that is not there.

## Before tagging: record the benchmark

Every release gets a benchmark summary on zion, so `aegis bench history`
shows whether rendering, latency, CPU and memory moved.

1. Make sure the machine is quiet. `aegis bench run` warns when the CPU is
   over 50% busy, and a busy run does not compare. Stop other sessions'
   test suites first.
2. From the release commit, with `src/` clean:

   ```bash
   aegis bench run --save
   aegis bench compare <run-id> --baseline latest-release
   ```

3. `--save` writes `bench/history/zion/<version>-<sha>.json`. Rename it to
   `bench/history/zion/<version>.json`, which is what `latest-release` and
   `history` treat as a release, and commit it with the release.

Read every `regressed` row before tagging, and either explain it in the
changelog or fix it. See `know-how/benchmarking.md`.

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
   then verify with
   `uv sync --inexact --locked --extra voice --group dev --group docs`.
   The bare form passes too, and takes 37 packages with it — see above.
5. Run the fast suite locally: `uv run python -m pytest -q -m "not live"`.
   It is expected to be green: the old "1-2 TUI/watchdog tests flake on
   the inotify limit" caveat was a leak plus two teardown races, fixed in
   0.25.0. A red run is a regression — do not re-roll it.
6. Commit `chore(release): vX.Y.Z`, push `main`.
7. `git tag -a vX.Y.Z -m "Release vX.Y.Z"` and `git push origin vX.Y.Z`.
8. Watch the run: `gh run watch $(gh run list --workflow=release.yml --limit 1 --json databaseId --jq '.[0].databaseId') --exit-status`.
9. `gh release create vX.Y.Z --generate-notes --title vX.Y.Z`.
10. Verify PyPI by **installing it**, not by asking the JSON API:

    ```bash
    uv venv /tmp/pypi-check -q
    uv pip install --python /tmp/pypi-check/bin/python --no-cache aegis-harness==X.Y.Z
    /tmp/pypi-check/bin/aegis --version
    ```

    The three PyPI surfaces disagree for minutes and answer different
    questions. At v0.39.0 the simple index listed both files while
    `/pypi/<name>/json` still said the previous version and
    `/project/<name>/X.Y.Z/` returned 503 — PyPI was on *Partially Degraded
    Service* (`status.python.org`), and a resolver pointed at one CDN node
    still could not see the release. Only the install answers "can a user
    get this". Read its exit code directly: piping it through `tail` and
    then testing `$?` reports `tail`'s status and turns a failed install
    green.

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
