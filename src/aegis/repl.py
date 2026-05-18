from __future__ import annotations

import asyncio
from collections.abc import Callable

from rich.console import Console

from aegis.drivers.base import HarnessSession
from aegis.render import Renderer

_QUIT = {"exit", "quit", "/exit", "/quit"}


async def _drain_turn(session: HarnessSession, renderer: Renderer) -> None:
    async for ev in session.events():
        renderer.render(ev)


async def run_repl(
    session: HarnessSession,
    console: Console,
    input_fn: Callable[[str], str] = input,
    initial_prompt: str | None = None,
) -> None:
    renderer = Renderer(console)
    await session.start()
    try:
        if initial_prompt:
            await session.send(initial_prompt)
            await _drain_turn(session, renderer)
        while True:
            try:
                line = (await asyncio.to_thread(input_fn, "aegis> ")).strip()
            except (EOFError, KeyboardInterrupt):
                break
            if not line:
                continue
            if line.lower() in _QUIT:
                break
            await session.send(line)
            try:
                await _drain_turn(session, renderer)
            except KeyboardInterrupt:
                console.print("[dim]^C - turn interrupted[/dim]")
    finally:
        await session.close()
