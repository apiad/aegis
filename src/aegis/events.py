from __future__ import annotations

import json
import re
from dataclasses import dataclass, field, replace


@dataclass(frozen=True)
class TokenUsage:
    """One usage snapshot. The stream's `input` is uncached-only; the true
    context the model ingests is input + cache_creation + cache_read
    (canonical derivation, cf. bin/claude-usage-aggregate)."""
    input: int
    cache_creation: int
    cache_read: int
    output: int

    @property
    def true_input(self) -> int:
        return self.input + self.cache_creation + self.cache_read

    @property
    def cached_pct(self) -> int:
        ti = self.true_input
        return round(100 * self.cache_read / ti) if ti else 0


@dataclass
class SystemInit:
    session_id: str | None
    # Optional boot-time metadata; both substrates populate these
    # opportunistically. claude pulls from system.init (model,
    # permissionMode, claude_code_version, slash_commands); ACP from
    # InitializeResponse.agent_info + (later) AvailableCommandsUpdate.
    model: str | None = None
    permission_mode: str | None = None
    version: str | None = None
    available_commands: tuple[str, ...] = ()


@dataclass
class AssistantText:
    text: str
    usage: TokenUsage | None = None
    message_id: str | None = None
    parent_tool_use_id: str | None = None


@dataclass
class AssistantThinking:
    text: str
    usage: TokenUsage | None = None
    message_id: str | None = None
    parent_tool_use_id: str | None = None
    # Harness-reported reasoning-token estimate for this block. Claude
    # streams the running estimate via `system/thinking_tokens` events and
    # redacts the thinking text itself, so this — not len(text) — is the
    # real token count. 0 when the harness doesn't report it (renderers
    # fall back to a length heuristic).
    token_estimate: int = 0


@dataclass
class ThinkingTokens:
    """A streamed reasoning-token estimate (Claude `system/thinking_tokens`).

    `estimated` is the running total for the *current* thinking block
    (resets per block); `delta` is the increment since the previous event.
    Invisible in transcripts — consumed only by metrics + the thought
    summary. Sum `delta` across a turn/session for cumulative thinking."""
    estimated: int = 0
    delta: int = 0
    parent_tool_use_id: str | None = None


@dataclass
class ToolUse:
    name: str
    summary: str
    usage: TokenUsage | None = None
    kind: str | None = None
    raw_input: dict | None = None
    tool_call_id: str | None = None
    locations: tuple[tuple[str, int | None], ...] = ()
    status: str | None = None
    parent_tool_use_id: str | None = None


@dataclass
class ToolResult:
    text: str
    is_error: bool
    tool_call_id: str | None = None
    kind: str | None = None
    # (path, old_text, new_text) for edit/write tool calls. None for
    # everything else. Drivers populate; renderer shows a 3-line preview.
    diff: tuple[str, str, str] | None = None
    parent_tool_use_id: str | None = None


@dataclass
class Result:
    duration_ms: int | None
    is_error: bool
    input_tokens: int | None = None
    output_tokens: int | None = None
    usage: TokenUsage | None = None
    # Full stop_reason enum, not just is_error — claude exposes
    # end_turn / max_tokens / refusal / tool_use / stop_sequence; ACP
    # exposes end_turn / max_tokens / max_turn_requests / refusal /
    # cancelled.
    stop_reason: str | None = None
    # Time-to-first-token (claude result.ttft_ms; ACP measured locally).
    ttft_ms: int | None = None
    # Model-rebound count claude exposes as result.num_turns.
    num_turns: int | None = None
    # Dollar cost claude exposes as result.total_cost_usd; ACP from the
    # last UsageUpdate.cost.amount of the turn.
    cost_usd: float | None = None
    # Per-model token attribution — claude exposes as result.modelUsage,
    # gemini as field_meta.quota.model_usage. Stored as ((model_id,
    # usage), ...) for stable ordering.
    model_usage: tuple[tuple[str, "TokenUsage | None"], ...] = ()
    # Tool calls the user denied during the turn (claude only).
    permission_denials: tuple[str, ...] = ()


