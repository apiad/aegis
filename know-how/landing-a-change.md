---
when: starting any non-trivial change here, deciding where to branch from, reading a red CI run, or merging a PR — the issue → worktree → PR loop and the traps in each step
---

# Landing a change (issue → worktree → PR → green CI)

The contract is in AGENTS.md under *How work lands here*. This is the
mechanics, and the traps each step has already cost someone.

## 1. The issue carries evidence, not a plan

Reproduce it, measure it, then write it. `/spawn` was reported as "loses
context"; the issue that came out of it carried *30 of 71 real preambles
carried no `user:` line*, because the corpus under
`.aegis/state/sessions/` was there to count. A number survives the branch
being deleted; an impression does not.

The logs are plain JSONL and replaying them is cheap:

```bash
python3 - <<'PY'
import json, glob
for f in glob.glob(".aegis/state/sessions/*.jsonl"):
    for line in open(f, errors="replace"):
        ev = json.loads(line).get("event", {})
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
- A clean tree across a big rename. A rebase across the stage-6 web
  deletion leaves `src/aegis/web/__pycache__/` behind, and a bare
  directory is a package, so `test_no_old_web_layer.py` fails until you
  `rm -rf src/aegis/web`.

## 3. Write the release note as a fragment, not an edit

A user-visible change adds a file, never a line to `CHANGELOG.md`:

```bash
cat > changelog.d/42-the-thing.fixed.md <<'EOF'
- **The thing no longer does the wrong thing.** What was wrong, and what it cost.
EOF
make changelog-check    # the filename parses
make changelog          # preview the collated result; writes nothing
```

`CHANGELOG.md` was the only file in this repo that conflicted on a merge, and it
conflicted on every one — three PRs in one afternoon, three conflicts, nothing
else in the tree colliding once. A fragment is its own file, so two PRs never
touch the same bytes. Categories and house style are in
`changelog.d/README.md`; an unknown category is refused by name rather than
skipped, because a dropped fragment is a missing release note.

Do not edit `CHANGELOG.md` directly. The releaser collates.

## 4. Run the gates before pushing

```bash
make test          # the fast lane
make check         # every gate
rift check         # CI cannot run this one — rift is private, not on PyPI
```

`make test` green is not the same as CI green. Reproduce the runner:

```bash
GITHUB_ACTIONS=true uv run pytest -q -m "not live"
```

`typer.rich_utils` sets `FORCE_TERMINAL = True` when `GITHUB_ACTIONS` is
set, so `--help` renders with ANSI escapes on a runner and nowhere else.
That alone kept the `ci` workflow red while every laptop was green (#7).
`FORCE_COLOR=1` is *not* the same switch — it also colours the
application's own Rich consoles, and it breaks nine tests instead of two.

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
