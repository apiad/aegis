"""/btw end to end: real sessions on the fake claude, the real app."""

import asyncio

import pytest

from aegis import btw
from aegis.ops import OpError
from aegis.transcript.store import read_store

from .conftest import until
from .test_agents import CONFIG, World, turn
from .test_recap_e2e import alive, oneshots


@pytest.fixture
async def world(tmp_path, fake_claude):
    (tmp_path / ".aegis.yaml").write_text(CONFIG + "btw: {agent: opus}\n")
    w = await World(tmp_path, fake_claude).start()
    yield w
    await w.stop()


def notes(a):
    return [e for e in a.entries() if e["kind"] == "btw"]


async def ask(world, a, q):
    return await world.app.registry.call(
        "session.send", {"log_id": a.log_id, "text": f"/btw {q}"}
    )


async def settled(a, n=1):
    await until(
        lambda: len(notes(a)) >= n and notes(a)[n - 1]["status"] != "running",
        timeout=8,
        what="the side note's answer",
    )
    return notes(a)[n - 1]


async def test_a_side_note_is_answered_into_one_block_and_costs(world):
    a = await world.spawn()
    await turn(a, "first ask")
    r = await ask(world, a, "what did I ask?")
    assert r["status"] == "asked" and r["id"]
    e = await settled(a)
    assert e["status"] == "ok" and e["detail"]["question"] == "what did I ask?"
    assert e["detail"]["answer"] == "btw of: what did I ask?"
    assert e["detail"]["header"] == "all 1 turn"
    assert a.wire()["btw_cost_usd"] == 0.004
    assert a.wire()["recap_cost_usd"] == 0.0


async def test_mid_turn_it_answers_and_the_agent_never_sees_it(world):
    a = await world.spawn()
    await a.send("/busy 2")
    await until(lambda: a.status == "working", what="working")
    await ask(world, a, "the hidden question")
    e = await settled(a)
    assert e["status"] == "ok" and a.status == "working"
    await until(lambda: a.status == "idle", timeout=10, what="the turn's end")
    assert "the hidden question" not in await turn(a, "/recall")


async def test_a_question_opening_on_dashes_reaches_claude_as_text(world):
    a = await world.spawn()
    await ask(world, a, "--help is a flag?")
    e = await settled(a)
    assert e["status"] == "ok" and e["detail"]["answer"] == "btw of: --help is a flag?"


async def test_refused_without_config_and_while_one_runs(world, tmp_path, monkeypatch):
    a = await world.spawn()
    monkeypatch.setenv("FAKE_CLAUDE_ONESHOT", "slow")
    await ask(world, a, "one")
    with pytest.raises(OpError) as e:
        await ask(world, a, "two")
    assert e.value.code == "btw_busy"
    assert len(notes(a)) == 1
    (tmp_path / ".aegis.yaml").write_text(CONFIG)
    b = await world.spawn()
    with pytest.raises(OpError) as e:
        await ask(world, b, "three")
    assert e.value.code == "btw_off" and "btw: {agent:" in e.value.message
    assert notes(b) == []


@pytest.mark.parametrize("mode", ["fail", "garbage"])
async def test_a_broken_call_leaves_a_failed_block_and_frees_the_slot(
    world, mode, monkeypatch
):
    a = await world.spawn()
    monkeypatch.setenv("FAKE_CLAUDE_ONESHOT", mode)
    await ask(world, a, "q")
    e = await settled(a)
    assert e["status"] == "failed" and e["detail"]["why"]
    monkeypatch.delenv("FAKE_CLAUDE_ONESHOT")
    await ask(world, a, "again")
    assert (await settled(a, 2))["status"] == "ok"


@pytest.mark.parametrize("mode", ["fail", "garbage"])
async def test_the_slot_is_free_the_moment_the_block_stops_running(
    world, mode, monkeypatch
):
    # #826: a waiter woken between the status and the task's end asked again
    # before the done callback ran. Asking from inside the write has no such gap.
    a = await world.spawn()
    monkeypatch.setenv("FAKE_CLAUDE_ONESHOT", mode)
    report, again = a.report, []

    def ask_again(record):
        report(record)
        if record["kind"] == "btw_answer" and not again:
            again.append(world.app.side_notes.ask(a, "again"))

    monkeypatch.setattr(a, "report", ask_again)
    await ask(world, a, "q")
    assert (await settled(a))["status"] == "failed"
    await until(lambda: again, what="the second ask")
    assert again[0]["status"] == "asked"
    await settled(a, 2)


async def test_a_call_past_the_timeout_fails(world, monkeypatch):
    a = await world.spawn()
    monkeypatch.setattr(btw, "TIMEOUT_S", 1)
    monkeypatch.setenv("FAKE_CLAUDE_ONESHOT", "slow")
    await ask(world, a, "q")
    e = await settled(a)
    assert e["status"] == "failed" and "1s" in e["detail"]["why"]


async def test_cancel_kills_the_call_and_folds_the_block(world, monkeypatch, tmp_path):
    a = await world.spawn()
    monkeypatch.setenv("FAKE_CLAUDE_ONESHOT", "slow")
    r = await ask(world, a, "q")
    await until(lambda: oneshots(tmp_path), what="the call to start")
    pid = int(oneshots(tmp_path)[-1])
    c = await world.app.registry.call("btw.cancel", {"log_id": a.log_id, "id": r["id"]})
    assert c["status"] == "cancelled"
    e = await settled(a)
    assert e["status"] == "cancelled" and e["detail"]["folded"]
    await until(lambda: not alive(pid), what="claude to die")
    assert (
        await world.app.registry.call("btw.cancel", {"log_id": a.log_id, "id": r["id"]})
    )["status"] == "none"


async def test_closing_the_session_kills_the_call(world, monkeypatch, tmp_path):
    a = await world.spawn()
    monkeypatch.setenv("FAKE_CLAUDE_ONESHOT", "slow")
    await ask(world, a, "q")
    await until(lambda: oneshots(tmp_path), what="the call to start")
    pid = int(oneshots(tmp_path)[-1])
    await world.app.sessions.close(a.log_id)
    await until(lambda: not alive(pid), what="claude to die")
    await asyncio.sleep(0.2)  # nothing may write to the closed store
    kinds = [
        r.get("kind") for r in read_store(world.app.sessions.store_path(a.log_id))[0]
    ]
    assert kinds[-1] == "close"  # nothing written after the store shut


async def test_cancel_settles_a_block_left_running_by_a_crash(world):
    a = await world.spawn()
    # A question record with no call behind it: what a killed server leaves.
    a.report({"kind": "btw", "id": "orphan", "question": "q"})
    r = await world.app.registry.call(
        "btw.cancel", {"log_id": a.log_id, "id": "orphan"}
    )
    assert r["status"] == "cancelled"
    (e,) = notes(a)
    assert e["status"] == "cancelled" and e["detail"]["folded"]
    again = await world.app.registry.call(
        "btw.cancel", {"log_id": a.log_id, "id": "orphan"}
    )
    assert again["status"] == "none"
