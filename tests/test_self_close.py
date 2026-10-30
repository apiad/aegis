"""turn_end's close (#592): an agent told to close when it finishes archives
itself and every session it or they started once its turn ends done, and is
refused while anything in that tree still runs."""

import pytest

from aegis.ops import OpError

from .conftest import until
from .test_agents import CONFIG, World, mcp, turn
from .test_session_close import closed, spawn_child


@pytest.fixture
async def world(tmp_path, fake_claude):
    (tmp_path / ".aegis.yaml").write_text(CONFIG)
    w = await World(tmp_path, fake_claude).start()
    yield w
    await w.stop()


async def closing_turn(world, s, text: str, *also) -> str:
    """Send ``text``, wait until ``s`` and ``also`` are archived, and return the
    reply, as turn() does: the session never shows idle, it is gone."""
    before = len([e for e in s.entries() if e["kind"] == "prose"])
    await s.send(text)
    await until(
        lambda: all(closed(world, x) for x in (s, *also)),
        timeout=8,
        what="the tree archived",
    )
    return [e["md"] for e in s.entries() if e["kind"] == "prose"][before]


def done(close: bool = True, **more) -> str:
    return mcp("turn_end", attention="done", line="merged", close=close, **more)


async def test_close_goes_with_done_only(world):
    a = await world.spawn()
    said = await turn(a, mcp("turn_end", attention="review", line="read", close=True))
    assert said.startswith("mcp error: bad_params: close goes with done only"), said
    assert not closed(world, a)


async def test_a_lone_session_closes_once_its_turn_ends(world):
    a = await world.spawn()
    said = await closing_turn(world, a, done())
    assert said.startswith("mcp ok: noted. aegis archives this session when"), said


async def test_without_close_a_done_session_stays_open(world):
    a = await world.spawn()
    await turn(a, done(close=False))
    await turn(a, "hello")  # the next turn ends with no turn_end at all
    assert not closed(world, a)


async def test_the_whole_spawn_tree_closes_with_it(world):
    a = await world.spawn()
    child = await spawn_child(
        world, a, mcp("session_spawn", agent="opus", prompt="hello")
    )
    await until(lambda: child.status == "idle", timeout=8, what="the child idle")
    (grandchild,) = world.app.sessions.descendants(child.log_id)
    await until(
        lambda: grandchild.wire()["attention"] == "done",
        timeout=8,
        what="the grandchild done",
    )
    await until(
        lambda: child.wire()["attention"] == "done", timeout=8, what="the child done"
    )
    said = await closing_turn(world, a, done(), child, grandchild)
    assert "and the 2 sessions it started" in said, said


async def test_a_working_child_refuses_the_close_and_keeps_everyone(world):
    a = await world.spawn()
    child = await spawn_child(world, a, "/busy 30")
    await until(lambda: child.status == "working", timeout=8, what="the child busy")
    said = await turn(a, done())
    assert said.startswith("mcp error: close_refused: Not closed"), said
    assert f"{child.handle} (working)" in said
    assert not closed(world, a) and not closed(world, child)


async def test_a_live_monitor_refuses_the_close(world):
    a = await world.spawn()
    await turn(
        a,
        mcp("monitor_start", description="w", done="false", progress=None, source=None),
    )
    said = await turn(a, done())
    assert said.startswith("mcp error: close_refused:") and "1 monitor" in said, said
    assert not closed(world, a)


# Autoclose (#691): the person's word to close, given ahead of time.


async def autoclose(world, s, on: bool = True) -> dict:
    return await world.app.registry.call(
        "session.autoclose", {"log_id": s.log_id, "on": on}
    )


async def test_autoclose_refuses_done_without_close(world):
    a = await world.spawn()
    await autoclose(world, a)
    said = await turn(a, done(close=False))
    assert said.startswith("mcp error: autoclose: this session is in autoclose"), said
    assert not closed(world, a)


async def test_autoclose_leaves_review_and_needs_you_open(world):
    a = await world.spawn()
    await autoclose(world, a)
    for attention in ("review", "needs_you"):
        said = await turn(a, mcp("turn_end", attention=attention, line="read"))
        assert not said.startswith("mcp error"), said
    assert not closed(world, a)


async def test_autoclose_lets_done_with_close_archive_it(world):
    a = await world.spawn()
    await autoclose(world, a)
    said = await closing_turn(world, a, done())
    assert said.startswith("mcp ok: noted. aegis archives this session"), said


async def test_autoclose_off_allows_done_without_close_again(world):
    a = await world.spawn()
    await autoclose(world, a)
    r = await autoclose(world, a, on=False)
    assert r["autoclose"] is False
    said = await turn(a, done(close=False))
    assert not said.startswith("mcp error"), said


async def test_slash_autoclose_toggles_and_takes_on_or_off(world):
    a = await world.spawn()

    async def typed(text: str) -> bool:
        send = {"log_id": a.log_id, "text": text}
        return (await world.app.registry.call("session.send", send))["autoclose"]

    assert await typed("/autoclose") is True and a.autoclose
    assert await typed("/autoclose") is False
    assert await typed("/autoclose on") is True
    assert await typed("/autoclose on") is True
    assert await typed("/autoclose off") is False
    with pytest.raises(OpError, match=r"usage: /autoclose \[on\|off\]"):
        await typed("/autoclose maybe")


@pytest.mark.slow  # a restart
async def test_autoclose_survives_a_restart(world):
    a = await world.spawn()
    await autoclose(world, a)
    await world.restart()
    assert world.session(a.log_id).autoclose is True
