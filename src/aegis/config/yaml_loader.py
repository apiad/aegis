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
    Agent,
    ConfigError,
    Permission,
    VoiceConfig,
    WebConfig,
)
from aegis.config.harnesses import (
    HarnessRegistration,
    merge_harnesses,
    resolve_agent_entry,
)
from aegis.hosts.models import HostSpec
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
    harnesses: dict[str, HarnessRegistration] = field(default_factory=dict)
    queues: dict[str, QueueSpec] = field(default_factory=dict)
    schedules: dict[str, dict[str, Any]] = field(default_factory=dict)
    workflows: list[str] = field(default_factory=list)
    plugin_dirs: list[Path] = field(default_factory=list)
    scheduler: dict[str, Any] = field(default_factory=dict)
    groups: dict[str, Any] = field(default_factory=dict)
    remotes: dict[str, RemoteSpec] = field(default_factory=dict)
    hosts: dict[str, HostSpec] = field(default_factory=dict)
    remote_plane: RemotePlaneSpec | None = None
    web: WebConfig | None = None
    voice: VoiceConfig = field(default_factory=VoiceConfig)
    root: Path | None = None
    # Agent profile used for one-shot generation (`/btw` side notes,
    # generated session titles) rather than for conversation. None means
    # "fall back to the session's own profile", which is the expensive
    # default — the surfaces that use it say so once per session.
    text_generation: str | None = None
    inline_schedule_names: set[str] = field(default_factory=set)
    dynamic_workflow_autoapprove_agents: int = 5


_VALID_DRIVERS = {"claude-code", "gemini", "opencode", "lovelaice"}


def _harness_from_dict(name: str, d: dict[str, Any]) -> HarnessRegistration:
    """Construct a HarnessRegistration from a `harnesses:` YAML entry.

    Requires a `driver` naming one of the four drivers. Unknown drivers
    fail loud.
    """
    driver = d.get("driver")
    if driver not in _VALID_DRIVERS:
        raise ConfigError(
            f"harness {name!r}: unknown driver {driver!r}; "
            f"known: {sorted(_VALID_DRIVERS)}")
    pd = d.get("permission_default")
    return HarnessRegistration(
        name=name,
        driver=driver,
        base_url=d.get("base_url"),
        api_key_file=d.get("api_key_file"),
        default_model=d.get("default_model"),
        permission_default=Permission(pd) if pd else None,
    )


def _host_from_dict(name: str, d: dict[str, Any]) -> HostSpec:
    """Construct a HostSpec from a `hosts:` YAML entry.

    `local` is an implicit host that always exists and cannot be
    redeclared — allowing it would make the meaning of `host: local`
    depend on config, which is exactly the ambiguity the implicit host
    exists to prevent.
    """
    if name == "local":
        raise ConfigError(
            "hosts: 'local' is implicit (the machine aegis runs on) and "
            "cannot be declared.")
    for key in ("ssh", "cwd"):
        if not d.get(key):
            raise ConfigError(f"hosts[{name!r}]: {key!r} is required.")
    port = d.get("remote_mcp_port")
    return HostSpec(
        name=name,
        ssh=str(d["ssh"]),
        cwd=str(d["cwd"]),
        ssh_opts=[str(o) for o in (d.get("ssh_opts") or [])],
        login_shell=bool(d.get("login_shell", True)),
        remote_mcp_port=int(port) if port is not None else None,
    )


_SECTIONS = ("agents", "harnesses", "queues", "schedules", "remotes",
             "hosts")


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
        "harnesses": dict(raw.get("harnesses") or {}),
        "queues": dict(raw.get("queues") or {}),
        "schedules": dict(raw.get("schedules") or {}),
        "remotes": dict(raw.get("remotes") or {}),
        "hosts": dict(raw.get("hosts") or {}),
    }
    overlay = _collect_overlays(root)
    merged: dict[str, dict[str, Any]] = {}
    for section in _SECTIONS:
        merged[section] = _merge_or_die(
            section, inline[section], overlay[section])

    explicit_harnesses = {
        k: _harness_from_dict(k, dict(v))
        for k, v in merged["harnesses"].items()}
    harnesses = merge_harnesses(explicit_harnesses)
    agents = {k: resolve_agent_entry(dict(v), harnesses)
              for k, v in merged["agents"].items()}
    queues = {k: QueueSpec(**v) for k, v in merged["queues"].items()}
    remotes = {k: RemoteSpec(**v) for k, v in merged["remotes"].items()}
    hosts = {k: _host_from_dict(k, dict(v))
             for k, v in merged["hosts"].items()}

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

    text_generation = raw.get("text_generation")
    if text_generation is not None and text_generation not in agents:
        raise ConfigError(
            f"{base}: `text_generation`={text_generation!r} is not in "
            f"`agents` (known: {sorted(agents)}).")

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

    # An agent profile may name a default execution host; it must exist.
    for aname, aprofile in agents.items():
        h = getattr(aprofile, "host", None)
        if h and h != "local" and h not in hosts:
            raise ConfigError(
                f"{base}: agents[{aname!r}].host={h!r} does not reference "
                f"a declared host (known: {sorted(hosts)} + 'local').")

    web = _build_web(raw.get("web"))
    voice = _build_voice(raw.get("voice"))

    return AegisConfig(
        default_agent=default_agent,
        text_generation=text_generation,
        agents=agents,
        harnesses=harnesses,
        queues=queues,
        schedules=merged["schedules"],
        workflows=list(raw.get("workflows") or []),
        plugin_dirs=plugin_dirs,
        scheduler=dict(raw.get("scheduler") or {}),
        groups=groups,
        remotes=remotes,
        hosts=hosts,
        remote_plane=remote_plane,
        web=web,
        voice=voice,
        root=root,
        inline_schedule_names=set(inline["schedules"].keys()),
        dynamic_workflow_autoapprove_agents=int(
            raw.get("dynamic_workflow_autoapprove_agents", 5)),
    )


def _build_web(raw: dict[str, Any] | None) -> WebConfig | None:
    """Build a WebConfig from a `web:` YAML block, or None when absent.

    Token resolution: `AEGIS_WEB_TOKEN` env var wins, else the YAML
    `token:` field. `bind` defaults to localhost; `port` None means
    auto-pick a free port at serve time.
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


def _build_voice(raw: Any) -> VoiceConfig:
    """Build a VoiceConfig from a `voice:` YAML block. Absent -> disabled."""
    if not raw:
        return VoiceConfig()
    if not isinstance(raw, dict):
        raise ConfigError("voice: must be a mapping")
    defaults = VoiceConfig()
    return VoiceConfig(
        enabled=bool(raw.get("enabled", defaults.enabled)),
        model=str(raw.get("model", defaults.model)),
        key=str(raw.get("key", defaults.key)),
        preview=bool(raw.get("preview", defaults.preview)),
        language=raw.get("language", defaults.language),
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
