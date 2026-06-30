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
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ruamel.yaml import YAML

from aegis.config import (
    DEFAULT_TELEGRAM_PROMPT,
    Agent,
    ClaudeCode,
    ConfigError,
    GeminiCLI,
    OpenCode,
    TelegramConfig,
    WebConfig,
)
from aegis.remote.config import RemotePlaneSpec, RemoteSpec


@dataclass
class QueueSpec:
    """Lightweight queue spec parsed from YAML.

    Carries the queue's agent profile reference, parallel cap, and
    raw budget entries (parsed lazily by `load_queues` so the YAML
    layer does not depend on `aegis.budget`).
    """
    agent: str
    max_parallel: int = 1
    budgets: list[dict[str, Any]] | None = None


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
    groups: dict[str, Any] = field(default_factory=dict)
    remotes: dict[str, RemoteSpec] = field(default_factory=dict)
    remote_plane: RemotePlaneSpec | None = None
    telegram: TelegramConfig | None = None
    web: WebConfig | None = None
    root: Path | None = None
    inline_schedule_names: set[str] = field(default_factory=set)


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


_SECTIONS = ("agents", "queues", "schedules", "remotes")


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
        "remotes": dict(raw.get("remotes") or {}),
    }
    overlay = _collect_overlays(root)
    merged: dict[str, dict[str, Any]] = {}
    for section in _SECTIONS:
        merged[section] = _merge_or_die(
            section, inline[section], overlay[section])

    agents = {k: _agent_from_dict(dict(v))
              for k, v in merged["agents"].items()}
    queues = {k: QueueSpec(**v) for k, v in merged["queues"].items()}
    remotes = {k: RemoteSpec(**v) for k, v in merged["remotes"].items()}

    rp_raw = raw.get("remote_plane")
    remote_plane = RemotePlaneSpec(**rp_raw) if rp_raw else None

    groups = _resolve_groups(root, raw.get("groups") or {})

    plugin_dirs_raw = raw.get("plugin_dirs") or [".aegis/plugins"]
    plugin_dirs = [root / Path(p) for p in plugin_dirs_raw]

    default_agent = raw.get("default_agent")
    if agents:
        if default_agent is None:
            raise ConfigError(
                f"{base}: `default_agent` is required when `agents:` "
                f"is set (known: {sorted(agents)}).")
        if default_agent not in agents:
            raise ConfigError(
                f"{base}: `default_agent`={default_agent!r} is not in "
                f"`agents` (known: {sorted(agents)}).")
    if not agents and default_agent is not None:
        raise ConfigError(
            f"{base}: `default_agent` is set but no `agents:` declared.")

    # Validate queue.agent references + max_parallel sanity.
    for qname, qspec in queues.items():
        if qspec.agent not in agents:
            raise ConfigError(
                f"{base}: queues[{qname!r}].agent={qspec.agent!r} does "
                f"not reference a declared agent profile "
                f"(known: {sorted(agents)}).")
        if not isinstance(qspec.max_parallel, int) or qspec.max_parallel < 1:
            raise ConfigError(
                f"{base}: queues[{qname!r}].max_parallel must be an int "
                f">= 1 (got {qspec.max_parallel!r}).")

    telegram = _build_telegram(raw.get("telegram") or {})
    web = _build_web(raw.get("web"))

    return AegisConfig(
        default_agent=default_agent,
        agents=agents,
        queues=queues,
        schedules=merged["schedules"],
        workflows=list(raw.get("workflows") or []),
        plugin_dirs=plugin_dirs,
        scheduler=dict(raw.get("scheduler") or {}),
        groups=groups,
        remotes=remotes,
        remote_plane=remote_plane,
        telegram=telegram,
        web=web,
        root=root,
        inline_schedule_names=set(inline["schedules"].keys()),
    )


