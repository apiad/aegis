---
title: ClaudeReplDriver — subscription-safe Claude harness
date: 2026-05-27
status: design
---

# ClaudeReplDriver

A second Claude Code harness driver that drives the **interactive REPL** via
PTY + transcript JSONL, in parallel with today's `claude -p` driver.

Motivated by the Anthropic billing split on **June 15, 2026**: `claude -p`
hits the metered API credit pool from that date; the interactive REPL stays
on the subscription bucket. Aegis sessions need a path that stays on
subscription.

The migration path proposed in the prior `TASKS.md` note — "strip `-p`,
write prompts to `proc.stdin`, stream-JSON protocol is identical" — was
verified against the CLI and **does not work**. The flags
`--input-format`/`--output-format`/`--include-partial-messages`/
`--no-session-persistence`/`--max-budget-usd`/`--fallback-model` are all
explicitly gated to `--print` mode. Running without `-p` and the same JSON
flags exits silently. A different architecture is required.

Probe notes captured at `.playground/aegis-repl-probe/FINDINGS.md`.

## Goal

Add `ClaudeReplDriver` alongside the existing `ClaudeDriver`, drive-selectable
per-profile via `ClaudeCode(mode="repl" | "print")`. Reach **full parity**
with the `-p` driver on the four substrate-essential capabilities:

1. Model + effort + system-prompt args.
2. Per-session aegis-MCP injection (the queue / handoff / Telegram substrate
   depends on this).
3. `--permission-mode auto` — no human-in-the-loop permission UI.
4. Multi-turn within a single session, plus mid-turn interrupt.

Plus the queue-worker contract: capture the final assistant text of a
worker's terminal turn.

Aligns with aegis's cross-driver floor: anything `ClaudeReplDriver` does, the
Gemini / OpenCode ACP drivers must already do (verified — ACP v2 covers
MCP-per-session + multi-turn). The driver floor is the minimum aegis
guarantees to the substrate.

## Non-goals (v1)

Deferred to follow-up specs, each on its own merits:

- **Slash commands** (`/skill-name`, `/clear`, `/compact`). The REPL surface
  unlocks these but they require new substrate semantics.
- **Session resume** via `claude --resume <transcript>`. Trivial to add on
  top of this driver later — the transcript tailer is the same machinery.
- **`--brief` / `SendUserMessage`** agent-to-user channel.
- **Token-by-token streaming.** The transcript writes whole messages, not
  deltas. Today's `--include-partial-messages` is the closest equivalent;
  not available on the REPL path. Documented gap; acceptable in v1.

## Architecture

**Two channels, both stable.**

### Input → PTY

Spawn `claude` (no `-p`) inside a pseudo-terminal. Reuse the existing
`src/aegis/terminal/pty.py` `AsyncPty` primitive (already shipped for
long-lived bash terminals in v0.5+).

- Send a user turn: write `<prompt>\r` to the PTY master.
- Interrupt mid-turn: write `\x03` (Ctrl-C) to the PTY master.
- Close: write `/quit\r` (graceful) with PTY kill as fallback after 2s.

PTY input is maximally stable: Anthropic cannot break PTY driving without
breaking every human running `claude` in a terminal. It is literally the
shape of the product.

### Output → transcript JSONL tail

Claude writes its full structured session transcript to:

```
~/.claude/projects/<cwd-slug>/<session-uuid>.jsonl
```

where `<cwd-slug>` is the absolute cwd path with every non-alphanumeric
character replaced by `-`. Verified against real slugs in
`~/.claude/projects/`: `/` → `-`, `.` → `-`, `_` → `-`. So
`/home/apiad/Workspace/repos/aegis` →
`-home-apiad-Workspace-repos-aegis`, and
`/home/apiad/Workspace/.playground/aegis-smoke` →
`-home-apiad-Workspace--playground-aegis-smoke` (note the double dash
where `.` lived). Implementable as
`re.sub(r"[^A-Za-z0-9]", "-", str(Path(cwd).resolve()))`.

The file is append-only, grows live as the session runs, and carries
structured events including `assistant`, `user`, `system`, `attachment`,
`last-prompt`, `permission-mode`, `mode`, `ai-title`,
`file-history-snapshot`, and `queue-operation` (aegis-MCP tool calls show
up structured already).

Tail via `watchdog` filesystem events (already an aegis dependency from
the v0.11.2 FileIndexer) plus a fallback polling tick at ~50ms. Parse each
new JSON line into an `aegis.events.*` typed event.

Transcript stability argument: `claude --resume <path>.jsonl` reads these
files. Resume is a flagship feature. Anthropic has skin in the game keeping
this format stable across versions in a way they don't have for `-p`
stream-JSON or the undocumented daemon sockets at `/tmp/cc-daemon-*/`.

### Session ID is driver-controlled

Driver generates a UUID before spawn and passes `--session-id <uuid>`. The
transcript path is therefore deterministic — no need to discover "which file
just appeared." This matches what existing tooling does (verified in `ps`
output: the chrome-bound claude session is invoked with
`--session-id 7c35700f-...`).

