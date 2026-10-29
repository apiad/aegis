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

### Do the lock edit surgically when only the version changes

For a version-only bump, edit just the self-version line rather than re-locking
everything (an older uv on zion once rewrote the whole file; uv 0.11 does not,
and a dependency change needs `uv lock` anyway):

```toml
[[package]]
name = "aegis-harness"
version = "0.18.0"   # <-- match the new pyproject version
source = { editable = "." }
```

Then confirm the gate is satisfied — this must print nothing about needing
an update:

```bash
uv sync --inexact --locked --group dev
```

`--inexact` verifies the lock without uninstalling what it does not mention;
a bare `uv sync` strips everything outside the locked default set.

## Before tagging: read the benchmark

Every PR's CI benches its base and head (`scripts/bench.py`) and warns on a
metric more than 20% worse whose samples are worse than base's in 90% of
their pairs. Before tagging, read the warnings of the PRs in the
range (`gh pr checks <n>`, the `bench` job's summary), and either explain each
in the changelog or fix it. On a quiet zion, `make bench` gives the numbers for
the release notes.

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

Same sweep for the docs: grep `README.md` for each new command, config key and
MCP tool. At v0.29.0 five features had shipped with only AGENTS.md entries and
no user-facing doc.

## Release checklist

1. Clean tree, on `main`, not behind origin.
2. Collate the changelog fragments into a release section — **after** running
   the coverage diff above, and after closing any doc gaps it exposes:

   ```bash
   uv run python scripts/changelog.py apply --version X.Y.Z   # --date defaults to today, UTC
   git add CHANGELOG.md changelog.d
   ```

   This writes the new `## [X.Y.Z] - YYYY-MM-DD` section below `## [Unreleased]`
   and deletes the fragments it consumed, so the deletions are part of the
   release commit. Preview first with `make changelog` — it writes nothing.

   Anything still sitting under `## [Unreleased]` by hand (entries written
   before fragments landed, #10) is not moved; fold it into the release section
   yourself if it belongs there.
3. Refresh the model scores. With an Artificial Analysis key (a free one is
   enough; one run spends one request of its daily allowance), on a machine
   where both Claude Code and OpenCode are installed:

   ```bash
   AA_API_KEY=... uv run scripts/refresh-model-ranking
   git diff src/aegis/data/models.json
   ```

   Read the diff and the `unmapped:` lines it printed. A catalog model that
   should be ranked and is not needs an entry in the script's `ALIASES`; run
   it again. Commit the file as `chore(ranking): refresh model scores`.
4. Review the landing film against the release (*Keeping the landing film
   current*, below). Commit a re-render, or file an issue for one, before
   the tag; a release does not ship a film of a client it changed.
5. Bump `version` in `pyproject.toml`.
6. **Bump the `aegis-harness` version line in `uv.lock`** (surgical edit),
   then verify with `uv sync --inexact --locked --group dev`.
7. Read the last `nightly.yml` run on the commit you are about to tag (run
   it by hand if it is older): its slow shards must be green, and its
   quarantine summary names any flaky test to read before tagging (#776).
   Run the canary on that commit:
   `gh workflow run canary.yml --ref main`, then read
   `gh issue list --label canary`. An open canary issue is a harness that
   does not work under this release; fix it or name it in the release notes.
   Then move the tested harness range (*Moving the tested harness range*,
   below) and run the canary at its ends.
   Run the full gates locally, once, here and not per PR: `make check`,
   `make test-slow` and `make bench`. A red run is a regression; do not
   re-roll it.
8. Commit `chore(release): vX.Y.Z`, push `main`.
9. `git tag -a vX.Y.Z -m "Release vX.Y.Z"` and `git push origin vX.Y.Z`.
10. Watch the run: `gh run watch $(gh run list --workflow=release.yml --limit 1 --json databaseId --jq '.[0].databaseId') --exit-status`.
11. `gh release create vX.Y.Z --generate-notes --title vX.Y.Z`.
12. Verify PyPI by **installing it**, not by asking the JSON API:

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

## Keeping the landing film current

The film on the landing page (`site/video/aegis.mp4`) is recorded from the
real client by `scripts/demo/director.py`, and its script is
`scripts/demo/beats/film.py`: the narration in `L`, the beats that drive the
client below it. At every release, read the script beside the release's
changelog section and answer two questions.

- **Does the script still tell the truth, and the whole of it?** A feature
  the release adds that a new user would choose aegis for (a new rung, a new
  way to work together, a new view) gets a line and a shot. A line that
  names something the release renamed or removed changes with it.
- **Does the film still show this client?** Open the film beside an
  `aegis serve` of the release and compare the shots: the tab bar, the
  Fleet cards, the composer, the side panels, the theme. A change a viewer
  would notice (a moved control, a new panel, a restyle) is a re-render.
  A change only a maintainer would see is not.

Either answer yes means a re-render, in the release or in an issue filed
before the tag. To render:

```bash
uv run python scripts/demo/director.py film --out ~/.cache/aegis-film/out \
    --audio ~/.cache/aegis-film/audio --tts '<a command that speaks {yaml} into {out}>'
ffmpeg -y -i ~/.cache/aegis-film/out/film.mp4 -c:v libx264 -preset slow -crf 30 \
    -pix_fmt yuv420p -c:a aac -b:a 96k -movflags +faststart site/video/aegis.mp4
cp ~/.cache/aegis-film/out/film.vtt site/video/aegis.vtt
ffmpeg -y -ss 2.2 -i site/video/aegis.mp4 -frames:v 1 -q:v 3 site/video/aegis-poster.jpg
```

A line whose clip is not in `--audio` is spoken with `--tts`; without it, the
film is captioned but silent there. The render takes about ten minutes and
holds ffmpeg for all of them: on a host short of memory an OOM killer picks
it first, and the run ends in `BrokenPipeError`. Keep `--out` off a tmpfs
`/tmp`, which spends the same memory. The site encode keeps the film near
17 MB. Watch the whole render before
committing it: a beat that waited on a selector the release renamed fails
the run, but a shot of the wrong element does not.

## Moving the tested harness range

`src/aegis/data/tested-harnesses.toml` names, per harness, the min and max
versions this release was tested with; a person on a version outside them is
asked before a session starts. A range moves only on canary evidence:

1. The latest run's `versions.txt` (the `canary-report-latest` artifact, or
   `scripts/canary.sh` locally) marks each harness `in_range` or `newer`. A
   `newer` one whose scenarios all passed may become the new `max`; one with
   a failure stays out, and its issue goes in the release notes.
2. Edit `max` (and `min`, when aegis drops an old version) in the toml.
3. Run the canary at both ends, on the branch with the edit, and read each
   run green: `gh workflow run canary.yml --ref <branch> -f versions=max`,
   then `-f versions=min`; locally, `AEGIS_CANARY_VERSIONS=max scripts/canary.sh`.
   Those runs open no issues: a failure at an end means the range is wrong.
   Dispatch them one at a time: the workflow's `concurrency: canary` group
   keeps one pending run and cancels the older, which at 2.13.0 cancelled
   the `min` run queued behind `latest`.

The canary's own server turns the gate off and tests whatever it installed,
so a green nightly run on a newer version is evidence for a move, not a move.

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
