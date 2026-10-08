"""Attention end to end: the fake claude calls the real tools over /mcp."""

import json

import pytest

from aegis.ops import OpError

from .conftest import until
from .test_agents import CONFIG, World, mcp, turn


@pytest.fixture
async def world(tmp_path, fake_claude):
    (tmp_path / ".aegis.yaml").write_text(CONFIG)
    w = await World(tmp_path, fake_claude).start()
    yield w
    await w.stop()


@pytest.fixture
def published(world):
    """The last card the ``sessions`` channel carried for a log id, or None.

    ``wire()`` computes a card fresh on every read, so only this sees what a
    browser was sent."""
    sent: list[dict] = []
    original = world.app.channels.publish

    def publish(channel, ops):
        if channel == "sessions":
            sent.extend(op["upsert"] for op in ops if "upsert" in op)
        original(channel, ops)

    world.app.channels.publish = publish
    return lambda log_id: next(
        (c for c in reversed(sent) if c["log_id"] == log_id), None
    )


async def test_a_question_turn_needs_you_and_carries_its_line_and_replies(world):
    a = await world.spawn()
    await turn(
        a,
        mcp(
            "turn_end",
            attention="needs_you",
            line="Rebase or merge?",
            replies=["rebase onto main", "merge main into it"],
        ),
    )
    c = a.wire()
    assert c["attention"] == "needs_you"
    assert c["attention_line"] == "Rebase or merge?"
    assert c["replies"] == ["rebase onto main", "merge main into it"]
    await turn(a, "rebase onto main")
    assert a.wire()["attention"] == "done" and a.wire()["replies"] == []


async def test_the_plan_reaches_the_card(world):
    a = await world.spawn()
    items = [{"text": "read", "state": "done"}, {"text": "fix", "state": "doing"}]
    await turn(a, mcp("plan_update", items=items))
    c = a.wire()
    assert (c["plan_now"], c["plan_did"], c["plan_done"], c["plan_total"]) == (
        "fix",
        "read",
        1,
        2,
    )


async def test_a_live_monitor_is_waiting_and_cancelling_it_is_done(world):
    a = await world.spawn()
    said = await turn(
        a,
        mcp(
            "monitor_start",
            description="never",
            done="false",
            progress=None,
            interval_s=60,
        ),
    )
    assert a.wire()["attention"] == "waiting"
    assert a.wire()["waiting_on"] == "1 monitor"
    mid = json.loads(said.removeprefix("mcp ok: "))["monitor_id"]
    await world.app.registry.call("monitor.cancel", {"monitor_id": mid})
    assert a.wire()["attention"] == "done"


async def test_a_dead_process_is_an_error_until_the_next_send(world):
    a = await world.spawn()
    await a.send("/exit 3")
    await until(lambda: a.status == "stopped", timeout=8, what="the exit")
    c = a.wire()
    assert c["attention"] == "error"
    assert c["attention_line"] == "claude exited with code 3"
    await turn(a, "hello again")
    assert a.wire()["attention"] == "done"


async def test_a_parent_waits_on_a_working_child(world, published):
    a = await world.spawn()
    said = await turn(a, mcp("session_spawn", agent="opus", prompt="/sleep 1"))
    child = world.session(json.loads(said.removeprefix("mcp ok: "))["log_id"])
    await until(lambda: child.status == "working", timeout=8, what="the child working")
    assert a.wire()["attention"] == "waiting" and a.wire()["waiting_on"] == "1 session"
    await until(lambda: child.status == "idle", timeout=10, what="the child done")
    assert a.wire()["attention"] == "done"
    await until(
        lambda: published(a.log_id)["attention"] == "done",
        what="the parent's published card done",
    )


