# Token-bench tasks

Each folder here is one task for `make bench-tokens`, in Terminal-Bench's shape
without the Dockerfile: `setup.sh` builds the starting state on the host,
deterministically, from the folder's files and a fixed seed. The design is
`docs/superpowers/specs/2026-10-17-token-bench-design.html`, section 04.

```text
bench/tasks/<name>/
  task.toml          category, expert_minutes, timeout_s; optional requires = ["gcc", ...]
  instruction.md     the first user turn, verbatim, in both arms
  followups.md       optional: later user turns, split by a line holding only ---
  setup.sh WORK      builds the starting state in WORK, commits it and tags it start
  tests/test.sh WORK the hidden check: exit 0 on pass, last line PASS or FAIL: <reason>
  solution/*.sh WORK oracles: each one, run on a fresh WORK, makes test.sh pass
```

`tests/` is never in WORK while the agent works. `test.sh` finds any helper file
beside it through `$(dirname "$0")`, so it runs the same from the task folder or
from a copy. Hidden helper files are not named `test_*.py`, so a `pytest tests`
in WORK never collects them even when `tests/` is copied there.

Tasks run `python3`, `git`, `bash` and, where `requires` says so, `gcc` and
`make`. `python3` must have pytest. `make bench-tasks-check` puts the
virtualenv's `bin` first on PATH, which gives it pytest.

## Two rules for a check

1. **Accept any correct solution.** A check reads behaviour, not file layout. It
   runs the original tests from the `start` tag (`git archive start tests`)
   rather than forbidding edits to test files: in the first spike a correct run
   added its new test to the existing test file and a check that forbade test
   edits failed it. When the follow-up can be met more than one way, ship one
   `solution/*.sh` per way: `make bench-tasks-check` runs every one.
2. **Every command runs under `timeout`.** A seeded bug can hang: ledger-fix's
   month-rollover bug loops forever on a range that starts in December, and
   allocates as it goes, so its check also caps memory with `ulimit -v`.
   `tests/test_bench_tasks.py` fails a `python3`, `pytest`, `make` or `./`
   command in `test.sh` that is not preceded by `timeout`.

## The band

A task takes an expert 15 to 45 minutes (`expert_minutes`) and costs a stock
Opus run between $0.50 and $2 cache-neutral, which is about 20 to 80 API
requests. Below that the bench measures only aegis's fixed per-turn tax; above
it, failures add noise. `timeout_s` bounds one arm's whole run.

## Calibrating a task

`make bench-tasks-check` proves a task is solvable and not already solved; it
says nothing about cost. To calibrate, run the stock arm on the task alone, three
times (`scripts/bench_tokens.py --tasks <name> --refresh-stock`), and read the
requests and the cache-neutral cost. Under the band, give the agent more to find:
a larger codebase, another seeded fault in a different module, a follow-up turn.
Over it, cut the codebase or split the task. A task joins the set once its three
stock runs pass and land inside the band.
