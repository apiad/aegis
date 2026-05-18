# Agents

## Running

    aegis init && aegis

## Package management

Use `uv` (not pip): `uv pip install -e .`, `uv run pytest`.

## Layout

- `src/aegis/cli.py` - typer entrypoint (`aegis`, `aegis init`)
- `src/aegis/config.py` - Agent profile + .aegis.py loader
- `src/aegis/drivers/` - HarnessDriver seam; ClaudeDriver in claude.py
- `src/aegis/events.py` - stream-json parser (typed events)
- `src/aegis/render.py` - pure render_event(ev) -> Rich renderable | None
- `src/aegis/tui/` - Textual app shell (app.py) + per-tab ConversationPane
  (pane.py), TabBar/StatusBar (widgets.py), AgentState (state.py),
  SessionMetrics (metrics.py), generated handles (names.py), AgentPicker
  modal (picker.py), Theme registry + AegisColors role map (themes.py;
  `aegis-ink` default)
- Theme colors are threaded as an `AegisColors` object (`app.palette`,
  passed into `render_event`/`dot`/widgets) — not a module global; the
  app attribute is `palette` (not `colors`) to avoid shadowing Textual's
  `App.colors`
- `legacy/` - sidelined workflow-engine prototype (not built, not tested)
- `docs/superpowers/{specs,plans}/*.html` - specs & plans are self-contained
  HTML (house format), not Markdown

## Tests

`uv run pytest -q -k "not live"` for the fast hermetic suite. Drop the filter
to include the live `claude` round-trip (`tests/test_integration_live.py`,
auto-skips if `claude` is not on PATH).

Regenerate parser fixtures with `scripts/capture_fixtures.sh` (captures real
`claude` stream-json output, then sanitizes identifiers/paths before commit).

## Conventions

- TDD: failing test first, then minimal implementation, commit per logical unit.
- `claude -p` with `--output-format stream-json` also requires `--verbose` and
  `--input-format stream-json --replay-user-messages` — see
  `drivers/claude.py:build_argv`.
- The TUI is Textual 8.x. Interrupt is `Escape` (Textual reserves `ctrl+c`).
  The line REPL was removed in Phase 1.5; there is no `--plain` mode, so the
  TUI requires a TTY. Live/driver tests do not go through the App.

## Python

Requires Python 3.13+.
