"""The REPOS section's rows. Pure — no Textual import.

One row per repo: a mark saying whether you are in there, the repo, its
branch, what is uncommitted, and who else is writing. Degradation is by
tier, the same mechanism the rest of the sidebar uses, with one rule of its
own: **the narrowest tier truncates rather than being dropped.** ``fit_rows``
answers "no tier fits" by omitting the segment, and a repo that vanished
because its name was long reads exactly like a repo nobody has touched.
"""
from __future__ import annotations

from collections.abc import Sequence

from rich.cells import cell_len
from rich.text import Text

from aegis.repos.models import RepoView
from aegis.tui.fit import Segment, fit_rows, truncate_cells

# The mark is always space-separated from the name: ● is East Asian
# Ambiguous, Rich measures it as one cell and terminals draw it as two, so a
# glued neighbour overlaps it. Same tax the plan dock pays on every circle.
MINE, THEIRS = "●", "·"

# Longest repo name we will align a column to. Past this the name eats the
# row it is supposed to be labelling.
_NAME_CAP = 16


def _mark_style(v: RepoView, palette) -> str:
    if v.shared:
        # More than one live writer — the collision the claims registry
        # exists to prevent, and the only thing in this section that wants
        # to be noticed.
        return palette.working
    return palette.ok if v.mine else palette.muted


def _branch_cell(v: RepoView) -> tuple[str, bool]:
    """``(text, is_alarming)``.

    An operation in flight or a detached HEAD *replaces* the branch rather
    than annotating it: on a repo mid-rebase the branch name is the least
    true thing you could print there.
    """
    if v.host != "local":
        return "", False
    if v.state.op:
        return f"({v.state.op})", True
    if v.state.detached:
        return "(detached)", True
    return v.state.branch, False


def _counts(v: RepoView) -> str:
    """Dirty, ahead, behind — zeros omitted.

    A clean repo shows nothing at all. Rendering `~0 ↑0` would make every
    row look like it had news, which costs the rows that actually do.
    """
    if v.host != "local":
        return ""
    parts = []
    if v.state.dirty:
        parts.append(f"~{v.state.dirty}")
    if v.state.ahead:
        parts.append(f"↑{v.state.ahead}")
    if v.state.behind:
        parts.append(f"↓{v.state.behind}")
    return " ".join(parts)


def _peers(v: RepoView) -> tuple[str, ...]:
    """Writers worth naming. On your own row the mark already says "you"."""
    return v.writers[1:] if v.mine else v.writers


def row_tiers(v: RepoView, palette, namew: int, width: int) -> tuple[str, ...]:
    """This row, widest form first. The last entry always fits ``width``."""
    style = _mark_style(v, palette)
    dim = palette.muted
    mark = f"[{style}]{MINE if v.mine else THEIRS}[/]"

    label = v.label
    name = label.ljust(namew) if cell_len(label) < namew else label
    name_style = dim if v.state.stale else palette.ink or ""
    named = f"[{name_style}]{name}[/]" if name_style else name

    branch, alarming = _branch_cell(v)
    b_style = palette.err if alarming else dim
    branch_part = f" [{b_style}]{branch}[/]" if branch else ""

    counts = _counts(v)
    counts_part = f" [{dim if v.state.stale else palette.accent}]{counts}[/]" \
        if counts else ""

    peers = _peers(v)
    peers_part = f"  [{dim}]{' '.join(peers)}[/]" if peers else ""
    plus_part = f"  [{dim}]+{len(peers)}[/]" if peers else ""

    base = f"{mark} {named}"
    tiers = [
        f"{base}{branch_part}{counts_part}{peers_part}",
        f"{base}{branch_part}{counts_part}{plus_part}",
        f"{base}{branch_part}{counts_part}",
        f"{base}{branch_part}",
        base,
    ]
    # The floor: the mark, a space, and as much of the name as fits. Two
    # cells go to the mark and its separator, and `width <= 0` means the
    # widget has not been laid out yet — render it whole rather than
    # truncating to nothing.
    if width > 0:
        floor_name = truncate_cells(label, max(width - 2, 1))
        tiers.append(f"[{style}]{MINE if v.mine else THEIRS}[/] "
                     f"[{name_style}]{floor_name}[/]" if name_style
                     else f"[{style}]{MINE if v.mine else THEIRS}[/] "
                          f"{floor_name}")
    # Consecutive duplicates (no peers, no counts, no branch) buy nothing.
    out: list[str] = []
    for t in tiers:
        if not out or t != out[-1]:
            out.append(t)
    return tuple(out)


def render_repos(views: Sequence[RepoView], palette,
                 width: int) -> Text | None:
    """The rows of the REPOS section, or ``None`` when nothing is on the
    list. The heading is the caller's — composed in ``tui/sidebar.py``
    alongside every other section's."""
    if not views:
        return None
    namew = min(max(cell_len(v.label) for v in views), _NAME_CAP)
    segs = [Segment(key=str(v.state.root),
                    tiers=row_tiers(v, palette, namew, width),
                    priority=0)
            for v in views]
    rows = fit_rows(segs, width)
    if not rows:
        return None
    out = Text()
    for i, r in enumerate(rows):
        if i:
            out.append("\n")
        out.append_text(Text.from_markup(r))
    return out
