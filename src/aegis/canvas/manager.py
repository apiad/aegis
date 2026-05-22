"""CanvasManager — file-backed shared markdown blackboards.

Holds a registry of named canvases, each backed by a markdown file on
disk. Mediates reads, section writes, appends, and subscriber tracking
with a per-canvas async lock so concurrent writes serialize cleanly.

Notifications are decoupled: write operations return a ``WriteResult``
the caller (the MCP layer in slice 4) feeds to the notify module
(slice 3) to dispatch ``InboxMessage`` deliveries.
"""
from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from aegis.canvas.parser import (
    BODY,
    PREAMBLE,
    Section,
    append_to_section,
    find_section,
    parse_sections,
    render_sections,
    section_line_count,
    valid_section_name,
    write_section,
)


class CanvasError(Exception):
    """Base error for canvas operations."""


class CanvasNotOpen(CanvasError):
    pass


class CanvasFileMissing(CanvasError):
    pass


class CanvasNameBound(CanvasError):
    """Attempting to bind an existing canvas name to a different file."""


@dataclass(frozen=True)
class SectionInfo:
    name: str
    lines: int
    last_writer: str | None
    updated_at: str | None


@dataclass(frozen=True)
class CanvasInfo:
    name: str
    file: str
    sections: list[SectionInfo]
    created_at: str


@dataclass(frozen=True)
class WriteResult:
    canvas: str
    section: str
    op: str            # "write" | "append"
    writer: str
    added: int         # new line count after the op
    removed: int       # old line count before the op
    new_body: str      # content of the section after the op
    appended_text: str | None  # only for op=="append"
    timestamp: str


@dataclass
class _CanvasState:
    name: str
    file_path: Path
    state_path: Path
    created_at: str
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    subscribers: dict[str, frozenset[str] | None] = field(default_factory=dict)
    # subscribers[handle] = frozenset[section_names] for filtered, or None
    # for "all sections".
    last_writer: dict[str, str] = field(default_factory=dict)
    last_updated: dict[str, str] = field(default_factory=dict)


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# Optional notifier called after every successful write.
Notifier = Callable[[WriteResult, "_CanvasState"], Awaitable[None]]


