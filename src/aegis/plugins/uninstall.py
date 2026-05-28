"""uninstall_plugin — run _uninstall.py, delete folder, strip config."""
from __future__ import annotations

import importlib.util
import shutil
import sys
from pathlib import Path
from typing import Any

from aegis.plugins import lockfile
from aegis.plugins.install_context import InstallContext


class UninstallError(RuntimeError):
    """Uninstall failed in a way the caller should surface."""


def uninstall_plugin(
    *,
    name: str,
    project_root: Path,
    yes: bool = False,
    console: Any = None,
) -> None:
    """Reverse the install:

    1. Run `_uninstall.py::uninstall(ctx)` if present. Exceptions logged + continue.
    2. Delete `.aegis/plugins/<name>/`.
    3. Strip `plugins.<name>` from `.aegis.yaml` keys that were declared in
       the manifest's [default_config] (user hand-edits preserved).
    4. Remove the lockfile entry.
    """
    plugin_dir = project_root / ".aegis" / "plugins" / name
    if not plugin_dir.exists():
        raise UninstallError(f"plugin {name!r} not installed")

    # 1. Read manifest (still inside plugin_dir before we delete) so we know
    # which config keys we wrote at install time.
    from aegis.plugins.manifest import load_manifest
    manifest_path = plugin_dir / "plugin.toml"
    default_config: dict[str, Any] = {}
    manifest = None
    if manifest_path.exists():
        manifest = load_manifest(manifest_path)
        default_config = manifest.default_config

    # 2. _uninstall.py
    uninstall_py = plugin_dir / "_uninstall.py"
    if uninstall_py.exists():
        ctx = InstallContext(
            project_root=project_root,
            aegis_dir=project_root / ".aegis",
            plugin_dir=plugin_dir,
            plugin_name=name,
            manifest=manifest.raw if manifest is not None else {},
            config=None,
            console=console,
            _yes=yes,
        )
        try:
            _invoke_uninstall(uninstall_py, ctx)
        except Exception as exc:  # noqa: BLE001 — log + continue
            import logging
            logging.getLogger("aegis.plugins").exception(
                "uninstall hook for %s raised: %s", name, exc,
            )

    # 3. Delete folder
    shutil.rmtree(plugin_dir)

    # 4. Strip config keys
    _strip_config(project_root, name, default_config)

    # 5. Remove lockfile entry
    lockfile.remove(project_root, name)


def _invoke_uninstall(path: Path, ctx: InstallContext) -> None:
    spec = importlib.util.spec_from_file_location(
        f"_aegis_uninstall_{ctx.plugin_name}", path,
    )
    if spec is None or spec.loader is None:
        raise UninstallError(f"could not load {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    fn = getattr(module, "uninstall", None)
    if fn is not None:
        fn(ctx)


def _strip_config(
    project_root: Path, name: str, default_config: dict[str, Any],
) -> None:
    yaml_path = project_root / ".aegis.yaml"
    if not yaml_path.exists():
        return
    from ruamel.yaml import YAML
    yaml = YAML(typ="rt")
    data = yaml.load(yaml_path)
    if not isinstance(data, dict):
        return
    plugins = data.get("plugins") or {}
    plugin_section = plugins.get(name) or {}
    for k in default_config:
        plugin_section.pop(k, None)
    if not plugin_section:
        plugins.pop(name, None)
    if not plugins:
        data.pop("plugins", None)
    with yaml_path.open("w") as f:
        yaml.dump(data, f)
