"""A session's canvas: which docked panes sit where, in which window.

A layout is ``{"windows": [{"n": 1, "columns": [{"w": 0.6, "panes":
[{"id": "transcript", "h": 1.0}]}]}]}``. Window 1 holds the transcript, once.
A window's ``n`` never changes: a new one takes one more than the highest in
use, and an emptied one is dropped without renumbering, so a browser tab open
on window 3 never starts showing another. ``w`` and ``h`` are shares that add
up to 1 per window and per column.

These are pure functions over plain dicts, so the fold, the operations and the
agent's tools share one rule. Each returns a new layout and never changes its
argument; each raises ``LayoutError`` with a message meant for whoever asked.
"""

from __future__ import annotations

import copy
import hashlib
import re
from collections.abc import Callable

TRANSCRIPT = "transcript"
# A fifth column or a fourth stacked pane is too narrow to read on a 1440 px
# screen; a fifth window has no monitor to go to.
MAX_WINDOWS = 4
MAX_COLUMNS = 4
MAX_PANES = 3

DEFAULT: dict = {
    "windows": [
        {"n": 1, "columns": [{"w": 1.0, "panes": [{"id": TRANSCRIPT, "h": 1.0}]}]}
    ]
}

_WHERE = re.compile(
    r"^(?:(?P<edge>left|right)|window:(?P<win>new|\d+)"
    r"|(?P<side>left|right|above|below|swap):(?P<to>.+))$"
)


class LayoutError(ValueError):
    """A layout or a placement aegis refuses; the message says which rule."""


def file_key(source: str) -> str:
    """The key every version of one sent file shares: its source path, hashed."""
    return "f-" + hashlib.sha256(source.encode()).hexdigest()[:12]


def ids(layout: dict | None) -> list[str]:
    """Every pane's id, window by window, column by column, top to bottom."""
    lay = layout or DEFAULT
    return [p["id"] for w in lay["windows"] for c in w["columns"] for p in c["panes"]]


def window_of(layout: dict | None, pane: str) -> int | None:
    for w in (layout or DEFAULT)["windows"]:
        if any(p["id"] == pane for c in w["columns"] for p in c["panes"]):
            return w["n"]
    return None


def _shares(xs: list[dict], key: str) -> None:
    """Make ``xs[i][key]`` shares of 1; a missing or non-positive one is an
    equal share."""
    n = len(xs)
    raw = []
    for x in xs:
        try:
            v = float(x.get(key) or 0)
        except (TypeError, ValueError):
            v = 0.0
        raw.append(v if v > 0 else 1 / n)
    total = sum(raw)
    for x, v in zip(xs, raw):
        x[key] = round(v / total, 4)


def normalise(layout: dict, dockable: Callable[[str], bool]) -> dict:
    """``layout`` checked against every rule and with its shares fitted.
    ``dockable(id)`` says whether an id other than the transcript may dock."""
    windows = layout.get("windows") if isinstance(layout, dict) else None
    if not isinstance(windows, list) or not windows:
        raise LayoutError("a layout is a list of windows, and the list is empty")
    seen: set[str] = set()
    numbers: set[int] = set()
    out: list[dict] = []
    for w in windows:
        n = w.get("n") if isinstance(w, dict) else None
        if not isinstance(n, int) or n < 1:
            raise LayoutError("each window has a number n, 1 or more")
        if n in numbers:
            raise LayoutError(f"window {n} twice")
        numbers.add(n)
        cols = []
        for c in w.get("columns") or []:
            panes = []
            for p in c.get("panes") or []:
                pid = str(p.get("id") or "")
                if pid in seen:
                    raise LayoutError(f"{pid} appears twice")
                if pid == TRANSCRIPT and n != 1:
                    raise LayoutError("the transcript stays in window 1")
                if pid != TRANSCRIPT and not dockable(pid):
                    raise LayoutError(
                        f"no pane {pid} to dock: it is not a live page or a file "
                        "sent in this session"
                    )
                seen.add(pid)
                panes.append({"id": pid, "h": p.get("h")})
            if len(panes) > MAX_PANES:
                raise LayoutError(f"a column holds at most {MAX_PANES} panes")
            if panes:
                _shares(panes, "h")
                cols.append({"w": c.get("w"), "panes": panes})
        if len(cols) > MAX_COLUMNS:
            raise LayoutError(f"a window holds at most {MAX_COLUMNS} columns")
        if cols:
            _shares(cols, "w")
            out.append({"n": n, "columns": cols})
    if TRANSCRIPT not in seen:
        raise LayoutError("the transcript must be in the layout, in window 1")
    if len(out) > MAX_WINDOWS:
        raise LayoutError(f"a canvas spans at most {MAX_WINDOWS} windows")
    out.sort(key=lambda w: w["n"])
    return {"windows": out}


