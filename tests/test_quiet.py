"""Quiet sessions: no tab until the turn ends (#386)."""

import json

import pytest

from aegis import restarts
from aegis.commands import parse_spawn
from aegis.ops import OpError
from aegis.queues import queues_from

from .conftest import until
from .test_agents import CONFIG, World, hold, inbox, mcp, turn


@pytest.fixture
async def world(tmp_path, fake_claude):
    (tmp_path / ".aegis.yaml").write_text(CONFIG)
    w = await World(tmp_path, fake_claude).start()
    yield w
    await w.stop()


def meta_of(world, log_id: str) -> dict:
    """The session's meta file as boot would read it."""
    return json.loads(world.app.sessions.metas.path(log_id).read_text())


async def test_an_agent_quiets_a_waiting_session(world):
    a, b = await world.spawn(), await world.spawn()
    await hold(b)
    said = await turn(a, mcp("session_quiet", handle=b.handle))
    assert not said.startswith("mcp error"), said
    assert b.quiet and b.wire()["quiet"] is True
    assert meta_of(world, b.log_id)["quiet"] is True


async def test_an_idle_session_cannot_be_quieted(world):
    a, b = await world.spawn(), await world.spawn()
    await turn(b, mcp("turn_end", attention="done", line="Done."))
    said = await turn(a, mcp("session_quiet", handle=b.handle))
    assert said.startswith("mcp error: not_running"), said
    assert not b.quiet


async def test_the_person_quiets_and_unquiets_by_log_id(world):
    b = await world.spawn()
    await hold(b)
    r = await world.app.registry.call("session.quiet", {"log_id": b.log_id})
    assert r["quiet"] is True
    r = await world.app.registry.call("session.unquiet", {"log_id": b.log_id})
    assert r["quiet"] is False and not b.quiet


async def test_slash_quiet_quiets_the_session_it_is_typed_in(world):
    b = await world.spawn()
    await hold(b)
    send = {"log_id": b.log_id, "text": "/quiet"}
    r = await world.app.registry.call("session.send", send)
    assert r["quiet"] is True and b.quiet
    assert meta_of(world, b.log_id)["quiet"] is True


async def test_slash_quiet_is_refused_on_an_idle_session(world):
    b = await world.spawn()
    await turn(b, mcp("turn_end", attention="done", line="Done."))
    with pytest.raises(OpError, match="only a working or waiting session"):
        await world.app.registry.call(
            "session.send", {"log_id": b.log_id, "text": "/quiet"}
        )
    assert not b.quiet


@pytest.mark.slow  # a restart
async def test_quiet_survives_a_restart(world):
    b = await world.spawn()
    await hold(b)
    await world.app.registry.call("session.quiet", {"log_id": b.log_id})
    await world.restart()
    assert world.session(b.log_id).quiet is True


@pytest.mark.parametrize("attention", ["done", "review", "needs_you"])
async def test_a_quiet_session_comes_back_when_its_turn_ends(world, attention):
    b = await world.spawn()
    held = await hold(b)
    await world.app.registry.call("session.quiet", {"log_id": b.log_id})
    await turn(b, mcp("monitor_cancel", monitor_id=held))
    await turn(b, mcp("turn_end", attention=attention, line="Over to you."))
    await until(lambda: not b.quiet, timeout=5, what="back in the tab bar")
    assert meta_of(world, b.log_id)["quiet"] is False


async def test_a_turn_that_ends_waiting_keeps_it_quiet(world):
    b = await world.spawn()
    await hold(b)
    await world.app.registry.call("session.quiet", {"log_id": b.log_id})
    await hold(b)  # a second turn that ends on a live monitor
    assert b.wire()["attention"] == "waiting" and b.quiet


async def test_a_session_that_quiets_itself_is_back_when_that_turn_ends(world):
    b = await world.spawn()
    await turn(b, mcp("session_quiet", handle=b.handle))
    await until(lambda: not b.quiet, timeout=5, what="back after its own turn")


async def test_a_session_whose_process_dies_comes_back(world):
    b = await world.spawn()
    await hold(b)
    await world.app.registry.call("session.quiet", {"log_id": b.log_id})
    await b.send("/exit 3")
    await until(lambda: not b.quiet, timeout=8, what="back after the exit")


async def test_a_quiet_spawn_starts_without_a_tab(world):
    a = await world.spawn()
    said = await turn(
        a, mcp("session_spawn", agent="opus", prompt="/busy 2", quiet=True)
    )
    assert not said.startswith("mcp error"), said
    log_id = json.loads(said.removeprefix("mcp ok: "))["log_id"]
    assert world.session(log_id).quiet is True


