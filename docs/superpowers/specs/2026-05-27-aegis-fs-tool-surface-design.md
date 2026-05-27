---
title: Aegis filesystem tool surface + harness suppression + permissions
date: 2026-05-27
status: design
---

# Aegis filesystem tool surface

A six-tool aegis-owned filesystem surface (`aegis_bash`, `aegis_read`,
`aegis_write`, `aegis_edit`, `aegis_grep`, `aegis_listdir`) layered on
the existing MCP plane, plus harness-side suppression of built-in
file/shell tools (where the harness allows it) and a simple per-agent
permission framework (`allow` / `deny` / `ask`) with TUI + Telegram
routing.

Motivation: route every agent's file and shell access through aegis so
that visibility, custom policy, and a consistent cross-driver tool
surface are all available from one place. Today each harness (Claude,
Gemini, OpenCode) brings its own built-in tools (`Bash`, `Read`,
`Write`, `Edit`, etc.) and aegis can only observe what passes through
the MCP plane — never what the harness chose to do with its own
built-ins.

## Goal

Make `aegis_*` tools the **primary** surface for filesystem and shell
operations on every harness aegis drives. Where the harness lets us,
remove its built-ins entirely; where it doesn't, push the agent toward
the aegis tools via system prompt and observe everything that passes
through the MCP plane.

Substrate-essential capabilities:

1. Six new MCP tools covering one-shot shell, read, write (new only),
   edit (targeted), literal-text grep, and listdir.
2. Per-tool per-profile permissions (`allow`, `deny`, `ask`) with
   default `allow` for unlisted tools, TUI/Telegram routing for `ask`
   verdicts, and timeout → deny.
3. Hard suppression of Claude built-ins via `--tools ""` when the
   profile sets `suppress_builtins=True`.
4. Soft suppression on ACP harnesses (Gemini, OpenCode) via a
   system-prompt addendum that instructs the agent to prefer aegis
   tools; built-in calls remain visible-but-not-blockable.
5. Per-handle tool-call audit log under `.aegis/state/tool-audit/`.

## Non-goals (v1)

Deferred to follow-up specs, each on its own merits:

- **`aegis_search`** — semantic / embedding-indexed search across the
  repo. Substantial standalone subsystem (embedding pipeline, vector
  store, query interface).
- **`aegis_search_other_sessions`** — visibility into peer agents'
  work. Requires its own visibility-model design (live transcripts
  vs historical, scope, redaction).
- **Fine-grained permission predicates** beyond `allow|deny|ask`.
  Future custom-Python permission functions can layer on the same
  router seam.
- **Hard suppression of Gemini / OpenCode built-ins.** Parked on
  upstream support — Gemini is migrating from `--allowed-tools` to
  a Policy Engine; OpenCode has no external knob today.
- **Observability dashboards** beyond the JSONL audit log. The log
  is the visibility floor for v1; richer surfaces (TUI dashboard,
  Telegram `/tools-recent`, etc.) can layer on later.

## Architecture

### 1. Tool surface

Six new tools on the MCP plane, registered via the existing
`aegis.mcp.server` registration shape used by the 25+ existing
`aegis_*` tools (canvas, terminal, group, queue, handoff, etc.).
Naming convention preserved (`aegis_` prefix everywhere).

