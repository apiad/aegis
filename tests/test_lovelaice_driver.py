from pathlib import Path

import pytest

from aegis.config import Agent, Lovelaice


def test_lovelaice_provider_parses():
    a = Agent(provider=Lovelaice(model="qwen2.5:7b",
                                 base_url="http://localhost:11434/v1"))
    assert a.harness == "lovelaice"
    assert a.model == "qwen2.5:7b"
    assert a.provider.base_url == "http://localhost:11434/v1"


def test_lovelaice_flat_shape_resolves():
    a = Agent(harness="lovelaice", model="anthropic/claude-haiku-4-5")
    assert a.provider.name == "lovelaice"


def test_acp_session_accepts_extra_env():
    from aegis.drivers.acp import AcpSession
    s = AcpSession(agent=None, cwd="/tmp", mcp_url="", handle="h",
                   extra_env={"LOVELAICE_MODEL": "x"})
    assert s._extra_env == {"LOVELAICE_MODEL": "x"}


def test_driver_registered():
    from aegis.drivers import get_driver
    from aegis.drivers.lovelaice import LovelaiceDriver
    assert isinstance(get_driver("lovelaice"), LovelaiceDriver)


def test_extra_env_maps_model_base_url_and_key(tmp_path):
    from aegis.drivers.lovelaice import LovelaiceDriver
    key = tmp_path / "or.token"
    key.write_text("sk-test-123\n")
    a = Agent(provider=Lovelaice(model="qwen2.5:7b",
                                 base_url="http://localhost:11434/v1",
                                 api_key_file=str(key)))
    env = LovelaiceDriver().extra_env(a)
    assert env["LOVELAICE_MODEL"] == "qwen2.5:7b"
    assert env["LOVELAICE_BASE_URL"] == "http://localhost:11434/v1"
    assert env["OPENROUTER_API_KEY"] == "sk-test-123"


def test_base_cmd():
    from aegis.drivers.lovelaice import LovelaiceDriver
    assert LovelaiceDriver().build_argv(
        Agent(harness="lovelaice", model="m"), ".", "", "h") == ["lovelaice-acp"]
