# F3 sidebar — REPOS section

**Status:** implemented 2026-08-10; plan at
`docs/superpowers/plans/2026-08-10-aegis-sidebar-repos-section.md`
(one change during implementation — see *The mark*, below).
Extended 2026-08-31 with session line churn — see *Session churn*.
**Scope:** TUI only. The web client is explicitly out of scope, same call
the F3 sidebar itself made.
**Extends** `docs/superpowers/specs/2026-08-07-aegis-f3-side-dashboard-design.md`.

## The problem

aegis runs many agents over one checkout, and nothing on screen says where
they are standing. A pane tells you what *this* agent is doing (`SESSION`,
`CONTEXT`, `PLAN`), and the queues and monitors tell you what the substrate
is doing, but the question a multi-agent harness actually raises — *which
repos are being written to right now, on what branch, and is more than one
agent in there* — has no surface at all.

The workspace makes this sharper than it would be elsewhere. `repos/*` are
independent git repos nested inside a git-tracked workspace, each with its
own branch and index, and the standing rule is to `cd` into a repo before
committing precisely because the shell's cwd lies. Two agents writing to
the same repo is the collision `bin/ws-lock` and `src/aegis/locks/` both
exist to prevent, and today you find out at `git diff`, hours later.

There is a second, quieter failure. An agent that writes seven files into a
repo and then moves on leaves them uncommitted, and nothing surfaces that
until someone happens to run `git status` in the right directory. The same
goes for commits that were never pushed — a VPS job that clones `origin`
silently gets the old tree.

## The shape

One more section in the `F3` column, between `MONITORS` and `SYSTEM`:

```
REPOS                              2
● aegis        main ~6 ↑6 +412 -88  calm-hopper
● Workspace    main ~2 +31 -4
· warden       feat/kv ~3 +7  +1
· enciclopedia (detached) ~1
```

| element | meaning |
|---|---|
| heading counter | repos on the list |
| `●` | this pane's agent is one of the writers |
| `·` | only peers have written here |
| `●` in amber | more than one live writer — the collision |
| `~n` | dirty files |
| `+n` / `-n` | lines added / deleted by this session, across commits |
| `↑n` / `↓n` | commits ahead of / behind upstream |
| `(detached)` / `(rebase)` | in the branch cell, in the error colour |

### The mark

**Changed during implementation.** The draft gave a detached or mid-rebase
repo a third mark, `!`, in the column that otherwise carries `●`/`·`. That
column answers *is anyone here*, and spending it on *what state is the repo
in* answers a different question at the cost of the first — a repo you are
writing to right now would stop saying so at exactly the moment it went
detached.

Both facts fit without competing: the mark stays `●`/`·`, and the alarming
state goes in the **branch cell**, in `palette.err`, *replacing* the branch
rather than annotating it. On a repo mid-rebase the branch name is the least
true thing that could be printed there, so nothing is lost by the swap. The
draft's mock also carried both `!` and `(detached)` on the same row, which
was the redundancy that gave this away.

Sorted **most-recently-written first**. The list is short (two to five rows
in practice), so recency beats alphabetical: the repo you just wrote to is
the one you are about to ask about.

The section is app-wide but rendered per pane, which is what makes the
`●`/`·` distinction do any work. Both precedents already exist in this
column — `SESSION`/`CONTEXT`/`PLAN` are the pane, `QUEUES`/`MONITORS`/
`SYSTEM` are the app — and `REPOS` is app-wide data with a per-pane mark.

### Section placement

Between `MONITORS` and `SYSTEM`. The column is ordered by volatility, and
repo state sits where it belongs on that axis: dirty counts move whenever an
agent writes, but a row demands less action than a monitor that just failed,
and far more than a version string that never changes.

### Row tiers

`fit_rows` picks, per row, the widest tier that fits the column:

1. `● aegis  main  ~7 ↑2  +412 -88  · calm-hopper`
2. `● aegis  main  ~7 ↑2  +412 -88  +1`
3. `● aegis  main  ~7 ↑2  +412 -88`
4. `● aegis  main  +412 -88`
5. `● aegis  main`
6. `● aegis` — repo name truncated to fit

Churn outlives `~n ↑n` on the way down (tier 4). Those describe a moment;
the churn describes the whole session, and when the column can only afford
one of them it is the one worth the cells.

**Tier 5 must always fit**, which is why it truncates rather than relying on
the budget. `fit_rows` answers "no tier fits" by omitting the segment
entirely — the trap the F3 spec already paid for, where charging a row for
the frame's padding made a whole *section* disappear at 80 columns. Here the
same mechanism would make a repo silently vanish because its name was long,
which is worse: a missing section reads as "nothing to report", and so does
a missing row.

