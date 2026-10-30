"""A handoff that continues on a linked server (#874)."""

import pytest

from aegis.ops import Caller, OpError

from .conftest import until
from .test_agents import mcp, turn
from .test_links import OPEN, member_link, pair  # noqa: F401


def far_session(beta):
    return next(iter(beta.app.sessions.sessions.values()), None)


def first_message(s) -> str:
    return next(str(e) for e in s.entries() if e["kind"] == "user")


async def send(node, s, text):
    params = {"log_id": s.log_id, "text": text}
    return await node.app.registry.call("session.send", params, Caller("user"))


async def test_an_agents_far_handoff_archives_when_the_far_side_takes_it(pair):  # noqa: F811
    alpha, beta = pair
    await member_link(alpha, beta, OPEN)
    here = await alpha.spawn(permission="write")
    log_id = here.log_id
    await turn(here, mcp("session_handoff", host="beta", msg="implement the plan"))
    await until(lambda: log_id in alpha.app.sessions.archived, 15, "archived")
    far = far_session(beta)
    await until(
        lambda: any(e["kind"] == "user" for e in far.entries()), 8, "its prompt"
    )
    said = first_message(far)
    assert "implement the plan" in said and "peer_read" not in said
    assert far.meta()["permission"] == "write"  # at most the caller's
    meta = alpha.app.sessions.archived[log_id]
    assert meta["continued_by"] == f"{far.handle}@beta"


async def test_a_persons_far_handoff_answers_with_the_far_server(pair):  # noqa: F811
    alpha, beta = pair
    await member_link(alpha, beta, OPEN)
    here = await alpha.spawn()
    r = await send(alpha, here, "/handoff --agent opus@beta go on")
    assert r["server"] == "beta" and here.log_id in alpha.app.sessions.archived
    assert far_session(beta) is not None


async def test_it_cannot_be_reopened(pair):  # noqa: F811
    alpha, beta = pair
    await member_link(alpha, beta, OPEN)
    here = await alpha.spawn()
    await send(alpha, here, "/handoff --agent opus@beta go on")
    with pytest.raises(OpError) as e:
        alpha.app.sessions.reopen(here.log_id)
    assert e.value.code == "handed_off" and "@beta" in e.value.message


async def test_a_refused_far_spawn_leaves_it_open_and_unlocked(pair):  # noqa: F811
    alpha, beta = pair
    await member_link(alpha, beta, {})  # every pool closed for the link
    here = await alpha.spawn()
    with pytest.raises(OpError) as e:
        await send(alpha, here, "/handoff --agent opus@beta go on")
    assert e.value.code == "quota_closed" and here.continued_by is None
    assert here.log_id in alpha.app.sessions.sessions
    await send(alpha, here, "back to work")


async def test_a_refused_far_spawn_after_the_turn_gives_the_agent_its_session(pair):  # noqa: F811
    alpha, beta = pair
    await member_link(alpha, beta, {})
    here = await alpha.spawn()
    await turn(here, mcp("session_handoff", host="beta"))
    await until(lambda: here.continued_by is None, 8, "the handoff called off")
    assert any("did not start" in str(e) for e in here.entries())
    assert here.log_id in alpha.app.sessions.sessions


async def test_open_children_refuse_a_far_handoff(pair):  # noqa: F811
    alpha, beta = pair
    await member_link(alpha, beta, OPEN)
    here = await alpha.spawn()
    await alpha.app.registry.call(
        "session.spawn", {"agent": "opus"}, Caller("agent", here.log_id)
    )
    with pytest.raises(OpError) as e:
        await send(alpha, here, "/handoff --agent opus@beta go on")
    assert e.value.code == "has_children" and here.continued_by is None


async def test_a_live_run_refuses_a_far_handoff(pair, monkeypatch):  # noqa: F811
    alpha, beta = pair
    await member_link(alpha, beta, OPEN)
    here = await alpha.spawn()

    class Run:
        run = "r1"

    monkeypatch.setattr(alpha.app.sessions, "_running", lambda s: Run)
    with pytest.raises(OpError) as e:
        await send(alpha, here, "/handoff --agent opus@beta go on")
    assert e.value.code == "has_wakers" and "workflow r1" in e.value.message
