from __future__ import annotations

import textwrap
from pathlib import Path

from aegis.cli import _ensure_web_token
from aegis.config.yaml_loader import load_config


def _min_config(root: Path) -> None:
    (root / ".aegis.yaml").write_text(textwrap.dedent("""
        default_agent: c
        agents:
          c:
            provider: claude-code
            model: opus
    """))


def test_ensure_web_token_generates_and_persists(tmp_path: Path):
    _min_config(tmp_path)
    token = _ensure_web_token(tmp_path)
    assert token and len(token) >= 20
    cfg = load_config(tmp_path)
    assert cfg.web is not None
    assert cfg.web.token == token


def test_ensure_web_token_is_idempotent(tmp_path: Path):
    _min_config(tmp_path)
    first = _ensure_web_token(tmp_path)
    second = _ensure_web_token(tmp_path)
    assert first == second
