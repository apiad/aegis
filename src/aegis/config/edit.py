"""Comment-preserving edits to ``.aegis.yaml`` and its overlay files.

Uses ruamel.yaml so operator-curated comments survive automated
mutations (e.g. the TUI's Space toggle, the ``aegis schedule
enable/disable`` CLI commands). All writes go through an
atomic-write helper so a crash mid-mutation can't corrupt the file.
"""
from __future__ import annotations

import os
import tempfile
from pathlib import Path

from ruamel.yaml import YAML


def _yaml() -> YAML:
    yaml = YAML()
    yaml.preserve_quotes = True
    yaml.indent(mapping=2, sequence=4, offset=2)
    return yaml


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
