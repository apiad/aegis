---
when: a plan under docs/superpowers/plans/ is approved and about to be executed — choosing between running it yourself and one queue worker per slice, what a worker's brief says, which tests a worker runs, and when a review agent is worth its cost
---

# Running a plan

A plan here is executed one of two ways. The maintainer picks; this is what
each means.

## Native, for a short and linear plan

When the plan is a handful of tasks that build on each other in a straight
line, the session that wrote it runs it, task by task, marking each
`data-state` as it goes. No workers, no per-task review.

## One queue worker per slice, for anything bigger

A plan with slices gets one aegis queue worker per slice, never one per task.
A task is too small to be worth a fresh session: the worker spends more
context learning the code than the task costs, and the next task's worker
learns it again. A slice is the right unit because it ends in something that
can be tried, which is what a worker can verify on its own.

- **Enqueue the slices one at a time,** on the `general` queue unless the
  maintainer names another, each after the one before has come back. Slices
  touch the same code, so two at once would collide in one worktree. Between
  two slices, read the worker's result and `git log` the branch; do not re-run
  its tests.
- **The worker runs only the tests its tasks touch.** The plan names them in
  each task's verify block. The full suite is not the worker's job.
- **The enqueuer runs the full gates once, at the end:** merge `main` into the
  branch, open the PR, let CI run the suite, and run what CI cannot
  (`make test-slow` where CI skips it, `rift check`, `make bench`).
- **A review agent is sent only when the work could hide something:** a slice
  that changes how state is folded, recorded or synced across browsers, or one
  whose code in the plan was a sketch rather than finished code. Brief it to
  read the slice's diff against the spec and the plan, harshly, and report
  defects with file and line. A slice that is mechanical, or whose tests
  already pin the behaviour, needs none.

## The worker's brief

The worker knows nothing but its first prompt. Say:

1. the worktree path and the branch, and that it works only there;
2. the plan's path and the spec's path, and that it reads both, then the
   repo's `AGENTS.md`;
3. which tasks are its slice, by number, and that it marks each task's
   `data-state` in the plan as it starts and finishes it, one edit each;
4. that a task's code in the plan is a hypothesis: the task's tests judge it,
   and where the code and the tests disagree with the real code, it follows the
   real code and says so in its result;
5. which tests to run, and that it runs nothing else;
6. the commit rules: one commit per task, conventional, `git commit --
   <paths>`, push after each;
7. what its final message must hold: the commits, what it verified and how,
   every place it departed from the plan with the reason, and anything it could
   not finish.

Say also that the report must be its last message: if a monitor or anything
else wakes it after it wrote the report, it ends that turn by repeating the
whole report. Only the last message reaches the enqueuer; on the canvas plan
(#347) one worker answered a late monitor with a one-line note, and the report
had to be dug out of its transcript store.

The worker's final message is the slice's report. Copy its departures into the
plan's "Where this plan departs from the spec" section before the PR.
