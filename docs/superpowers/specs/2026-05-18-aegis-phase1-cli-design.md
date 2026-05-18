# Aegis Phase-1 CLI — Design

- **Date:** 2026-05-18
- **Status:** approved (pending written-spec review)
- **Scope:** Phase 1 of the Aegis vision only. Fresh start in `apiad/aegis`; prototype sidelined.
- **Vision:** `vault/Atlas/Architecture/2026-05-17-aegis-vision.md`

## Goal

Ship a usable interactive `aegis` CLI that drives Claude Code as a subprocess
via its structured streaming protocol, re-renders its output cleanly, and
selects behavior through a named **agent** profile loaded from a Python config
file. This is the first concrete deliverable of the meta-harness vision: it
proves the parse → re-render loop and establishes the harness-agnostic agent
seam that every later phase builds on.

## Locked decisions

1. **Phase 1 only.** No multi-tab, MCP plane, workflows, skills, handoff,
   queues, terminals, or additional harnesses. Those are explicit non-goals
   (see below).
2. **Fresh start in `apiad/aegis`.** The existing FastMCP workflow-engine
   prototype (`src/aegis/server.py`, `src/aegis/demo.py`) is moved untouched to
   `legacy/` — not deleted (its MCP scaffolding is reusable in Phase 3).
3. **Interactive REPL.** `aegis` is a multi-turn conversation loop, a real
   `claude` replacement — not a one-shot wrapper.
4. **Minimal-clean rendering.** Readable transcript, not a faithful
   reproduction of Claude Code's TUI.
5. **Agent profiles, not raw flags.** `aegis --agent <name>`. No `--model` /
   `--permission-mode` flags; behavior comes from the agent profile.
6. **Config is always Python.** `.aegis.py`. `aegis init` writes the scaffold.
7. **No implicit fallback.** With no `.aegis.py` found, `aegis` refuses to run
   and points at `aegis init`.

## The mechanism

The installed `claude` (v2.1.143) supports a bidirectional structured stream:

```
claude -p \
  --input-format stream-json \
  --output-format stream-json \
  --replay-user-messages \
  [--model ...] [--effort ...] [--permission-mode ...]
```

Spawned **once** and kept alive for the session. User turns are written to
stdin as `{"type":"user","message":{"role":"user","content":"..."}}\n`.
Structured events stream back on stdout. Conversation state lives in that
process for its lifetime; multi-turn is "write another user message" — no
`--resume` within a run. This is the canonical drive-Claude-Code pattern and
deliberately avoids scraping the human-readable TUI (the "unstable contract"
risk the vision flags).

## The Agent abstraction

An **agent** is a named profile that encapsulates everything about how a turn
runs. It is harness-agnostic in shape; only the `claude-code` driver exists in
v1, but the profile is the seam for `opencode` / `gemini` later.

```python
# .aegis.py — Aegis config is always Python
from aegis import Agent

agents = {
    "default": Agent(
        harness="claude-code",   # only driver in v1
        model="opus",            # passthrough alias to the harness
        effort="high",           # low | medium | high | max
        permission="auto",       # read | write | full | auto
    ),
}

default_agent = "default"
```

### Permission model

Aegis exposes four permission levels. The **driver** owns the mapping to its
harness's concrete mechanism (this table is the `claude-code` driver's):

| aegis level | meaning | claude `--permission-mode` |
|---|---|---|
| `read`  | no mutations; propose only      | `plan` |
| `write` | edit files, no shell            | `acceptEdits` |
| `full`  | edits **and** bash              | `bypassPermissions` |
| `auto`  | harness's own smart mode        | `auto` |

`auto` requires harness support. Claude Code has it. A future driver whose
harness lacks an equivalent falls back to `write` — recorded here as a known
future-driver concern, not implemented in v1.

### Effort

Aegis effort `low | medium | high | max` maps 1:1 to claude `--effort`
`low | medium | high | max`. (`xhigh` is intentionally not exposed at the
aegis level in v1; `max` is the ceiling.)

### Model

`model` is an opaque passthrough string handed to the harness as `--model`
(alias like `opus`/`sonnet` or a full model id). Aegis does not validate or
enumerate models — that is harness territory.

## Config loading

- `load_config()` searches, in order: `./.aegis.py`, then `~/.aegis.py`.
- The first found file is executed in a fresh namespace; `agents` (a dict of
  `str → Agent`) and `default_agent` (a `str` key into `agents`) are read out.
- If neither file exists: raise a clear error instructing the user to run
  `aegis init`. No built-in default agent for the run path.
- Validation errors (missing `agents`, `default_agent` not a key, unknown
  `harness`, invalid `permission`/`effort` enum) produce actionable messages
  naming the offending field.

`aegis init` writes the scaffold above to `./.aegis.py` (refuses to overwrite
an existing file). Interactive prompted `init` is a near-term follow-up, not
v1.

## Architecture — modules under `src/aegis/`

| Module | Responsibility | Depends on |
|---|---|---|
| `cli.py` | Typer app. `aegis init` subcommand; default run command. Resolves the agent, hands off to the REPL. | `config`, `drivers`, `repl` |
| `config.py` | `Agent`, `Permission`, `Effort` types; `load_config()`; `init` scaffold writer; the `INIT_TEMPLATE` string. | — |
| `drivers/base.py` | `HarnessDriver` seam: `build_argv(agent, cwd) -> list[str]` and the session contract (`send(text)`, async `events()`). | `config`, `events` |
| `drivers/claude.py` | `ClaudeDriver` + `ClaudeSession`. Maps an `Agent` to claude argv via the permission/effort tables; owns the stream-json subprocess and its lifecycle. | `drivers/base`, `events` |
| `events.py` | Pydantic models for the rendered stream-json subset (`system/init`, `assistant` text/thinking/tool_use, `user` tool_result, `result`) plus an `Unknown` catch-all. `parse(line) -> Event`. | — |
| `render.py` | `Renderer` — typed event → `rich` renderable. Pure: event in, output out. | `events` |
| `repl.py` | The loop: read input → `session.send()` → drain `session.events()` to the `Renderer` until the turn's `result` → prompt again. Ctrl-D / `exit` quits; Ctrl-C best-effort interrupts the current turn. | `drivers/base`, `render` |

