from __future__ import annotations

import pytest

from aegis.config.yaml_loader import AegisConfig
from aegis.mcp.server import build_server
from aegis.workflow.decorator import _REGISTRY
from aegis.workflows import dynamic as _dyn_mod  # noqa: F401  registers "dynamic"


@pytest.fixture
def dsl_mcp_env(fake_bridge_with_runner, tmp_path, monkeypatch):
    """Build server against fake bridge; stub config load with a hermetic
    AegisConfig exposing agents={'w'}, default_agent='w', threshold=5."""

    fake_bridge_with_runner.set_state_dir(tmp_path / "wf")
    fake_bridge_with_runner.session_send_and_await = (
        fake_bridge_with_runner.send_and_await_reply)

    cfg = AegisConfig(
        default_agent="w",
        agents={"w": object()},  # only key set is checked (agents lookup)
        queues={},
        root=tmp_path,
        dynamic_workflow_autoapprove_agents=5)

    monkeypatch.setattr(
        "aegis.config.find_project_root", lambda: tmp_path, raising=True)
    monkeypatch.setattr(
        "aegis.config.yaml_loader.load_config", lambda root: cfg, raising=True)

    server = build_server(fake_bridge_with_runner)

    async def get_tool(name):
        tools = await server.list_tools()
        return next(t for t in tools if t.name == name)

    class Env:
        pass

    env = Env()
    env.server = server
    env.get_tool = get_tool
    env.cfg = cfg
    return env


@pytest.fixture(autouse=True)
def _keep_dynamic_registered():
    saved = dict(_REGISTRY)
    yield
    _REGISTRY.clear()
    _REGISTRY.update(saved)


async def _call(tool, args):
    res = await tool.run(args)
    return res.structured_content


async def test_run_dynamic_workflow_gated_above_threshold(dsl_mcp_env):
    tool = await dsl_mcp_env.get_tool("aegis_run_dynamic_workflow")
    spec = {"meta": {"name": "big"},
            "root": {"type": "loop", "id": "r", "max_rounds": 50,
                     "until": {"kind": "shell", "cmd": "true"},
                     "body": {"type": "agent", "prompt": "p",
                              "target": {"kind": "spawn", "profile": "w"}}}}
    res = await _call(tool, {"spec": spec, "from_handle": "agent-7"})
    assert res["status"] == "gated"
    assert res["projected_agents"] == 50
    assert "upper bound" in res["plan"]


async def test_run_dynamic_workflow_autoapprove_and_launch(dsl_mcp_env):
    tool = await dsl_mcp_env.get_tool("aegis_run_dynamic_workflow")
    spec = {"meta": {"name": "small"},
            "root": {"type": "agent", "id": "a", "prompt": "p",
                     "target": {"kind": "spawn", "profile": "w"}}}
    res = await _call(tool, {"spec": spec, "from_handle": "agent-7"})
    assert res["status"] == "running"
    assert "workflow_id" in res


async def test_malformed_spec_returns_validation_error(dsl_mcp_env):
    tool = await dsl_mcp_env.get_tool("aegis_run_dynamic_workflow")
    res = await _call(tool, {"spec": {"meta": {"name": "x"},
                                      "root": {"type": "frobnicate"}},
                             "from_handle": "a"})
    assert "error" in res