async def test_a_cancelled_queue_task_leaves_the_published_card(world, published):
    a = await world.spawn()
    said = await turn(
        a, mcp("queue_enqueue", queue="solo", payload="/sleep 30", callback=False)
    )
    busy = json.loads(said.removeprefix("mcp ok: "))["task_id"]
    said = await turn(a, mcp("queue_enqueue", queue="solo", payload="/sleep 5"))
    held = json.loads(said.removeprefix("mcp ok: "))["task_id"]
    assert world.app.queues.tasks[held].status == "pending"
    await until(
        lambda: published(a.log_id)["waiting_on"] == "1 queue task",
        what="the pending callback task on the published card",
    )
    await world.app.registry.call("task.cancel", {"task_id": held})
    await until(
        lambda: (
            published(a.log_id)["waiting_on"] == ""
            and published(a.log_id)["attention"] == "done"
        ),
        what="the cancelled task gone from the published card",
    )
    await world.app.registry.call("task.cancel", {"task_id": busy})


async def test_a_change_of_attention_alone_goes_out_at_once(world):
    a = await world.spawn()
    card = a.wire()
    world.app._sessions_key({"upsert": card})
    _, urgent = world.app._sessions_key({"upsert": card})
    assert not urgent
    _, urgent = world.app._sessions_key({"upsert": {**card, "attention": "needs_you"}})
    assert urgent


@pytest.mark.parametrize(
    "args",
    [
        {"attention": "needs_you", "line": "q", "replies": ["x" * 81]},
        {"attention": "needs_you", "line": "q", "replies": ["one\ntwo"]},
        {"attention": "needs_you", "line": "q", "replies": ["a", "b", "c", "d"]},
        {"attention": "waiting", "line": "q", "replies": []},
        {"attention": "done", "line": "x" * 141, "replies": []},
        {"attention": "done", "line": "one\ntwo", "replies": []},
    ],
)
async def test_bad_reports_are_refused_and_change_nothing(world, args):
    from aegis.ops import Caller

    a = await world.spawn()
    with pytest.raises(OpError) as e:
        await world.app.registry.call("turn.end", args, Caller("agent", a.log_id))
    assert e.value.code == "bad_params"
    assert a.wire()["attention"] == "done" and a.wire()["attention_line"] == ""


async def test_two_items_doing_is_refused_and_long_plans_are_cut(world):
    from aegis.ops import Caller

    a = await world.spawn()
    me = Caller("agent", a.log_id)
    with pytest.raises(OpError):
        await world.app.registry.call(
            "plan.update",
            {
                "items": [
                    {"text": "a", "state": "doing"},
                    {"text": "b", "state": "doing"},
                ]
            },
            me,
        )
    items = [{"text": "t" * 200, "state": "pending"} for _ in range(40)]
    await world.app.registry.call("plan.update", {"items": items}, me)
    plan = a.wire()["plan"]
    assert len(plan) == 30 and len(plan[0]["text"]) == 120


async def test_reading_the_reply_clears_a_done_mark_on_the_published_card(
    world, published
):
    a = await world.spawn()
    await turn(a, mcp("turn_end", attention="done", line="did it", replies=[]))
    await until(
        lambda: (published(a.log_id) or {}).get("mark") == "done",
        what="the done mark",
    )
    ids = [e["id"] for e in a.view() if e["kind"] == "prose" and e["unread"]]
    assert ids
    r = await world.app.registry.call(
        "session.read", {"log_id": a.log_id, "ids": ids + ["e0.0"]}
    )
    assert r == {"read": len(ids), "unread": 0}
    await until(
        lambda: published(a.log_id).get("mark") == "idle", what="the cleared mark"
    )
    assert published(a.log_id)["attention"] == "done"


async def test_session_read_is_for_people_only(world):
    from aegis.ops import Caller

    a = await world.spawn()
    with pytest.raises(OpError) as e:
        await world.app.registry.call(
            "session.read", {"log_id": a.log_id, "ids": []}, Caller("agent", a.log_id)
        )
    assert e.value.code == "not_for_agents"