@dataclass(frozen=True)
class PlanEntry:
    """One row of an AgentPlan. Status vocabulary follows ACP's
    PlanEntry.status enum (pending / in_progress / completed) so the
    same renderer can handle both ACP and claude TodoWrite sources."""
    content: str
    status: str            # pending / in_progress / completed
    priority: str = "medium"   # high / medium / low (default for claude)
    # Stable identifier, present only for claude's Task* family. Snapshot
    # sources (TodoWrite, ACP) resend a full ordered list each time and
    # carry no identity, so this stays None for them.
    id: str | None = None
    # Present-continuous label ("Writing the spec") the Task* tools supply
    # beside the imperative subject. Shown while the task is in progress.
    active_form: str | None = None


@dataclass(frozen=True)
class CostUsage:
    """Mid-turn cost + context-window snapshot. ACP UsageUpdate fires
    these in-band; claude has no equivalent and reports at turn end
    via Result.cost_usd. Each field optional — different sources
    populate different subsets."""
    amount_usd:   float | None = None
    context_used: int | None = None
    context_size: int | None = None


@dataclass
class ContextUpdate:
    """Mid-turn telemetry that doesn't render in the transcript —
    consumed by the status bar / metrics observers. ACP-only signal:
    cost from UsageUpdate, mode from CurrentModeUpdate, title from
    SessionInfoUpdate. The renderer returns None for this so the pane
    skips it; downstream subscribers receive it through the standard
    event observer surface."""
    cost:  CostUsage | None = None
    mode:  str | None = None
    title: str | None = None


@dataclass
class AgentPlan:
    """Canonical plan-tracking event. Emitted by:
    - the claude parser when it sees a TodoWrite tool_use (the model's
      explicit plan revision);
    - the ACP driver when it receives an AgentPlanUpdate notification.

    Entries arrive cumulatively (not as deltas) — each event carries
    the full current plan. Pane renderers should treat a new AgentPlan
    in the same turn as a replacement for any earlier one.
    """
    entries: tuple[PlanEntry, ...] = ()
    parent_tool_use_id: str | None = None


@dataclass(frozen=True)
class UserMessage:
    """The user's own turn, echoed back by claude's --replay-user-messages.

    The live pane mounts the user's line at send time, so a running session
    looks right whether or not this event exists. Every path that rebuilds
    from the log instead — Ctrl+R reopen, a restarted aegis, the web
    client's history, ``aegis doctor`` — has only the transcript to work
    from, and without this the conversation reads as the agent talking to
    itself.
    """
    text: str


@dataclass
class Unknown:
    raw: str


@dataclass(frozen=True)
class SessionMeta:
    """First record of a user-initiated session log — the gating header that
    makes a log show up in the Ctrl+H history. Substrate ephemera (queue
    workers, workflow spawns) skip this write."""
    handle: str
    profile: str
    provider: str
    cwd: str
    created_at: str
    origin: str
    preview: str = ""
    # A label, never an identity. The handle keeps doing routing and log-id
    # duty; this is only what a human reads on a tab. ``title_source``
    # records who set it, so a late write cannot clobber a more
    # authoritative one — see ``aegis.state.titles.outranks``.
    title: str = ""
    title_source: str = ""   # "" | auto | agent | human


@dataclass(frozen=True)
class SessionClosed:
    """Close marker appended when a session's pane/handle is torn down. A meta
    header with no close marker is read back as an inferred crash."""
    closed_at: str
    reason: str


Event = (
    SystemInit | AssistantText | AssistantThinking | ThinkingTokens
    | ToolUse | ToolResult | AgentPlan | ContextUpdate
    | Result | UserMessage | Unknown | SessionMeta | SessionClosed
)

