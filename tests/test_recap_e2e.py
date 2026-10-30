import asyncio
import contextlib
import os

import pytest

from aegis import recap
from aegis.ops import Caller, OpError
from aegis.recaps import Recaps

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
    await a.send("/busy 2")
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
    await until(lambda: a.status != "working", timeout=10, what="the turn's end")
    (tmp_path / ".aegis.yaml").write_text(CONFIG + "recap: opus\n")
    off = await world.app.registry.call(
        "recap.request", {"log_id": a.log_id, "force": True}
    )
    assert off["status"] == "off" and "recap:" in off["why"]
    (tmp_path / ".aegis.yaml").write_text(CONFIG)
    off = await world.app.registry.call(
        "recap.request", {"log_id": a.log_id, "force": True}
    )
    assert off["status"] == "off" and "recap: {agent:" in off["why"]


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


def oneshots(tmp_path) -> list[str]:
    """The pids of every one-shot the fake claude ran, one per call."""
    log = tmp_path / "fake-home" / "oneshot.log"
    return log.read_text().split() if log.exists() else []


def alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


async def test_a_call_past_the_timeout_is_failed_and_frees_the_slot(world, monkeypatch):
    a = await two_unread(world)
    monkeypatch.setattr(recap, "TIMEOUT_S", 1)
    monkeypatch.setenv("FAKE_CLAUDE_ONESHOT", "slow")
    r = await world.app.registry.call("recap.request", {"log_id": a.log_id})
    assert r["status"] == "failed" and "1s" in r["why"]
    assert not [x for x in a.entries() if x["kind"] == "recap"]
    monkeypatch.delenv("FAKE_CLAUDE_ONESHOT")
    assert (await world.app.registry.call("recap.request", {"log_id": a.log_id}))[
        "status"
    ] == "made"


async def test_a_cancelled_requester_leaves_the_call_holding_its_slot(
    world, monkeypatch, tmp_path
):
    a = await two_unread(world)
    monkeypatch.setattr(recap, "TIMEOUT_S", 2)
    monkeypatch.setenv("FAKE_CLAUDE_ONESHOT", "slow")
    first = asyncio.create_task(
        world.app.registry.call("recap.request", {"log_id": a.log_id})
    )
    await asyncio.sleep(0.3)
    first.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await first
    r = await world.app.registry.call("recap.request", {"log_id": a.log_id})
    assert r["status"] == "failed"
    assert len(oneshots(tmp_path)) == 1


async def test_shutdown_kills_a_running_call(world, monkeypatch, tmp_path):
    a = await two_unread(world)
    monkeypatch.setattr(recap, "TIMEOUT_S", 60)
    monkeypatch.setenv("FAKE_CLAUDE_ONESHOT", "slow")
    pending = asyncio.create_task(
        world.app.registry.call("recap.request", {"log_id": a.log_id})
    )
    await until(lambda: oneshots(tmp_path), timeout=10, what="the one-shot")
    (pid,) = map(int, oneshots(tmp_path))
    await world.app.recaps.shutdown()
    await until(lambda: not alive(pid), what="the one-shot's death")
    with contextlib.suppress(asyncio.CancelledError):
        await pending
    assert pending.done()


async def test_a_session_closed_during_the_call_gets_no_record(world, monkeypatch):
    a = await two_unread(world)
    monkeypatch.setenv("FAKE_CLAUDE_ONESHOT", "slow")
    monkeypatch.setenv("FAKE_CLAUDE_ONESHOT_SLEEP", "1")
    pending = asyncio.create_task(
        world.app.registry.call("recap.request", {"log_id": a.log_id})
    )
    await asyncio.sleep(0.2)
    await world.app.registry.call("session.close", {"log_id": a.log_id})
    r = await pending
    assert r == {"status": "failed", "why": "the session was closed"}
    assert not a.recap_cost_usd


def rows_after_a_remove(world, monkeypatch) -> list[dict]:
    """Every session row published after that session's removal, as it grows."""
    late: list[dict] = []
    removed: set[str] = set()
    publish = world.app.channels.publish

    def spy(channel, ops):
        if channel == "sessions":
            for op in ops:
                if "remove" in op:
                    removed.add(op["remove"])
                elif "upsert" in op and op["upsert"].get("log_id") in removed:
                    late.append(op["upsert"])
        publish(channel, ops)

    monkeypatch.setattr(world.app.channels, "publish", spy)
    return late


