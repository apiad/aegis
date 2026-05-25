"""YAML config loader for aegis.

Reads `.aegis.yaml` (inline entries) + drop-in overlay folders
(`.aegis/{agents,queues,schedules}/*.yaml`). Merges with fail-loud
conflict — if the same entry key appears in both an inline section
and an overlay file, boot aborts.

Also handles plugin auto-import from `.aegis/plugins/*.py` and
opt-in built-in workflow registration via the top-level
`workflows:` list.
"""
from __future__ import annotations

import importlib
import importlib.util
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ruamel.yaml import YAML

from aegis.config import (
    Agent,
    ClaudeCode,
    ConfigError,
    GeminiCLI,
    OpenCode,
)


@dataclass
class QueueSpec:
    """Lightweight queue spec parsed from YAML.

    Mirrors the queue dict shape in `.aegis.py` (agent profile name +
    max_parallel cap). Compatible with `aegis.queue.Queue.from_dict`
    callers via attribute access.
    """
    agent: str
    max_parallel: int = 1


@dataclass
class AegisConfig:
    """Loaded YAML config (in-memory)."""
    default_agent: str | None = None
    agents: dict[str, Agent] = field(default_factory=dict)
    queues: dict[str, QueueSpec] = field(default_factory=dict)
    schedules: dict[str, dict[str, Any]] = field(default_factory=dict)
    workflows: list[str] = field(default_factory=list)
    plugin_dirs: list[Path] = field(default_factory=list)
    scheduler: dict[str, Any] = field(default_factory=dict)
    root: Path | None = None


_PROVIDERS: dict[str, type] = {
    "claude-code": ClaudeCode,
    "gemini-cli": GeminiCLI,
    "gemini": GeminiCLI,
    "opencode": OpenCode,
}


def _agent_from_dict(d: dict[str, Any]) -> Agent:
    """Construct an Agent from a flat YAML mapping.

    The mapping must carry `provider:` naming one of `claude-code`,
    `gemini-cli`/`gemini`, or `opencode`, plus the provider-specific
    fields. Unknown providers raise ConfigError.
    """
    body = dict(d)
    provider_name = body.pop("provider", None)
    if provider_name is None:
        raise ConfigError(f"agent missing `provider` field: {d!r}")
    cls = _PROVIDERS.get(provider_name)
    if cls is None:
        raise ConfigError(
            f"unknown provider {provider_name!r}; "
            f"known: {sorted(_PROVIDERS)}")
    return Agent(provider=cls(**body))


_SECTIONS = ("agents", "queues", "schedules")


def _collect_overlays(root: Path) -> dict[str, dict[str, Any]]:
    """Walk `.aegis/{agents,queues,schedules}/*.yaml`.

    Each file's stem is the entry key; the file body is the entry
    contents directly (not re-keyed under the name inside the file).
    Returns `{section: {name: body}}`.
    """
    yaml = YAML(typ="safe")
    out: dict[str, dict[str, Any]] = {s: {} for s in _SECTIONS}
    for section in _SECTIONS:
        folder = root / ".aegis" / section
        if not folder.is_dir():
            continue
        for path in sorted(folder.glob("*.yaml")):
            name = path.stem
            body = yaml.load(path.read_text()) or {}
            if not isinstance(body, dict):
                raise ConfigError(
                    f"overlay {path} must be a mapping at top level")
            out[section][name] = body
    return out


def _merge_or_die(section: str, inline: dict, overlay: dict) -> dict:
    """Merge inline + overlay; raise on key collision."""
    conflict = sorted(set(inline) & set(overlay))
    if conflict:
        raise ConfigError(
            f"{section}: keys appear in both .aegis.yaml and "
            f".aegis/{section}/*.yaml: {conflict}. "
            f"One source of truth per entry.")
    return {**inline, **overlay}


def load_config(root: Path) -> AegisConfig:
    """Parse `.aegis.yaml` at root + collect drop-in overlays.

    Returns a fully-resolved `AegisConfig`. Raises `ConfigError` on
    parse failure or merge conflict.
    """
    yaml = YAML(typ="safe")
    base = root / ".aegis.yaml"
    raw: dict[str, Any] = {}
    if base.is_file():
        raw = yaml.load(base.read_text()) or {}
        if not isinstance(raw, dict):
            raise ConfigError(
                f"{base}: top level must be a mapping")

    inline: dict[str, dict[str, Any]] = {
        "agents": dict(raw.get("agents") or {}),
        "queues": dict(raw.get("queues") or {}),
        "schedules": dict(raw.get("schedules") or {}),
    }
    overlay = _collect_overlays(root)
    merged: dict[str, dict[str, Any]] = {}
    for section in _SECTIONS:
        merged[section] = _merge_or_die(
            section, inline[section], overlay[section])

    agents = {k: _agent_from_dict(dict(v))
              for k, v in merged["agents"].items()}
    queues = {k: QueueSpec(**v) for k, v in merged["queues"].items()}

    plugin_dirs_raw = raw.get("plugin_dirs") or [".aegis/plugins"]
    plugin_dirs = [root / Path(p) for p in plugin_dirs_raw]

    return AegisConfig(
        default_agent=raw.get("default_agent"),
        agents=agents,
        queues=queues,
        schedules=merged["schedules"],
        workflows=list(raw.get("workflows") or []),
        plugin_dirs=plugin_dirs,
        scheduler=dict(raw.get("scheduler") or {}),
        root=root,
    )


def import_plugins(cfg: AegisConfig) -> None:
    """Auto-import every `*.py` in each configured plugin dir.

    Side effects: any `@workflow`-decorated function is registered.
    Import errors fail loud.
    """
    for d in cfg.plugin_dirs:
        if not d.is_dir():
            continue
        for path in sorted(d.glob("*.py")):
            mod_name = f"aegis_plugin_{path.stem}"
            spec = importlib.util.spec_from_file_location(mod_name, path)
            if spec is None or spec.loader is None:
                raise ConfigError(f"could not load plugin {path}")
            module = importlib.util.module_from_spec(spec)
            sys.modules[mod_name] = module
            spec.loader.exec_module(module)


def register_builtins(cfg: AegisConfig) -> None:
    """Import each name in cfg.workflows from aegis.workflows.builtins."""
    for name in cfg.workflows:
        try:
            importlib.import_module(f"aegis.workflows.builtins.{name}")
        except ModuleNotFoundError as e:
            raise ConfigError(
                f"workflows list references unknown built-in: {name!r}"
            ) from e


def find_yaml_root(start: Path | None = None) -> Path | None:
    """Closest ancestor of `start` (default cwd) containing
    `.aegis.yaml`."""
    cur = (start or Path.cwd()).resolve()
    for d in (cur, *cur.parents):
        if (d / ".aegis.yaml").is_file():
            return d
    return None
