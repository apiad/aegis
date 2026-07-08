"""Serialize aegis Event dataclasses to/from JSON-safe dicts.

Used by session_log to persist a tab's event stream for local
transcript redraw on resume. Type tag is the dataclass name under
``t``; field names mirror the dataclass.
"""
from __future__ import annotations

from dataclasses import replace

from aegis.events import (
    AgentPlan, AssistantText, AssistantThinking, ContextUpdate,
    CostUsage, Event, PlanEntry, Result, SystemInit, TokenUsage,
    ToolResult, ToolUse, Unknown,
)


def _encode_usage(u: TokenUsage | None) -> dict | None:
    if u is None:
        return None
    return {"input": u.input, "cache_creation": u.cache_creation,
            "cache_read": u.cache_read, "output": u.output}


def _decode_usage(d: dict | None) -> TokenUsage | None:
    if d is None:
        return None
    return TokenUsage(input=d["input"],
                      cache_creation=d["cache_creation"],
                      cache_read=d["cache_read"],
                      output=d["output"])


def encode_event(ev: Event) -> dict:
    out = _encode_inner(ev)
    # Uniformly persist the subagent grouping key for any event that carries
    # one (Task-child events), so replay can reconstruct the grouping.
    pid = getattr(ev, "parent_tool_use_id", None)
    if pid is not None:
        out["parent_tool_use_id"] = pid
    return out


def _encode_inner(ev: Event) -> dict:
    if isinstance(ev, SystemInit):
        out = {"t": "SystemInit", "session_id": ev.session_id}
        if ev.model is not None:
            out["model"] = ev.model
        if ev.permission_mode is not None:
            out["permission_mode"] = ev.permission_mode
        if ev.version is not None:
            out["version"] = ev.version
        if ev.available_commands:
            out["available_commands"] = list(ev.available_commands)
        return out
    if isinstance(ev, AssistantText):
        out = {"t": "AssistantText", "text": ev.text,
               "usage": _encode_usage(ev.usage)}
        if ev.message_id is not None:
            out["message_id"] = ev.message_id
        return out
    if isinstance(ev, AssistantThinking):
        out = {"t": "AssistantThinking", "text": ev.text,
               "usage": _encode_usage(ev.usage)}
        if ev.message_id is not None:
            out["message_id"] = ev.message_id
        return out
    if isinstance(ev, ToolUse):
        out = {"t": "ToolUse", "name": ev.name, "summary": ev.summary,
               "usage": _encode_usage(ev.usage)}
        if ev.kind is not None:
            out["kind"] = ev.kind
        if ev.tool_call_id is not None:
            out["tool_call_id"] = ev.tool_call_id
        if ev.raw_input is not None:
            out["raw_input"] = ev.raw_input
        if ev.locations:
            out["locations"] = [[p, ln] for p, ln in ev.locations]
        if ev.status is not None:
            out["status"] = ev.status
        return out
    if isinstance(ev, ToolResult):
        out = {"t": "ToolResult", "text": ev.text,
               "is_error": ev.is_error}
        if ev.tool_call_id is not None:
            out["tool_call_id"] = ev.tool_call_id
        if ev.kind is not None:
            out["kind"] = ev.kind
        if ev.diff is not None:
            path, old, new = ev.diff
            out["diff"] = {"path": path, "old": old, "new": new}
        return out
    if isinstance(ev, Result):
        out = {"t": "Result", "duration_ms": ev.duration_ms,
               "is_error": ev.is_error,
               "input_tokens": ev.input_tokens,
               "output_tokens": ev.output_tokens,
               "usage": _encode_usage(ev.usage)}
        if ev.stop_reason is not None:
            out["stop_reason"] = ev.stop_reason
        if ev.ttft_ms is not None:
            out["ttft_ms"] = ev.ttft_ms
        if ev.num_turns is not None:
            out["num_turns"] = ev.num_turns
        if ev.cost_usd is not None:
            out["cost_usd"] = ev.cost_usd
        if ev.model_usage:
            out["model_usage"] = [
                [name, _encode_usage(u)] for name, u in ev.model_usage]
        if ev.permission_denials:
            out["permission_denials"] = list(ev.permission_denials)
        return out
    if isinstance(ev, AgentPlan):
        return {"t": "AgentPlan",
                "entries": [
                    {"content": e.content, "status": e.status,
                     "priority": e.priority}
                    for e in ev.entries
                ]}
    if isinstance(ev, ContextUpdate):
        out: dict = {"t": "ContextUpdate"}
        if ev.cost is not None:
            out["cost"] = {
                "amount_usd": ev.cost.amount_usd,
                "context_used": ev.cost.context_used,
                "context_size": ev.cost.context_size,
            }
        if ev.mode is not None:
            out["mode"] = ev.mode
        if ev.title is not None:
            out["title"] = ev.title
        return out
    if isinstance(ev, Unknown):
        return {"t": "Unknown", "raw": ev.raw}
    raise ValueError(f"unknown event type: {type(ev).__name__}")


