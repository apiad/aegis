"""The hint block that rides on every send.

It uses the notice header, so ``split_notices`` folds it into its own row
under the person's message with no change to the fold. Each item is one
``name: description`` entry, the description cut at 120 characters; a kind
with no items leaves its line out, and with none at all there is no block.
The last line names the tools that read and search the kinds shown.
"""

from __future__ import annotations

from .search import Hit
from ..transcript.entries import NOTICE_HEADER

HEADER = f"{NOTICE_HEADER}knowledge · may be relevant"
DESCRIPTION_CHARS = 120
# aegis's own wakes: a notice, a monitor that ended, a page's answer. They
# carry no new request, so they get no hint; a message from the person,
# another agent, a queue task, a schedule or a workflow does.
WAKES = ("> from aegis:", "> from monitor:", "> from artifact:")


def wanted(text: str) -> bool:
    """Whether a send of ``text`` gets a hint."""
    return not text.startswith(WAKES)


def _line(label: str, hits: list[Hit]) -> str:
    return f"{label}: " + " · ".join(
        f"{h.name}: {h.description[:DESCRIPTION_CHARS]}" for h in hits
    )


def block(skills: list[Hit], memories: list[Hit]) -> str:
    if not skills and not memories:
        return ""
    lines = []
    if skills:
        lines.append(_line("skills", skills))
    if memories:
        lines.append(_line("memories", memories))
    kinds = [k for k, hits in (("skill", skills), ("memory", memories)) if hits]
    lines.append(
        "read with "
        + " / ".join(f"{k}_read" for k in kinds)
        + " · search with "
        + " / ".join(f"{k}_search" for k in kinds)
    )
    # A blank line between the lines, so the transcript's Markdown shows each
    # on its own instead of running them into one paragraph.
    return HEADER + "\n" + "\n\n".join(lines)