# Tool name -> input key whose value is the one-line summary.
_TOOL_SUMMARY_KEY = {
    "Bash": "command",
    "Read": "file_path",
    "Write": "file_path",
    "Edit": "file_path",
    "Glob": "pattern",
    "Grep": "pattern",
}

# Tool name -> semantic kind (parity with ACP's tool_call kind enum).
# Unknown tools fall through to "other" so the renderer still gets
# something to switch on.
_KIND_BY_NAME = {
    "Read": "read",
    "Bash": "execute", "BashOutput": "execute", "KillShell": "execute",
    "Edit": "edit", "Write": "edit", "NotebookEdit": "edit",
    "Glob": "search", "Grep": "search",
    "WebFetch": "fetch", "WebSearch": "fetch",
    "Task": "think", "Agent": "think",
}


@dataclass
class ParserState:
    """Per-session state threaded through parse() so tool_result blocks
    can carry the kind of the matching tool_use. claude's stream-json
    doesn't put the kind on the tool_result itself — the only way to
    enrich it is to remember each tool_use.id → kind as the assistant
    stream goes by.

    Also remembers the (path, old, new) tuple for Edit/Write tool calls
    so the matching ToolResult can attach a diff — claude's tool_result
    body is just "ok" or error text; the diff lives on the Edit/Write
    tool_use input."""
    tool_kinds: dict[str, str] = field(default_factory=dict)
    tool_diffs: dict[str, tuple[str, str, str]] = field(default_factory=dict)
    # Accumulated Task* plan. claude's current task tools speak in deltas
    # with ids, unlike TodoWrite/ACP which resend a full snapshot, so the
    # parser folds them here and emits a cumulative AgentPlan after each
    # mutation. Insertion-ordered, which is the order the agent means.
    plan_tasks: dict[str, dict] = field(default_factory=dict)
    # tool_call_id → plan_tasks key, for a TaskCreate whose real id has
    # not landed yet (it comes in the tool_result, never the tool_use).
    plan_pending: dict[str, str] = field(default_factory=dict)
    # Monotonic counter for provisional keys, pre-id.
    plan_seq: int = 0
    # Running sum of thinking-token deltas since the last thinking block was
    # emitted — stamped onto that block's AssistantThinking, then reset.
    thinking_estimate: int = 0


_TASK_CREATED_RE = re.compile(r"Task #(\S+) created successfully")
# A TaskList row: "#11 [in_progress] T8: fold plan revisions".
_TASK_ROW_RE = re.compile(r"^#(\S+)\s+\[([a-z_]+)\]\s+(.*)$")


def _rehydrate_plan(state: ParserState, text: str) -> bool:
    """Rebuild the plan accumulator from a TaskList listing.

    ParserState is per-process, so a restart loses every task created
    before it — and a later TaskUpdate against those ids resolves to
    nothing and is silently dropped. TaskList returns the whole list,
    which makes it the natural recovery point.

    Returns False (leaving state untouched) when the text parses to no
    rows, so an empty or unexpected result cannot wipe a live plan.
    """
    rows = []
    for line in text.splitlines():
        if m := _TASK_ROW_RE.match(line.strip()):
            rows.append(m.groups())
    if not rows:
        return False
    prior = {t.get("id"): t for t in state.plan_tasks.values()}
    state.plan_tasks = {
        tid: {
            "id": tid,
            "subject": subject,
            "status": status,
            # The listing carries no activeForm, so keep what we had.
            "active_form": (prior.get(tid) or {}).get("active_form"),
        }
        for tid, status, subject in rows
    }
    return True


def _plan_entries(state: ParserState) -> tuple[PlanEntry, ...]:
    """Snapshot the accumulated Task* plan as canonical PlanEntry rows."""
    return tuple(
        PlanEntry(content=t["subject"], status=t["status"],
                  id=t.get("id"), active_form=t.get("active_form"))
        for t in state.plan_tasks.values()
    )


