"""A stand-in for ``lovelaice-acp``: an ACP v1 agent replaying chunks.

This is the only path where aegis renders a reply chunk by chunk today,
so it is where per-delta rendering cost shows up. Steps without a
``chunk`` (claude-shaped fallbacks) are skipped, so an unknown prompt ends
its turn with no text rather than an error.
"""

from __future__ import annotations

import asyncio
import json
import os
import time
from pathlib import Path

import acp
from acp.schema import AgentMessageChunk, TextContentBlock

from aegis.bench.frames import marker
from aegis.bench.records import MarkerSeq, Recorder
from aegis.bench.script import route


class BenchAgent(acp.Agent):
    def __init__(self) -> None:
        emit = Path(os.environ["AEGIS_BENCH_EMIT"])
        self._script = json.loads(Path(os.environ["AEGIS_BENCH_SCRIPT"]).read_text())
        self._emit = Recorder(emit)
        self._seq = MarkerSeq(emit.parent / "marker.seq")
        self._pid = os.getpid()
        self._emit.write({"k": "argv", "pid": self._pid, "partial": True})

    def on_connect(self, conn) -> None:
        self._conn = conn

    async def initialize(
        self, protocol_version, client_capabilities=None, client_info=None, **kw
    ):
        return acp.InitializeResponse(
            protocolVersion=1,
            agentCapabilities={"loadSession": False, "mcpCapabilities": {"http": True}},
            agentInfo={"name": "aegis-bench", "version": "1"},
        )

    async def new_session(
        self, cwd, mcp_servers=None, additional_directories=None, **kw
    ):
        return acp.NewSessionResponse(sessionId=f"bench-{self._pid}")

    async def prompt(self, session_id, prompt, message_id=None, **kw):
        text = " ".join(getattr(b, "text", "") or "" for b in prompt)
        self._emit.write(
            {
                "k": "prompt",
                "pid": self._pid,
                "t_ns": time.monotonic_ns(),
                "word": (text.split() or [""])[0].lower(),
            }
        )
        speed = float(self._script.get("speed") or 1.0)
        n = 0
        for step in route(self._script, text):
            if "chunk" not in step:
                continue
            delay = float(step.get("dt_ms", 0.0)) / speed
            if delay > 0:
                await asyncio.sleep(delay / 1000.0)
            chunk, m = step["chunk"], None
            if step.get("mark", True):
                m = marker(self._seq.next())
                chunk = f"{chunk}{m} "
            t = time.monotonic_ns()
            await self._conn.session_update(
                session_id=session_id,
                update=AgentMessageChunk(
                    content=TextContentBlock(text=chunk, type="text"),
                    sessionUpdate="agent_message_chunk",
                ),
            )
            n += 1
            if m is not None:
                self._emit.write(
                    {"k": "marker", "pid": self._pid, "marker": m, "t_emit_ns": t}
                )
        self._emit.write(
            {"k": "turn_end", "pid": self._pid, "t_ns": time.monotonic_ns(), "lines": n}
        )
        return acp.PromptResponse(stopReason="end_turn")

    async def cancel(self, session_id, **kw):
        return None


def main() -> None:
    asyncio.run(acp.run_agent(BenchAgent()))


if __name__ == "__main__":
    main()
