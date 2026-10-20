"""Every start picks its model: a cascade in the real app, with the fake
OpenCode and an agent of three models."""

import asyncio
import time

import pytest

from aegis.cascade import Fall
from aegis.ops import OpError

from .conftest import until
from .test_agents import World

CONFIG = """\
default_agent: free
agents:
  free:
    harness: opencode
    models: [opencode/a, opencode/b]
    effort: high
    permission: full
queues:
  general: {agent: free, max_parallel: 2}
"""


@pytest.fixture
async def world(tmp_path, fake_claude, fake_opencode):
    (tmp_path / ".aegis.yaml").write_text(CONFIG)
    w = await World(tmp_path, fake_claude, fake_opencode).start()
    yield w
    await w.stop()


async def spawn(world):
    r = await world.app.registry.call("session.spawn", {"agent": "free"})
    return world.session(r["log_id"])


async def test_spawn_starts_on_the_first_model_not_cooling(world):
    s = await spawn(world)
    assert s.spec.model == "opencode/a"
    assert s.spec.cascade == ("opencode/a", "opencode/b")
    world.app.cool("opencode", "opencode/a", Fall("model", None))
    s = await spawn(world)
    assert s.spec.model == "opencode/b"


async def test_spawn_is_refused_when_every_model_cools(world):
    for m in ("opencode/a", "opencode/b"):
        world.app.cool("opencode", m, Fall("model", None))
    with pytest.raises(OpError) as e:
        await spawn(world)
    assert e.value.code == "quota_closed"
    assert "every model in the cascade is cooling" in e.value.message


async def test_a_queue_task_waits_for_cooling_and_starts_when_it_ends(world):
    app = world.app
    for m in ("opencode/a", "opencode/b"):
        app.cool("opencode", m, Fall("model", 1.0))
    t = await asyncio.wait_for(
        app.queues.enqueue("general", "hello", False, None, str(world.root)), 3
    )
    assert t.status == "pending"
    assert app.cooling.earliest(time.time()) is not None
    await until(lambda: t.status != "pending", timeout=5, what="the cooling to end")
    assert world.session(t.worker).spec.model == "opencode/a"


async def test_a_handoff_keeps_a_model_chosen_outside_the_cascade(world):
    old = await spawn(world)
    await old.configure(model="opencode/z")
    for m in ("opencode/a", "opencode/b"):
        world.app.cool("opencode", m, Fall("model", None))
    r = await world.app.registry.call("session.handoff", {"log_id": old.log_id})
    assert world.session(r["log_id"]).spec.model == "opencode/z"
