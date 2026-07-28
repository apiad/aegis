import pytest

from aegis.config import ConfigError
from aegis.config.yaml_loader import load_config


def _write(tmp_path, text):
    (tmp_path / ".aegis.yaml").write_text(text)
    return tmp_path


def test_harness_ref_agent_loads(tmp_path):
    _write(tmp_path, """
harnesses:
  fast:
    driver: opencode
    default_model: opencode/mimo-v2.5-free
default_agent: quick
agents:
  quick:
    harness: fast
""")
    cfg = load_config(tmp_path)
    a = cfg.agents["quick"]
    assert a.harness == "opencode"
    assert a.model == "opencode/mimo-v2.5-free"
    assert "fast" in cfg.harnesses


def test_legacy_provider_agent_still_loads(tmp_path):
    _write(tmp_path, """
default_agent: main
agents:
  main:
    provider: claude-code
    model: opus
    effort: high
""")
    cfg = load_config(tmp_path)
    assert cfg.agents["main"].harness == "claude-code"


def test_unknown_driver_fails_loud(tmp_path):
    _write(tmp_path, """
harnesses:
  bad:
    driver: not-a-driver
default_agent: x
agents:
  x: { harness: bad, model: m }
""")
    with pytest.raises(ConfigError):
        load_config(tmp_path)


def test_harness_overlay_merges(tmp_path):
    _write(tmp_path, """
default_agent: q
agents:
  q: { harness: openrouter }
""")
    hdir = tmp_path / ".aegis" / "harnesses"
    hdir.mkdir(parents=True)
    (hdir / "openrouter.yaml").write_text(
        "driver: lovelaice\n"
        "base_url: https://openrouter.ai/api/v1\n"
        "default_model: qwen/qwen3-32b\n")
    cfg = load_config(tmp_path)
    assert cfg.agents["q"].harness == "lovelaice"
    assert cfg.agents["q"].model == "qwen/qwen3-32b"
    assert cfg.harnesses["openrouter"].base_url.endswith("/v1")
