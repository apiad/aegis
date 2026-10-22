"""The rename nudge (#586): a session remembers whether its handle and its title
were set, and turn_end reminds an agent until both are."""

import json

from aegis.agent_ops import RENAME_NUDGE

from .conftest import until
from .test_agents import inbox, mcp, turn, world  # noqa: F401

END = mcp("turn_end", attention="review", line="a step")


async def test_a_new_session_has_neither_name_set(world):  # noqa: F811
    s = await world.spawn()
    assert (s.handle_set, s.title_set) == (False, False)
    assert s.meta()["handle_set"] is False


async def test_rename_with_a_handle_sets_handle_set_only(world):  # noqa: F811
    s = await world.spawn()
    world.app.sessions.rename(s.log_id, "tab-bar", None)
    assert (s.handle_set, s.title_set) == (True, False)
    assert s.meta()["handle_set"] is True


async def test_a_meta_from_before_the_field_reads_as_set(world):  # noqa: F811
    s = await world.spawn()
    old = s.meta()
    del old["handle_set"]
    assert world.app.sessions._session(old).handle_set is True


async def test_turn_end_reminds_a_fresh_session(world):  # noqa: F811
    s = await world.spawn()
    assert await turn(s, END) == f"mcp ok: {RENAME_NUDGE}"


async def test_a_handle_alone_still_gets_the_reminder(world):  # noqa: F811
    s = await world.spawn()
    await turn(s, mcp("session_rename", handle="tab-bar"))
    assert await turn(s, END) == f"mcp ok: {RENAME_NUDGE}"


async def test_handle_and_title_end_the_reminder(world):  # noqa: F811
    s = await world.spawn()
    await turn(s, mcp("session_rename", handle="tab-bar", title="Redesign the bar"))
    assert await turn(s, END) == "mcp ok: noted"


async def test_a_worker_never_gets_it(world):  # noqa: F811
    a = await world.spawn()
    said = await turn(a, mcp("queue_enqueue", queue="general", payload=END))
    t = world.app.queues.tasks[json.loads(said.removeprefix("mcp ok: "))["task_id"]]
    await until(
        lambda: t.status == "completed" and inbox(a),
        timeout=12,
        what="the worker's result",
    )
    assert "mcp ok: noted" in inbox(a)[0]["md"]
    assert RENAME_NUDGE not in inbox(a)[0]["md"]
