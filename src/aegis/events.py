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


@dataclass
class AssistantThinking:
    text: str
    usage: TokenUsage | None = None


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


@dataclass
class Unknown:
    raw: str


Event = (
    SystemInit | AssistantText | AssistantThinking
    | ToolUse | ToolResult | Result | Unknown
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


def parse(line: str) -> Event:
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
        btype = block.get("type")
        if btype == "text":
            return AssistantText(text=block.get("text", ""), usage=u)
        if btype == "thinking":
            return AssistantThinking(text=block.get("thinking", ""),
                                     usage=u)
        if btype == "tool_use":
            return ToolUse(
                name=block.get("name", "?"),
                summary=_summarize_tool(
                    block.get("name", ""), block.get("input", {}) or {}
                ),
                usage=u,
            )
        return Unknown(raw=line)

    if etype == "user":
        content = obj.get("message", {}).get("content", [])
        if isinstance(content, list):
            for block in content:
                if isinstance(block, dict) and block.get("type") == "tool_result":
                    raw = block.get("content", "")
                    text = raw if isinstance(raw, str) else json.dumps(raw)
                    return ToolResult(
                        text=text,
                        is_error=bool(block.get("is_error", False)),
                    )
        return Unknown(raw=line)

    return Unknown(raw=line)
