"""Cursor behind the harness interface: ``cursor-agent acp`` over stdio."""

from __future__ import annotations

from pathlib import Path

from ..claude.control import Catalog
from ..harness import Launch
from .config import SERVER
from .process import CursorProcess, probe
from .stream import LABEL


class Cursor:
    name = "cursor"
    src = "cursor"
    label = LABEL
    steers_mid_turn = False  # the process queues mid-turn input itself
    steers_notices = False
    # Cursor shows MCP tools under their bare name (measured: ``meta``), so
    # the fold strips aegis's prefix from what it prefixes.
    tool_prefix = f"mcp__{SERVER}__"

    def __init__(self, bin: str) -> None:
        self.bin = bin

    def process(self, launch: Launch) -> CursorProcess:
        return CursorProcess(self.bin, launch)

    async def probe(self, spec, stderr_path: Path) -> Catalog:
        return await probe(self.bin, spec.cwd, stderr_path, spec.model)