def _summarize_tool(name: str, tool_input: dict) -> str:
    key = _TOOL_SUMMARY_KEY.get(name)
    if key and isinstance(tool_input.get(key), str):
        return tool_input[key]
    for v in tool_input.values():
        if isinstance(v, str):
            return v
    return ""


def _first_block(content: list) -> dict | None:
    for kind in ("text", "thinking", "tool_use"):
        for block in content:
            if isinstance(block, dict) and block.get("type") == kind:
                return block
    return content[0] if content and isinstance(content[0], dict) else None


def _user_text(content: object) -> str:
    """Flatten a user message's content to its text. Real transcripts carry a
    plain string; tolerate the block form so an attachment alongside text
    doesn't lose the text."""
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        return "\n".join(
            b.get("text", "") for b in content
            if isinstance(b, dict) and b.get("type") == "text").strip()
    return ""


def _token_usage(d: object) -> TokenUsage | None:
    if not isinstance(d, dict):
        return None
    keys = ("input_tokens", "cache_creation_input_tokens",
            "cache_read_input_tokens", "output_tokens")
    if not any(k in d for k in keys):
        return None
    return TokenUsage(
        input=int(d.get("input_tokens") or 0),
        cache_creation=int(d.get("cache_creation_input_tokens") or 0),
        cache_read=int(d.get("cache_read_input_tokens") or 0),
        output=int(d.get("output_tokens") or 0),
    )


def parse(line: str, state: ParserState | None = None) -> Event:
    if state is None:
        state = ParserState()
    try:
        obj = json.loads(line)
    except (json.JSONDecodeError, ValueError):
        return Unknown(raw=line)
    if not isinstance(obj, dict):
        return Unknown(raw=line)

    ev = _classify_event(obj, line, state)
    # Subagent (Task) events carry parent_tool_use_id pointing at the
    # dispatching Task tool_use; stamp it uniformly so the UIs can group them.
    parent = obj.get("parent_tool_use_id")
    if parent is not None and hasattr(ev, "parent_tool_use_id"):
        ev = replace(ev, parent_tool_use_id=parent)
    return ev