| Tool | Args | Semantics |
|---|---|---|
| `aegis_bash` | `command: str`, `cwd: str \| None = None`, `timeout_s: int = 120` | One-shot subprocess. Captures `stdout`, `stderr`, `returncode`. No interactive input. For long-lived shells the existing `aegis_term_*` substrate already exists; `aegis_bash` is its one-shot counterpart. |
| `aegis_read` | `path: str`, `offset: int = 0`, `limit: int = 2000` | Returns file content with `cat -n`-style line numbers. Pagination shape matches Claude's `Read` so the agent's mental model carries over. |
| `aegis_write` | `path: str`, `content: str` | **New-file-only.** Errors if the path exists. (Per Alex's spec: "write — for new files only".) For modifying existing files, agents use `aegis_edit`. |
| `aegis_edit` | `path: str`, `old_string: str`, `new_string: str`, `replace_all: bool = False` | Exact-string replace. Errors if `old_string` is not unique unless `replace_all=True`. Matches Claude's `Edit` semantics. |
| `aegis_grep` | `pattern: str`, `path: str \| None = None`, `case_insensitive: bool = False`, `max_results: int = 200` | **Literal-string match** (not regex by default — per "exact text match" from Alex's spec). Returns lines as `path:line:match`. Implementation prefers `ripgrep` if on PATH; falls back to `grep -F`. Respects `.gitignore` by default. |
| `aegis_listdir` | `path: str = "."`, `recursive: bool = False`, `respect_gitignore: bool = True` | Names + types only (`file` / `dir` / `symlink`). Mirrors `ls`. |

**Execution location.** All six tools run in the aegis process (same
as every existing `aegis_*` tool) — never in a per-session
subprocess. Each tool wraps blocking syscalls in `asyncio.to_thread`
to avoid stalling the event loop.

**Default cwd.** The session's working directory (carried in the
MCP bridge context) is the default for `cwd` / `path` arguments.
Agents may pass an explicit `cwd`; permission policy may later clamp
that override, but the v1 router does not.

**File layout.** Each tool lives in its own small module under
`src/aegis/mcp/fs_tools/` (one file per tool, ~30–80 lines each).
The MCP server imports and registers them. This keeps
`mcp/server.py` from ballooning past its current ~1500 lines.

```
src/aegis/mcp/fs_tools/
  __init__.py          # exports register_fs_tools(server, bridge)
  bash.py              # aegis_bash
  read.py              # aegis_read
  write.py             # aegis_write
  edit.py              # aegis_edit
  grep.py              # aegis_grep
  listdir.py           # aegis_listdir
```

### 2. Tool suppression

Honest about what each binary supports today:

**Claude (`ClaudePrintDriver`, `ClaudeReplDriver`).** `build_argv`
appends `--tools ""` when the agent's `suppress_builtins=True`
(new field, default `False` so existing profiles keep working). With
`--tools ""`, claude's built-in `Bash`, `Read`, `Write`, `Edit`,
`Grep`, etc. all disappear. Tools provided by attached MCP servers
(i.e. our six `aegis_*` tools) remain. Effect: the agent's only
filesystem and shell surface is aegis-controlled.

**Gemini / OpenCode (ACP drivers).** No hard-suppression knob in
the ACP layer or either CLI today (Gemini's `--allowed-tools` is
deprecated; their Policy Engine is the planned successor but not
shipped; OpenCode has no external tool-control). Soft suppression
only.

**Prefer-aegis-tools system-prompt addendum — always injected,
regardless of `suppress_builtins`.** The existing `aegis.mcp.PRIMING`
template (which every driver passes through `--append-system-prompt`
or the ACP equivalent) grows a permanent block:

> *Prefer aegis tools over harness built-ins: `aegis_bash` instead
> of `Bash`/`Shell`, `aegis_read` instead of `Read`, `aegis_edit`
> instead of `Edit`, `aegis_write` instead of `Write`, `aegis_grep`
> instead of `Grep`, `aegis_listdir` instead of `ls`. They route
> through your operator's permission and visibility layer.*

Rationale for unconditional injection: the addendum is a no-op on
Claude when `suppress_builtins=True` (the agent has no built-ins to
prefer aegis tools over anyway), useful on Claude when
`suppress_builtins=False`, and useful on every ACP harness session.
Always-on means one less branch in driver code and one less surprise
when an agent doesn't get the nudge because the profile didn't
opt-in. Soft, model-trust-based — built-in calls on ACP harnesses
remain visible to aegis via the event stream and are written to the
audit log alongside aegis-tool calls.

**`suppress_builtins` schema location.** On `_ProviderBase`
(`src/aegis/config/__init__.py`), so any driver can read it through
the flat `agent.suppress_builtins` access pattern. Only the hard
suppression (Claude `--tools ""`) is gated on this flag; the soft
guidance via `PRIMING` is universal. The flag becomes the natural
knob when Gemini's Policy Engine or an OpenCode equivalent ships
hard suppression.

### 3. Permission framework

**Schema.** Per-profile `permissions` field on `_ProviderBase`:

```python
ClaudeCode(
    model="opus",
    suppress_builtins=True,
    permissions={
        "aegis_bash":  "ask",
        "aegis_write": "ask",
        "aegis_edit":  "ask",
        # aegis_read, aegis_grep, aegis_listdir omitted → default "allow"
    },
)
```

Type: `dict[str, Literal["allow", "deny", "ask"]] = {}`. Missing tools
default to `"allow"` (per Q3 of brainstorming). Applies to any
`aegis_*` tool, not only the six filesystem tools — the same router
can later gate `aegis_enqueue`, `aegis_handoff`, etc.

**Enforcement.** A new `PermissionRouter` class in
`src/aegis/mcp/permissions.py` sits between the FastMCP transport and
each `aegis_*` tool registration. Wrapping pattern: each tool
registration is decorated by `permission_gate(server, bridge)` which
consults the agent's profile before invoking the inner tool. The
existing tool registrations are untouched in shape — only the
`register_*` helpers change.

Three verdicts:

- **`allow`** → call through; result returned as-is.
- **`deny`** → return `{"error": "permission_denied", "tool": <name>,
  "reason": "denied by agent profile"}` as the tool result. The
  agent observes a normal tool error and adapts.
- **`ask`** → block, route an approval prompt, await verdict,
  then either call through or return `permission_denied` with
  reason `"declined by operator"` / `"ask timed out"`.

**"Ask" routing.** Two surfaces, picked per session via the same
session-context map the `InboxRouter` already uses:

- **TUI bound:** if a `ConversationPane` is currently mounted for
  the session's handle, the router emits an inline approval modal
  in that pane. Three buttons: `Approve` (`y`), `Deny` (`n`),
  `Always allow` (`ya`). Keystroke matches the button. The modal
  disappears on click and a one-line audit entry is written to the
  pane log.

- **Telegram-bound or detached** (queue worker, VPS session,
  scheduled job): the router sends one Telegram message via the
  existing `TelegramFrontend` plumbing with an
  `InlineKeyboardMarkup` of three buttons:

  ```
  [lucid-knuth] aegis_bash:
    rm -rf /tmp/x

  [ ✅ Approve ]   [ ❌ Deny ]   [ ✅ Always allow ]
  ```

  Each button's `callback_data` is `perm:<req_id>:allow` /
  `perm:<req_id>:deny` / `perm:<req_id>:allow_always`. On click,
  Telegram delivers a `callback_query` update; the bot dispatches
  it to `PermissionRouter.resolve(req_id, verdict)`, then edits
  the original message to show the verdict (`✅ Approved`, `❌
  Denied`, `✅ Approved (won't ask again)`) with the inline
  keyboard removed.

No text parsing. Permission flows ride a separate update type
(`callback_query`) from normal inbox arrivals (`message`), so the
two surfaces do not interfere with each other or with the existing
command registry (`/queue`, `/schedule`, `/budget`, `/peers`,
`/help`).

**Caching.** Verdicts cache per-session per-tool in an in-memory
dict on the `PermissionRouter`. `Always allow` upgrades the
specific `(handle, tool)` key to `"allow"` for the rest of the
session. Cache resets on session close. No cross-session persistence
in v1 (avoids the "I approved that *yesterday*, why is it asking
now" failure mode where the surrounding context has drifted).

**Timeout.** Default 5 minutes per ask, configurable per profile via
`permission_timeout_s: int = 300` on `_ProviderBase`. Timeout → deny
with `reason: "ask timed out"`.

**Audit log.** Every aegis tool invocation writes one JSONL line
to `.aegis/state/tool-audit/<handle>.jsonl`:

```json
{"ts":"2026-05-27T12:34:56Z","handle":"lucid-knuth",
 "tool":"aegis_bash","args":{"command":"ls -la"},
 "verdict":"allow","cache_hit":false,"latency_ms":42}
```

Verdict is one of `allow|deny|ask_allow|ask_deny|ask_timeout`.
Cache-hit captures whether the verdict came from session cache vs a
fresh decision. Args are written as-is (truncate strings > 4 KiB to
avoid runaway-log scenarios when an agent passes a large `content`
to `aegis_write`). The audit dir is gitignored (already-covered by
`.aegis/state/`).

### 4. Integration points

| File | Change |
|---|---|
| `src/aegis/config/__init__.py` | `_ProviderBase` gains `suppress_builtins: bool = False`, `permissions: dict[str, Literal["allow","deny","ask"]] = {}`, `permission_timeout_s: int = 300`. Inherited by `ClaudeCode`, `GeminiCLI`, `OpenCode`. |
| `src/aegis/mcp/fs_tools/` | New directory; one tool per file. |
| `src/aegis/mcp/permissions.py` | New `PermissionRouter` + `permission_gate` decorator + `Verdict` enum + `PermissionRequest` dataclass. |
| `src/aegis/mcp/server.py` | Imports `register_fs_tools(server, bridge)` from `fs_tools`; wraps each registration with `permission_gate`. |
| `src/aegis/mcp/__init__.py` (or wherever `PRIMING` lives) | Extend `PRIMING` template with the unconditional prefer-aegis-tools block. Every driver picks it up automatically — no per-driver branching needed. |
| `src/aegis/drivers/claude_print.py` + `claude_repl.py` | `build_argv` appends `--tools ""` when `agent.suppress_builtins=True`. (No prompt-addendum work — PRIMING change covers it.) |
| `src/aegis/drivers/acp.py` | No changes needed — the soft-suppression addendum rides PRIMING, which acp.py already passes through. |
| `src/aegis/tui/pane.py` | `ConversationPane` registers with `PermissionRouter` on mount / unregisters on unmount. Renders inline approval modal on `PermissionRequest`. |
| `src/aegis/telegram/bot.py` | `BotClient` gains `send_message_with_inline_keyboard(chat_id, text, buttons)` and `edit_message_text(chat_id, message_id, text, reply_markup=None)`. Long-poll loop extended to dispatch `callback_query` updates. |
| `src/aegis/telegram/frontend.py` | `TelegramFrontend` registers a `callback_query` handler for the `perm:` prefix that calls `PermissionRouter.resolve(req_id, verdict)`. |

No changes propagate beyond these files. `AgentSession`,
`SessionManager`, `QueueManager`, `InboxRouter`, the workflow
engine, the canvas/terminal substrates, the scheduler, the
budget tracker — all untouched.

### 5. PermissionRouter ↔ InboxRouter relationship

Independent. `InboxRouter` delivers user-message-shaped arrivals to
the agent (queue callbacks, peer handoffs, Telegram replies);
`PermissionRouter` delivers operator-question-shaped prompts to the
operator and collects their verdict. Different directions, different
recipients. The only thing they share is the *session-context map*
(which handle is bound to which TUI pane / Telegram chat) and the
delivery primitives in `BotClient`. The two are coordinated via a
small `OperatorChannel` abstraction the spec captures:

```python
class OperatorChannel(Protocol):
    """Where to deliver an operator-facing message for a given handle."""
    def for_handle(self, handle: str) -> Surface: ...
    # Surface is one of: TuiSurface(pane), TelegramSurface(chat_id),
    # NullSurface (detached → routes to default operator chat).
```

`InboxRouter` uses the *inverse* — `for_session(handle) → AgentSurface`.
The two abstractions can later collapse into a single `Routing` module
if they grow in parallel, but in v1 they are sibling units.

## Risks & open verifications

1. **`--tools ""` actually suppresses every built-in including the
   `mcp__*` tools.** The help text says *"from the built-in set"* —
   strongly implies MCP-provided tools survive, but the wording is
   ambiguous. **Plan task 0:** smoke test — spawn `claude --tools ""`
   with `--mcp-config` for a tiny MCP server, ask the agent to call
   the MCP tool, assert it succeeds. If `--tools ""` zeros out MCP
   tools too, fall back to `--tools aegis_bash,aegis_read,…` (explicit
   allowlist of just the aegis MCP tools).

2. **Telegram approval latency for queue workers.** A 5-minute
   default timeout means a queue worker on the VPS can block for up
   to 5 min on a tool call. Acceptable for v1 — `permission_timeout_s`
   is per-profile, so tight-loop workers can lower it. Future
   refinement: per-tool timeouts in the `permissions` dict
   (`{"aegis_bash": {"verdict": "ask", "timeout_s": 60}}` shape).

3. **System-prompt instruction in ACP harnesses may not be honored.**
   Some agents will use `Bash` because it's faster/familiar to them.
   The audit log catches it; we can tighten over time. Honest gap.

4. **Concurrent `aegis_write` collisions.** Two agents writing the
   same new path race. Same property as today; the substrate already
   has `ws-lock` for explicit coordination, and `aegis_write` is
   new-file-only so the race window is "both check file-doesn't-exist,
   both create" — bounded and recoverable.

5. **`aegis_grep` performance on large repos.** Prefer `ripgrep`
   when present; default to `grep -F` otherwise. A 200-result cap
   prevents runaway responses. If real workloads exceed 200, a
   `cursor: str` continuation arg can land in a follow-up.

6. **Audit log unbounded growth.** `.aegis/state/tool-audit/<handle>.jsonl`
   grows without rotation. Acceptable in v1 (queue worker handles are
   ephemeral; sessions are bounded). Plan task: log size measurement
   after a week of normal use; if > 100 MB, add a daily rotation in a
   follow-up.

7. **Default-allow + an aggressive agent.** With `suppress_builtins=False`
   and all `aegis_*` allow, a Claude agent will probably still use
   built-in `Bash` because no system-prompt pushes them to aegis tools.
   The migration to "agents actually use aegis tools" depends on
   either flipping `suppress_builtins=True` per-profile *or* the
   system-prompt addendum. Spec calls this out so it doesn't become a
   surprise. Future: a global `default_suppress_builtins` knob in
   `.aegis.py` so the operator can flip all profiles at once.

## Estimated effort

≤1 day on zion with the existing MCP plumbing and Telegram primitive
reused. Plan will split into ~16 tasks across four slices:

1. **Six filesystem tools** (5–6 tasks, one per tool — `bash`, `read`,
   `write`, `edit`, `grep`, `listdir`) with TDD per tool.
2. **PermissionRouter** + `permission_gate` decorator + session
   cache + audit log (3 tasks).
3. **Operator surfaces** — TUI inline modal + Telegram inline-button
   message + `callback_query` dispatch (3 tasks).
4. **Driver integration** — `suppress_builtins` on Claude
   (`--tools ""`) + ACP system-prompt addendum + smoke test risk #1
   (3 tasks). Final task: AGENTS.md update + CHANGELOG entry +
   version bump.
