from __future__ import annotations

import os
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Sequence

from pydantic import BaseModel, ValidationError


DEFAULT_TELEGRAM_PROMPT = (
    "You are replying via telegram, keep response compact and focused "
    "if possible, only resort to long responses if it really matters")


@dataclass(frozen=True)
class TelegramConfig:
    token: str | None
    chat_id: int | None
    auto_prompt: str


def load_telegram_config(path: Path) -> TelegramConfig:
    ns: dict[str, object] = {}
    if path.is_file():
        exec(compile(path.read_text(), str(path), "exec"), ns)  # noqa: S102
    token = os.environ.get("AEGIS_TELEGRAM_TOKEN") or ns.get("telegram_token")
    chat_id = ns.get("telegram_chat_id")
    auto = ns.get("auto_add_to_telegram_prompt", DEFAULT_TELEGRAM_PROMPT)
    return TelegramConfig(
        token=token or None,
        chat_id=int(chat_id) if chat_id is not None else None,
        auto_prompt="" if auto == "" else str(auto))


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


def find_project_root(start: Path | None = None) -> Path | None:
    """Closest ancestor of `start` (default cwd) containing .aegis.py."""
    cur = (start or Path.cwd()).resolve()
    for d in (cur, *cur.parents):
        if (d / ".aegis.py").is_file():
            return d
    return None


def load_config(
    search_paths: Sequence[Path] | None = None,
) -> tuple[dict[str, Agent], str]:
    if search_paths is None:
        root = find_project_root()
        paths = ([root / ".aegis.py"] if root else []) + [Path.home() / ".aegis.py"]
    else:
        paths = list(search_paths)
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
