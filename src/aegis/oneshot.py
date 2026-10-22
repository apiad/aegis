"""One tool-less ``claude -p`` call: the path the recap and /btw share.

Every flag in ``argv`` was measured in the legacy driver
(legacy/aegis/drivers/claude.py, ``_oneshot_argv``): ``--tools ""`` and
``--system-prompt`` stop the call going agentic, ``--setting-sources ""`` and an
empty cwd shed the project's instructions (21,445 -> 7,749 input tokens), and
thinking off saves seconds a short answer does not need. Each caller names its
agent under its own key in ``.aegis.yaml``; nothing defaults (agents.py).
"""

from __future__ import annotations

import asyncio
import json
import os
import pwd
import re
from pathlib import Path
from typing import TypeVar

from pydantic import BaseModel, ValidationError

from . import launch
from .agents import ConfigError, load_agents, read_config

T = TypeVar("T", bound=BaseModel)


class OneshotError(Exception):
    """Why a call left nothing to parse, in words a person reads."""


def load_generator(config_root: Path, key: str) -> dict | None:
    """``<key>: {agent: <name>}``: None when absent, else the agent or why not."""
    try:
        raw = read_config(config_root).get(key)
    except ConfigError as e:
        return {"error": str(e)}
    if raw is None:
        return None
    name = raw.get("agent") if isinstance(raw, dict) else None
    if not name:
        return {"error": f"{key}: needs an agent: {key}: {{agent: <name>}}"}
    agent = next((a for a in load_agents(config_root) if a.name == name), None)
    if agent is None:
        return {"error": f"{key}: names agent {name!r}, which agents: does not define"}
    if agent.harness != "claude-code":
        return {
            "error": f"{key}: agent {name!r} runs {agent.harness}; a {key} needs a claude-code agent"
        }
    if agent.error:
        return {"error": f"{key}: agent {name!r}: {agent.error}"}
    return {"agent": name, "model": agent.model}


def argv(
    claude_bin: str, model: str, schema: type[BaseModel], system: str, prompt: str
) -> list[str]:
    return [
        claude_bin,
        "-p",
        "--model",
        model,
        "--output-format",
        "json",
        "--json-schema",
        json.dumps(schema.model_json_schema()),
        "--system-prompt",
        system,
        "--tools",
        "",
        "--setting-sources",
        "",
        "--mcp-config",
        json.dumps({"mcpServers": {}}),
        "--strict-mcp-config",
        # Last and after --: a prompt that opens on "-" would otherwise be
        # rejected as an unknown option.
        "--",
        prompt,
    ]


_OBJ = re.compile(r"\{.*\}", re.S)


def _number(value, kind: type[float] | type[int]) -> float | int:
    """A bad cost or duration reads as 0, so the answer still lands."""
    try:
        return kind(value or 0)
    except (TypeError, ValueError, OverflowError):
        return kind(0)


def parse(stdout: str, out: type[T]) -> tuple[T | None, float, int]:
    """The envelope's structured answer, cost and time. Never raises."""
    try:
        env = json.loads(stdout)
    except (ValueError, TypeError):
        return None, 0.0, 0
    if not isinstance(env, dict):
        return None, 0.0, 0
    cost = _number(env.get("total_cost_usd"), float)
    ms = int(_number(env.get("duration_ms"), int))
    candidates = []
    if isinstance(env.get("structured_output"), dict):
        candidates.append(env["structured_output"])
    text = str(env.get("result") or "")
    for raw in (text, *(_OBJ.findall(text))):
        try:
            candidates.append(json.loads(raw))
        except (ValueError, TypeError):
            pass
    for c in candidates:
        if isinstance(c, dict):
            try:
                return out.model_validate(c), cost, ms
            except ValidationError:
                continue
    return None, cost, ms


async def run(
    argv: list[str], cwd: Path, timeout: float, user: str | None = None
) -> str:
    """stdout of one call, as ``user`` (launch.py). A cancelled caller kills
    its claude: one left running would keep billing. A call as a user runs in
    that user's home, since ``cwd`` is under the server's state."""
    if user is not None:
        try:
            cwd = Path(pwd.getpwnam(user).pw_dir)
        except KeyError:
            raise OneshotError(f"Linux user {user} does not exist") from None
    cwd.mkdir(parents=True, exist_ok=True)
    try:
        proc = await launch.child(
            argv,
            cwd=cwd,
            env={**os.environ, "MAX_THINKING_TOKENS": "0"},
            stdin=launch.DEVNULL,
            stdout=launch.PIPE,
            stderr=launch.DEVNULL,
            user=user,
        )
    except OSError as e:
        raise OneshotError(str(e)) from e
    try:
        out, _ = await asyncio.wait_for(proc.communicate(), timeout)
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()
        raise OneshotError(f"no answer in {timeout:g}s") from None
    except BaseException:
        if proc.returncode is None:
            proc.kill()
            await proc.wait()
        raise
    if proc.returncode != 0:
        raise OneshotError(f"claude exited with code {proc.returncode}")
    return out.decode(errors="replace")