async def test_a_call_paid_after_the_close_does_not_bring_the_session_back(
    world, monkeypatch
):
    a = await two_unread(world)
    late = rows_after_a_remove(world, monkeypatch)
    monkeypatch.setenv("FAKE_CLAUDE_ONESHOT", "empty")
    monkeypatch.setenv("FAKE_CLAUDE_ONESHOT_SLEEP", "1")
    pending = asyncio.create_task(
        world.app.registry.call("recap.request", {"log_id": a.log_id})
    )
    await asyncio.sleep(0.2)
    await world.app.registry.call("session.close", {"log_id": a.log_id})
    assert (await pending)["status"] == "failed"
    assert a.recap_cost_usd == 0.004  # paid: the card's change is what comes late
    await asyncio.sleep(0.5)  # past PUBLISH_EVERY_S
    assert not late


async def test_a_publish_waiting_at_the_close_does_not_bring_the_session_back(
    world, monkeypatch
):
    a = await two_unread(world)
    await a.stop()
    late = rows_after_a_remove(world, monkeypatch)
    a.add_recap_cost(0.01)  # arms the timer, and the close below never awaits it
    await world.app.registry.call("session.close", {"log_id": a.log_id})
    await asyncio.sleep(0.5)  # past PUBLISH_EVERY_S
    assert not late


async def test_stopping_the_server_kills_a_running_call(world, monkeypatch, tmp_path):
    a = await two_unread(world)
    monkeypatch.setattr(recap, "TIMEOUT_S", 60)
    monkeypatch.setenv("FAKE_CLAUDE_ONESHOT", "slow")
    pending = asyncio.create_task(
        world.app.registry.call("recap.request", {"log_id": a.log_id})
    )
    await until(lambda: oneshots(tmp_path), timeout=10, what="the one-shot")
    (pid,) = map(int, oneshots(tmp_path))
    await world.stop()
    await until(lambda: not alive(pid), what="the one-shot's death")
    with contextlib.suppress(asyncio.CancelledError):
        await pending


async def test_stopping_the_session_after_a_recap_leaves_it_covered(world):
    a = await two_unread(world)
    r = await world.app.registry.call("recap.request", {"log_id": a.log_id})
    assert r["status"] == "made"
    await world.app.registry.call("session.stop", {"log_id": a.log_id})
    await until(lambda: a.status == "asleep", what="stopped")
    again = await world.app.registry.call("recap.request", {"log_id": a.log_id})
    assert again["status"] == "exists"


async def test_a_recap_is_not_session_activity(world):
    a = await two_unread(world)
    before = a.last_activity
    r = await world.app.registry.call("recap.request", {"log_id": a.log_id})
    assert r["status"] == "made"
    assert a.last_activity == before


async def test_a_recap_that_finishes_after_a_send_is_stale(world, monkeypatch):
    a = await two_unread(world)
    monkeypatch.setattr(recap, "TIMEOUT_S", 60)
    monkeypatch.setenv("FAKE_CLAUDE_ONESHOT", "slow")
    monkeypatch.setenv("FAKE_CLAUDE_ONESHOT_SLEEP", "1")
    pending = asyncio.create_task(
        world.app.registry.call("recap.request", {"log_id": a.log_id})
    )
    await asyncio.sleep(0.2)
    await world.app.registry.call("session.send", {"log_id": a.log_id, "text": "more"})
    assert (await pending) == {"status": "stale"}
    assert not [x for x in a.entries() if x["kind"] == "recap"]
    assert a.recap_cost_usd > 0


async def test_a_paid_call_with_no_usable_answer_still_counts_its_cost(
    world, monkeypatch
):
    a = await two_unread(world)
    monkeypatch.setenv("FAKE_CLAUDE_ONESHOT", "empty")
    r = await world.app.registry.call("recap.request", {"log_id": a.log_id})
    assert r["status"] == "failed"
    assert a.recap_cost_usd == 0.004


async def test_a_call_that_raises_on_cancel_does_not_stop_shutdown():
    async def stubborn():
        try:
            await asyncio.sleep(60)
        except asyncio.CancelledError:
            raise RuntimeError("cleanup went wrong") from None

    rs = Recaps(app=None)
    rs._running["x"] = asyncio.create_task(stubborn())
    await asyncio.sleep(0)
    await rs.shutdown()
    assert rs._running["x"].done()


async def test_a_recap_keeps_its_ask_only_while_the_session_needs_its_person(
    world, monkeypatch
):
    # Waiting on a monitor or CI, the model turned the agent's own next step
    # into an order from the person ("Merge #636 when CI passes").
    a = await two_unread(world)
    r = await world.app.registry.call("recap.request", {"log_id": a.log_id})
    assert (r["status"], r["ask"]) == ("made", "")
    (e,) = [x for x in a.entries() if x["kind"] == "recap"]
    assert e["detail"]["ask"] == ""
    monkeypatch.setattr(world.app.sessions, "attention_of", lambda s: "needs_you")
    r = await world.app.registry.call(
        "recap.request", {"log_id": a.log_id, "force": True}
    )
    assert (r["status"], r["ask"]) == ("made", "answer it")
