"""session_close (#278): an agent closes what it started once it is done, and
anything else only after a second thought, with a one-time token."""

import json
import re

import httpx
import pytest

from aegis.confirm import TTL_S, Confirmations

from .conftest import until
from .test_agents import CONFIG, World, inbox, mcp, turn


@pytest.fixture
async def world(tmp_path, fake_claude):
    (tmp_path / ".aegis.yaml").write_text(CONFIG)
    w = await World(tmp_path, fake_claude).start()
    yield w
    await w.stop()


def token(said: str) -> str:
    return re.search(r'token "([^"]+)"', said).group(1)


def refused(said: str) -> str:
    assert said.startswith("mcp error: second_thought: "), said
    assert "Not closed: " in said and said.rstrip().endswith("for the next 5 minutes.")
    return said


async def spawn_child(world, parent, prompt: str = "hello"):
    said = await turn(parent, mcp("session_spawn", agent="opus", prompt=prompt))
    return world.session(json.loads(said.removeprefix("mcp ok: "))["log_id"])


def closed(world, s) -> bool:
    return s.log_id not in world.app.sessions.sessions and (
        s.log_id in world.app.sessions.archived
    )


async def in_archive(world, s) -> bool:
    page = await world.app.registry.call("archive.list", {})
    return any(m["log_id"] == s.log_id for m in page["items"])


async def test_the_tool_takes_a_handle_and_a_token_and_says_its_rule(world):
    async with httpx.AsyncClient() as c:
        r = await c.post(
            world.base + "/mcp",
            json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
            headers={"accept": "application/json, text/event-stream"},
        )
    (tool,) = [t for t in r.json()["result"]["tools"] if t["name"] == "session_close"]
    assert set(tool["inputSchema"]["properties"]) == {"handle", "token"}
    assert "spawned" in tool["description"] and "token" in tool["description"]


async def test_a_done_session_you_spawned_closes_at_once_into_the_archive(world):
    a = await world.spawn()
    child = await spawn_child(world, a)
    await until(lambda: child.status == "idle", timeout=8, what="the child done")
    assert child.wire()["attention"] == "done"
    said = await turn(a, mcp("session_close", handle=child.handle))
    assert (
        said
        == f"mcp ok: closed {child.handle}: its tab is gone, and it stays in the archive"
    )
    assert closed(world, child) and await in_archive(world, child)


async def test_a_working_session_you_spawned_needs_the_token(world):
    a = await world.spawn()
    child = await spawn_child(world, a, "/sleep 30")
    await until(lambda: child.status == "working", timeout=8, what="the child working")
    said = refused(await turn(a, mcp("session_close", handle=child.handle)))
    assert (
        f"{child.handle} is working right now, and closing it stops that work" in said
    )
    assert "You started it, but it is not done." in said
    assert "A person may be reading that tab right now" in said
    assert not closed(world, child)
    said = await turn(a, mcp("session_close", handle=child.handle, token=token(said)))
    assert said.startswith("mcp ok: closed"), said
    assert closed(world, child) and await in_archive(world, child)


async def test_a_session_you_spawned_awaiting_review_needs_the_token(world):
    a = await world.spawn()
    child = await spawn_child(world, a)
    await until(lambda: child.status == "idle", timeout=8, what="the child idle")
    await turn(child, mcp("turn_end", attention="review", line="Read the notes"))
    assert child.wire()["attention"] == "review"
    said = refused(await turn(a, mcp("session_close", handle=child.handle)))
    assert (
        'waiting for the person to read what it showed them: "Read the notes"' in said
    )
    assert "You started it, but it is not done." in said
    assert not closed(world, child)


async def test_a_done_session_you_did_not_spawn_needs_the_token(world):
    a, b = await world.spawn(), await world.spawn()
    await turn(b, "hello")
    assert b.wire()["attention"] == "done"
    said = refused(await turn(a, mcp("session_close", handle=b.handle)))
    assert f"you did not start {b.handle}; a person opened it." in said
    assert "its state is idle and its attention done" in said
    assert "its last activity was" in said and "ago" in said
    assert "ask the person" in said and f"peer_handoff {b.handle}" in said
    # the token comes last, after the better moves
    assert said.index("peer_handoff") < said.index("token")
    said = await turn(a, mcp("session_close", handle=b.handle, token=token(said)))
    assert said.startswith("mcp ok: closed"), said
    assert closed(world, b) and await in_archive(world, b)


async def test_another_sessions_child_names_its_starter_and_both_reasons(world):
    a, b = await world.spawn(), await world.spawn()
    child = await spawn_child(world, b, "/sleep 30")
    await until(lambda: child.status == "working", timeout=8, what="the child working")
    said = refused(await turn(a, mcp("session_close", handle=child.handle)))
    assert f"It is not done, and it is not yours either: {b.handle} started it." in said
    assert f"(or {b.handle}, which started it)" in said