async def test_a_quiet_spawn_needs_a_prompt(world):
    a = await world.spawn()
    said = await turn(a, mcp("session_spawn", agent="opus", quiet=True))
    assert said.startswith("mcp error: quiet_needs_prompt"), said


def test_spawn_line_takes_quiet_as_a_bare_flag():
    line = parse_spawn("opus --quiet --model sonnet fix the build")
    assert line.quiet is True and line.model == "sonnet"
    assert line.prompt == "fix the build"
    assert parse_spawn("opus hello").quiet is False


def test_a_queue_is_quiet_unless_it_says_otherwise():
    qs = queues_from(
        {
            "a": {"agent": "opus", "max_parallel": 1},
            "b": {"agent": "opus", "max_parallel": 1, "quiet": False},
            "c": {"agent": "opus", "max_parallel": 1, "quiet": "no"},
        }
    )
    assert qs["a"]["quiet"] is True and qs["b"]["quiet"] is False
    assert qs["c"] == {"error": "quiet 'no' is not true or false"}


async def test_a_worker_starts_quiet_and_never_shows_when_it_finishes(world):
    a = await world.spawn()
    said = await turn(a, mcp("queue_enqueue", queue="general", payload="/busy 1"))
    task_id = json.loads(said.removeprefix("mcp ok: "))["task_id"]
    t = world.app.queues.tasks[task_id]
    await until(lambda: t.worker, timeout=8, what="the worker")
    assert world.session(t.worker).quiet is True
    # The task completes a moment before its worker is archived (queues.py).
    await until(
        lambda: t.worker in world.app.sessions.archived, timeout=12, what="archive"
    )
    # Archived without ever coming back: its last meta is still quiet.
    assert world.app.sessions.archived[t.worker]["quiet"] is True


async def test_a_quiet_worker_that_fails_comes_back(world):
    a = await world.spawn()
    said = await turn(a, mcp("queue_enqueue", queue="general", payload="/exit 4"))
    t = world.app.queues.tasks[json.loads(said.removeprefix("mcp ok: "))["task_id"]]
    await until(lambda: inbox(a), timeout=10, what="the error callback")
    assert t.worker in world.app.sessions.sessions
    assert world.session(t.worker).quiet is False


async def test_a_reopened_session_starts_visible(world):
    a = await world.spawn()
    said = await turn(a, mcp("queue_enqueue", queue="general", payload="hi"))
    t = world.app.queues.tasks[json.loads(said.removeprefix("mcp ok: "))["task_id"]]
    # The task completes a moment before its worker is archived (queues.py).
    await until(
        lambda: t.worker in world.app.sessions.archived, timeout=12, what="archive"
    )
    assert world.app.sessions.archived[t.worker]["quiet"] is True
    r = await world.app.registry.call("session.reopen", {"log_id": t.worker})
    assert r["quiet"] is False and meta_of(world, t.worker)["quiet"] is False


async def test_a_sigterm_exit_keeps_a_session_quiet(world):
    """Under systemd every harness gets SIGTERM a moment before aegis stops;
    the next boot continues it, so it stays quiet (restarts.py)."""
    b = await world.spawn()
    await hold(b)
    await world.app.registry.call("session.quiet", {"log_id": b.log_id})
    world.app.sessions.exited(b, restarts.SIGTERM_CODES[0], [])
    assert b.quiet


async def test_a_finished_worker_whose_turn_errs_stays_quiet(world):
    """Its task completes and it is archived at once: a tab for the length
    of the stop would only flash."""
    a = await world.spawn()
    payload = mcp(
        "monitor_start",
        description="w",
        done="false",
        progress=None,
        interval_s=60,
        source=None,
    )
    said = await turn(a, mcp("queue_enqueue", queue="general", payload=payload))
    t = world.app.queues.tasks[json.loads(said.removeprefix("mcp ok: "))["task_id"]]
    await until(
        lambda: t.worker and world.app.monitors.of(t.worker), timeout=10, what="hold"
    )
    w = world.session(t.worker)
    await until(lambda: w.status == "idle", timeout=10, what="the worker idle")
    w.standing = {**w.standing, "turn_error": "boom"}
    world.app.sessions._maybe_unquiet(w)  # still waiting on its monitor
    assert not w.quiet, "an unfinished worker's error is the person's"
    w._set(quiet=True)
    world.app.monitors.drop_owner(w.log_id)
    assert world.app.queues.finished(w)
    world.app.sessions._maybe_unquiet(w)
    assert w.quiet, "a finished worker is archived; its error is the enqueuer's"
