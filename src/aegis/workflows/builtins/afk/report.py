"""The report a worker must end with, and its failure modes.

A worker that cannot report has not demonstrably done anything, so every
parse failure here is a `failed` card with the raw message quoted — never a
silently accepted partial result.
"""

from __future__ import annotations

import io
import re
from dataclasses import dataclass

from ruamel.yaml import YAML, YAMLError

VALID_STATUS = frozenset({"needs-review", "blocked", "failed"})
BLOCK_RE = re.compile(r"```aegis-report[ \t]*\n(.*?)\n```", re.S)
_GATE_RE = re.compile(r"^(?P<cmd>.+?)\s*->\s*(?P<exit>-?\d+)$")


class ReportError(ValueError):
    """The final message did not carry exactly one usable report."""


@dataclass(frozen=True)
class Report:
    status: str
    summary: str
    gate_cmd: str
    gate_exit: int | None = None
    artifacts: tuple[str, ...] = ()
    changed: int | None = None
    judgement: tuple[str, ...] = ()
    notes: str = ""


def _as_tuple(value) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, (list, tuple)):
        return tuple(str(v) for v in value)
    return (str(value),)


def parse_report(text: str) -> Report:
    blocks = BLOCK_RE.findall(text or "")
    if not blocks:
        raise ReportError("no aegis-report block in the worker's final message")
    if len(blocks) > 1:
        raise ReportError(
            f"{len(blocks)} aegis-report blocks; which one is the report is a guess"
        )
    try:
        data = YAML(typ="safe").load(io.StringIO(blocks[0]))
    except YAMLError as e:
        raise ReportError(f"aegis-report block is not valid YAML: {e}") from e
    if not isinstance(data, dict):
        raise ReportError("aegis-report block is not a mapping")

    status = str(data.get("status") or "").strip()
    if status not in VALID_STATUS:
        raise ReportError(f"status {status!r} is not one of {sorted(VALID_STATUS)}")
    summary = str(data.get("summary") or "").strip()
    if not summary:
        raise ReportError("report has no summary")

    raw_gate = str(data.get("gate") or "").strip()
    if not raw_gate:
        raise ReportError("report has no gate line")
    if raw_gate == "none":
        gate_cmd, gate_exit = "none", None
    else:
        m = _GATE_RE.match(raw_gate)
        if not m:
            raise ReportError(
                f"gate line {raw_gate!r} is not '<command> -> <exit code>' or 'none'"
            )
        gate_cmd, gate_exit = m.group("cmd").strip(), int(m.group("exit"))

    changed = data.get("changed")
    return Report(
        status=status,
        summary=summary,
        gate_cmd=gate_cmd,
        gate_exit=gate_exit,
        artifacts=_as_tuple(data.get("artifacts")),
        changed=int(changed) if changed is not None else None,
        judgement=_as_tuple(data.get("judgement")),
        notes=str(data.get("notes") or "").strip(),
    )