class CanvasManager:
    """Project-scoped registry of canvases.

    Parameters
    ----------
    state_dir:
        Project state directory (``.aegis/state``). Canvases live at
        ``<state_dir>/canvases/<name>/`` for meta + ledger.
    notifier:
        Optional async callback called with the WriteResult and canvas
        state after each successful write. Wired by the MCP layer (slice
        3) to dispatch inbox notifications; left None in unit tests that
        only care about state.
    """

    def __init__(self, state_dir: Path,
                 notifier: Notifier | None = None) -> None:
        self._root = state_dir / "canvases"
        self._canvases: dict[str, _CanvasState] = {}
        self._notifier = notifier
        # Recover any canvases that were created in a prior run by
        # scanning <root>/*/meta.json. Subscribers are not recovered
        # (session-scoped).
        if self._root.exists():
            for d in self._root.iterdir():
                if not d.is_dir():
                    continue
                meta = d / "meta.json"
                if not meta.exists():
                    continue
                try:
                    raw = json.loads(meta.read_text(encoding="utf-8"))
                except (json.JSONDecodeError, ValueError):
                    continue
                name = raw.get("name")
                file_path = raw.get("file")
                created_at = raw.get("created_at")
                if not name or not file_path or not created_at:
                    continue
                self._canvases[name] = _CanvasState(
                    name=name, file_path=Path(file_path),
                    state_path=d, created_at=created_at)
                # Replay ledger to recover last_writer + last_updated
                ledger = d / "ledger.jsonl"
                if ledger.exists():
                    for line in ledger.read_text(encoding="utf-8").splitlines():
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            rec = json.loads(line)
                        except (json.JSONDecodeError, ValueError):
                            continue
                        sec = rec.get("section")
                        if sec is None:
                            continue
                        self._canvases[name].last_writer[sec] = \
                            rec.get("writer", "")
                        self._canvases[name].last_updated[sec] = \
                            rec.get("ts", "")

    # ------------------------------------------------------------------
    # opening / inspection
    # ------------------------------------------------------------------
    async def open(self, name: str, file: str | None = None) -> CanvasInfo:
        existing = self._canvases.get(name)
        if existing is not None:
            if file is not None and Path(file) != existing.file_path:
                raise CanvasNameBound(
                    f"canvas {name!r} already bound to {existing.file_path}")
            return await self._info(existing)
        if file is None:
            raise CanvasNotOpen(
                f"canvas {name!r} not opened; pass 'file' on first open")
        file_path = Path(file)
        state_path = self._root / name
        state_path.mkdir(parents=True, exist_ok=True)
        if not file_path.exists():
            file_path.parent.mkdir(parents=True, exist_ok=True)
            file_path.write_text("", encoding="utf-8")
        created_at = _now_iso()
        meta = {"name": name, "file": str(file_path),
                "created_at": created_at}
        (state_path / "meta.json").write_text(
            json.dumps(meta, separators=(",", ":")), encoding="utf-8")
        st = _CanvasState(name=name, file_path=file_path,
                          state_path=state_path, created_at=created_at)
        self._canvases[name] = st
        return await self._info(st)

    def is_open(self, name: str) -> bool:
        return name in self._canvases

    def list_canvases(self) -> list[CanvasInfo]:
        # Synchronous metadata snapshot; subscribers/lines reflect last
        # observed state without holding the per-canvas lock.
        out: list[CanvasInfo] = []
        for st in self._canvases.values():
            try:
                secs = self._parse_file(st)
            except CanvasFileMissing:
                secs = []
            out.append(self._info_sync(st, secs))
        return out

    # ------------------------------------------------------------------
    # reads
    # ------------------------------------------------------------------
    async def read(self, name: str, section: str | None = None) -> str:
        st = self._require_open(name)
        async with st.lock:
            secs = self._parse_file(st)
            if section is None:
                return st.file_path.read_text(encoding="utf-8")
            found = find_section(secs, section)
            if found is None:
                raise CanvasError(f"section {section!r} not in canvas {name!r}")
            return found.body

    # ------------------------------------------------------------------
    # writes
    # ------------------------------------------------------------------
    async def write_section(self, name: str, section: str, content: str,
                            writer: str) -> WriteResult:
        if not valid_section_name(section):
            raise CanvasError(f"invalid section name: {section!r}")
        st = self._require_open(name)
        async with st.lock:
            secs = self._parse_file(st)
            self._guard_body_section(section, secs)
            old = find_section(secs, section)
            old_body = old.body if old is not None else ""
            new_secs = write_section(secs, section, content)
            self._render_and_write(st, new_secs)
            result = WriteResult(
                canvas=name, section=section, op="write", writer=writer,
                added=section_line_count(content),
                removed=section_line_count(old_body),
                new_body=content, appended_text=None,
                timestamp=_now_iso())
            self._append_ledger(st, result)
            st.last_writer[section] = writer
            st.last_updated[section] = result.timestamp
        await self._fire_notifier(result, st)
        return result

    async def append_to_section(self, name: str, section: str, text: str,
                                writer: str) -> WriteResult:
        if not valid_section_name(section):
            raise CanvasError(f"invalid section name: {section!r}")
        st = self._require_open(name)
        async with st.lock:
            secs = self._parse_file(st)
            self._guard_body_section(section, secs)
            old = find_section(secs, section)
            old_body = old.body if old is not None else ""
            new_secs = append_to_section(secs, section, text)
            self._render_and_write(st, new_secs)
            new_body = find_section(new_secs, section).body
            result = WriteResult(
                canvas=name, section=section, op="append", writer=writer,
                added=section_line_count(text),
                removed=0,  # append never removes
                new_body=new_body, appended_text=text,
                timestamp=_now_iso())
            # On append, "removed" stays 0 but we still know prior line count
            # via old_body — kept implicit (not in the InboxMessage math).
            _ = old_body
            self._append_ledger(st, result)
            st.last_writer[section] = writer
            st.last_updated[section] = result.timestamp
        await self._fire_notifier(result, st)
        return result

    # ------------------------------------------------------------------
    # subscriptions
    # ------------------------------------------------------------------
    def subscribe(self, name: str, handle: str,
                  sections: list[str] | None = None) -> list[str]:
        st = self._require_open(name)
        if sections is None:
            st.subscribers[handle] = None
        else:
            for s in sections:
                if not valid_section_name(s):
                    raise CanvasError(f"invalid section filter: {s!r}")
            st.subscribers[handle] = frozenset(sections)
        return list(st.subscribers)

    def unsubscribe(self, name: str, handle: str) -> None:
        st = self._require_open(name)
        st.subscribers.pop(handle, None)

    def subscribers(self, name: str) -> dict[str, frozenset[str] | None]:
        st = self._require_open(name)
        return dict(st.subscribers)

    def subscribers_for_section(self, name: str,
                                section: str) -> list[str]:
        """Subscribers that should be notified for a write to ``section``."""
        st = self._require_open(name)
        out: list[str] = []
        for h, flt in st.subscribers.items():
            if flt is None or section in flt:
                out.append(h)
        return out

    # ------------------------------------------------------------------
    # helpers
    # ------------------------------------------------------------------
    def _require_open(self, name: str) -> _CanvasState:
        st = self._canvases.get(name)
        if st is None:
            raise CanvasNotOpen(f"canvas {name!r} not opened")
        return st

    def _parse_file(self, st: _CanvasState) -> list[Section]:
        if not st.file_path.exists():
            raise CanvasFileMissing(
                f"canvas file vanished: {st.file_path}")
        text = st.file_path.read_text(encoding="utf-8")
        return parse_sections(text)

    def _render_and_write(self, st: _CanvasState,
                          sections: list[Section]) -> None:
        out = render_sections(sections)
        # Keep a trailing newline for editor friendliness when non-empty.
        if out and not out.endswith("\n"):
            out += "\n"
        st.file_path.write_text(out, encoding="utf-8")

    def _append_ledger(self, st: _CanvasState, result: WriteResult) -> None:
        rec = {
            "ts": result.timestamp,
            "writer": result.writer,
            "section": result.section,
            "op": result.op,
            "added": result.added,
            "removed": result.removed,
        }
        with (st.state_path / "ledger.jsonl").open("a", encoding="utf-8") as f:
            f.write(json.dumps(rec, separators=(",", ":")) + "\n")

    def _guard_body_section(self, section: str,
                            secs: list[Section]) -> None:
        # BODY is only valid for files with no ## headings.
        if section == BODY:
            has_headings = any(
                s.name not in (BODY, PREAMBLE) for s in secs)
            if has_headings:
                raise CanvasError(
                    "cannot write to 'body' in a file that has ## sections")

    async def _info(self, st: _CanvasState) -> CanvasInfo:
        async with st.lock:
            try:
                secs = self._parse_file(st)
            except CanvasFileMissing:
                secs = []
        return self._info_sync(st, secs)

    def _info_sync(self, st: _CanvasState,
                   secs: list[Section]) -> CanvasInfo:
        sec_infos = [
            SectionInfo(
                name=s.name,
                lines=section_line_count(s.body),
                last_writer=st.last_writer.get(s.name),
                updated_at=st.last_updated.get(s.name),
            )
            for s in secs
        ]
        return CanvasInfo(name=st.name, file=str(st.file_path),
                          sections=sec_infos, created_at=st.created_at)

    async def _fire_notifier(self, result: WriteResult,
                             st: _CanvasState) -> None:
        if self._notifier is None:
            return
        await self._notifier(result, st)
