"""Comment-preserving edits to ``.aegis.yaml`` and its overlay files.

Uses ruamel.yaml so operator-curated comments survive automated
mutations (e.g. the TUI's Space toggle, the ``aegis schedule
enable/disable`` CLI commands). All writes go through an
atomic-write helper so a crash mid-mutation can't corrupt the file.
"""
from __future__ import annotations

import os
import tempfile
from io import StringIO
from pathlib import Path
from typing import Any

from ruamel.yaml import YAML

from aegis.config import ConfigError


def _yaml() -> YAML:
    yaml = YAML()
    yaml.preserve_quotes = True
    yaml.indent(mapping=2, sequence=4, offset=2)
    return yaml


def _load(path: Path) -> dict[str, Any]:
    """Read .aegis.yaml as a ruamel-backed dict; empty dict if missing."""
    if not path.exists():
        return {}
    data = _yaml().load(path.read_text())
    if data is None:
        return {}
    if not isinstance(data, dict):
        raise ConfigError(f"{path}: top level must be a mapping")
    return data


def _validate_and_dump(root: Path, data: dict[str, Any]) -> str:
    """Render `data` to a YAML string, validate by feeding it through
    yaml_loader.load_config in a tempfile, and return the string on
    success. Raises ConfigError on any validation failure — the caller
    is responsible for NOT having written the file yet."""
    from aegis.config.yaml_loader import load_config as _load_yaml

    buf = StringIO()
    _yaml().dump(data, buf)
    payload = buf.getvalue()

    # Validate in an isolated sibling directory so overlays under
    # `.aegis/*` are still discovered (some validation depends on them).
    fd, tmp = tempfile.mkstemp(
        dir=str(root), prefix=".aegis-validate-", suffix=".yaml")
    os.close(fd)
    tmp_path = Path(tmp)
    try:
        tmp_path.write_text(payload)
        # The loader expects the file to be named ".aegis.yaml" inside
        # the root. Stage a sibling root for validation.
        from tempfile import TemporaryDirectory
        with TemporaryDirectory(dir=str(root)) as scratch:
            scratch_path = Path(scratch)
            (scratch_path / ".aegis.yaml").write_text(payload)
            # Mirror any `.aegis/` overlay tree so cross-section
            # references (queues -> agents in overlays) still resolve.
            overlay_src = root / ".aegis"
            if overlay_src.is_dir():
                import shutil
                shutil.copytree(overlay_src, scratch_path / ".aegis",
                                dirs_exist_ok=True)
            try:
                _load_yaml(scratch_path)
            except ConfigError as e:
                msg = str(e).replace(str(scratch_path), str(root))
                raise ConfigError(msg) from None
            except Exception as e:  # noqa: BLE001 — pydantic etc.
                # Wrap as ConfigError so callers see a uniform type.
                msg = str(e).replace(str(scratch_path), str(root))
                raise ConfigError(msg) from None
    finally:
        if tmp_path.exists():
            tmp_path.unlink()
    return payload


