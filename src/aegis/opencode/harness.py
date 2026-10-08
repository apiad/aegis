"""OpenCode behind the harness interface: ``opencode serve`` over HTTP."""

from __future__ import annotations

from pathlib import Path

from ..claude.control import Catalog
from ..harness import Launch
from .process import OpenCodeProcess, probe
from .stream import LABEL


class OpenCode:
    name = "opencode"
    src = "opencode"
    label = LABEL
    tool_prefix = "aegis_"

    def __init__(self, bin: str) -> None:
        self.bin = bin

    def process(self, launch: Launch) -> OpenCodeProcess:
        return OpenCodeProcess(self.bin, launch)

    async def probe(self, spec, stderr_path: Path) -> Catalog:
        return await probe(self.bin, spec.cwd, stderr_path)
