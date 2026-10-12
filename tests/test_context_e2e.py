"""Context size, cache counters and the handoff, end to end on the fake claude."""

import pytest

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