def _build_telegram(raw: dict[str, Any]) -> TelegramConfig:
    """Build a TelegramConfig from a `telegram:` YAML block.

    Token resolution: `AEGIS_TELEGRAM_TOKEN` env var wins, else the
    YAML `token:` field. Either may be absent; the headless plane
    only activates Telegram when both token and chat_id are present.
    """
    if not isinstance(raw, dict):
        raise ConfigError("telegram: must be a mapping")
    token = os.environ.get("AEGIS_TELEGRAM_TOKEN") or raw.get("token") or None
    chat_id = raw.get("chat_id")
    auto = raw.get("auto_prompt", DEFAULT_TELEGRAM_PROMPT)
    return TelegramConfig(
        token=token,
        chat_id=int(chat_id) if chat_id is not None else None,
        auto_prompt="" if auto == "" else str(auto),
    )


def _build_web(raw: dict[str, Any] | None) -> WebConfig | None:
    """Build a WebConfig from a `web:` YAML block, or None when absent.

    Token resolution mirrors telegram: `AEGIS_WEB_TOKEN` env var wins,
    else the YAML `token:` field. `bind` defaults to localhost; `port`
    None means auto-pick a free port at serve time.
    """
    if raw is None:
        return None
    if not isinstance(raw, dict):
        raise ConfigError("web: must be a mapping")
    token = os.environ.get("AEGIS_WEB_TOKEN") or raw.get("token") or None
    bind = str(raw.get("bind", "127.0.0.1"))
    port = raw.get("port")
    return WebConfig(
        token=token,
        bind=bind,
        port=int(port) if port is not None else None,
    )


def _resolve_groups(root: Path, inline: dict[str, Any]) -> dict[str, Any]:
    """Merge inline `groups:` block with `.aegis/groups/*.yaml` overlays.

    Inline shape: {defaults: {...}, presets: {name: {...}}}.
    Overlay: each file's body is a single preset's body, keyed by stem.
    Preset-name collisions between inline and overlay fail loud.
    """
    yaml = YAML(typ="safe")
    if not isinstance(inline, dict):
        raise ConfigError("groups: top level must be a mapping")
    defaults = dict(inline.get("defaults") or {})
    presets_inline = dict(inline.get("presets") or {})

    presets_overlay: dict[str, Any] = {}
    folder = root / ".aegis" / "groups"
    if folder.is_dir():
        for path in sorted(folder.glob("*.yaml")):
            body = yaml.load(path.read_text()) or {}
            if not isinstance(body, dict):
                raise ConfigError(
                    f"overlay {path} must be a mapping at top level")
            presets_overlay[path.stem] = body

    presets = _merge_or_die("groups/presets", presets_inline,
                            presets_overlay)
    if not defaults and not presets:
        return {}
    return {"defaults": defaults, "presets": presets}


def import_plugins(cfg: AegisConfig) -> None:
    """Auto-import every non-underscore-prefixed `*.py` under each
    configured plugin dir, recursively. Underscore-prefixed files and
    directories are skipped at any depth.

    Side effects: any `@workflow`, `@hook`, or `@tool` decorated
    function is registered. Import errors fail loud.
    """
    for d in cfg.plugin_dirs:
        if not d.is_dir():
            continue
        for path in _iter_plugin_files(d):
            mod_name = (
                "aegis_plugin_"
                + str(path.relative_to(d)).replace("/", "_").replace(".py", "")
            )
            spec = importlib.util.spec_from_file_location(mod_name, path)
            if spec is None or spec.loader is None:
                raise ConfigError(f"could not load plugin {path}")
            module = importlib.util.module_from_spec(spec)
            sys.modules[mod_name] = module
            spec.loader.exec_module(module)


def _iter_plugin_files(root: Path):
    """Yield every `*.py` under `root`, recursively, skipping any path
    component whose basename starts with `_` or `.`.
    Order is deterministic (lexical by relative path)."""
    out: list[Path] = []
    for path in root.rglob("*.py"):
        if any(part.startswith(("_", ".")) for part in path.relative_to(root).parts):
            continue
        out.append(path)
    out.sort(key=lambda p: str(p.relative_to(root)))
    yield from out


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
