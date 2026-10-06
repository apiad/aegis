"""Hermetic tests for the socks-proxy plugin (install + runtime hook)."""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

from aegis.hooks import PreSpawnContext, SessionHandle
from aegis.hooks.decorator import _reset_registry_for_tests
from aegis.plugins.install_context import InstallContext


def _load_module(name: str):
    repo_root = Path(__file__).parent.parent
    path = repo_root / "plugins" / "socks-proxy" / f"{name}.py"
    spec = importlib.util.spec_from_file_location(f"_test_sp_{name}", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[f"_test_sp_{name}"] = module
    spec.loader.exec_module(module)
    return module


def _ctx(tmp_path: Path, *, yes: bool) -> InstallContext:
    (tmp_path / ".aegis.yaml").write_text("", encoding="utf-8")
    aegis_dir = tmp_path / ".aegis"
    aegis_dir.mkdir(exist_ok=True)
    return InstallContext(
        project_root=tmp_path,
        aegis_dir=aegis_dir,
        plugin_dir=aegis_dir / "plugins" / "socks-proxy",
        plugin_name="socks-proxy",
        manifest={"plugin": {"name": "socks-proxy", "version": "0.1.0"}},
        config=None,
        console=None,
        _confirm_default=True,
        _yes=yes,
    )


def _spawn_ctx(cwd: Path,
               argv: tuple[str, ...] = ("claude", "-p")) -> PreSpawnContext:
    return PreSpawnContext(
        session=SessionHandle(handle="h", agent_profile="h",
                              harness="claude-code"),
        argv=argv,
        env={"PATH": "/usr/bin"},
        cwd=str(cwd),
    )


@pytest.fixture(autouse=True)
def _clean():
    _reset_registry_for_tests()
    yield
    _reset_registry_for_tests()


# ---------- install / uninstall -----------------------------------------


def test_install_writes_proxychains_conf_with_default_endpoint(
    tmp_path: Path,
) -> None:
    install = _load_module("_install")
    install.install(_ctx(tmp_path, yes=True))
    conf = tmp_path / ".aegis" / "socks-proxy.conf"
    assert conf.is_file()
    body = conf.read_text()
    assert "[ProxyList]" in body
    assert "socks5 127.0.0.1 1080" in body
    assert "proxy_dns" in body


def test_install_is_idempotent(tmp_path: Path) -> None:
    install = _load_module("_install")
    ctx = _ctx(tmp_path, yes=True)
    install.install(ctx)
    conf = tmp_path / ".aegis" / "socks-proxy.conf"
    conf.write_text(conf.read_text() + "\n# user edit\n")
    install.install(ctx)
    assert "# user edit" in conf.read_text()


def test_uninstall_removes_conf(tmp_path: Path) -> None:
    install = _load_module("_install")
    uninstall = _load_module("_uninstall")
    ctx = _ctx(tmp_path, yes=True)
    install.install(ctx)
    conf = tmp_path / ".aegis" / "socks-proxy.conf"
    assert conf.exists()
    uninstall.uninstall(ctx)
    assert not conf.exists()


# ---------- runtime hook ------------------------------------------------


@pytest.mark.asyncio
async def test_pre_spawn_hook_prepends_proxychains_when_conf_present(
    tmp_path: Path,
) -> None:
    install = _load_module("_install")
    install.install(_ctx(tmp_path, yes=True))
    sp = _load_module("socks_proxy")
    result = await sp.proxify(_spawn_ctx(tmp_path))
    assert result is not None
    expected_conf = tmp_path / ".aegis" / "socks-proxy.conf"
    assert result.argv == (
        "proxychains4", "-q", "-f", str(expected_conf), "claude", "-p")


@pytest.mark.asyncio
async def test_pre_spawn_hook_is_noop_when_conf_absent(tmp_path: Path) -> None:
    # project_root present (.aegis.yaml exists) but no conf file.
    (tmp_path / ".aegis.yaml").write_text("", encoding="utf-8")
    sp = _load_module("socks_proxy")
    result = await sp.proxify(_spawn_ctx(tmp_path))
    assert result is None


@pytest.mark.asyncio
async def test_pre_spawn_hook_is_noop_outside_project(tmp_path: Path) -> None:
    sp = _load_module("socks_proxy")
    result = await sp.proxify(_spawn_ctx(tmp_path))
    assert result is None
