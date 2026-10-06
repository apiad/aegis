import pytest

from aegis.config import ConfigError
from aegis.config.edit import add_harness, remove_harness
from aegis.config.yaml_loader import load_config

_BASE = ("default_agent: m\n"
         "agents:\n  m: { provider: claude-code, model: opus }\n")


def test_add_harness_roundtrips(tmp_path):
    (tmp_path / ".aegis.yaml").write_text(_BASE)
    add_harness(tmp_path, "openrouter", driver="lovelaice",
                base_url="https://openrouter.ai/api/v1",
                default_model="qwen/qwen3-32b")
    cfg = load_config(tmp_path)
    assert cfg.harnesses["openrouter"].base_url.endswith("/v1")
    assert cfg.harnesses["openrouter"].default_model == "qwen/qwen3-32b"


def test_add_harness_unknown_driver(tmp_path):
    (tmp_path / ".aegis.yaml").write_text(_BASE)
    with pytest.raises(ConfigError):
        add_harness(tmp_path, "bad", driver="nope")


def test_add_harness_duplicate_fails(tmp_path):
    (tmp_path / ".aegis.yaml").write_text(_BASE)
    add_harness(tmp_path, "or", driver="lovelaice")
    with pytest.raises(ConfigError):
        add_harness(tmp_path, "or", driver="lovelaice")


def test_remove_harness(tmp_path):
    (tmp_path / ".aegis.yaml").write_text(_BASE)
    add_harness(tmp_path, "or", driver="lovelaice",
                default_model="qwen/qwen3-32b")
    remove_harness(tmp_path, "or")
    cfg = load_config(tmp_path)
    assert "or" not in cfg.harnesses  # implicit-only registry has no 'or'


def test_remove_missing_harness_fails(tmp_path):
    (tmp_path / ".aegis.yaml").write_text(_BASE)
    with pytest.raises(ConfigError):
        remove_harness(tmp_path, "nope")
