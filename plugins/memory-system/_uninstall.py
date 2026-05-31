"""Teardown hook for memory-system: strip yaml, remove overlay, optionally wipe data."""
from __future__ import annotations

import io
import shutil
from pathlib import Path

from ruamel.yaml import YAML

from aegis.plugins.install_context import InstallContext


def _yaml() -> YAML:
    y = YAML()
    y.preserve_quotes = True
    y.default_flow_style = False
    return y


def _strip_yaml(yaml_path: Path) -> None:
    """Drop `memory:` block and the `dreamer` agent (clearing default if it
    pointed at dreamer). Comment-preserving via ruamel."""
    y = _yaml()
    data = y.load(yaml_path.read_text(encoding="utf-8")) or {}
    if not isinstance(data, dict):
        return
    changed = False
    if "memory" in data:
        del data["memory"]
        changed = True
    agents = data.get("agents") or {}
    if "dreamer" in agents:
        del agents["dreamer"]
        if not agents:
            data.pop("agents", None)
        changed = True
    if data.get("default_agent") == "dreamer":
        del data["default_agent"]
        changed = True
    if not changed:
        return
    buf = io.StringIO()
    y.dump(data, buf)
    tmp = yaml_path.with_suffix(".yaml.tmp")
    tmp.write_text(buf.getvalue(), encoding="utf-8")
    tmp.replace(yaml_path)


def uninstall(ctx: InstallContext) -> None:
    yaml_path = ctx.aegis_dir / ".aegis.yaml"
    if yaml_path.exists():
        _strip_yaml(yaml_path)

    overlay = ctx.aegis_dir / ".aegis" / "schedules" / "memory-dream.yaml"
    if overlay.exists():
        overlay.unlink()

    mem_dir = ctx.aegis_dir / ".aegis" / "memory"
    if mem_dir.exists():
        consent = ctx._yes or ctx.confirm(
            f"Also delete {mem_dir} and all stored memories and dream logs?",
            default=False,
        )
        if consent:
            shutil.rmtree(mem_dir)
            if ctx.console is not None:
                ctx.console.print(
                    "[yellow]memory-system[/] removed (data deleted).")
        else:
            if ctx.console is not None:
                ctx.console.print(
                    f"[yellow]memory-system[/] removed "
                    f"(data preserved at {mem_dir}).")
