"""Per-view state: what you are looking at, not what exists.

Opening a tab opens it for everyone; what you have focused, scrolled to and
half-typed is yours. ``state/workspace.py`` holds the brain half — which
tabs exist and in what order.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class ViewState:
    view_id: str
    geometry: tuple[int, int]
    active_handle: str | None = None
    scroll: dict[str, int] = field(default_factory=dict)
    drafts: dict[str, str] = field(default_factory=dict)


def _path(state_dir: Path, view_id: str) -> Path:
    # View ids come from clients in stage 5. Reject anything that is not a
    # single path component rather than sanitising it: a silently rewritten
    # id resolves to a different view than the client believes it has.
    if view_id != Path(view_id).name or view_id in ("", ".", ".."):
        raise ValueError(f"invalid view id: {view_id!r}")
    return state_dir / "views" / f"{view_id}.json"


def _safe_path(state_dir: Path, view_id: str) -> Path | None:
    """``_path``, but None instead of a raise — the read-side contract.

    ``load_view`` answers "missing or damaged" with ``None`` everywhere
    else, and stage 5 feeds it client-supplied ids. A reader that raises on
    one kind of bad input and returns None on the others makes every caller
    handle two failure shapes for one question.
    """
    try:
        return _path(state_dir, view_id)
    except ValueError:
        return None


def save_view(state_dir: Path, vs: ViewState) -> None:
    p = _path(state_dir, vs.view_id)
    p.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "view_id": vs.view_id,
        "geometry": list(vs.geometry),
        "active_handle": vs.active_handle,
        "scroll": vs.scroll,
        "drafts": vs.drafts,
    }
    tmp = p.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    tmp.replace(p)          # atomic; a torn view file costs a scroll position


def load_view(state_dir: Path, view_id: str) -> ViewState | None:
    p = _safe_path(state_dir, view_id)
    if p is None or not p.is_file():
        return None
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
        return ViewState(
            view_id=raw["view_id"],
            geometry=tuple(raw["geometry"]),
            active_handle=raw.get("active_handle"),
            scroll=raw.get("scroll", {}),
            drafts=raw.get("drafts", {}),
        )
    except (json.JSONDecodeError, KeyError, TypeError, ValueError):
        # A damaged view file must never take the daemon down.
        return None
