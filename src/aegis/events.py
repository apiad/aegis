from __future__ import annotations

import json
from dataclasses import dataclass


@dataclass
class SystemInit:
    session_id: str | None


@dataclass
class AssistantText:
    text: str


@dataclass
class AssistantThinking:
    text: str


@dataclass
class ToolUse:
    name: str
    summary: str


@dataclass
class ToolResult:
    text: str
    is_error: bool


@dataclass
class Result:
    duration_ms: int | None
    is_error: bool


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
        return Result(
            duration_ms=obj.get("duration_ms"),
            is_error=bool(obj.get("is_error", False)),
        )

    if etype == "assistant":
        content = obj.get("message", {}).get("content", [])
        block = _first_block(content) if isinstance(content, list) else None
        if block is None:
            return Unknown(raw=line)
        btype = block.get("type")
        if btype == "text":
            return AssistantText(text=block.get("text", ""))
        if btype == "thinking":
            return AssistantThinking(text=block.get("thinking", ""))
        if btype == "tool_use":
            return ToolUse(
                name=block.get("name", "?"),
                summary=_summarize_tool(
                    block.get("name", ""), block.get("input", {}) or {}
                ),
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
