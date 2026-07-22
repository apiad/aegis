from __future__ import annotations

import json
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


@dataclass
class Unknown:
    raw: str


Event = (
    SystemInit | AssistantText | AssistantThinking | ThinkingTokens
    | ToolUse | ToolResult | AgentPlan | ContextUpdate
    | Result | Unknown
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
    # Running sum of thinking-token deltas since the last thinking block was
    # emitted — stamped onto that block's AssistantThinking, then reset.
    thinking_estimate: int = 0


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
                    diff = state.tool_diffs.get(tcid) if tcid else None
                    return ToolResult(
                        text=text,
                        is_error=bool(block.get("is_error", False)),
                        tool_call_id=tcid,
                        kind=kind,
                        diff=diff,
                    )
        return Unknown(raw=line)

    return Unknown(raw=line)