async def test_a_spent_expired_misdirected_or_borrowed_token_is_refused_anew(world):
    a, b, c = await world.spawn(), await world.spawn(), await world.spawn()
    first = token(refused(await turn(a, mcp("session_close", handle=b.handle))))

    # for another session: refused, says why, and gives a fresh token for this one
    said = refused(await turn(a, mcp("session_close", handle=c.handle, token=first)))
    assert said.startswith(
        "mcp error: second_thought: The token you passed did not count: "
        "it was given for another session."
    )
    assert token(said) != first and not closed(world, c)

    # a token is spent by its first use, right or wrong
    said = refused(await turn(a, mcp("session_close", handle=b.handle, token=first)))
    assert "it was already used once" in said and not closed(world, b)

    # another agent cannot use a token it was not given
    mine = token(said)
    said = refused(await turn(c, mcp("session_close", handle=b.handle, token=mine)))
    assert "it was given to another session" in said and not closed(world, b)

    # an expired token
    clock = world.app.confirmations.clock
    world.app.confirmations.clock = lambda: clock() + TTL_S + 1
    said = refused(await turn(a, mcp("session_close", handle=b.handle, token=mine)))
    assert "it expired: a token lasts 5 minutes" in said and not closed(world, b)

    # a token aegis never issued
    said = refused(
        await turn(a, mcp("session_close", handle=b.handle, token="made-up"))
    )
    assert "aegis did not issue it" in said

    # and the fresh one still works
    said = await turn(a, mcp("session_close", handle=b.handle, token=token(said)))
    assert said.startswith("mcp ok: closed") and closed(world, b)


async def test_closing_yourself_needs_the_token(world):
    a = await world.spawn()
    said = refused(await turn(a, mcp("session_close", handle=a.handle)))
    assert f"{a.handle} is your own session" in said and "turn_end(done)" in said
    await a.send(mcp("session_close", handle=a.handle, token=token(said)))
    await until(lambda: closed(world, a), timeout=8, what="the self-close")
    assert await in_archive(world, a)


async def test_a_session_on_a_linked_server_is_refused(world):
    a = await world.spawn()
    said = await turn(a, mcp("session_close", handle="knuth-review@far"))
    assert said.startswith("mcp error: not_across_links"), said


async def test_a_worker_you_enqueued_counts_as_yours(world):
    a, b = await world.spawn(), await world.spawn()
    said = await turn(
        a, mcp("queue_enqueue", queue="general", payload="/sleep 30", callback=False)
    )
    t = world.app.queues.tasks[json.loads(said.removeprefix("mcp ok: "))["task_id"]]
    await until(lambda: t.worker in world.app.sessions.sessions, what="the worker")
    w = world.session(t.worker)
    await until(lambda: w.status == "working", timeout=8, what="the worker working")
    # working: yours but not done
    said = refused(await turn(a, mcp("session_close", handle=w.handle)))
    assert f"is queue general's worker for task#{t.id}" not in said
    assert "You started it, but it is not done." in said
    # to another agent it is not theirs, and says who enqueued it
    said = refused(await turn(b, mcp("session_close", handle=w.handle)))
    assert f"queue general's worker for task#{t.id}, enqueued by {a.handle}" in said


async def test_a_done_worker_you_enqueued_closes_at_once(world):
    a = await world.spawn()
    said = await turn(a, mcp("queue_enqueue", queue="general", payload="summarize"))
    t = world.app.queues.tasks[json.loads(said.removeprefix("mcp ok: "))["task_id"]]
    await until(lambda: inbox(a), timeout=12, what="the callback")
    assert t.status == "completed" and t.worker in world.app.sessions.archived
    # a person reopens the finished worker to look at it, and leaves it open
    await world.app.registry.call("session.reopen", {"log_id": t.worker})
    w = world.session(t.worker)
    assert w.wire()["attention"] == "done"
    said = await turn(a, mcp("session_close", handle=w.handle))
    assert said.startswith("mcp ok: closed"), said
    assert closed(world, w) and await in_archive(world, w)


async def test_a_person_still_closes_by_log_id(world):
    a = await world.spawn()
    await world.app.registry.call("session.close", {"log_id": a.log_id})
    assert closed(world, a)


def test_a_token_binds_its_action_caller_and_target_and_works_once():
    c = Confirmations()
    t = c.issue("session.close", "me", "you")
    assert (
        c.redeem(t, "peer.interrupt", "me", "you") == "it was given for another action"
    )
    t = c.issue("session.close", "me", "you")
    assert c.redeem(t, "session.close", "me", "you") is None
    assert c.redeem(t, "session.close", "me", "you") == "it was already used once"