def _atomic_write(path: Path, payload: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(
        dir=str(path.parent), prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as f:
            f.write(payload)
        os.replace(tmp, path)
    except Exception:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


def set_schedule_enabled(root: Path, name: str, value: bool) -> bool:
    """Set ``enabled`` for schedule ``name`` to ``value``.

    Checks ``.aegis/schedules/<name>.yaml`` first (overlay), falls back
    to ``.aegis.yaml`` (inline section). Returns the new enabled state.
    Raises ``FileNotFoundError`` if neither location carries the
    schedule, ``KeyError`` if the inline section exists but the name
    isn't in it.
    """
    yaml = _yaml()
    overlay = root / ".aegis" / "schedules" / f"{name}.yaml"
    if overlay.exists():
        from io import StringIO
        data = yaml.load(overlay.read_text())
        if not isinstance(data, dict):
            raise ValueError(f"{overlay} must be a mapping")
        data["enabled"] = bool(value)
        buf = StringIO()
        yaml.dump(data, buf)
        _atomic_write(overlay, buf.getvalue())
        return bool(value)

    base = root / ".aegis.yaml"
    if not base.exists():
        raise FileNotFoundError(
            f"neither {overlay} nor {base} exists for schedule {name!r}")
    from io import StringIO
    data = yaml.load(base.read_text())
    schedules = (data or {}).get("schedules") or {}
    if name not in schedules:
        raise KeyError(f"schedule {name!r} not in {base}")
    schedules[name]["enabled"] = bool(value)
    buf = StringIO()
    yaml.dump(data, buf)
    _atomic_write(base, buf.getvalue())
    return bool(value)


# --- agents ----------------------------------------------------------

_VALID_PROVIDERS = {"claude-code", "gemini", "opencode"}


def add_agent(
    root: Path,
    slug: str,
    *,
    provider: str,
    model: str,
    effort: str | None = None,
    permission: str | None = None,
) -> None:
    """Add an agent profile to .aegis.yaml.

    Creates the file with `default_agent: <slug>` if it does not exist.
    Validates the result by re-loading through the YAML loader; rolls
    back (file unchanged) on validation failure.
    """
    if provider not in _VALID_PROVIDERS:
        raise ConfigError(
            f"unknown provider {provider!r}; "
            f"known: {sorted(_VALID_PROVIDERS)}")
    base = root / ".aegis.yaml"
    data = _load(base)

    agents = data.setdefault("agents", {})
    if slug in agents:
        raise ConfigError(
            f"agent {slug!r} already exists in {base}")

    entry: dict[str, Any] = {"provider": provider, "model": model}
    if effort is not None:
        if provider != "claude-code":
            raise ConfigError(
                f"effort only applies to claude-code, not {provider!r}")
        entry["effort"] = effort
    if permission is not None:
        entry["permission"] = permission
    agents[slug] = entry

    # First agent → make it the default unless one is already set.
    if "default_agent" not in data:
        data["default_agent"] = slug

    payload = _validate_and_dump(root, data)
    _atomic_write(base, payload)


def remove_agent(root: Path, slug: str) -> None:
    """Drop an agent profile from .aegis.yaml.

    Fails loud if the slug is unknown, if it's the current
    default_agent (without a clean replacement), or if any queue
    references it.
    """
    base = root / ".aegis.yaml"
    data = _load(base)
    agents = data.get("agents") or {}
    if slug not in agents:
        raise ConfigError(f"agent {slug!r} not in {base}")

    # Reject if any queue still binds to it.
    queues = data.get("queues") or {}
    bad = [name for name, q in queues.items()
           if isinstance(q, dict) and q.get("agent") == slug]
    if bad:
        raise ConfigError(
            f"cannot remove agent {slug!r}: referenced by "
            f"queue(s) {bad}. Remove or re-bind those queues first.")

    # Reject if it's the current default and there's no other agent to
    # promote — let the operator make the call explicitly via
    # `aegis config default-agent <slug>`.
    if data.get("default_agent") == slug:
        raise ConfigError(
            f"cannot remove agent {slug!r}: it is the current "
            f"default_agent. Set a different default first via "
            f"`aegis config default-agent <slug>`.")

    del agents[slug]
    payload = _validate_and_dump(root, data)
    _atomic_write(base, payload)


# --- queues ----------------------------------------------------------

def add_queue(
    root: Path,
    name: str,
    *,
    agent: str,
    max_parallel: int,
    budgets: list[dict[str, Any]] | None = None,
) -> None:
    """Add a queue to .aegis.yaml. Fails loud on unknown agent ref,
    duplicate name, or bad max_parallel."""
    base = root / ".aegis.yaml"
    data = _load(base)
    queues = data.setdefault("queues", {})
    if name in queues:
        raise ConfigError(f"queue {name!r} already exists in {base}")
    entry: dict[str, Any] = {"agent": agent, "max_parallel": max_parallel}
    if budgets:
        entry["budgets"] = list(budgets)
    queues[name] = entry
    payload = _validate_and_dump(root, data)
    _atomic_write(base, payload)


def remove_queue(root: Path, name: str) -> None:
    """Drop a queue from .aegis.yaml. Fails loud if the name is unknown."""
    base = root / ".aegis.yaml"
    data = _load(base)
    queues = data.get("queues") or {}
    if name not in queues:
        raise ConfigError(f"queue {name!r} not in {base}")
    del queues[name]
    payload = _validate_and_dump(root, data)
    _atomic_write(base, payload)


# --- telegram --------------------------------------------------------

class _Unchanged:
    """Sentinel: distinguishes "leave alone" from "set to None / clear"."""
    def __repr__(self) -> str:
        return "UNCHANGED"


UNCHANGED = _Unchanged()


def set_telegram(
    root: Path,
    *,
    token: str | None | _Unchanged = UNCHANGED,
    chat_id: int | None | _Unchanged = UNCHANGED,
    auto_prompt: str | None | _Unchanged = UNCHANGED,
) -> None:
    """Mutate the `telegram:` block. Pass a concrete value to set,
    `None` to clear that field, or omit (leaving the sentinel) to
    leave it alone. An empty block is removed entirely."""
    base = root / ".aegis.yaml"
    data = _load(base)
    block = data.get("telegram") or {}
    if not isinstance(block, dict):
        raise ConfigError(f"{base}: telegram block must be a mapping")

    def _apply(key: str, value):
        if isinstance(value, _Unchanged):
            return
        if value is None:
            block.pop(key, None)
        else:
            block[key] = value

    _apply("token", token)
    _apply("chat_id", chat_id)
    _apply("auto_prompt", auto_prompt)

    if block:
        data["telegram"] = block
    else:
        data.pop("telegram", None)

    payload = _validate_and_dump(root, data)
    _atomic_write(base, payload)


# --- default_agent ---------------------------------------------------

def set_default_agent(root: Path, slug: str) -> None:
    base = root / ".aegis.yaml"
    data = _load(base)
    agents = data.get("agents") or {}
    if slug not in agents:
        raise ConfigError(
            f"agent {slug!r} not declared (known: {sorted(agents)})")
    data["default_agent"] = slug
    payload = _validate_and_dump(root, data)
    _atomic_write(base, payload)


# --- plugin_dirs -----------------------------------------------------

def add_plugin_dir(root: Path, path: str) -> None:
    """Append a plugin directory. Idempotent."""
    base = root / ".aegis.yaml"
    data = _load(base)
    dirs = data.get("plugin_dirs") or []
    if not isinstance(dirs, list):
        raise ConfigError(f"{base}: plugin_dirs must be a list")
    if path in dirs:
        return
    dirs.append(path)
    data["plugin_dirs"] = dirs
    payload = _validate_and_dump(root, data)
    _atomic_write(base, payload)


def remove_plugin_dir(root: Path, path: str) -> None:
    """Drop a plugin directory. No-op if not present."""
    base = root / ".aegis.yaml"
    data = _load(base)
    dirs = data.get("plugin_dirs") or []
    if path in dirs:
        dirs.remove(path)
        if dirs:
            data["plugin_dirs"] = dirs
        else:
            data.pop("plugin_dirs", None)
        payload = _validate_and_dump(root, data)
        _atomic_write(base, payload)


def toggle_schedule_enabled(root: Path, name: str) -> bool:
    """Flip ``enabled``; return the new state."""
    yaml = _yaml()
    overlay = root / ".aegis" / "schedules" / f"{name}.yaml"
    if overlay.exists():
        data = yaml.load(overlay.read_text())
        current = bool(data.get("enabled", True)) if isinstance(
            data, dict) else True
        return set_schedule_enabled(root, name, not current)
    base = root / ".aegis.yaml"
    if not base.exists():
        raise FileNotFoundError(
            f"neither {overlay} nor {base} exists for schedule {name!r}")
    data = yaml.load(base.read_text())
    schedules = (data or {}).get("schedules") or {}
    if name not in schedules:
        raise KeyError(f"schedule {name!r} not in {base}")
    current = bool(schedules[name].get("enabled", True))
    return set_schedule_enabled(root, name, not current)
