"""Turn the legacy tree's session logs into archived aegis sessions.

The legacy tree (aegis before 2.0) kept one log per session in ``sessions/``,
each line ``{"v": 1, "aegis_ts": ..., "event": {"t": ...}}``: Claude's output
already parsed into the tree's own events, plus the raw stream-json line of
anything it did not parse (``Unknown``). ``backfill/`` holds prompts recovered
later for the same logs, under the same file name, merged by timestamp as the
legacy reader did.

Each log is written back out as the stream-json lines Claude would have
printed, so the ordinary fold reads it: the archive lists it and a tab opens
it. Nothing reads the legacy tree afterwards. Reopening an imported session
resumes its last Claude session id, which works only while Claude Code still
keeps that session; reading never depends on it.

A log's log id is derived from its file name, so a second run skips what the
first one wrote and imports only what is new.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from .claude.stream import Echo, Garbled, Ignored, parse
from .meta import MetaStore
from .names import default_title, mint_handle, valid_handle

# `20260730T130711775955Z-placid-perlis`: birth time, then the handle.
_STEM = re.compile(r"^(\d{8})T(\d{6})\d*Z-(.+)$")

# Claude's permission modes, in aegis's names.
_PERMISSION = {
    "bypassPermissions": "full",
    "acceptEdits": "write",
    "plan": "read",
    "default": "auto",
}


@dataclass
class Report:
    imported: int = 0
    already: int = 0
    empty: int = 0
    damaged_lines: int = 0
    log_ids: list[str] = field(default_factory=list)


def log_id_for(stem: str, first_ts: float | None) -> str:
    """The aegis log id of legacy log ``stem``: its birth time, as aegis mints
    them, and a hash of the whole stem, so the same log always maps to the
    same id."""
    m = _STEM.match(stem)
    if m:
        when = f"{m.group(1)}-{m.group(2)}"
    elif first_ts is not None:
        when = datetime.fromtimestamp(first_ts).strftime("%Y%m%d-%H%M%S")
    else:
        when = "00000000-000000"
    return f"{when}-{hashlib.sha1(stem.encode()).hexdigest()[:6]}"


def _ts(iso: str | None) -> float | None:
    if not iso:
        return None
    try:
        return datetime.fromisoformat(iso.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def _read(path: Path) -> tuple[list[tuple[float | None, dict]], int]:
    """The (timestamp, event) pairs of one legacy file, and its damaged lines."""
    out: list[tuple[float | None, dict]] = []
    damaged = 0
    if not path.exists():
        return out, damaged
    with path.open(encoding="utf-8", errors="replace") as f:
        for raw in f:
            if not raw.strip():
                continue
            try:
                rec = json.loads(raw)
                ev = rec["event"]
                if not isinstance(ev, dict):
                    raise ValueError
            except (ValueError, KeyError, TypeError):
                damaged += 1
                continue
            out.append((_ts(rec.get("aegis_ts")), ev))
    return out, damaged


def _usage(u: dict | None) -> dict:
    u = u or {}
    return {
        "input_tokens": int(u.get("input") or 0),
        "cache_creation_input_tokens": int(u.get("cache_creation") or 0),
        "cache_read_input_tokens": int(u.get("cache_read") or 0),
        "output_tokens": int(u.get("output") or 0),
    }


def _assistant(ev: dict, block: dict, model: str | None) -> dict:
    message: dict = {
        "role": "assistant",
        "content": [block],
        "usage": _usage(ev.get("usage")),
    }
    if ev.get("message_id"):
        message["id"] = ev["message_id"]
    if model:
        message["model"] = model
    return {
        "type": "assistant",
        "parent_tool_use_id": ev.get("parent_tool_use_id"),
        "message": message,
    }


def _echo(text: str) -> dict:
    return {
        "type": "user",
        "isReplay": True,
        "message": {"role": "user", "content": text},
    }


def _echo_text(raw: str) -> str | None:
    """The prompt a raw replay line echoes, if it is one."""
    return next((e.text for e in parse(raw) if isinstance(e, Echo)), None)


def _useful(raw: str) -> bool:
    """Whether the fold makes anything of a raw line the legacy tree kept."""
    return any(not isinstance(e, (Ignored, Garbled)) for e in parse(raw))


def convert(
    events: list[tuple[float | None, dict]],
) -> tuple[list[dict], dict]:
    """Store records for one merged legacy log, and what its meta needs."""
    info: dict = {
        "handle": None,
        "title": "",
        "profile": None,
        "provider": None,
        "cwd": None,
        "model": None,
        "permission": None,
        "session_id": None,
        "first_prompt": None,
        "costs": [],
    }
    for _, ev in events:
        if ev.get("t") == "SessionMeta":
            for k in ("handle", "profile", "provider", "cwd"):
                info[k] = ev.get(k) or info[k]
            info["title"] = ev.get("title") or ev.get("preview") or info["title"]

    # A prompt shows once: through Claude's own echo when the log kept it,
    # else through one made from the tree's record of the send.
    echoed: dict[str, int] = {}
    for _, ev in events:
        if ev.get("t") == "Unknown":
            text = _echo_text(str(ev.get("raw") or ""))
            if text is not None:
                echoed[text.strip()] = echoed.get(text.strip(), 0) + 1

    records: list[dict] = []

    def claude(ts: float | None, line: dict | str) -> None:
        if not isinstance(line, str):
            line = json.dumps(line, ensure_ascii=False)
        records.append({"ts": ts, "src": "claude", "line": line})

    for ts, ev in events:
        t = ev.get("t")
        if t == "SystemInit":
            info["session_id"] = ev.get("session_id") or info["session_id"]
            info["model"] = ev.get("model") or info["model"]
            info["permission"] = ev.get("permission_mode") or info["permission"]
            claude(
                ts,
                {
                    "type": "system",
                    "subtype": "init",
                    "session_id": ev.get("session_id"),
                    "model": ev.get("model"),
                    "claude_code_version": ev.get("version"),
                },
            )
        elif t == "UserMessage":
            text = str(ev.get("text") or "")
            info["first_prompt"] = info["first_prompt"] or text
            key = text.strip()
            if echoed.get(key):
                echoed[key] -= 1
            elif key:
                claude(ts, _echo(text))
        elif t == "AssistantText":
            claude(
                ts,
                _assistant(
                    ev, {"type": "text", "text": ev.get("text") or ""}, info["model"]
                ),
            )
        elif t == "AssistantThinking":
            if (ev.get("text") or "").strip():
                claude(
                    ts,
                    _assistant(
                        ev,
                        {"type": "thinking", "thinking": ev["text"]},
                        info["model"],
                    ),
                )
        elif t == "ToolUse":
            raw_input = ev.get("raw_input")
            block = {
                "type": "tool_use",
                "id": ev.get("tool_call_id") or "",
                "name": ev.get("name") or "?",
                "input": raw_input if isinstance(raw_input, dict) else {},
            }
            claude(ts, _assistant(ev, block, info["model"]))
        elif t == "ToolResult":
            claude(
                ts,
                {
                    "type": "user",
                    "parent_tool_use_id": ev.get("parent_tool_use_id"),
                    "message": {
                        "role": "user",
                        "content": [
                            {
                                "type": "tool_result",
                                "tool_use_id": ev.get("tool_call_id") or "",
                                "content": ev.get("text") or "",
                                "is_error": bool(ev.get("is_error")),
                            }
                        ],
                    },
                },
            )
        elif t == "Result":
            if isinstance(ev.get("cost_usd"), (int, float)):
                info["costs"].append(float(ev["cost_usd"]))
            line = {
                "type": "result",
                "subtype": "error_during_execution"
                if ev.get("is_error")
                else "success",
                "is_error": bool(ev.get("is_error")),
                "duration_ms": ev.get("duration_ms"),
                "num_turns": ev.get("num_turns"),
                "stop_reason": ev.get("stop_reason"),
                "usage": _usage(ev.get("usage")),
            }
            if "cost_usd" in ev:
                line["total_cost_usd"] = ev["cost_usd"]
            claude(ts, line)
        elif t == "Unknown":
            raw = str(ev.get("raw") or "")
            if _useful(raw):
                claude(ts, raw)
        elif t == "SessionClosed":
            records.append({"ts": ts, "src": "aegis", "kind": "close"})
        # SessionMeta was read above. RecapNote, AgentPlan and ContextUpdate
        # described the live session's card, which an archive has no use for.
    return records, info


def _cost(costs: list[float]) -> float | None:
    """Claude reports a running total per process: sum the last total of each
    process, a drop in the total marking a new one."""
    if not costs:
        return None
    total, prev = 0.0, costs[0]
    for c in costs[1:]:
        if c < prev:
            total += prev
        prev = c
    return round(total + prev, 6)


def _free_handle(handle: str, taken: set[str]) -> str:
    """``handle``, or ``handle-2``, ``handle-3``... when the legacy tree reused
    it; a fresh one when no such form is a valid handle."""
    if valid_handle(handle) and handle not in taken:
        return handle
    n = 2
    while valid_handle(f"{handle}-{n}"):
        if f"{handle}-{n}" not in taken:
            return f"{handle}-{n}"
        n += 1
    return mint_handle(taken)


def import_legacy(source: Path, state_root: Path) -> Report:
    """Write every log under ``source/sessions`` into ``state_root`` as an
    archived session, skipping the ones already there."""
    report = Report()
    metas = MetaStore(state_root / "sessions")
    tdir = state_root / "transcripts"
    existing, _ = metas.read_all()
    taken = {str(m["handle"]) for m in existing if m.get("handle")}
    for log in sorted((source / "sessions").glob("*.jsonl")):
        events, damaged = _read(log)
        extra, damaged_extra = _read(source / "backfill" / log.name)
        damaged += damaged_extra
        merged = sorted(events + extra, key=lambda p: p[0] if p[0] is not None else 0.0)
        first_ts = next((ts for ts, _ in merged if ts is not None), None)
        # The log's own first line, never the backfill's: the id must not
        # change when a sidecar does.
        log_id = log_id_for(log.stem, next((ts for ts, _ in events if ts), None))
        if metas.path(log_id).exists():
            report.already += 1
            continue
        body, info = convert(merged)
        if not any(r["src"] == "claude" for r in body):
            report.empty += 1
            continue
        report.damaged_lines += damaged

        m = _STEM.match(log.stem)
        handle = info["handle"] or (m.group(3) if m else "")
        handle = _free_handle(handle, taken)
        taken.add(handle)

        harness = "opencode" if info["provider"] == "opencode" else "claude-code"
        cwd = info["cwd"] or str(state_root.parent.parent)
        spawn = {
            "ts": first_ts,
            "src": "aegis",
            "kind": "spawn",
            "agent": info["profile"] or "",
            "harness": harness,
            "model": info["model"] or "",
            "effort": "high",
            "permission": _PERMISSION.get(info["permission"] or "", "auto"),
            "cwd": cwd,
        }
        head = [spawn]
        if damaged:
            head.append(
                {"ts": first_ts, "src": "aegis", "kind": "damaged", "count": damaged}
            )
        records = head + body

        tdir.mkdir(parents=True, exist_ok=True)
        target = tdir / f"{log_id}.jsonl"
        tmp = target.with_suffix(".jsonl.tmp")
        with tmp.open("w", encoding="utf-8") as f:
            for i, r in enumerate(records):
                f.write(json.dumps({"i": i, **r}, ensure_ascii=False) + "\n")
        os.replace(tmp, target)

        last_ts = next(
            (r["ts"] for r in reversed(records) if r["ts"] is not None), None
        )
        metas.write(
            {
                "log_id": log_id,
                "handle": handle,
                "title": info["title"] or default_title(info["first_prompt"] or ""),
                "title_set": bool(info["title"]),
                "agent": spawn["agent"],
                "harness": harness,
                "model": spawn["model"],
                "effort": spawn["effort"],
                "permission": spawn["permission"],
                "cwd": cwd,
                "claude_session_id": info["session_id"],
                "archived": True,
                "created_at": first_ts,
                "last_activity": last_ts,
                "last_status": "stopped",
                "cost_usd": _cost(info["costs"]),
                "context_tokens": None,
                "context_window": None,
                "activity": "",
                "imported_from": str(log),
            }
        )
        report.imported += 1
        report.log_ids.append(log_id)
    return report