def _without(lay: dict, pane: str) -> None:
    for w in lay["windows"]:
        for c in w["columns"]:
            c["panes"] = [p for p in c["panes"] if p["id"] != pane]
        w["columns"] = [c for c in w["columns"] if c["panes"]]
    lay["windows"] = [w for w in lay["windows"] if w["columns"] or w["n"] == 1]


def _find(lay: dict, pane: str) -> tuple[dict, int, int]:
    for w in lay["windows"]:
        for ci, c in enumerate(w["columns"]):
            for pi, p in enumerate(c["panes"]):
                if p["id"] == pane:
                    return w, ci, pi
    raise LayoutError(
        f"no pane {pane} in the canvas; its panes are {', '.join(ids(lay))}"
    )


def place(
    layout: dict | None, pane: str, where: str, dockable: Callable[[str], bool]
) -> dict:
    """``layout`` with ``pane`` put ``where``; a pane already in it moves."""
    m = _WHERE.match(where or "")
    if not m:
        raise LayoutError(
            "dock is left, right, window:<n>, window:new, or left:, right:, "
            "above:, below: or swap: and a pane id"
        )
    lay = copy.deepcopy(layout or DEFAULT)
    if m["side"] == "swap":
        w1, c1, p1 = _find(lay, pane)
        w2, c2, p2 = _find(lay, m["to"])
        a, b = w1["columns"][c1]["panes"][p1], w2["columns"][c2]["panes"][p2]
        a["id"], b["id"] = b["id"], a["id"]
        return normalise(lay, lambda i: i == pane or dockable(i))
    if m["side"] and m["to"] == pane:
        raise LayoutError("a pane cannot dock next to itself")
    if m["side"]:
        _find(lay, m["to"])  # before the move, so the error lists what exists
    _without(lay, pane)
    new_col = {"w": 0, "panes": [{"id": pane, "h": 0}]}
    if m["edge"]:
        cols = lay["windows"][0]["columns"]
        cols.insert(0 if m["edge"] == "left" else len(cols), new_col)
    elif m["win"]:
        if m["win"] == "new":
            n = max(w["n"] for w in lay["windows"]) + 1
            lay["windows"].append({"n": n, "columns": [new_col]})
        else:
            w = next((w for w in lay["windows"] if w["n"] == int(m["win"])), None)
            if w is None:
                raise LayoutError(
                    f"no window {m['win']}; the windows are "
                    + ", ".join(str(w["n"]) for w in lay["windows"])
                )
            w["columns"].append(new_col)
    else:
        w, ci, pi = _find(lay, m["to"])
        if m["side"] in ("left", "right"):
            w["columns"].insert(ci if m["side"] == "left" else ci + 1, new_col)
        else:
            w["columns"][ci]["panes"].insert(
                pi if m["side"] == "above" else pi + 1, {"id": pane, "h": 0}
            )
    return normalise(lay, lambda i: i == pane or dockable(i))


def trim(layout: dict, gone: str) -> dict:
    """``layout`` without ``gone``, emptied columns and windows dropped."""
    if gone not in ids(layout):
        return layout
    lay = copy.deepcopy(layout)
    _without(lay, gone)
    return normalise(lay, lambda _: True)


def resize(
    layout: dict | None,
    n: int,
    widths: list[float] | None,
    heights: list[list[float]] | None,
) -> dict:
    """Window ``n``'s column widths and per-column pane heights, as shares."""
    lay = copy.deepcopy(layout or DEFAULT)
    w = next((w for w in lay["windows"] if w["n"] == n), None)
    if w is None:
        raise LayoutError(f"no window {n}")
    cols = w["columns"]
    if widths is not None:
        if len(widths) != len(cols):
            raise LayoutError(f"window {n} has {len(cols)} columns")
        for c, v in zip(cols, widths):
            c["w"] = v
    if heights is not None:
        if len(heights) != len(cols) or any(
            len(h) != len(c["panes"]) for h, c in zip(heights, cols)
        ):
            raise LayoutError(f"window {n}'s heights do not match its panes")
        for c, hs in zip(cols, heights):
            for p, v in zip(c["panes"], hs):
                p["h"] = v
    return normalise(lay, lambda _: True)
