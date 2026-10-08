"""What aegis asks of a live ``claude`` through control requests, and the
catalog its ``initialize`` answer carries.

Measured on Claude Code 2.1.283 (spec 2026-10-07): ``set_model`` and
``set_permission_mode`` answer success or an error. ``apply_flag_settings``
answers success even for a level it ignores, so ``set_effort`` reads the effort
back with ``get_settings`` and fails unless it applied. ``initialize`` makes no
API call and lists every command the cwd has: Claude's own, skills, plugins and
``.claude/commands``.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path


from .process import PERMISSION_MODE, ClaudeProcess, ControlError, build_argv

__all__ = ["Catalog", "ControlError", "Model", "catalog", "from_initialize", "probe"]

DESC_MAX = 140
_SOURCE = re.compile(r"\s*\(([^()]+)\)\s*$")


@dataclass(frozen=True)
class Model:
    value: str
    resolved: str
    label: str
    doc: str
    efforts: tuple[str, ...]
    # The context window, when the harness's catalog names one (OpenCode).
    window: int | None = None

    def wire(self) -> dict:
        return {
            "value": self.value,
            "resolved": self.resolved,
            "label": self.label,
            "doc": self.doc,
            "efforts": list(self.efforts),
        }


def _doc(text: str) -> str:
    """The first sentence, without the trailing ``(source)``, at most DESC_MAX."""
    text = _SOURCE.sub("", text or "").strip()
    first = re.split(r"(?<=\.)\s|\n", text, maxsplit=1)[0].strip()
    return first if len(first) <= DESC_MAX else first[: DESC_MAX - 1] + "…"


def _source(c: dict) -> str:
    if c.get("builtin"):
        return "claude"
    m = _SOURCE.search(c.get("description") or "")
    return m.group(1) if m else "skill"


@dataclass(frozen=True)
class Catalog:
    commands: tuple[dict, ...]
    models: tuple[Model, ...]

    def has(self, name: str) -> bool:
        return any(c["name"] == name for c in self.commands)

    def model(self, name: str) -> Model | None:
        return next((m for m in self.models if name in (m.value, m.resolved)), None)

    def wire_commands(self, shadowed: Iterable[str]) -> list[dict]:
        skip = set(shadowed)
        return [c for c in self.commands if c["name"] not in skip]

    def wire_models(self) -> list[dict]:
        return [m.wire() for m in self.models]


def from_initialize(body: dict) -> Catalog:
    commands = tuple(
        {
            "name": str(c["name"]),
            "hint": str(c.get("argumentHint") or ""),
            "doc": _doc(str(c.get("description") or "")),
            "source": _source(c),
        }
        for c in body.get("commands") or []
        if isinstance(c, dict) and c.get("name")
    )
    models = tuple(
        Model(
            value=str(m["value"]),
            resolved=str(m.get("resolvedModel") or m["value"]),
            label=str(m.get("displayName") or m["value"]),
            doc=_doc(str(m.get("description") or "")),
            efforts=tuple(m.get("supportedEffortLevels") or ()),
        )
        for m in body.get("models") or []
        if isinstance(m, dict) and m.get("value") and not m.get("disabled")
    )
    return Catalog(commands, models)


async def catalog(proc: ClaudeProcess) -> Catalog:
    return from_initialize(await proc.request("initialize"))


async def probe(
    claude_bin: str,
    model: str,
    effort: str,
    permission: str,
    cwd: Path,
    stderr_path: Path,
) -> Catalog:
    """A catalog for a cwd with no live process: start ``claude``, ask, end it.
    About 0.5 s and no tokens; ``initialize`` makes no API call."""
    proc = ClaudeProcess(
        build_argv(claude_bin, model, effort, permission),
        cwd,
        stderr_path,
        lambda line: None,
        lambda code, tail: None,
    )
    await proc.start()
    try:
        return await catalog(proc)
    finally:
        await proc.terminate()


async def set_model(proc: ClaudeProcess, value: str) -> None:
    await proc.request("set_model", model=value)


async def set_effort(proc: ClaudeProcess, level: str) -> None:
    await proc.request("apply_flag_settings", settings={"effortLevel": level})
    applied = (await proc.request("get_settings")).get("applied") or {}
    if applied.get("effort") != level:
        raise ControlError(
            f"claude did not apply effort {level}; it reports {applied.get('effort')}"
        )


async def set_permission(proc: ClaudeProcess, permission: str) -> None:
    await proc.request(
        "set_permission_mode", mode=PERMISSION_MODE.get(permission, permission)
    )
