"""Context size, cache counters and the handoff, end to end on the fake claude."""

import pytest

from aegis.ops import Caller, OpError

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


async def handoff(world, a):
    return await world.app.registry.call(
        "session.handoff", {"log_id": a.log_id}, Caller("user")
    )


def first_message(s) -> str:
    return next(str(e) for e in s.entries() if e["kind"] == "user")


async def test_handoff_starts_the_same_agent_with_the_brief_and_the_tail(world):
    a = await world.spawn()
    await turn(a, "/ctx 342000")
    await end(world, a, handoff="Done: the parser. Next: the archive view.")
    r = await handoff(world, a)
    b = world.session(r["log_id"])
    assert b.spec.continues == a.log_id and b.spec.agent == a.spec.agent
    assert b.spec.cwd == a.spec.cwd and b.spec.model == a.spec.model
    assert a.continued_by == b.log_id and a.wire()["continued_by"] == b.log_id
    await until(lambda: any(e["kind"] == "user" for e in b.entries()), 8, "b's prompt")
    said = first_message(b)
    assert "Done: the parser." in said and "ctx 342000" in said and a.handle in said


async def test_a_second_click_starts_nothing(world):
    a = await world.spawn()
    await turn(a, "/ctx 342000")
    await end(world, a, handoff="Brief.")
    await handoff(world, a)
    with pytest.raises(OpError) as e:
        await handoff(world, a)
    assert e.value.code == "handed_off"
    sessions = world.app.sessions.sessions.values()
    assert sum(1 for s in sessions if s.spec.continues == a.log_id) == 1


async def test_an_agent_cannot_hand_a_session_off(world):
    a = await world.spawn()
    with pytest.raises(OpError):
        await world.app.registry.call(
            "session.handoff", {"log_id": a.log_id}, Caller("agent", a.log_id)
        )


async def handed(world, brief="Brief."):
    a = await world.spawn()
    await turn(a, "/ctx 342000")
    await end(world, a, handoff=brief)
    r = await handoff(world, a)
    return a, world.session(r["log_id"])


async def test_the_old_session_is_archived_after_the_new_ones_first_turn(world):
    a, b = await handed(world)
    reg = world.app.sessions
    await until(lambda: a.log_id in reg.archived, 8, "the old session archived")
    assert reg.archived[a.log_id]["continued_by"] == b.log_id
    await turn(b, "more")
    assert b.log_id in reg.sessions  # a later turn archives nothing else


async def test_a_message_to_the_old_session_calls_the_archive_off(world):
    a, b = await handed(world, "[[slow]] Brief.")  # b's first turn takes 1.5 s
    await turn(a, "still here")  # the person types into the old tab meanwhile
    reg = world.app.sessions
    await until(
        lambda: a.continued_by is None or a.log_id in reg.archived, 8, "b answered"
    )
    assert a.log_id in reg.sessions and a.continued_by is None


async def test_the_archive_survives_a_restart_before_the_first_turn(world):
    a, b = await handed(world, "[[slow]] Brief.")
    log_a, log_b = a.log_id, b.log_id
    await world.restart()
    reg = world.app.sessions
    await turn(world.session(log_b), "go on")
    await until(lambda: log_a in reg.archived, 8, "archived after restart")


async def test_peer_read_reaches_the_archived_predecessor_for_its_successor_only(world):
    a, b = await handed(world)
    reg = world.app.sessions
    await until(lambda: a.log_id in reg.archived, 8, "archived")
    read = {"target": a.handle, "last": 5}
    got = await world.app.registry.call("peer.read", read, Caller("agent", b.log_id))
    assert "ctx 342000" in got
    c = await world.spawn()
    with pytest.raises(OpError) as e:
        await world.app.registry.call("peer.read", read, Caller("agent", c.log_id))
    assert e.value.code == "archived"
