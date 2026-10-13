"""Context size, cache counters and the handoff, end to end on the fake claude."""

import pytest

from aegis.ops import Caller

from .conftest import until
from .test_agents import CONFIG, World, turn


@pytest.fixture
async def world(tmp_path, fake_claude):
    (tmp_path / ".aegis.yaml").write_text(CONFIG)
    w = await World(tmp_path, fake_claude).start()
    yield w
    await w.stop()


async def test_ctx_sets_the_context_size(world):
    a = await world.spawn()
    await turn(a, "/ctx 350000")
    assert a.context_tokens == 350000


async def test_counters_count_parentless_calls_and_survive_a_restart(world):
    a = await world.spawn()
    await a.send("/many 3")  # writes no prose, so turn() would never return
    await until(lambda: a.tool_calls == 3 and a.status == "idle", 8, "three calls")
    await turn(a, "/ctx 200000 dup")
    await a.send("/compact")
    await until(lambda: a.wire()["compactions"] == 1, 8, "the compaction counted")
    w = a.wire()
    assert w["tool_calls"] == 4  # three from /many, one from the dup line
    reads = [e for e in a.cache_recent if e[1] > 100000]
    assert len(reads) == 1  # the dup line's usage counted once
    log_id, recent = a.log_id, list(a.cache_recent)
    await world.restart()
    b = world.session(log_id)
    assert (b.tool_calls, b.compactions, b.cache_recent) == (4, 1, recent)


async def test_a_miss_in_a_warm_session_turns_the_rate_red(world):
    a = await world.spawn()
    await turn(a, "/ctx 200000")
    await turn(a, "/ctx 200000 miss")
    w = a.wire()
    assert w["cache_hit"] < 80 and w["cache_alarm"] is True


async def test_the_card_carries_the_cold_thresholds_for_claude(world):
    a = await world.spawn()
    assert a.wire()["cache_cold"] == {"after": 3600, "min": 100000}


async def end(world, a, **args):
    args = {"attention": "done", "line": "Shipped it."} | args
    return await world.app.registry.call("turn.end", args, Caller("agent", a.log_id))


async def test_turn_end_answers_noted_below_the_threshold(world):
    a = await world.spawn()
    await turn(a, "/ctx 299999")
    assert await end(world, a) == "noted"


async def test_turn_end_names_the_context_above_it_and_takes_a_brief(world):
    a = await world.spawn()
    await turn(a, "/ctx 342000")
    said = await end(world, a)
    assert said.startswith("noted. Your context is 342k tokens")
    assert "handoff" in said
    assert await end(world, a, handoff="Done: A. Next: B.") == "noted"
    assert a.wire()["handoff"] == "Done: A. Next: B."
    await end(world, a)
    assert a.wire()["handoff"] is None  # a later turn_end without one clears it


async def test_handoff_at_zero_turns_the_line_off(world):
    cfg = world.root / ".aegis.yaml"
    cfg.write_text(cfg.read_text() + "context: {handoff_at: 0}\n")
    a = await world.spawn()
    await turn(a, "/ctx 900000")
    assert await end(world, a) == "noted"


async def test_a_queue_worker_always_hears_noted_and_keeps_no_brief(world):
    a = await world.spawn()
    await turn(a, "/ctx 400000")
    a.worker = {"queue": "general", "task_id": "t1"}  # what queues.py sets
    assert await end(world, a) == "noted"
    assert await end(world, a, handoff="Brief.") == "noted"
    assert a.standing["report"].get("handoff") is None
