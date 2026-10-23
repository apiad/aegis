"""The hint block that rides on every send.

It uses the notice header, so ``split_notices`` folds it into its own row
under the person's message with no change to the fold. Each item is one
``name: description`` entry, the description cut at 120 characters; a kind
with no items leaves its line out, and with none at all there is no block.
"""

from __future__ import annotations

from aegis.knowledge.search import Hit
from aegis.transcript.entries import NOTICE_HEADER

HEADER = f"{NOTICE_HEADER}knowledge · may be relevant"
DESCRIPTION_CHARS = 120
FOOTER = "read with skill_read / memory_read · search with skill_search / memory_search"


def _line(label: str, hits: list[Hit]) -> str:
    return f"{label}: " + " · ".join(
        f"{h.name}: {h.description[:DESCRIPTION_CHARS]}" for h in hits
    )


def block(skills: list[Hit], memories: list[Hit]) -> str:
    if not skills and not memories:
        return ""
    lines = [HEADER]
    if skills:
        lines.append(_line("skills", skills))
    if memories:
        lines.append(_line("memories", memories))
    lines.append(FOOTER)
    return "\n".join(lines)