def decode_event(d: dict) -> Event:
    ev = _decode_inner(d)
    pid = d.get("parent_tool_use_id")
    if pid is not None and hasattr(ev, "parent_tool_use_id"):
        ev = replace(ev, parent_tool_use_id=pid)
    return ev


def _decode_inner(d: dict) -> Event:
    t = d.get("t")
    if t is None:
        raise ValueError("event dict missing type tag 't'")
    if t == "SystemInit":
        return SystemInit(
            session_id=d.get("session_id"),
            model=d.get("model"),
            permission_mode=d.get("permission_mode"),
            version=d.get("version"),
            available_commands=tuple(d.get("available_commands") or ()),
        )
    if t == "AssistantText":
        return AssistantText(text=d["text"],
                             usage=_decode_usage(d.get("usage")),
                             message_id=d.get("message_id"))
    if t == "AssistantThinking":
        return AssistantThinking(text=d["text"],
                                 usage=_decode_usage(d.get("usage")),
                                 message_id=d.get("message_id"))
    if t == "ToolUse":
        locs = tuple((p, ln) for p, ln in d.get("locations", []))
        return ToolUse(name=d["name"], summary=d["summary"],
                       usage=_decode_usage(d.get("usage")),
                       kind=d.get("kind"),
                       raw_input=d.get("raw_input"),
                       tool_call_id=d.get("tool_call_id"),
                       locations=locs,
                       status=d.get("status"))
    if t == "ToolResult":
        diff_d = d.get("diff")
        diff = ((diff_d["path"], diff_d["old"], diff_d["new"])
                if isinstance(diff_d, dict) else None)
        return ToolResult(text=d["text"], is_error=d["is_error"],
                          tool_call_id=d.get("tool_call_id"),
                          kind=d.get("kind"),
                          diff=diff)
    if t == "Result":
        mu_raw = d.get("model_usage") or []
        mu = tuple((name, _decode_usage(u)) for name, u in mu_raw)
        return Result(duration_ms=d.get("duration_ms"),
                      is_error=d["is_error"],
                      input_tokens=d.get("input_tokens"),
                      output_tokens=d.get("output_tokens"),
                      usage=_decode_usage(d.get("usage")),
                      stop_reason=d.get("stop_reason"),
                      ttft_ms=d.get("ttft_ms"),
                      num_turns=d.get("num_turns"),
                      cost_usd=d.get("cost_usd"),
                      model_usage=mu,
                      permission_denials=tuple(
                          d.get("permission_denials") or ()))
    if t == "AgentPlan":
        entries = tuple(
            PlanEntry(content=e["content"], status=e["status"],
                      priority=e.get("priority", "medium"))
            for e in d.get("entries", [])
        )
        return AgentPlan(entries=entries)
    if t == "ContextUpdate":
        cost_d = d.get("cost")
        cost = (CostUsage(
            amount_usd=cost_d.get("amount_usd"),
            context_used=cost_d.get("context_used"),
            context_size=cost_d.get("context_size"),
        ) if isinstance(cost_d, dict) else None)
        return ContextUpdate(
            cost=cost, mode=d.get("mode"), title=d.get("title"))
    if t == "Unknown":
        return Unknown(raw=d["raw"])
    raise ValueError(f"unknown event type tag: {t!r}")
