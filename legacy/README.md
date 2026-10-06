# legacy: aegis before 2.0

The terminal-era aegis: a Textual TUI over a daemon, with queues, workflows,
schedules, canvases, shared terminals, execution hosts and the rest. It was the
`aegis` package up to 0.42.0, and is kept here as reference for future work.

Nothing here is packaged, tested or launchable, and nothing in `src/` imports it
(`tests/test_imports.py`). Read it to see how something was done; copy what is
worth copying. Its design is `legacy/DESIGN.md`, its procedures `legacy/know-how/`,
its user docs `legacy/docs/`. The last release of it is
`pip install "aegis-harness<2"`.
