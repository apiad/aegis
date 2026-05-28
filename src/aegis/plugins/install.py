"""install_plugin — local-path source. Registry resolution lives in slice 4."""
from __future__ import annotations

import importlib.util
import shutil
import sys
import time
from io import StringIO
from pathlib import Path
from typing import Any

from ruamel.yaml import YAML

from aegis.plugins import lockfile
from aegis.plugins.install_context import InstallContext
from aegis.plugins.manifest import load_manifest


class InstallError(RuntimeError):
    """Install failed in a way the caller should surface to the user."""


def install_plugin(
    *,
    name: str,
    source: Path,
    project_root: Path,
    yes: bool = False,
    force: bool = False,
    console: Any = None,
) -> None:
    """Install a plugin from a local-path source.

    Steps: parse manifest → copy → merge config → run _install.py → record lock.
    Rolls back the copy on _install.py failure; lockfile is only written on
    full success. Config merges are not rolled back (user-visible state).
    """
    src_manifest = source / "plugin.toml"
    if not src_manifest.exists():
        raise InstallError(f"no plugin.toml at {source}")
    manifest = load_manifest(src_manifest)
    if manifest.name != name:
        raise InstallError(
            f"manifest name {manifest.name!r} does not match requested {name!r}"
        )

    dest = project_root / ".aegis" / "plugins" / name
    if dest.exists() and not force:
        raise InstallError(f"plugin {name!r} already installed at {dest}")
    if dest.exists():
        shutil.rmtree(dest)

    # 1. Copy
    shutil.copytree(source, dest)

    # 2. Config merge
    try:
        _merge_default_config(project_root, name, manifest.default_config)
    except Exception:
        shutil.rmtree(dest, ignore_errors=True)
        raise

    # 3. _install.py
    install_py = dest / "_install.py"
    if install_py.exists():
        ctx = InstallContext(
            project_root=project_root,
            aegis_dir=project_root / ".aegis",
            plugin_dir=dest,
            plugin_name=name,
            manifest=manifest.raw,
            config=None,
            console=console,
            _yes=yes,
        )
        try:
            _invoke_install_py(install_py, ctx)
        except Exception:
            shutil.rmtree(dest, ignore_errors=True)
            raise

    # 4. Lockfile
    lockfile.upsert(project_root, {
        "name":      name,
        "version":   manifest.version,
        "source":    str(source),
        "installed": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "file_hashes": lockfile.hash_dir(dest),
    })


def _invoke_install_py(path: Path, ctx: InstallContext) -> None:
    spec = importlib.util.spec_from_file_location(
        f"_aegis_install_{ctx.plugin_name}", path,
    )
    if spec is None or spec.loader is None:
        raise InstallError(f"could not load {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    fn = getattr(module, "install", None)
    if fn is None:
        return  # no install fn, fine
    fn(ctx)


def _yaml() -> YAML:
    y = YAML()
    y.preserve_quotes = True
    y.indent(mapping=2, sequence=4, offset=2)
    return y


def _merge_default_config(
    project_root: Path, name: str, default_config: dict[str, Any],
) -> None:
    """Merge default_config into .aegis.yaml under plugins.<name>.

    Uses ruamel.yaml directly to preserve comments. Bypasses the regular
    validator since `plugins:` is a new section the schema doesn't yet know.
    """
    if not default_config:
        return
    yaml_path = project_root / ".aegis.yaml"
    y = _yaml()
    if yaml_path.exists():
        data = y.load(yaml_path.read_text()) or {}
    else:
        data = {}
    plugins_section = data.setdefault("plugins", {})
    plugin_section = plugins_section.setdefault(name, {})
    for k, v in default_config.items():
        plugin_section.setdefault(k, v)  # never overwrite user edits
    buf = StringIO()
    y.dump(data, buf)
    yaml_path.write_text(buf.getvalue())
