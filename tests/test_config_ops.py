import pytest

from aegis.app import App
from aegis.ops import Caller, OpError
from aegis.roots import make_roots

CONFIG = "default_agent: opus\nagents:\n  opus: {harness: claude-code, model: opus, effort: high, permission: full}\n"


@pytest.fixture
def app(tmp_path, fake_claude, fake_opencode):
    (tmp_path / ".aegis.yaml").write_text(CONFIG)
    return App(
        make_roots(tmp_path, tmp_path),
        claude_bin=fake_claude,
        opencode_bin=fake_opencode,
    )


async def test_read_then_write_round_trips_and_publishes(app, tmp_path):
    sent = []
    app.channels.subscribe("config", sent.append)
    w = await app.registry.call("config.read", {})
    w["doc"]["agents"][0]["effort"] = "max"
    r = await app.registry.call("config.write", {"doc": w["doc"], "stamp": w["stamp"]})
    assert r["saved"] and r["problems"] == []
    assert "effort: max" in (tmp_path / ".aegis.yaml").read_text()
    assert r["config"]["doc"]["agents"][0]["effort"] == "max"
    assert (
        sent[-1]["t"] == "patch"
        and sent[-1]["ops"][0]["set"]["doc"]["agents"][0]["effort"] == "max"
    )


async def test_a_stale_write_is_refused(app, tmp_path):
    w = await app.registry.call("config.read", {})
    (tmp_path / ".aegis.yaml").write_text(CONFIG + "# edited\n")
    with pytest.raises(OpError) as e:
        await app.registry.call("config.write", {"doc": w["doc"], "stamp": w["stamp"]})
    assert e.value.code == "stale"


async def test_an_invalid_write_returns_its_problems(app):
    w = await app.registry.call("config.read", {})
    w["doc"]["default_agent"] = "ghost"
    r = await app.registry.call("config.write", {"doc": w["doc"], "stamp": w["stamp"]})
    assert not r["saved"] and r["problems"][0]["row"] == "default_agent"


async def test_agents_may_run_the_doctor_but_not_write(app):
    agent = Caller("agent", log_id="x")
    with pytest.raises(OpError) as e:
        await app.registry.call("config.write", {"doc": {}, "stamp": None}, agent)
    assert e.value.code == "not_for_agents"
    assert "config.doctor" in [op.name for op in app.registry.agent_ops()]


@pytest.mark.slow
async def test_doctor_detect_and_propose(app, monkeypatch):
    import aegis.config_ops as config_ops

    calls = []
    real = config_ops.detect

    async def counted(*args, **kwargs):
        calls.append(args)
        return await real(*args, **kwargs)

    monkeypatch.setattr(config_ops, "detect", counted)
    findings = await app.registry.call("config.doctor", {})
    assert not [f for f in findings if f["level"] == "error"]
    found = await app.registry.call("config.detect", {})
    assert [f["harness"] for f in found] == ["claude-code", "opencode"]
    doc = await app.registry.call("config.propose", {})
    assert doc["default_agent"] == "opus"
    assert len(calls) == 1, "detect and propose share one probe within DETECT_TTL_S"


async def test_the_stamp_survives_a_browser_round_trip(app, tmp_path):
    # A JavaScript number holds integers exactly only up to 2**53, and st_mtime_ns
    # is about 1.8e18: a numeric stamp came back rounded and every save from the
    # page was refused as stale.
    import json

    w = json.loads(
        json.dumps(await app.registry.call("config.read", {})), parse_int=float
    )
    r = await app.registry.call("config.write", {"doc": w["doc"], "stamp": w["stamp"]})
    assert r["saved"]
