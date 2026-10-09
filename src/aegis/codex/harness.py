"""Codex behind the harness interface: ``codex app-server`` over stdio."""

from __future__ import annotations

from pathlib import Path

from ..claude.control import Catalog
from ..harness import Launch
from .process import CodexProcess, probe
from .stream import LABEL


class Codex:
    name = "codex"
    src = "codex"
    label = LABEL
    tool_prefix = "mcp__aegis__"

    def __init__(self, bin: str) -> None:
        self.bin = bin

    def process(self, launch: Launch) -> CodexProcess:
        return CodexProcess(self.bin, launch)

    async def probe(self, spec, stderr_path: Path) -> Catalog:
        return await probe(self.bin, spec.cwd, stderr_path, spec.model)
