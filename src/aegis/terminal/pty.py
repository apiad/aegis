"""Thin async wrapper around ptyprocess for live terminals."""
from __future__ import annotations

import asyncio
from pathlib import Path

from ptyprocess import PtyProcessUnicode


class AsyncPty:
    """Async-friendly wrapper. Reads from the PTY in a background thread
    via run_in_executor; writes are non-blocking via the underlying fd."""

    def __init__(self, proc: PtyProcessUnicode) -> None:
        self._proc = proc

    @classmethod
    def spawn(
        cls,
        argv: list[str],
        cwd: str | Path | None = None,
        env: dict[str, str] | None = None,
        dimensions: tuple[int, int] = (24, 80),
    ) -> "AsyncPty":
        proc = PtyProcessUnicode.spawn(
            argv,
            cwd=str(cwd) if cwd else None,
            env=env,
            dimensions=dimensions,
        )
        return cls(proc)

    @property
    def pid(self) -> int:
        return self._proc.pid

    @property
    def is_alive(self) -> bool:
        return self._proc.isalive()

    async def read(self, n: int = 4096) -> bytes:
        loop = asyncio.get_running_loop()
        try:
            chunk = await loop.run_in_executor(None, self._proc.read, n)
            return chunk.encode("utf-8", errors="replace") if isinstance(chunk, str) else chunk
        except EOFError:
            return b""

    def write(self, data: bytes) -> None:
        self._proc.write(data.decode("utf-8", errors="replace"))

    def close(self, force: bool = False) -> None:
        try:
            if force:
                self._proc.kill(9)
            else:
                self._proc.terminate(force=False)
        except Exception:
            pass
