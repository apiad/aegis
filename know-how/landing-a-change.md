---
when: starting any non-trivial change here, deciding where to branch from, reading a red CI run, or merging a PR — the issue → worktree → PR loop and the traps in each step
---

# Landing a change (issue → worktree → PR → green CI)

The contract is in AGENTS.md under *How work lands here*. This is the
mechanics, and the traps each step has already cost someone.

## 1. The issue carries evidence, not a plan

Reproduce it, measure it, then write it. The daily-driver slice (#129) chose
its tools from 2,588 real tool calls in the legacy tree's comms ledger, not from
a guess at what agents use. A number survives the branch being deleted; an
impression does not.

The stores are plain JSONL, one raw Claude line or aegis record per line, and
folding them is cheap:

```bash
uv run python - <<'PY'
from pathlib import Path
from aegis.transcript.store import read_store
from aegis.transcript.entries import fold_records
for p in Path(".aegis/state/transcripts").glob("*.jsonl"):
    entries = fold_records(read_store(p)[0]).entries()
    ...
PY
```

## 2. Branch from `origin/main`, in a worktree

```bash
git fetch origin
git worktree add .claude/worktrees/<slug> -b <branch> origin/main
```

Claude Code's `EnterWorktree` does the same thing and cleans up after
itself; prefer it when you have it.

**Never branch from another PR's branch.** Merging the base deletes it and
closes the dependent PR with it.

**Never `git stash` here.** The stash stack is shared with the main
checkout and every other worktree, so a bare `git stash pop` can take a
peer's work. Use a throwaway commit instead.

Two things a fresh worktree does not give you:

- `uv sync` — run it, or you are testing against whatever was there.
- A clean tree across a big rename. A rebase across a moved package leaves
  its `__pycache__/` behind, and a bare directory is a package, so an
  import that should fail succeeds until you delete it.
- Chromium for the browser tests: `uv run playwright install chromium`.

## 3. Write the release note as a fragment, not an edit

A user-visible change adds a file, never a line to `CHANGELOG.md`:

```bash
cat > changelog.d/42-the-thing.fixed.md <<'EOF'
- **The thing no longer does the wrong thing.** What was wrong, and what it cost.
EOF
make changelog-check    # the filename parses
make changelog          # preview the collated result; writes nothing
```

An `added` or `changed` fragment ends with `help: page` or
`help: page#section`, naming the help page that shows the feature;
`make changelog-check` refuses one without it. Write or update that page in
the same PR (`changelog.d/README.md`).

`CHANGELOG.md` was the only file in this repo that conflicted on a merge, and it
conflicted on every one — three PRs in one afternoon, three conflicts, nothing
else in the tree colliding once. A fragment is its own file, so two PRs never
touch the same bytes. Categories and house style are in
`changelog.d/README.md`; an unknown category is refused by name rather than
skipped, because a dropped fragment is a missing release note.

Do not edit `CHANGELOG.md` directly. The releaser collates.

## 4. Run what covers the change, then push

Run only the tests that cover the change, and `make lint-docs` if a doc or
help page changed:

```bash
uv run pytest -q tests/test_<what_you_touched>.py   # and its neighbours
make lint-docs     # rift, the help pages among its rules; CI runs it too
```

Do not run `make check`, `make test-slow` or `make bench` for a PR: CI runs
the whole hermetic suite on every PR, slow and browser tests included, and
compares the bench of base and head. Those three run locally once, before a
release (`releasing.md`). On a host where other agents run suites too, the
whole slow lane takes about 30 min and its browser tests fail from load
alone; CI runs it in four shards in a fraction of that.

`make gates` runs the checks that take seconds: format, lint, typecheck,
the changelog fragments and rift. `make hooks` makes it a pre-push hook.
Run that once per clone, because the setting lives in the shared
`.git/config` and covers every worktree. In #506, 18 of 40 red CI runs had
failed on one of these checks, after a wait of up to half an hour.

**Push when the PR is ready to be judged, not after every commit.** Each
push to an open PR cancels its running CI and starts the whole workflow
again. Commit as often as you like, push when you open the PR, and push once
per round of review fixes. In #506, a merged PR cost about 9 CI runs.

### What CI runs, and how long it should take

`ci.yml` has four jobs: `gates`, the `fast` lane with coverage in its
summary, `multiuser`, and `bench` on PRs. Until the fair release (#768) the
`slow` shards run in `nightly.yml`, every night and by hand
(`gh workflow run nightly.yml`), and no merge waits on them. A test that
fails on a PR for a reason the PR did not touch is read in full first (#770
was a fixed date, not a flake); if it is a flake, mark it
`@pytest.mark.quarantine("#N")` with its issue and add it to #776, rather than
re-running until it passes. Each job's
`timeout-minutes` comment states how long the job should take. A job that
keeps running past that time is a regression in the suite. Open an issue
with the slowest tests (`--durations=20`) instead of raising the timeout. #353
and #475 each raised it once, while the suite doubled.

The shards balance on `.test_durations`. A slow test that is missing from
it counts as an average one. When one shard keeps finishing minutes after
the others, refresh the file with `make test-durations` and commit it.

A local green is not the same as CI green. To reproduce a CI failure, run the
runner's way:

```bash
GITHUB_ACTIONS=true uv run pytest -q -m "not live"
```

`typer.rich_utils` sets `FORCE_TERMINAL = True` when `GITHUB_ACTIONS` is
set, so `--help` renders with ANSI escapes on a runner and nowhere else.
That kept the `ci` workflow red while every laptop was green (#7).

A runner is also slower than zion, which widens races a laptop never sees:
#128's browser test failed only on CI because a tab switch rendered a moment
after the URL changed. When a test fails only there, capture both ends (the
page's websocket frames, `aegis serve --log-level info`) before guessing.

Never read a gate's exit code through a pipe. `make test | tail` gives you
`tail`'s status, which turns a red gate green.

## 5. A red CI run is not automatically yours

Check whether `main` fails the same way before you believe it:

```bash
gh run view <pr-run-id>   --log-failed | grep -oE "^FAILED tests/[^ ]+" | sort -u > /tmp/pr.txt
gh run view <main-run-id> --log-failed | grep -oE "^FAILED tests/[^ ]+" | sort -u > /tmp/main.txt
diff /tmp/main.txt /tmp/pr.txt
```

If a failure is only on your branch, prove whether your change can even
reach it before assuming a flake. Wrap every function you touched in a
call counter and run that one test:

```python
hits = {}
def count(mod, name):
    orig = getattr(mod, name); hits[name] = 0
    def wrapper(*a, **k):
        hits[name] += 1
        return orig(*a, **k)
    setattr(mod, name, wrapper)
```

Zero calls means the failure is not yours, and that is worth a line in the
PR so nobody re-investigates it.

**A flake gets an issue the same day, not a silent re-run.** A test that
failed and then passed on the same commit is a flake. Search the issues for
its name, then open one or comment on the open one. Name the test, the run
and its log line before you re-run. In #506, 26 commits flipped from red to
green with no change, and the three flakes found while measuring had no
issue. A re-run without a record hides the race until it fails every run.

## 6. Merge, then confirm it merged

```bash
gh pr merge <n> --rebase --delete-branch
```

Run from inside a worktree this prints:

```
failed to run git: fatal: 'main' is already used by worktree at ...
```

**after the merge has already succeeded on GitHub.** It is `gh`'s local
post-merge checkout tripping on the multi-worktree layout, not a failed
merge. Confirm the truth rather than the error:

```bash
gh pr view <n> --json state,mergeCommit --jq '{state, oid: .mergeCommit.oid}'
```

Then `git fetch origin --prune` and remove the worktree.

## The PR body

Carry what a reviewer cannot re-derive: what was measured, what was tried
and rejected, and what was deliberately left out of scope. Record the
wrong guesses you eliminated — "it is not terminal width, the tests pass
at `COLUMNS=80`" saves the next agent the same hour.
