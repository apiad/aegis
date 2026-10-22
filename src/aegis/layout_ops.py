"""The canvas's operations (layout.py): a person's moves, and an agent's read
and set of its own canvas.

A person's operation names what they did (dock this there, undock that, these
widths) and the server applies it to the layout as it stands, so a drop never
overwrites what the agent docked while the pointer was moving. An agent sets
the whole layout, or docks as it sends (file.send, artifact.send, workflow.arm).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, Field

from . import layout as canvas
from .ops import OpError

if TYPE_CHECKING:
    from .app import App


class _Strict(BaseModel):
    model_config = {"extra": "forbid"}


class LayoutRef(_Strict):
    log_id: str


class LayoutDock(LayoutRef):
    pane: str
    where: str


class LayoutUndock(LayoutRef):
    pane: str


class LayoutResize(LayoutRef):
    n: int = 1
    widths: list[float] | None = None
    heights: list[list[float]] | None = None


class LayoutSet(_Strict):
    windows: list[dict[str, Any]] = Field(
        description="Every window, as layout_read answers it: "
        '[{"n": 1, "columns": [{"w": 0.6, "panes": [{"id": "transcript", "h": 1}]}]}]. '
        "Widths and heights are optional shares. The transcript stays in window 1."
    )


def _bad(e: canvas.LayoutError) -> OpError:
    return OpError("bad_params", str(e))


def described(s) -> dict:
    """``s``'s layout with each pane's kind and title, for the agent."""
    fold = s.fold()

    def pane(p: dict) -> dict:
        pid = p["id"]
        if pid == canvas.TRANSCRIPT:
            kind, title = "transcript", "transcript"
        elif pid in fold.file_keys:
            kind, title = "file", fold.file_keys[pid]["name"]
        elif pid.startswith("b-"):
            brs = getattr(s._host, "browsers", None)
            b = brs.open.get(pid) if brs is not None else None
            kind, title = "browser", (b.meta["name"] if b else pid)
        elif pid.startswith("wf-"):
            # Titled as the person sees it: the definition's title, not the run.
            wfs = getattr(s._host, "workflows", None)
            d = wfs.definition(pid[3:]) if wfs is not None else None
            return {
                **p,
                "kind": "workflow",
                "run": pid[3:],
                "title": d.title if d else pid,
            }
        else:
            e = fold.entry(pid)
            kind, title = "artifact", (e["title"] if e else pid)
        return {**p, "kind": kind, "title": title}

    return {
        "windows": [
            {
                **w,
                "columns": [
                    {**c, "panes": [pane(p) for p in c["panes"]]} for c in w["columns"]
                ],
            }
            for w in s.layout["windows"]
        ]
    }


def register_layout_ops(app: App) -> None:
    r, reg = app.registry, app.sessions

    def own(caller):
        if not caller.is_agent or caller.log_id not in reg.sessions:
            raise OpError("agents_only", "only an agent's session can do this")
        return reg.sessions[caller.log_id]

    # -- the person ------------------------------------------------------------
    @r.op("layout.dock", LayoutDock, owned="log_id")
    async def dock(p: LayoutDock, caller):
        s = reg.open(p.log_id)
        # place() lets the pane it places through, so an agent's dock= can
        # place a page before its send lands; a person docks what exists.
        if p.pane != canvas.TRANSCRIPT and not s.dockable(p.pane):
            raise OpError(
                "bad_params",
                f"no pane {p.pane} to dock: it is not a live page, a file "
                "or a workflow of this session",
            )
        try:
            new = canvas.place(s.layout, p.pane, p.where, s.dockable)
        except canvas.LayoutError as e:
            raise _bad(e) from e
        return s.set_layout(new, "person")

    @r.op("layout.undock", LayoutUndock, owned="log_id")
    async def undock(p: LayoutUndock, caller):
        s = reg.open(p.log_id)
        if p.pane == canvas.TRANSCRIPT:
            raise OpError("bad_params", "the transcript cannot be undocked")
        if p.pane not in canvas.ids(s.layout):
            return s.layout
        return s.set_layout(canvas.trim(s.layout, p.pane), "person")

    @r.op("layout.resize", LayoutResize, owned="log_id")
    async def resize(p: LayoutResize, caller):
        s = reg.open(p.log_id)
        try:
            new = canvas.resize(s.layout, p.n, p.widths, p.heights)
        except canvas.LayoutError as e:
            raise _bad(e) from e
        return s.set_layout(new, "person")

    # -- the agent -------------------------------------------------------------
    @r.op("layout.read", agent=True, read=True)
    async def read(p, caller):
        """Your canvas as it stands: the windows the person sees, each a list of
        columns with their width shares, each column a stack of panes with
        their id, height share, kind (transcript, artifact, file or workflow)
        and title; a workflow's pane also names its run.
        Window 1 holds the transcript; other windows are browser tabs the person
        opens. Wakes nobody."""
        return described(own(caller))

    @r.op("layout.set", LayoutSet, agent=True, read=True)
    async def set_(p: LayoutSet, caller):
        """Replace your canvas: which pages and files sit beside the transcript,
        where, and in which window. Pass every window, as layout_read answers;
        a pane is a live page's id, a sent file's key, a workflow's
        wf-<run> or a browser's id. Limits: 4 windows, 4 columns in a window, 3 panes in a
        column; the transcript stays in window 1. You cannot open a window's
        browser tab: the person's card offers it. To place one item as you
        send it, use dock= on artifact_send, file_send or workflow_arm
        instead."""
        s = own(caller)
        try:
            new = canvas.normalise({"windows": p.windows}, s.dockable)
        except canvas.LayoutError as e:
            raise _bad(e) from e
        s.set_layout(new, "agent")
        return described(s)
