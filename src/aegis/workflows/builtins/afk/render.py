"""The pinned comment's three sections.

Rewritten in place each tick rather than appended, so a card carries one
current statement instead of forty stale ones. Each section is owned by a
different part of the system, which is why they are separate here.
"""

from __future__ import annotations

import re

from aegis.workflows.builtins.afk.report import Report

PLAN_PLACEHOLDER = "_(no plan reported)_"


def card_comment(*, coordinator: str, plan: str, result: str, marker: str) -> str:
    parts = [f"### Coordinator\n\n{coordinator.strip()}"]
    parts.append(f"### Plan\n\n{plan.strip() or PLAN_PLACEHOLDER}")
    if result.strip():
        parts.append(f"### Result\n\n{result.strip()}")
    parts.append(marker)
    return "\n\n".join(parts)


_SECTION_RE_TEMPLATE = r"(?ms)^### {name}\n(.*?)(?=^### |\Z)"


def replace_section(body: str, section: str, new_body: str) -> str:
    """Swap one ``### Section`` in place, leaving its siblings alone.

    The progress schedule owns Plan and nothing else. Rewriting the whole
    comment from the progress tick would erase the Coordinator and Result
    sections, which it has no way to reconstruct.
    """
    pattern = re.compile(_SECTION_RE_TEMPLATE.format(name=re.escape(section)))
    replacement = f"### {section}\n\n{new_body.strip()}\n\n"
    if pattern.search(body or ""):
        return pattern.sub(lambda _m: replacement, body, count=1)
    return (body or "").rstrip() + "\n\n" + replacement


def result_section(
    report: Report | None,
    *,
    gate_cmd: str,
    measured_exit: int | None,
    gate_output: str = "",
    review: str = "",
) -> str:
    if report is None:
        return (
            "The worker's final message carried no usable report, so nothing "
            "it did can be confirmed.\n\n"
            f"```\n{gate_output.strip()[:2000]}\n```"
        )
    lines = [report.summary, ""]
    if gate_cmd == "none":
        lines.append(
            "**Gate:** this repo declares no gate target — nothing was verified."
        )
    else:
        claimed = report.gate_exit
        lines.append(
            f"**Gate:** `{gate_cmd}` exited {measured_exit} when the "
            f"coordinator ran it (the worker reported {claimed})."
        )
        if (
            claimed is not None
            and measured_exit is not None
            and claimed != measured_exit
        ):
            lines.append(
                "> The worker and the coordinator disagree about the gate. "
                "The coordinator's run is what counts."
            )
    if measured_exit not in (0, None) and gate_output.strip():
        lines += ["", "```", gate_output.strip()[:2000], "```"]
    if report.artifacts:
        lines += ["", "**Artifacts:**"] + [f"- {a}" for a in report.artifacts]
    if report.judgement:
        lines += ["", "**Judgement calls:**"] + [f"- {j}" for j in report.judgement]
    if report.notes:
        lines += ["", report.notes]
    if review:
        lines += ["", f"**Review:** {review}"]
    return "\n".join(lines)
