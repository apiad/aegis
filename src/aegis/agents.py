"""Agents: named presets read from the ``agents:`` map of ``.aegis.yaml``.

An agent fixes a harness, a model, an effort, a permission and, optionally, a
priming prompt. A spawn starts from one agent and may override every field
but the priming (``resolve``); one-off instructions belong in the first
message.

Nothing in the file is a default. An agent that omits a field, or sets one to
an empty string, is listed with an ``error`` and cannot be spawned, and the
other agents in the file are unaffected. A field the loader filled in would be
a setting nobody chose, with nothing to show it was filled.

Three forms name the harness: flat ``harness:``, ``provider: <harness>`` as a
string (the Workspace's own form), and a nested ``provider:`` mapping whose
``name`` is the harness and whose other keys are fields.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ruamel.yaml import YAML, YAMLError

from .claude.process import PERMISSION_MODE
from .ops import OpError
from .roots import CONFIG_FILE
from .session import SpawnSpec

EFFORTS = ("low", "medium", "high", "xhigh", "max")
HARNESSES = ("claude-code", "opencode", "codex")
SUPPORTED_HARNESSES = HARNESSES
FIELDS = ("harness", "model", "effort", "permission")
# Least to most: an agent spawns sessions with at most its own permission.
PERMISSION_ORDER = ("read", "write", "auto", "full")


OPENCODE_MODEL = (
    "an OpenCode model is provider/model, such as opencode-go/deepseek-v4-pro"
)

CODEX_MODEL = "a Codex model is provider/model, such as openai/gpt-5.5"


def _model_error(harness: str, model: str) -> str | None:
    if harness == "opencode" and "/" not in model:
        return OPENCODE_MODEL
    if harness == "codex" and "/" not in model:
        return CODEX_MODEL
    return None


class ConfigError(Exception):
    """``.aegis.yaml`` exists but cannot be parsed."""


@dataclass(frozen=True)
class Agent:
    name: str
    harness: str
    model: str
    effort: str
    permission: str
    priming: str | None = None
    # Why this agent cannot be spawned as written; None when it can.
    error: str | None = None

    @property
    def enabled(self) -> bool:
        return self.error is None and self.harness in SUPPORTED_HARNESSES

    def as_dict(self) -> dict:
        return {
            "name": self.name,
            "harness": self.harness,
            "model": self.model,
            "effort": self.effort,
            "permission": self.permission,
            "enabled": self.enabled,
            "error": self.error,
            "has_priming": bool(self.priming),
        }


def _invalid(field: str, value: str) -> str | None:
    if field == "effort" and value not in EFFORTS:
        return f"effort {value!r} is not one of {', '.join(EFFORTS)}"
    if field == "permission" and value not in PERMISSION_MODE:
        return f"permission {value!r} is not one of {', '.join(PERMISSION_MODE)}"
    return None


def _agent(name: str, raw: Any) -> Agent:
    d: dict[str, Any] = dict(raw) if isinstance(raw, dict) else {}
    provider = d.pop("provider", None)
    if isinstance(provider, str):
        d["harness"] = provider
    elif isinstance(provider, dict):
        d |= {k: v for k, v in provider.items() if k != "name"}
        if provider.get("name"):
            d["harness"] = provider["name"]
    values = {f: "" if d.get(f) in (None, "") else str(d[f]) for f in FIELDS}
    missing = [f for f in FIELDS if not values[f]]
    if missing:
        verb = "is" if len(missing) == 1 else "are"
        error: str | None = f"{', '.join(missing)} {verb} missing"
    else:
        error = next((e for f in FIELDS if (e := _invalid(f, values[f]))), None)
        error = error or _model_error(values["harness"], values["model"])
    priming = d.get("priming")
    return Agent(
        name=str(name),
        **values,
        priming=str(priming) if priming else None,
        error=error,
    )


def read_config(config_root: Path) -> dict:
    path = config_root / CONFIG_FILE
    if not path.is_file():
        return {}
    try:
        data = YAML(typ="safe").load(path.read_text())
    except YAMLError as e:
        raise ConfigError(f"{path}: {e}") from e
    return data if isinstance(data, dict) else {}


def agents_from(data: dict) -> list[Agent]:
    """Every agent in a parsed config's ``agents:`` map."""
    agents = data.get("agents")
    if not isinstance(agents, dict):
        return []
    return [_agent(name, raw) for name, raw in agents.items()]


def load_agents(config_root: Path) -> list[Agent]:
    return agents_from(read_config(config_root))


def model_suggestions(agents: list[Agent]) -> dict[str, list[str]]:
    """Per harness, every model an agent of that harness names. The chip offers
    what the harness can run from its own catalog (``config.detect``); these
    are what it falls back to, and what it adds that the catalog lacks."""
    out: dict[str, list[str]] = {}
    for h in HARNESSES:
        models: list[str] = []
        for a in agents:
            if a.harness == h and a.model and a.model not in models:
                models.append(a.model)
        out[h] = models
    return out


def resolve(
    agents: list[Agent],
    default: str | None,
    name: str | None,
    overrides: dict[str, str | None],
    cwd: Path,
    spawned_by: str | None = None,
) -> SpawnSpec:
    """The spec a spawn runs: the agent's fields, each replaced by its override
    when one is given. The priming is always the agent's."""
    name = name or default
    if not name:
        raise OpError("no_agent", "no agent given and no default_agent in .aegis.yaml")
    agent = next((a for a in agents if a.name == name), None)
    if agent is None:
        raise OpError("unknown_agent", f"no agent named {name!r}")
    if agent.error:
        raise OpError("bad_agent", f"agent {name!r}: {agent.error}")
    fields = {f: getattr(agent, f) for f in FIELDS}
    overridden = tuple(
        f
        for f in FIELDS
        if overrides.get(f) not in (None, "") and overrides[f] != fields[f]
    )
    fields |= {f: str(overrides[f]) for f in overridden}
    if fields["harness"] not in SUPPORTED_HARNESSES:
        raise OpError(
            "harness_unsupported", f"{fields['harness']} is not supported yet"
        )
    if err := _model_error(fields["harness"], fields["model"]):
        raise OpError("bad_model", err)
    return SpawnSpec(
        agent=agent.name,
        model=fields["model"],
        effort=fields["effort"],
        permission=fields["permission"],
        cwd=cwd,
        harness=fields["harness"],
        priming=agent.priming,
        overridden=overridden,
        spawned_by=spawned_by,
    )
