"""One tool-less generation call: the path the recap and /btw share.

Claude's flags were measured in the legacy driver
(``legacy/aegis/drivers/claude.py``, ``_oneshot_argv``): ``--tools ""`` and
``--system-prompt`` stop that call going agentic, while OpenCode and Codex use
their non-interactive, read-only commands. Each caller names its agent under
its own key in ``.aegis.yaml``; nothing defaults (agents.py).
"""

from __future__ import annotations

import asyncio
import json
import os
import pwd
import signal
from pathlib import Path
from typing import TypeVar

from pydantic import BaseModel, ValidationError

from . import launch
from .claude import login
from .agents import ConfigError, load_agents, read_config
from .codex.config import split_model

T = TypeVar("T", bound=BaseModel)


# The harnesses ``argv`` can make a one-shot call with.
HARNESSES = ("claude-code", "opencode", "codex")


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
    if agent.error:
        return {"error": f"{key}: agent {name!r}: {agent.error}"}
    if agent.harness not in HARNESSES:
        return {
            "error": f"{key}: agent {name!r} runs on {agent.harness}, which has no "
            f"one-shot calls; use one on {', '.join(HARNESSES)}"
        }
    return {"agent": name, "harness": agent.harness, "model": agent.model}


def argv(
    binary: str,
    model: str,
    schema: type[BaseModel],
    system: str,
    *,
    harness: str = "claude-code",
) -> list[str]:
    """One call's argv. The prompt is not here: ``run`` writes ``message(...)``
    on stdin, since an argv is readable by every user on the host and a prompt
    holds a person's transcript."""
    if harness == "claude-code":
        return [
            binary,
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
        ]
    if harness == "opencode":
        # With no message argument, run reads it from stdin.
        return [
            binary,
            "run",
            "--standalone",
            "--model",
            model,
            "--agent",
            "plan",
            "--format",
            "json",
        ]
    if harness == "codex":
        _, model_id = split_model(model)
        return [
            binary,
            "exec",
            "--ephemeral",
            "--model",
            model_id,
            "--json",
            "--sandbox",
            "read-only",
            "--skip-git-repo-check",
            "-",  # the prompt is read from stdin
        ]
    raise ValueError(f"unsupported one-shot harness: {harness}")


def message(system: str, prompt: str, harness: str = "claude-code") -> str:
    """What ``run`` writes on stdin: Claude Code takes the system prompt as a
    flag, the others read it at the head of the message."""
    return prompt if harness == "claude-code" else f"{system}\n\n{prompt}"


def _json_values(stdout: str) -> list[dict]:
    """Read one JSON envelope or newline-delimited harness events."""
    values: list[dict] = []
    try:
        value = json.loads(stdout)
    except (ValueError, TypeError):
        value = None
    if isinstance(value, dict):
        values.append(value)
    elif isinstance(value, list):
        values.extend(v for v in value if isinstance(v, dict))
    for line in stdout.splitlines():
        try:
            value = json.loads(line)
        except (ValueError, TypeError):
            continue
        if isinstance(value, dict) and value not in values:
            values.append(value)
    return values


def _strings(value) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        return [text for child in value.values() for text in _strings(child)]
    if isinstance(value, list):
        return [text for child in value for text in _strings(child)]
    return []


def _dicts(value) -> list[dict]:
    if isinstance(value, dict):
        return [value] + [child for item in value.values() for child in _dicts(item)]
    if isinstance(value, list):
        return [child for item in value for child in _dicts(item)]
    return []


def _objects(text: str) -> list[dict]:
    """Find JSON objects embedded in a plain or event-formatted answer."""
    found: list[dict] = []
    decoder = json.JSONDecoder()
    for start, char in enumerate(text):
        if char != "{":
            continue
        try:
            value, _ = decoder.raw_decode(text[start:])
        except (ValueError, TypeError):
            continue
        if isinstance(value, dict) and value not in found:
            found.append(value)
    return found


def _number(value, kind: type[float] | type[int]) -> float | int:
    """A bad cost or duration reads as 0, so the answer still lands."""
    try:
        return kind(value or 0)
    except (TypeError, ValueError, OverflowError):
        return kind(0)


def parse(stdout: str, out: type[T]) -> tuple[T | None, float, int]:
    """The structured answer, cost and time from any supported harness."""
    values = _json_values(stdout)
    cost = 0.0
    ms = 0
    for value in values:
        for key in ("total_cost_usd", "cost_usd", "cost"):
            if key in value:
                cost = _number(value[key], float)
                break
        for key in ("duration_ms", "duration"):
            if key in value:
                ms = int(_number(value[key], int))
                break
    candidates: list[dict] = []
    for value in values:
        candidates.extend(_dicts(value))
        for text in _strings(value):
            candidates.extend(_objects(text))
    for c in candidates:
        try:
            return out.model_validate(c), cost, ms
        except ValidationError:
            continue
    return None, cost, ms


async def run(
    argv: list[str],
    cwd: Path,
    timeout: float,
    user: str | None,
    prompt: str,
    harness: str = "claude-code",
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
    env = {**os.environ, "MAX_THINKING_TOKENS": "0"}
    if harness == "claude-code":
        env = login.with_token(env, user) or env
    try:
        proc = await launch.child(
            argv,
            cwd=cwd,
            env=env,
            stdin=launch.PIPE,
            stdout=launch.PIPE,
            stderr=launch.DEVNULL,
            user=user,
            via=launch.resolve_via(),
        )
    except OSError as e:
        raise OneshotError(str(e)) from e
    try:
        out, _ = await asyncio.wait_for(proc.communicate(prompt.encode()), timeout)
    except asyncio.TimeoutError:
        await launch.signal_process_async(proc.pid, signal.SIGKILL, user=user)
        await proc.wait()
        raise OneshotError(f"no answer in {timeout:g}s") from None
    except BaseException:
        if proc.returncode is None:
            await launch.signal_process_async(proc.pid, signal.SIGKILL, user=user)
            await proc.wait()
        raise
    if proc.returncode != 0:
        raise OneshotError(f"one-shot exited with code {proc.returncode}")
    return out.decode(errors="replace")
