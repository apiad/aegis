"""Events in, exchanges out. Pure — no disk, no db, no clock.

Pure for the same reason `btw/window.py` is: it is the piece every other
part of the corpus depends on, so it is the piece worth testing hard.
"""
from __future__ import annotations

from dataclasses import dataclass

# A turn from one of these sources carries genuine new intent and opens a new
# exchange. Everything else (monitor wakes, queue callbacks, canvas notices)
# is a continuation of the task already in flight — letting those split would
# shatter one piece of work into a dozen fake exchanges.
BOUNDARY_SOURCES = frozenset({"operator", "agent"})

# Tool inputs whose file_path is worth indexing as a facet.
FILE_TOOLS = frozenset({"Read", "Edit", "Write", "NotebookEdit"})

_MAX_TEXT = 8000


@dataclass(frozen=True)
class Exchange:
    operator_text: str
    assistant_text: str
    source: str
    handle: str | None
    cwd: str | None
    ts_start: str
    ts_end: str
    files_touched: tuple[str, ...] = ()
    tools_used: tuple[str, ...] = ()
    friction: tuple[str, ...] = ()

    @property
    def key(self) -> str:
        """Stable id: handle + start timestamp uniquely locate an exchange."""
        return f"{self.handle or '?'}@{self.ts_start}"


def _text_of(ev: dict) -> str:
    return (ev.get("text") or "").strip()


def extract_exchanges(events, meta=None) -> list[Exchange]:
    """`events` is an iterable of (event_dict, aegis_ts) in log order."""
    meta = meta or {}
    handle, cwd = meta.get("handle"), meta.get("cwd")
    out: list[Exchange] = []

    cur: dict | None = None

    def flush():
        if cur is None:
            return
        out.append(Exchange(
            operator_text=cur["operator"][:_MAX_TEXT],
            assistant_text=" ".join(cur["assistant"])[:_MAX_TEXT],
            source=cur["source"],
            handle=handle, cwd=cwd,
            ts_start=cur["ts_start"], ts_end=cur["ts_end"],
            files_touched=tuple(dict.fromkeys(cur["files"])),
            tools_used=tuple(dict.fromkeys(cur["tools"])),
            friction=tuple(cur["friction"]),
        ))

    for ev, ts in events:
        t = ev.get("t")
        if t == "UserMessage":
            src = ev.get("source") or "unknown"
            if src in BOUNDARY_SOURCES:
                flush()
                cur = {"operator": _text_of(ev), "assistant": [], "source": src,
                       "ts_start": ts, "ts_end": ts, "files": [], "tools": [],
                       "friction": ["interrupted"] if ev.get("interrupted") else []}
            elif cur is not None:
                cur["assistant"].append(_text_of(ev))
                cur["ts_end"] = ts
            continue
        if cur is None:
            continue
        cur["ts_end"] = ts
        if t == "AssistantText":
            cur["assistant"].append(_text_of(ev))
        elif t == "ToolUse":
            name = ev.get("name") or ""
            if name:
                cur["tools"].append(name)
            # Persisted events carry `raw_input`; `input` is accepted only as
            # a fallback for a caller feeding live parser output. Reading
            # `input` alone is what left files_touched empty on every real
            # log — the field does not exist in any of them.
            inp = ev.get("raw_input")
            if not isinstance(inp, dict):
                inp = ev.get("input")
            if isinstance(inp, dict):
                fp = inp.get("file_path")
                if name in FILE_TOOLS and fp:
                    cur["files"].append(fp)
            # `locations` is [[path, line], ...], populated by the drivers
            # for file-ish tools regardless of tool name — a second real
            # source of the same facet, and the only one for tools whose
            # path does not arrive as `file_path`.
            for loc in ev.get("locations") or ():
                path = loc[0] if isinstance(loc, (list, tuple)) and loc else None
                if path:
                    cur["files"].append(path)
        elif t in ("Interrupted", "TurnAborted"):
            cur["friction"].append(t.lower())
        # ToolResult deliberately ignored: ~60% of corpus bytes, no signal.

    flush()
    return out
