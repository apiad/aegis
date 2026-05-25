from __future__ import annotations

from pathlib import Path

import pytest

from aegis.remote.config import RemotePlaneSpec, RemoteSpec


def test_remote_spec_minimal() -> None:
    spec = RemoteSpec(url="http://vps.tail-net.ts.net:8556")
    assert spec.url == "http://vps.tail-net.ts.net:8556"
    assert spec.token is None


def test_remote_spec_with_token() -> None:
    spec = RemoteSpec(url="http://vps:8556", token="secret")
    assert spec.token == "secret"


def test_remote_spec_rejects_missing_scheme() -> None:
    with pytest.raises(ValueError, match="must include scheme"):
        RemoteSpec(url="vps:8556")


def test_remote_plane_spec_minimal() -> None:
    p = RemotePlaneSpec(bind="100.64.0.1:8556")
    assert p.bind == "100.64.0.1:8556"
    assert p.accept_tokens == []
    assert p.accept_from == []


def test_remote_plane_spec_rejects_unparseable_bind() -> None:
    with pytest.raises(ValueError, match="bind"):
        RemotePlaneSpec(bind="not-a-host-port")


def _write(p: Path, body: str) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(body)


def test_load_remotes_inline(tmp_path: Path) -> None:
    from aegis.config.yaml_loader import load_config

    _write(tmp_path / ".aegis.yaml", """
remotes:
  vps:
    url: http://vps.tail-net.ts.net:8556
""")
    cfg = load_config(tmp_path)
    assert "vps" in cfg.remotes
    assert cfg.remotes["vps"].url == "http://vps.tail-net.ts.net:8556"
    assert cfg.remotes["vps"].token is None


def test_load_remotes_overlay(tmp_path: Path) -> None:
    from aegis.config.yaml_loader import load_config

    _write(tmp_path / ".aegis.yaml", "")
    _write(tmp_path / ".aegis" / "remotes" / "vps.yaml", """
url: http://vps:8556
token: secret
""")
    cfg = load_config(tmp_path)
    assert cfg.remotes["vps"].token == "secret"


def test_load_remotes_conflict_aborts(tmp_path: Path) -> None:
    from aegis.config import ConfigError
    from aegis.config.yaml_loader import load_config

    _write(tmp_path / ".aegis.yaml", """
remotes:
  vps:
    url: http://vps:8556
""")
    _write(tmp_path / ".aegis" / "remotes" / "vps.yaml", """
url: http://vps:9999
""")
    with pytest.raises(ConfigError, match="remotes"):
        load_config(tmp_path)


def test_load_remote_plane(tmp_path: Path) -> None:
    from aegis.config.yaml_loader import load_config

    _write(tmp_path / ".aegis.yaml", """
remote_plane:
  bind: 100.64.0.1:8556
  accept_tokens:
    - token-a
""")
    cfg = load_config(tmp_path)
    assert cfg.remote_plane is not None
    assert cfg.remote_plane.bind == "100.64.0.1:8556"
    assert cfg.remote_plane.accept_tokens == ["token-a"]


def test_load_remote_plane_absent_is_none(tmp_path: Path) -> None:
    from aegis.config.yaml_loader import load_config

    _write(tmp_path / ".aegis.yaml", "")
    cfg = load_config(tmp_path)
    assert cfg.remote_plane is None


def test_remote_spec_accepts_peer_name():
    from aegis.remote.config import RemoteSpec
    spec = RemoteSpec(url="http://1.2.3.4:8556", peer_name="laptop")
    assert spec.peer_name == "laptop"


def test_remote_spec_peer_name_defaults_to_none():
    from aegis.remote.config import RemoteSpec
    spec = RemoteSpec(url="http://1.2.3.4:8556")
    assert spec.peer_name is None
