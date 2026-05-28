from __future__ import annotations

import json
from dataclasses import dataclass, field


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


@dataclass
class AssistantText:
    text: str
    usage: TokenUsage | None = None
    message_id: str | None = None


@dataclass
class AssistantThinking:
    text: str
    usage: TokenUsage | None = None
    message_id: str | None = None


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


@dataclass
class ToolResult:
    text: str
    is_error: bool
    tool_call_id: str | None = None
    kind: str | None = None


@dataclass
class Result:
    duration_ms: int | None
    is_error: bool
    input_tokens: int | None = None
    output_tokens: int | None = None
    usage: TokenUsage | None = None


@dataclass(frozen=True)
class PlanEntry:
    """One row of an AgentPlan. Status vocabulary follows ACP's
    PlanEntry.status enum (pending / in_progress / completed) so the
    same renderer can handle both ACP and claude TodoWrite sources."""
    content: str
    status: str            # pending / in_progress / completed
    priority: str = "medium"   # high / medium / low (default for claude)


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


@dataclass
class Unknown:
    raw: str


Event = (
    SystemInit | AssistantText | AssistantThinking
    | ToolUse | ToolResult | AgentPlan | Result | Unknown
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
    stream goes by."""
    tool_kinds: dict[str, str] = field(default_factory=dict)


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

    etype = obj.get("type")

    if etype == "system" and obj.get("subtype") == "init":
        return SystemInit(session_id=obj.get("session_id"))

    if etype == "result":
        usage = obj.get("usage")
        if not isinstance(usage, dict):
            usage = {}
        return Result(
            duration_ms=obj.get("duration_ms"),
            is_error=bool(obj.get("is_error", False)),
            input_tokens=usage.get("input_tokens"),
            output_tokens=usage.get("output_tokens"),
            usage=_token_usage(usage),
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
            return AssistantThinking(text=block.get("thinking", ""),
                                     usage=u, message_id=mid)
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
                    return ToolResult(
                        text=text,
                        is_error=bool(block.get("is_error", False)),
                        tool_call_id=tcid,
                        kind=kind,
                    )
        return Unknown(raw=line)

    return Unknown(raw=line)
