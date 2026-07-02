from pathlib import Path

from aegis.config import VoiceConfig
from aegis.config.yaml_loader import load_config


def _write(tmp_path: Path, body: str) -> Path:
    (tmp_path / ".aegis.yaml").write_text(body)
    return tmp_path


def test_voice_defaults_when_absent(tmp_path):
    root = _write(tmp_path, "default_agent: a\n"
        "agents:\n  a: {provider: claude-code, model: opus}\n")
    cfg = load_config(root)
    assert cfg.voice == VoiceConfig()
    assert cfg.voice.enabled is False


def test_voice_block_parsed(tmp_path):
    root = _write(tmp_path, (
        "default_agent: a\n"
        "agents:\n  a: {provider: claude-code, model: opus}\n"
        "voice:\n"
        "  enabled: true\n"
        "  model: small\n"
        "  key: ctrl+b\n"
        "  preview: true\n"
        "  language: en\n"
    ))
    cfg = load_config(root)
    assert cfg.voice == VoiceConfig(
        enabled=True, model="small", key="ctrl+b",
        preview=True, language="en")


def test_voice_partial_block_fills_defaults(tmp_path):
    root = _write(tmp_path, (
        "default_agent: a\n"
        "agents:\n  a: {provider: claude-code, model: opus}\n"
        "voice:\n  enabled: true\n"
    ))
    cfg = load_config(root)
    assert cfg.voice.enabled is True
    assert cfg.voice.model == "base"
    assert cfg.voice.key == "ctrl+g"
