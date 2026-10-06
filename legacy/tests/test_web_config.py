from __future__ import annotations

import textwrap
from pathlib import Path

from aegis.config import WebConfig
from aegis.config.yaml_loader import load_config


def test_web_block_parses(tmp_path: Path) -> None:
    (tmp_path / ".aegis.yaml").write_text(textwrap.dedent("""
        default_agent: c
        agents:
          c:
            provider: claude-code
            model: opus
        web:
          token: "abc"
          bind: "127.0.0.1"
          port: 8765
    """))
    cfg = load_config(tmp_path)
    assert isinstance(cfg.web, WebConfig)
    assert cfg.web.token == "abc"
    assert cfg.web.bind == "127.0.0.1"
    assert cfg.web.port == 8765


def test_no_web_block_is_none(tmp_path: Path) -> None:
    (tmp_path / ".aegis.yaml").write_text(
        "default_agent: c\n"
        "agents:\n  c:\n    provider: claude-code\n    model: opus\n")
    cfg = load_config(tmp_path)
    assert cfg.web is None


def test_web_defaults(tmp_path: Path) -> None:
    (tmp_path / ".aegis.yaml").write_text(textwrap.dedent("""
        default_agent: c
        agents:
          c:
            provider: claude-code
            model: opus
        web:
          token: "t"
    """))
    cfg = load_config(tmp_path)
    assert cfg.web.bind == "127.0.0.1"
    assert cfg.web.port is None


def test_env_token_overrides_yaml(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("AEGIS_WEB_TOKEN", "from-env")
    (tmp_path / ".aegis.yaml").write_text(textwrap.dedent("""
        default_agent: c
        agents:
          c:
            provider: claude-code
            model: opus
        web:
          token: "from-yaml"
    """))
    cfg = load_config(tmp_path)
    assert cfg.web.token == "from-env"
