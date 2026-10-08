import asyncio

import pytest

from aegis.ops import Caller, OpError

from .conftest import until
from .test_agents import CONFIG, World, turn


@pytest.fixture
async def world(tmp_path, fake_claude):
    (tmp_path / ".aegis.yaml").write_text(CONFIG + "recap: {agent: opus}\n")
    w = await World(tmp_path, fake_claude).start()
    yield w
    await w.stop()


async def two_unread(world):
    a = await world.spawn()
    await turn(a, "first ask")
    await turn(a, "second ask?")
    assert len(a.unread) >= 2
    return a


async def test_a_long_unread_stretch_gets_one_recap_entry_and_its_cost(world):
    a = await two_unread(world)
    r = await world.app.registry.call("recap.request", {"log_id": a.log_id})
    assert r["status"] == "made"
    (e,) = [x for x in a.entries() if x["kind"] == "recap"]
    assert (
        e["detail"]["context"].startswith("recap of:")
        and e["detail"]["cost_usd"] == 0.004
    )
    assert a.wire()["recap_cost_usd"] == 0.004
    again = await world.app.registry.call("recap.request", {"log_id": a.log_id})
    assert again["status"] == "exists"
    forced = await world.app.registry.call(
        "recap.request", {"log_id": a.log_id, "force": True}
    )
    assert forced["status"] == "made"


async def test_two_requests_at_once_make_one_call(world):
    a = await two_unread(world)
    r1, r2 = await asyncio.gather(
        world.app.registry.call("recap.request", {"log_id": a.log_id}),
        world.app.registry.call("recap.request", {"log_id": a.log_id}),
    )
    assert {r1["status"], r2["status"]} <= {"made", "exists"}
    assert len([x for x in a.entries() if x["kind"] == "recap"]) == 1


async def test_skip_busy_off_and_people_only(world, tmp_path):
    a = await world.spawn()
    assert (await world.app.registry.call("recap.request", {"log_id": a.log_id}))[
        "status"
    ] == "skip"
    await a.send("/sleep 2")
    await until(lambda: a.status == "working", what="working")
    assert (
        await world.app.registry.call(
            "recap.request", {"log_id": a.log_id, "force": True}
        )
    )["status"] == "busy"
    with pytest.raises(OpError) as e:
        await world.app.registry.call(
            "recap.request", {"log_id": a.log_id}, Caller("agent", a.log_id)
        )
    assert e.value.code == "not_for_agents"
    (tmp_path / ".aegis.yaml").write_text(CONFIG)
    off = await world.app.registry.call(
        "recap.request", {"log_id": a.log_id, "force": True}
    )
    assert off["status"] in ("off", "busy")


@pytest.mark.parametrize("mode", ["fail", "garbage"])
async def test_a_broken_call_is_failed_and_frees_the_slot(world, mode, monkeypatch):
    a = await two_unread(world)
    monkeypatch.setenv("FAKE_CLAUDE_ONESHOT", mode)
    r = await world.app.registry.call("recap.request", {"log_id": a.log_id})
    assert r["status"] == "failed"
    assert not [x for x in a.entries() if x["kind"] == "recap"]
    monkeypatch.delenv("FAKE_CLAUDE_ONESHOT")
    assert (await world.app.registry.call("recap.request", {"log_id": a.log_id}))[
        "status"
    ] == "made"
