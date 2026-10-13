- **In a host OOM, the kernel kills a runaway session before the server.**
  Every agent process aegis starts now raises its `oom_score_adj` to 500, so
  the processes it spawns inherit it; before, they shared the server's 0, and
  the kernel could kill the server and every session with it. The README
  gives the unit settings to pair with it: `OOMPolicy=continue`, `MemoryHigh`,
  `MemoryMax` and a negative `OOMScoreAdjust`.
- **Concurrent test runs on one host share a cap on browsers.** Each browser
  test takes one of N slots under `flock` first (one per GiB of available
  memory, at most one per core; `AEGIS_TEST_BROWSERS` overrides it), so
  several `pytest -n auto` runs no longer start a Chromium per worker each.
