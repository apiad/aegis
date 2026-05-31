"""Setup hook for memory-system: directory tree, stub files, yaml edits,
optional schedule overlay."""
from __future__ import annotations

import io
from pathlib import Path

from ruamel.yaml import YAML

from aegis.config.edit import add_agent as _add_agent
from aegis.plugins.install_context import InstallContext
from aegis.scheduler.push import write_atomic as _write_schedule


def _yaml() -> YAML:
    y = YAML()
    y.preserve_quotes = True
    y.default_flow_style = False
    return y


def _add_top_level_section(yaml_path: Path, key: str, value: dict) -> None:
    y = _yaml()
    text = yaml_path.read_text(encoding="utf-8") if yaml_path.exists() else ""
    data = y.load(text) or {}
    if key in data:
        return
    data[key] = value
    buf = io.StringIO()
    y.dump(data, buf)
    tmp = yaml_path.with_suffix(".yaml.tmp")
    tmp.write_text(buf.getvalue(), encoding="utf-8")
    tmp.replace(yaml_path)


SOUL_STUB = """\
# Voice

(Edit this file to shape the agent's voice and behavior. The memory-system
plugin injects it on every session's first turn.)

- Concise.
- Explicit about uncertainty.
- Direct when correcting.
"""

USER_STUB = """\
# User

(Edit this file to record who the user is. The memory-system plugin
injects it on every session's first turn.)

- Name:
- Role:
- Preferences:
"""

MEMORY_STUB = """\
# Memory index

## Index
"""


def _add_dreamer_if_absent(root: Path) -> None:
    yaml_path = root / ".aegis.yaml"
    text = yaml_path.read_text(encoding="utf-8") if yaml_path.exists() else ""
    if "dreamer:" in text:
        return
    _add_agent(
        root, "dreamer",
        provider="claude-code", model="haiku",
        effort="low", permission="write",
    )


def _maybe_install_schedule(ctx: InstallContext) -> bool:
    if not ctx.confirm(
        "Schedule the dream pass daily at 3am?", default=True,
    ):
        return False
    _write_schedule(
        state_root=ctx.aegis_dir,
        name="memory-dream",
        spec={
            "workflow":  "dream",
            "cron":      "0 3 * * *",
            "lifecycle": "forever",
        },
        pushed_from="plugin:memory-system",
    )
    return True


def install(ctx: InstallContext) -> None:
    mem_dir = ctx.aegis_dir / ".aegis" / "memory"
    (mem_dir / "entries").mkdir(parents=True, exist_ok=True)
    (mem_dir / "dreams").mkdir(parents=True, exist_ok=True)

    for fname, body in (("SOUL.md", SOUL_STUB),
                        ("USER.md", USER_STUB),
                        ("MEMORY.md", MEMORY_STUB)):
        path = mem_dir / fname
        if not path.exists():
            path.write_text(body, encoding="utf-8")

    yaml_path = ctx.aegis_dir / ".aegis.yaml"
    _add_dreamer_if_absent(ctx.aegis_dir)
    defaults = dict(ctx.manifest.get(
        "default_config",
        {"lookback_days": 7, "max_session_files": 50,
         "dreamer_agent": "dreamer"}))
    _add_top_level_section(yaml_path, "memory", defaults)

    scheduled = _maybe_install_schedule(ctx)

    if ctx.console is not None:
        msg = f"[green]memory-system[/] ready at {mem_dir}/"
        if scheduled:
            msg += (" * dream scheduled at 03:00 -- fires "
                    "whenever `aegis serve` is running")
        ctx.console.print(msg)
