"""Workspace persistence: the tab roster on disk.

Single file at ``.aegis/state/workspace.json`` rewritten atomically on
every tab change. Crash-survivable single source of truth.
"""
from __future__ import annotations

import json
import os
import tempfile
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path

WORKSPACE_VERSION = 1


class CorruptWorkspace(Exception):
    """workspace.json exists but is unparseable or schema-mismatched."""


@dataclass(frozen=True)
class WorkspaceTab:
    handle: str
    profile: str
    order: int
    provider: str
    session_id: str | None
    created_at: str


@dataclass(frozen=True)
class Workspace:
    active_handle: str | None
    tabs: list[WorkspaceTab] = field(default_factory=list)


def state_dir(cwd: Path) -> Path:
    return cwd / ".aegis" / "state"


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def save(state_dir_path: Path, ws: Workspace) -> None:
    state_dir_path.mkdir(parents=True, exist_ok=True)
    payload = {
        "version": WORKSPACE_VERSION,
        "saved_at": _now_iso(),
        "active_handle": ws.active_handle,
        "tabs": [asdict(t) for t in ws.tabs],
    }
    target = state_dir_path / "workspace.json"
    # Atomic write: tmp file + rename, so a crash mid-write never leaves
    # a half-written workspace.json behind.
    fd, tmp = tempfile.mkstemp(prefix=".workspace.", suffix=".tmp",
                               dir=str(state_dir_path))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(payload, f, separators=(",", ":"))
        os.replace(tmp, target)
    except Exception:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


def load(state_dir_path: Path) -> Workspace | None:
    p = state_dir_path / "workspace.json"
    if not p.exists():
        return None
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, ValueError) as e:
        raise CorruptWorkspace(f"unparseable workspace.json: {e}") from e
    if not isinstance(raw, dict) or raw.get("version") != WORKSPACE_VERSION:
        raise CorruptWorkspace(
            f"workspace.json version mismatch (expected {WORKSPACE_VERSION}, "
            f"got {raw.get('version') if isinstance(raw, dict) else '?'})")
    try:
        tabs = [
            WorkspaceTab(
                handle=t["handle"],
                profile=t["profile"],
                order=t["order"],
                provider=t["provider"],
                session_id=t.get("session_id"),
                created_at=t["created_at"],
            )
            for t in raw["tabs"]
        ]
    except (KeyError, TypeError) as e:
        raise CorruptWorkspace(f"malformed tab record: {e}") from e
    return Workspace(active_handle=raw.get("active_handle"), tabs=tabs)