`DRIVERS = {"claude-code": ClaudeDriver}` in `drivers/__init__.py` — one entry;
the registry is the multi-harness seam.

`pyproject.toml`'s `[project.scripts]` repoints `aegis` from `aegis.demo:main`
to `aegis.cli:main`. Dependencies for v1: `rich`, `typer` (already present);
`pydantic` (present). `fastmcp` stays declared but unused in v1 (legacy code).

## Data flow (one turn)

```
user input ──▶ repl ──▶ session.send(text)
                           │  {"type":"user","message":{...}}\n  ▶ claude stdin
claude stdout ──▶ reader task ──▶ events.parse() ──▶ async queue
                           │
repl drains queue ──▶ Renderer.render(event) ──▶ rich Console
                           │
       stop on `result` event (turn complete) ──▶ prompt again
```

## Rendering (minimal-clean)

- **assistant text** → `rich.Markdown`.
- **thinking** → one dim line `✻ Thinking…`; content collapsed (not shown in v1).
- **tool_use** → one line, `⏺ Read(file.py)` / `⏺ Bash(npm test)` — tool name +
  the single salient argument.
- **tool_result** → collapsed: dim `  └ ok` / `  └ error` + first line only.
- **result** → dim turn separator with elapsed wall time. No cost/token UI.
- **Unknown** events → dropped from the rendered view; emitted to stderr only
  under `--debug`.

## CLI surface

```
aegis [PROMPT] [--agent NAME] [--cwd DIR]
aegis init
```

- `PROMPT` optional: if given, sent as the first turn, then the REPL continues.
- `--agent` defaults to the config's `default_agent`.
- `--cwd` sets the claude subprocess working directory (default: `.`).

(`--debug` raw-event echo is deferred out of v1 — see "Open items deferred".)

## Concurrency model

Single `asyncio` event loop. `asyncio.create_subprocess_exec` for the claude
process. One reader task parses stdout lines into typed events onto an
`asyncio.Queue`. The REPL coroutine reads user input (via
`asyncio.to_thread` around blocking input, or `prompt_toolkit` if it proves
necessary — `input()` in a thread is the v1 default to avoid a new dependency),
writes to stdin, and drains the queue until the turn's `result`.

Rationale: every post-Phase-1 capability (multi-tab, MCP plane, queues,
terminals) is inherently concurrent. A synchronous/threaded core would be a
rewrite. asyncio is the natural substrate.

## Error handling

- Subprocess fails to spawn (claude not on PATH / bad argv) → clear startup
  error, exit non-zero.
- Subprocess dies mid-session → surface the exit status and any stderr tail,
  exit the REPL cleanly (no crash trace).
- Malformed JSON line from claude → parsed as `Unknown`; never raises; visible
  only under `--debug`.
- Config errors → actionable message naming the field; exit non-zero before
  spawning anything.
- Ctrl-C during a turn → best-effort interrupt of the current turn (abandon
  rendering; send an interrupt control message if straightforward, else drop
  the in-flight turn); REPL stays alive. Ctrl-C at an idle prompt is a no-op.
- Ctrl-D / `exit` → terminate the subprocess, exit 0.

## Testing strategy

- **`events.py`** — unit tests over captured stream-json fixture lines
  (recorded from a real `claude -p` run): assert each line parses to the right
  typed event; a malformed/unknown line parses to `Unknown` without raising.
- **`config.py`** — unit tests: scaffold parses to the expected `Agent`;
  missing file → the `aegis init` error; bad enum / missing `default_agent` →
  named-field errors; cwd file shadows `~` file.
- **`drivers/claude.py`** — unit test `build_argv`: each permission level and
  effort maps to the right claude flags; model passthrough; the fixed
  stream-json flags are always present.
- **`render.py`** — feed known events, capture `Console(record=True)` output,
  assert the tool one-liner format, markdown rendering, collapsed thinking.
- **`drivers/claude.py` integration** — one slow smoke test: spawn real
  `claude -p` with a trivial no-tool prompt ("say hi"), assert assistant text
  then a `result`. Marked slow; the fast suite stays hermetic.

All tests are written and validated inline by the implementer — the
verification layer is not delegated.

## Explicit non-goals for v1 (YAGNI)

Multi-tab; cross-tab signalling; unified rendering across multiple harnesses;
MCP plane; workflows; skill plane; lifecycle hooks; live/sequential handoff;
conversation fork; task queues; long-lived terminals; subagent spawn;
OpenCode/Gemini drivers; interactive permission-prompt routing; session
persistence across `aegis` restarts; slash commands; interactive `aegis init`;
distribution / Telegram; subscription pooling. The `HarnessDriver` base and
`DRIVERS` registry exist as the seam, but no second driver is implemented.

## Open items deferred (not blocking v1)

- `--debug` raw stream-json echo to stderr (debug observability).
- Interactive `aegis init` (prompted Q&A).
- `auto` permission fallback semantics for harnesses lacking a native mode.
- Whether the REPL eventually needs `prompt_toolkit` (history, editing) — v1
  uses threaded `input()`.
- Interrupt-protocol fidelity (graceful turn cancellation vs. drop-and-continue).