## Session churn

`~n` answers *how much is uncommitted right now*, and goes to zero every
time an agent commits. On a session that commits as it goes — the normal
case here — the row therefore reads as though nothing happened. `+n -n`
answers the other question: **how much has this session written in this
repo**, commits included.

### The baseline

Churn is measured from a **baseline** captured once, at the session's first
write to that repo, and never re-captured — a moving anchor would silently
discard work already done. The baseline is three things:

- the `HEAD` sha, which is what `git diff <sha>` measures against and is why
  the number spans commits rather than resetting on each one;
- the tracked line counts already dirty at that moment;
- the set of untracked paths already present.

The last two are **subtracted back out**, so a checkout that was dirty
before the session started does not read as the session's doing. Without
them the number would inherit whatever another agent left behind.

Capture happens on the write *event*, which the harness emits before the
tool runs — so the first file's changes land on the session's side of the
line rather than inside the baseline. It is synchronous (three git calls,
measured at 43 ms on `Workspace` and 25 ms on `repos/aegis`) and runs once
per repo per session.

### Untracked files are counted by hand

`git diff` cannot see an untracked file, and a session of brand-new
uncommitted files is exactly the case the section was built for. So the
probe lists them with `git ls-files --others --exclude-standard` — not the
`?` lines of the status it already ran, which collapse an untracked
*directory* into one entry and would score a whole new package as zero
lines — and counts the newlines of the ones the baseline did not have.
Anything binary (a NUL byte) or over 1 MB counts zero: nothing an agent
wrote by hand is that big, and reading a dataset to count its newlines
would hang the probe for the file the number should ignore.

### What it costs, and where it lies

Two more subprocesses per repo per probe tick (`git diff --numstat <sha>`,
`git ls-files --others`) on top of the one `git status`, both inside the
same off-thread refresh and both degrading to zero on any failure. Measured
end to end: 76 ms for `Workspace`, 26 ms for `repos/aegis`.

The number is a summary, not an audit. Three things it does not model:

- **Reverting someone else's uncommitted work** drives the subtraction
  negative; it clamps at zero rather than printing `-3 lines added`.
- **A `Bash` heredoc** is invisible for membership (see below) but *is*
  counted once the repo is on the list some other way — churn reads the
  tree, not the tool calls.
- **A rewritten line** counts as one added and one deleted, the way `git
  diff` counts it.

## Membership: what enters the list, and what leaves

**Writes promote, reads do not.** A repo enters when an agent runs `Write`,
`Edit`, or `NotebookEdit` (or an ACP `write_text_file`) on a path inside it.
Reads and greps do not promote — otherwise every repo an agent searched
appears, and the section stops meaning *work is happening here*.

**A repo stays for the life of the session.** No time decay: a repo you
wrote to an hour ago is still a repo you are responsible for, and the
uncommitted-work case is precisely the one where the agent has moved on.
When a session closes its attribution drops, and a repo with no live writer
left disappears. The tracker is in-memory; a restart clears it, same as
`QueueDigest`.

**Bash writes are missed, deliberately.** Statically parsing a shell command
for write targets is the guess the mandatory-claims spec already declined to
dress up as complete (`2026-08-07-aegis-mandatory-file-claims-design.md`,
"the Bash rule inverts"). A `sed -i` will not register its repo. Under-
reporting is the right failure here: a row that appeared because a heuristic
misread a `>` inside a quoted string would make the whole section untrusted.

## Repo identity

The **repo root path** — the nearest ancestor containing `.git` — is the
identity; the basename is what gets displayed. This resolves the workspace's
nesting correctly with no special cases: `repos/aegis/src/...` → `aegis`,
`vault/Calendar/...` → `Workspace`.

Two checkouts sharing a basename (a `git worktree`) are distinct rows,
disambiguated by parent directory. A `.git` **file** rather than a directory
(worktree, submodule) is resolved through its `gitdir:` pointer.

A path with no `.git` ancestor produces no row at all, which is what keeps
writes to `/tmp` and `~/.cache` off the board.

## Components

New package `src/aegis/repos/`, in the shape `src/aegis/plan/` already uses
(models + tracker + pure renderer):

### `models.py`

`RepoState` — root path, branch, ahead, behind, dirty count, session
`added`/`deleted` lines, in-progress flag, `stale: bool`. `RepoView` — the render row: a `RepoState` plus the set
of live writer handles.

### `tracker.py` — `RepoTracker`

App-owned, one instance, `subscribe(cb)` in the shape `MonitorManager`
already uses so the pane's `on_mount` has somewhere familiar to hang.

