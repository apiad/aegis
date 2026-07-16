"""`!command` shell escape: run a command locally and format its result
for injection into the agent as a user message."""
from __future__ import annotations

import asyncio
from pathlib import Path

MAX_OUTPUT = 20_000  # cap injected output; keep the tail (most recent)


async def run_shell_escape(command: str, cwd: Path,
                           timeout: float = 60.0) -> str:
    """Run *command* through the shell in *cwd*; return a formatted block
    (`$ command` + combined stdout/stderr + a non-zero exit note) suitable
    for delivery as a user message."""
    try:
        proc = await asyncio.create_subprocess_shell(
            command, cwd=str(cwd),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
    except OSError as e:
        return f"$ {command}\n[failed to launch: {e}]"
    try:
        out, _ = await asyncio.wait_for(proc.communicate(), timeout)
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()
        return f"$ {command}\n[timed out after {timeout:.0f}s]"
    text = out.decode("utf-8", "replace").rstrip("\n")
    if len(text) > MAX_OUTPUT:
        text = "…(truncated)…\n" + text[-MAX_OUTPUT:]
    body = f"$ {command}"
    if text:
        body += f"\n{text}"
    if proc.returncode:
        body += f"\n[exited {proc.returncode}]"
    return body
