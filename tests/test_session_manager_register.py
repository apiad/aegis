from unittest.mock import MagicMock

import pytest

from aegis.config import Agent
from aegis.core.manager import SessionManager


def _agent(model: str = "opus") -> Agent:
    return Agent(harness="claude-code", model=model)


def _sm():
    return SessionManager(
        agents={"r": _agent()},
        default_agent="r",
        make_session=lambda *a, **kw: MagicMock(),
        mcp=None,
        inbox=MagicMock())


def test_register_agent_adds_to_live_map():
    sm = _sm()
    sm.register_agent("designer", _agent(model="sonnet"))
    assert "designer" in sm.list_agents()
    assert sm._agents["designer"].model == "sonnet"


def test_register_agent_duplicate_slug_with_different_spec_raises():
    sm = _sm()
    with pytest.raises(ValueError, match="already registered"):
        sm.register_agent("r", _agent(model="sonnet"))


def test_register_agent_idempotent_on_identical():
    sm = _sm()
    sm.register_agent("designer", _agent(model="sonnet"))
    sm.register_agent("designer", _agent(model="sonnet"))   # no raise
    assert sm._agents["designer"].model == "sonnet"


def test_register_queue_forwards_to_queue_manager():
    sm = _sm()
    qm = MagicMock()
    sm.attach_queue_manager(qm)
    queue = MagicMock()
    sm.register_queue(queue)
    qm.register_queue.assert_called_once_with(queue)


def test_register_queue_without_queue_manager_raises():
    sm = _sm()
    with pytest.raises(RuntimeError, match="no queue_manager attached"):
        sm.register_queue(MagicMock())


def test_reload_plugins_invokes_import_plugins(monkeypatch):
    sm = _sm()
    from pathlib import Path
    sm.state_root = Path("/tmp")
    calls = []
    monkeypatch.setattr(
        "aegis.config.yaml_loader.load_config",
        lambda root: MagicMock(plugin_dirs=[]))
    monkeypatch.setattr(
        "aegis.config.yaml_loader.import_plugins",
        lambda cfg: calls.append(cfg))
    sm.reload_plugins()
    assert len(calls) == 1
