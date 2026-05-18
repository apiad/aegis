from __future__ import annotations

from enum import Enum
from pathlib import Path
from typing import Sequence

from pydantic import BaseModel, ValidationError


class Permission(str, Enum):
    read = "read"
    write = "write"
    full = "full"
    auto = "auto"


class Effort(str, Enum):
    low = "low"
    medium = "medium"
    high = "high"
    max = "max"


class Agent(BaseModel):
    harness: str
    model: str
    effort: Effort
    permission: Permission


class ConfigError(Exception):
    pass


INIT_TEMPLATE = '''\
# .aegis.py - Aegis configuration (always Python)
from aegis import Agent

agents = {
    "default": Agent(
        harness="claude-code",   # only driver in v1
        model="opus",            # passthrough alias to the harness
        effort="high",           # low | medium | high | max
        permission="auto",       # read | write | full | auto
    ),
}

default_agent = "default"
'''


def default_search_paths() -> list[Path]:
    return [Path.cwd() / ".aegis.py", Path.home() / ".aegis.py"]


def load_config(
    search_paths: Sequence[Path] | None = None,
) -> tuple[dict[str, Agent], str]:
    paths = list(search_paths) if search_paths is not None else default_search_paths()
    target = next((p for p in paths if p.is_file()), None)
    if target is None:
        raise ConfigError(
            "No .aegis.py found in the current directory or home. "
            "Run `aegis init` to create one."
        )

    namespace: dict[str, object] = {}
    try:
        code = compile(target.read_text(), str(target), "exec")
        exec(code, namespace)  # noqa: S102 - config is intentionally Python
    except ValidationError as e:
        raise ConfigError(f"Invalid agent in {target} (permission/effort?): {e}") from e
    except Exception as e:  # noqa: BLE001 - surface any config error cleanly
        raise ConfigError(f"Failed to load {target}: {e}") from e

    agents = namespace.get("agents")
    default_agent = namespace.get("default_agent")
    if not isinstance(agents, dict) or not agents:
        raise ConfigError(f"{target} must define a non-empty `agents` dict.")
    for name, agent in agents.items():
        if not isinstance(agent, Agent):
            raise ConfigError(
                f"agents[{name!r}] in {target} is not an Agent instance."
            )
    if not isinstance(default_agent, str) or default_agent not in agents:
        raise ConfigError(
            f"`default_agent` in {target} must be one of {sorted(agents)}."
        )
    return agents, default_agent


def write_init_scaffold(path: Path) -> None:
    if path.exists():
        raise ConfigError(f"{path} already exists; refusing to overwrite.")
    path.write_text(INIT_TEMPLATE)