- `record(handle, path)` — resolve to repo root, capture the baseline on
  first sight, add the handle, bump recency.
- `drop(handle)` — on session close; removes the row when it was the last writer.
- `snapshot(for_handle)` — `RepoView`s with the mark resolved for that pane.

### `probe.py`

One `git status --porcelain=v2 --branch` per repo returns branch, upstream,
ahead/behind, and the dirty list in a **single** subprocess, so the design
question is never "which fields" but "do we shell out at all."

Two more calls follow when the repo has a baseline — `git diff --numstat
<sha>` and `git ls-files --others` — for the session churn. See *Session
churn* for why they are worth a second and third subprocess.

Off the UI thread, into a cache with a ~5s TTL. `git status` over the
workspace tree (vault plus thousands of files) can take a couple of hundred
milliseconds and a paint must never wait on it.

### `render.py`

`render_repos(views, palette, width) -> Text | None`. Pure, no Textual
import, tested as a plain function — the property that makes `fit.py` and
`plan/render.py` trustworthy. `None` when the list is empty, which is how
every section in this column stays honest.

### `tui/sidebar.py`

A `repos: list[RepoView]` field on `SidebarModel` and a `_repos()` section
composer, calling `render_repos` the way `_plan()` calls `render_plan_dock`
— trimming at the composition site rather than in the renderer, so the
renderer keeps its own contract in its own test.

## Data flow

The TUI has no unified `SessionManager` — panes construct `AgentSession`
directly (`tui/pane.py:841`). So the recording hook goes **inside
`AgentSession`**, where `PlanTracker` already lives: on a `ToolUse` for a
write tool, call `tracker.record(self.handle, file_path)`. The app passes
its one `RepoTracker` at construction.

That placement earns headless and queue-worker sessions for free, without a
second observer chain, and it is the same seam that made plan state
first-class session state rather than a TUI concern.

`pane.py` subscribes the tracker in `on_mount` alongside `_digest` and
`_monitor_manager`, and releases it in `on_unmount`. Both of those managers
outlive any one pane and a leaked handle is a cost this file has already
paid once.

**A hidden sidebar no-ops**, and no probe runs at all while it is closed.
The closed mode costs one branch per event, not a second render tree — the
discipline `PlanDock.refresh_plan` and `PlanStrip._tick` already use.

## Error handling

Every case below degrades rather than lies:

| case | behaviour |
|---|---|
| no `.git` ancestor | no row |
| `git` not on `PATH` | branch only, read from `.git/HEAD`; no counts, no flag |
| baseline uncapturable (no commits, `git` absent, capture raised) | row stands, churn reads zero |
| baseline sha no longer reachable (reset, rewritten history) | churn reads zero; the rest of the row still lands |
| probe times out (3s) or exits non-zero | keep last known values, render dim, `stale: true` |
| first paint after a repo enters | branch from `.git/HEAD` (free); counts fill in next tick |
| remote-host session | row reads `warden@vps`; no branch, no counts, never probed |
| sidebar closed | no probe at all |

The remote-host rule is not a nicety. A path from a `vps` session names a
file on that machine; running `git status` against the identically-named
local path returns a **silently wrong answer** rather than an error, which is
exactly the reasoning `Claim.host` and `render_shared.file_target` already
encode.

## Testing

- **`render_repos`** — pure. Tier selection, the `●`/`·` mark, the amber
  multi-writer row, the empty-list `None`, the tier-5 truncation of a long
  repo name, 26 versus 60 columns. Plain-text assertions via the existing
  `strip_markup`.
- **`RepoTracker`** — fake `ToolUse` events in, membership and attribution
  out. `drop` removes the handle, and removes the row when it was the last
  writer. Recency ordering.
- **`probe`** against a **real temporary git repo** — `init`, commit, dirty a
  file, add a local upstream and commit ahead of it, detach `HEAD`. Not
  mocked. A mocked probe asserts my model of `--porcelain=v2`, which is the
  part most likely to be wrong, and would stay green while the real parse
  broke.
- **The section in an open sidebar** — assert the row is *actually rendered*
  for a real temp repo, and that the section is absent when nothing has been
  written. Mutation-check the absent case: break it and confirm the test goes
  red. This is the one that matters; the pure functions will be right.

## Deliberately out of scope

- **The web client renders no `REPOS`** — same call as the sidebar itself and
  the live task list, recorded as debt in `TASKS.md` rather than quietly
  dropped.
- **Bash write detection**, per the membership rule above.
- **Acting on a row.** No click-to-commit, no click-to-open. A dashboard
  section that reports is a smaller thing than one that mutates a repo, and
  the second is a different feature.