def _classify_event(obj: dict, line: str, state: ParserState) -> Event:
    etype = obj.get("type")

    if etype == "system" and obj.get("subtype") == "init":
        cmds_raw = obj.get("slash_commands") or []
        if isinstance(cmds_raw, list):
            commands = tuple(
                c.get("name") for c in cmds_raw
                if isinstance(c, dict) and isinstance(c.get("name"), str)
            )
        else:
            commands = ()
        return SystemInit(
            session_id=obj.get("session_id"),
            model=obj.get("model") if isinstance(obj.get("model"), str)
                  else None,
            permission_mode=obj.get("permissionMode")
                  if isinstance(obj.get("permissionMode"), str) else None,
            version=obj.get("claude_code_version")
                  if isinstance(obj.get("claude_code_version"), str)
                  else None,
            available_commands=commands,
        )

    if etype == "system" and obj.get("subtype") == "thinking_tokens":
        delta = int(obj.get("estimated_tokens_delta") or 0)
        est = int(obj.get("estimated_tokens") or 0)
        state.thinking_estimate += delta
        return ThinkingTokens(estimated=est, delta=delta)

    if etype == "result":
        usage = obj.get("usage")
        if not isinstance(usage, dict):
            usage = {}
        mu_raw = obj.get("modelUsage") or {}
        model_usage: tuple[tuple[str, TokenUsage | None], ...] = ()
        if isinstance(mu_raw, dict):
            model_usage = tuple(
                (name, _token_usage(u))
                for name, u in mu_raw.items()
                if isinstance(name, str)
            )
        denials_raw = obj.get("permission_denials") or []
        denials: tuple[str, ...] = ()
        if isinstance(denials_raw, list):
            denials = tuple(
                d.get("tool_name") for d in denials_raw
                if isinstance(d, dict) and isinstance(
                    d.get("tool_name"), str)
            )
        ttft = obj.get("ttft_ms")
        cost = obj.get("total_cost_usd")
        return Result(
            duration_ms=obj.get("duration_ms"),
            is_error=bool(obj.get("is_error", False)),
            input_tokens=usage.get("input_tokens"),
            output_tokens=usage.get("output_tokens"),
            usage=_token_usage(usage),
            stop_reason=(obj.get("stop_reason")
                         if isinstance(obj.get("stop_reason"), str)
                         else None),
            ttft_ms=int(ttft) if isinstance(ttft, (int, float)) else None,
            num_turns=(int(obj["num_turns"])
                       if isinstance(obj.get("num_turns"), int) else None),
            cost_usd=(float(cost)
                      if isinstance(cost, (int, float)) else None),
            model_usage=model_usage,
            permission_denials=denials,
        )

    if etype == "assistant":
        message = obj.get("message", {})
        content = message.get("content", [])
        block = _first_block(content) if isinstance(content, list) else None
        if block is None:
            return Unknown(raw=line)
        u = _token_usage(message.get("usage"))
        mid = message.get("id") if isinstance(message.get("id"), str) else None
        btype = block.get("type")
        if btype == "text":
            return AssistantText(text=block.get("text", ""),
                                 usage=u, message_id=mid)
        if btype == "thinking":
            est = state.thinking_estimate
            state.thinking_estimate = 0
            return AssistantThinking(text=block.get("thinking", ""),
                                     usage=u, message_id=mid,
                                     token_estimate=est)
        if btype == "tool_use":
            name = block.get("name", "?")
            tool_input = block.get("input", {}) or {}
            # TodoWrite is the model's plan-revision channel — promote
            # to the canonical AgentPlan event so the renderer can show
            # a proper status block instead of a generic ⏺ TodoWrite(…).
            if name == "TodoWrite":
                todos = tool_input.get("todos") \
                    if isinstance(tool_input, dict) else None
                if not isinstance(todos, list):
                    todos = []
                entries = tuple(
                    PlanEntry(
                        content=str(t.get("content", "")),
                        status=str(t.get("status", "pending")),
                    )
                    for t in todos if isinstance(t, dict)
                )
                return AgentPlan(entries=entries)
            # The Task* family is the same channel in delta form: one call
            # per mutation, with the id arriving in the result. Fold into
            # the accumulated plan and emit it whole, so a consumer cannot
            # tell which source it came from. TaskList / TaskGet are reads
            # and deliberately fall through to the generic tool path.
            # A TaskList call is the agent asking what its plan is, so
            # answering with the plan is both the honest rendering and one
            # fewer empty row. Its result rehydrates the accumulator.
            if name == "TaskList":
                if tcid := block.get("id"):
                    state.tool_kinds[tcid] = "plan_list"
                return AgentPlan(entries=_plan_entries(state))
            if name in ("TaskCreate", "TaskUpdate"):
                tcid = block.get("id")
                if name == "TaskCreate":
                    state.plan_seq += 1
                    key = f"pending:{state.plan_seq}"
                    state.plan_tasks[key] = {
                        "id": None,
                        "subject": str(tool_input.get("subject", "")),
                        "status": "pending",
                        "active_form": tool_input.get("activeForm")
                            if isinstance(tool_input.get("activeForm"), str)
                            else None,
                    }
                    if tcid:
                        state.plan_pending[tcid] = key
                else:
                    task_id = str(tool_input.get("taskId", ""))
                    key = next(
                        (k for k, t in state.plan_tasks.items()
                         if t.get("id") == task_id), None)
                    # An update for a task we never saw created (resumed
                    # session, truncated log) is ignored, never fatal.
                    if key is not None:
                        status = tool_input.get("status")
                        if status == "deleted":
                            state.plan_tasks.pop(key, None)
                        else:
                            t = state.plan_tasks[key]
                            if isinstance(status, str):
                                t["status"] = status
                            if isinstance(tool_input.get("subject"), str):
                                t["subject"] = tool_input["subject"]
                            if isinstance(tool_input.get("activeForm"), str):
                                t["active_form"] = tool_input["activeForm"]
                if tcid:
                    # Marks the matching result for swallowing: this
                    # tool_use became an AgentPlan, so its result has no
                    # block to fold into and would mount standalone.
                    state.tool_kinds[tcid] = "plan"
                return AgentPlan(entries=_plan_entries(state))
            kind = _KIND_BY_NAME.get(name, "other")
            tool_call_id = block.get("id")
            if tool_call_id:
                state.tool_kinds[tool_call_id] = kind
            file_path = tool_input.get("file_path") \
                if isinstance(tool_input, dict) else None
            locations = (
                ((file_path, None),)
                if isinstance(file_path, str) else ()
            )
            # Remember Edit / Write inputs so the matching ToolResult
            # can attach a diff. Edit carries old_string/new_string;
            # Write replaces the file so the "old" side is empty.
            if tool_call_id and isinstance(file_path, str):
                if name == "Edit":
                    old = tool_input.get("old_string", "")
                    new = tool_input.get("new_string", "")
                    if isinstance(old, str) and isinstance(new, str):
                        state.tool_diffs[tool_call_id] = (
                            file_path, old, new)
                elif name == "Write":
                    content = tool_input.get("content", "")
                    if isinstance(content, str):
                        state.tool_diffs[tool_call_id] = (
                            file_path, "", content)
            return ToolUse(
                name=name,
                summary=_summarize_tool(name, tool_input),
                usage=u,
                kind=kind,
                raw_input=tool_input if isinstance(tool_input, dict) else None,
                tool_call_id=tool_call_id,
                locations=locations,
            )
        return Unknown(raw=line)

    if etype == "user":
        content = obj.get("message", {}).get("content", [])
        if isinstance(content, list):
            for block in content:
                if isinstance(block, dict) and block.get("type") == "tool_result":
                    raw = block.get("content", "")
                    text = raw if isinstance(raw, str) else json.dumps(raw)
                    tcid = block.get("tool_use_id")
                    kind = state.tool_kinds.get(tcid) if tcid else None
                    if kind == "plan_list":
                        # Rehydrate from the listing; a result that parses
                        # to no rows leaves the plan alone.
                        _rehydrate_plan(state, text)
                        return AgentPlan(entries=_plan_entries(state))
                    if kind == "plan":
                        # TaskCreate's id exists only here — backfill it so
                        # later TaskUpdates can resolve their target.
                        key = state.plan_pending.pop(tcid, None)
                        if key is not None and key in state.plan_tasks:
                            if m := _TASK_CREATED_RE.search(text):
                                state.plan_tasks[key]["id"] = m.group(1)
                        # Swallow: the matching tool_use became an
                        # AgentPlan, so this would orphan and mount as its
                        # own block. ContextUpdate renders as None.
                        return ContextUpdate()
                    diff = state.tool_diffs.get(tcid) if tcid else None
                    return ToolResult(
                        text=text,
                        is_error=bool(block.get("is_error", False)),
                        tool_call_id=tcid,
                        kind=kind,
                        diff=diff,
                    )
        # The user's own turn, echoed back because we run claude with
        # --replay-user-messages. `isReplay` marks that and nothing else:
        # skill bodies (isSynthetic) and Task prompts (parent_tool_use_id +
        # subagent_type) also arrive as role:user, and neither is the user
        # speaking. Measured over 269 real transcripts: 6,880 isReplay
        # records, every one a genuine user turn, no false positives.
        if obj.get("isReplay") is True:
            text = _user_text(content)
            if text:
                return UserMessage(text=text)
        return Unknown(raw=line)

    return Unknown(raw=line)