### Spawn argv

```
claude --session-id <uuid> --permission-mode auto \
       --mcp-config '<json>' --strict-mcp-config \
       --model <model> --effort <effort> \
       --append-system-prompt '<aegis preamble>'
```

No `-p`, no `--input-format`, no `--output-format`. The PTY is the input
channel; the JSONL is the output channel.

## Lifecycle

| Phase | Action |
|---|---|
| **spawn** | Generate `session_id` UUID. Compute transcript path. Open `watchdog` observer on the transcript-dir (file doesn't exist yet). Spawn `claude` via `AsyncPty` with the argv above. |
| **session-ready handshake** | Wait for first `system` event in the JSONL (claude's init line). Emit `SystemInit`. |
| **send turn** | Write `<prompt>\r` to the PTY master. Mark "turn open". |
| **await turn end** | Parse JSONL appends. Emit aegis events as they arrive. When an `assistant` event with `stop_reason: "end_turn"` lands, mark turn closed. Capture its `content[].text` joined as the final assistant text (queue-worker semantics). Tool-use steps emit `stop_reason: "tool_use"` and are intermediate — the loop continues until `end_turn`. |
| **interrupt** | Write `\x03` to the PTY master. Wait for the next `assistant` event (claude emits an interrupted message). Treat as turn end with `interrupted: true`. |
| **close** | Write `/quit\r` to PTY (graceful). After 2s without exit, send SIGTERM; after 4s, SIGKILL. Close `watchdog` observer. |

## JSONL → aegis event mapping

The existing `aegis.events` types map cleanly. Mapping table:

| JSONL shape | aegis event | Notes |
|---|---|---|
| `type=assistant`, content block `type=text` | `AssistantText(text)` | One emit per content block. |
| `type=assistant`, content block `type=thinking` | `AssistantThinking(text)` | Strip the `signature` field (cache-only). |
| `type=assistant`, content block `type=tool_use` | `ToolUse(name, id, input)` | |
| `type=user`, content block `type=tool_result` | `ToolResult(tool_use_id, content)` | Tied back via `tool_use_id`. |
| `type=assistant` (whole event) | track `usage` for `TokenUsage` | Token + cache shape matches existing `--print` event. |
| `type=system`, `subtype=init` | `SystemInit(session_id, model, …)` | Already in transcript. |
| `type=assistant`, `stop_reason=end_turn` (final) | `Result(final_text, stop_reason)` | Aegis's existing turn-terminating event. |
| `type=last-prompt` | (ignored) | Audit metadata, not a substrate event. |
| `type=attachment`, `permission-mode`, `mode`, `ai-title`, `file-history-snapshot` | (ignored in v1) | Future spec material if needed. |
| `type=queue-operation` | (already a `ToolUse` via the `aegis_*` MCP tool) | Confirmed: aegis-MCP tool calls land structured. |

## Integration

### Config seam

`src/aegis/config/__init__.py` — `ClaudeCode` config class gains:

```python
class ClaudeCode(_ProviderBase):
    name: Literal["claude-code"] = "claude-code"
    effort: Effort = Effort.high
    mode: Literal["print", "repl"] = "print"   # NEW; default unchanged for v1
```

Default `"print"` preserves today's behavior on existing profiles. Users
opt into the REPL driver by setting `mode="repl"` in their `.aegis.py`.
Default flips to `"repl"` in a follow-up release after burn-in (probably
v0.13.x, before June 15).

### Drivers module

The existing `src/aegis/drivers/claude.py` splits into three files,
matching the ACP-driver pattern (`acp.py` generic + `gemini.py` /
`opencode.py` shims):

- **`claude.py`** — thin router. Exports `ClaudeDriver` which dispatches
  to `ClaudePrintDriver` or `ClaudeReplDriver` based on the agent's
  `ClaudeCode.mode`. Keeps the import path stable for existing code
  (`from aegis.drivers.claude import ClaudeDriver`).
- **`claude_print.py`** — today's `ClaudeSession` / `ClaudeDriver`
  renamed to `ClaudePrintSession` / `ClaudePrintDriver`. Behavior
  unchanged.
- **`claude_repl.py`** — new. Houses `ClaudeReplSession` and
  `ClaudeReplDriver`. Reuses `terminal.pty.AsyncPty` for the PTY half;
  new private `_TranscriptTail` helper for the JSONL half.

Both session classes implement `drivers.base.HarnessSession`
(`start` / `send` / `events` / `close` / `session_id`). Both driver
classes implement `drivers.base.HarnessDriver`
(`build_argv` / `session` / `resume`). Zero changes propagate to
`AgentSession`, `SessionManager`, `QueueManager`, `InboxRouter`, the TUI
panes, the Telegram frontend, or any downstream consumer.

### `_TranscriptTail` helper

New module-private class in `claude_repl.py`. Wraps a `watchdog.Observer`
plus a polling fallback (the FileIndexer pattern from v0.11.2). Public
surface:

```python
class _TranscriptTail:
    def __init__(self, path: Path) -> None: ...
    async def start(self) -> None: ...                   # opens observer
    async def __aiter__(self) -> AsyncIterator[dict]: ... # yields parsed lines
    async def close(self) -> None: ...
```

Yields one parsed `dict` per new JSONL line. The session loop in
`ClaudeReplSession.events()` consumes the iterator and maps each line via
the table above.

### `--append-system-prompt` payload

Same aegis preamble the `-p` driver already uses (the "you are running
inside aegis…" text seen in `ps -ef` for `lone-lamport`). Reused
verbatim; `build_argv` synthesizes it from the agent's handle.

## Risks & open verifications

Each maps to a task in the impl plan.

1. **`--permission-mode auto` actually suppresses prompts in REPL.** Flag
   is accepted by the CLI parser (probe A confirmed). Not yet verified
   that claude doesn't fall back to a TTY-only confirmation UI for some
   tool classes. **Plan task 0:** smoke a tool-use turn end-to-end. If it
   fails, fall back to `--permission-mode bypassPermissions` (equivalent
   semantics to today's `-p auto` we already use).

2. **MCP injection lands correctly in REPL.** Flag accepted (probe B
   confirmed). The aegis MCP server has to be reachable at the URL we
   pass. Same constraint as today's `-p` driver; same
   `_AegisMcpServer` bootstrap. **Plan task 1:** call `aegis_meta` from
   a REPL-driven session and confirm the response lands.

3. **Transcript flush latency.** Claude may buffer JSONL writes such
   that `stop_reason: end_turn` arrives seconds after the text is
   rendered in the PTY, making the driver look laggy. **Mitigation:**
   `watchdog` observer + 50ms polling tick. If real latency proves
   problematic, add a PTY-side "prompt re-appeared" heuristic as a
   secondary turn-end signal. **Plan task:** measure once on a 5-turn
   conversation; gate further work on the number.

4. **Crash recovery.** If `claude` crashes mid-turn, the JSONL ends
   without an `end_turn`. Driver needs a timeout-based "turn aborted"
   path. Emit `Result(stop_reason="harness_crashed")` after a
   configurable idle-timeout (default 5min). **Plan task:** kill the
   subprocess mid-turn in a test and assert the timeout path.

5. **Skill / CLAUDE.md / hook injection in REPL mode.** Interactive
   claude renders skills and `CLAUDE.md` content differently than `-p`;
   the JSONL records the *messages*, not the rendering, so the
   substrate should be transparent to this. **Plan task:** smoke a
   skill invocation in a REPL session and assert the same event types
   appear as in a `-p` session.

6. **Workspace-trust dialog on first run in a new cwd.** `-p` skips it
   silently. The REPL TUI shows a modal. The flag
   `--allow-dangerously-skip-permissions` exists but is stronger than
   we want as a default. **Plan task:** verify behavior on a fresh cwd;
   if blocking, decide between (a) auto-answering via PTY keystrokes
   on first run, (b) using `--add-dir` to pre-trust the directory, or
   (c) accepting the one-time setup cost as documented friction.

## Open questions answered

These were discussed during brainstorming and pinned for the spec:

- **Default flip timing.** v1 ships with `mode="print"` as the default;
  opt-in to `"repl"` per-profile. Default flips to `"repl"` in a follow-up
  release after burn-in, before June 15. *Rationale:* safer rollout;
  proves parity on opt-in profiles first; existing configs keep working
  unchanged until we flip.
- **Driver file naming.** Three-file split (`claude.py` router +
  `claude_print.py` + `claude_repl.py`). *Rationale:* matches the ACP-driver
  pattern in `acp.py` + `gemini.py` + `opencode.py`; clearer separation;
  one-time rename cost.
- **Resume in v1.** Deferred. Trivial to add later via `--resume <path>`
  on the same transcript-tail mechanism; doesn't pull weight against the
  June 15 deadline.

## Out-of-scope details captured for the impl plan

- **TUI rendering choice.** Keep aegis's existing `ConversationPane`
  rendering. The pane consumes typed `Event`s; the new driver emits the
  same `Event` types. No UI change.
- **Metrics meter.** Token usage in the transcript JSONL has the same
  shape (`input_tokens`, `output_tokens`, `cache_creation_input_tokens`,
  `cache_read_input_tokens`); the existing status-line meter reads it
  unchanged.
- **`claude` binary path discovery.** Reuse the existing
  `ClaudePrintDriver.build_argv` resolver. No new path lookup.

## Estimated effort

≤1 day on zion with the existing PTY plumbing reused. Plan will split into
~12 tasks: 1 baseline smoke (verifies probes 1–6), 1 `_TranscriptTail` with
tests, 1 `ClaudeReplSession.send` + turn-end loop, 1 interrupt path, 1
crash-recovery timeout, 1 `ClaudeReplDriver.build_argv`, 1 router in
`claude.py`, 1 config seam, 2 hermetic + 1 live test, 1 docs + AGENTS.md
note + CHANGELOG entry.
