"""Attention end to end: the fake claude calls the real tools over /mcp."""

import json

import pytest

from aegis.agent_ops import LISTED
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
    assert c["plan_clock"]["running"] in ("work", "idle")
    assert all(isinstance(i["work_s"], float) for i in c["plan"])


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
            source=None,
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
    await until(lambda: a.status == "asleep", timeout=8, what="the exit")
    c = a.wire()
    assert c["attention"] == "error"
    assert c["attention_line"] == "Claude Code exited with code 3"
    await turn(a, "hello again")
    assert a.wire()["attention"] == "done"


async def test_a_clean_exit_between_turns_is_asleep_not_an_error(world):
    a = await world.spawn()
    await turn(a, "/quit 0.3")
    await until(lambda: a.status == "asleep", timeout=8, what="the exit")
    c = a.wire()
    assert c["attention"] != "error", c["attention_line"]
    assert c["asleep_why"] == "exited"
    assert a.entries()[-1]["summary"] == "asleep: Claude Code exited between turns"


async def test_a_parent_waits_on_a_working_child(world, published):
    a = await world.spawn()
    said = await turn(a, mcp("session_spawn", agent="opus", prompt="/busy 1"))
    child = world.session(json.loads(said.removeprefix("mcp ok: "))["log_id"])
    await until(lambda: child.status == "working", timeout=8, what="the child working")
    assert a.wire()["attention"] == "waiting" and a.wire()["waiting_on"] == child.handle
    await until(lambda: child.status == "idle", timeout=10, what="the child done")
    assert a.wire()["attention"] == "done"
    await until(
        lambda: published(a.log_id)["attention"] == "done",
        what="the parent's published card done",
    )


async def test_a_parent_waits_on_a_child_that_waits(world, published):
    a = await world.spawn()
    arm = mcp(
        "monitor_start",
        description="never",
        done="false",
        progress=None,
        interval_s=60,
        source=None,
    )
    said = await turn(a, mcp("session_spawn", agent="opus", prompt=arm))
    child = world.session(json.loads(said.removeprefix("mcp ok: "))["log_id"])
    await until(
        lambda: child.status == "idle" and world.app.monitors.of(child.log_id),
        timeout=10,
        what="the child idle on its monitor",
    )
    assert child.wire()["attention"] == "waiting"
    assert a.wire()["attention"] == "waiting" and a.wire()["waiting_on"] == child.handle
    await until(
        lambda: published(a.log_id)["attention"] == "waiting",
        what="the parent's published card waiting",
    )
    (m,) = world.app.monitors.of(child.log_id)
    await world.app.registry.call("monitor.cancel", {"monitor_id": m.id})
    await until(
        lambda: published(a.log_id)["attention"] == "done",
        what="the parent's published card done once the child's monitor ends",
    )


async def test_a_cancelled_queue_task_leaves_the_published_card(world, published):
    a = await world.spawn()
    said = await turn(
        a, mcp("queue_enqueue", queue="solo", payload="/busy 30", callback=False)
    )
    busy = json.loads(said.removeprefix("mcp ok: "))["task_id"]
    said = await turn(a, mcp("queue_enqueue", queue="solo", payload="/busy 5"))
    held = json.loads(said.removeprefix("mcp ok: "))["task_id"]
    assert world.app.queues.tasks[held].status == "pending"
    await until(
        lambda: published(a.log_id)["waiting_on"] == "1 pending queue task",
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


async def test_the_card_names_the_worker_it_waits_on(world, published):
    a = await world.spawn()
    said = await turn(a, mcp("queue_enqueue", queue="solo", payload="/busy 30"))
    task = world.app.queues.tasks[json.loads(said.removeprefix("mcp ok: "))["task_id"]]
    await until(lambda: task.worker in world.app.sessions.sessions, what="the worker")
    worker = world.session(task.worker)
    await until(
        lambda: published(a.log_id)["waiting_on"] == worker.handle,
        what="the worker's handle on the published card",
    )
    (w,) = published(a.log_id)["waiting_for"]
    assert w == {
        "kind": "task",
        "task_id": task.id,
        "queue": "solo",
        "status": "running",
        "key": worker.log_id,
        "handle": worker.handle,
    }
    world.app.sessions.rename(worker.log_id, "busy-worker", None)
    await until(
        lambda: published(a.log_id)["waiting_for"][0]["handle"] == "busy-worker",
        what="the worker's new handle on the published card",
    )
    await world.app.registry.call("task.cancel", {"task_id": task.id})
    await until(
        lambda: published(a.log_id)["waiting_for"] == [],
        what="the cancelled task gone from the published card",
    )


async def test_waiting_on_names_two_sessions_then_counts(world):
    a = await world.spawn()
    for _ in range(3):
        await turn(a, mcp("session_spawn", agent="opus", prompt="/busy 30"))
    kids = [
        s for s in world.app.sessions.sessions.values() if s.spec.spawned_by == a.log_id
    ]
    await until(
        lambda: all(k.status == "working" for k in kids),
        timeout=8,
        what="the children working",
    )
    c = a.wire()
    assert [w["kind"] for w in c["waiting_for"]] == ["session"] * 3
    first, second = (w["handle"] for w in c["waiting_for"][:2])
    assert c["waiting_on"] == f"{first}, {second}, +1"
    for k in kids:
        await world.app.sessions.close(k.log_id)


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


async def test_the_mark_follows_the_last_message_not_every_unread_one(world, published):
    a = await world.spawn()
    await turn(a, "hello")
    await turn(a, mcp("turn_end", attention="done", line="did it", replies=[]))
    prose = [e for e in a.view() if e["kind"] == "prose"]
    assert len(prose) == 2 and all(e["unread"] for e in prose)
    assert a.standing["last_message"] == prose[-1]["id"]
    await until(
        lambda: (published(a.log_id) or {}).get("mark") == "done",
        what="the done mark",
    )
    await world.app.registry.call(
        "session.read", {"log_id": a.log_id, "ids": [prose[-1]["id"]]}
    )
    await until(
        lambda: published(a.log_id).get("mark") == "idle", what="the cleared mark"
    )
    assert published(a.log_id)["unread"] == 1


async def test_session_read_is_for_people_only(world):
    from aegis.ops import Caller

    a = await world.spawn()
    with pytest.raises(OpError) as e:
        await world.app.registry.call(
            "session.read", {"log_id": a.log_id, "ids": []}, Caller("agent", a.log_id)
        )
    assert e.value.code == "not_for_agents"


async def test_session_list_reads_the_same_model_before_and_after_init(world):
    """Two sessions of one agent, one that has run a turn and one that has not,
    list the same model; the resolved id is its own field, null until named (#243)."""
    a, b = await world.spawn(), await world.spawn()
    said = await turn(a, mcp("session_list"))
    listed = {x["handle"]: x for x in json.loads(said.removeprefix("mcp ok: "))}
    assert listed[a.handle]["model"] == listed[b.handle]["model"]
    assert listed[a.handle]["model_id"] and listed[b.handle]["model_id"] is None


async def test_session_list_shows_another_sessions_card_and_plan(world):
    """One agent follows another's plan and state (#234)."""
    a, b = await world.spawn(), await world.spawn()
    items = [{"text": "read", "state": "done"}, {"text": "fix", "state": "doing"}]
    await turn(b, mcp("plan_update", items=items))
    await turn(b, mcp("turn_end", attention="needs_you", line="Rebase or merge?"))
    said = await turn(a, mcp("session_list"))
    listed = {x["handle"]: x for x in json.loads(said.removeprefix("mcp ok: "))}
    seen = listed[b.handle]
    card = b.wire()
    for key in LISTED:
        assert seen[key] == card[key], key
    assert (seen["attention"], seen["attention_line"]) == (
        "needs_you",
        "Rebase or merge?",
    )
    assert (seen["plan_now"], seen["plan_done"], seen["plan_total"]) == ("fix", 1, 2)
    for left_out in ("replies", "mark", "blink", "monitors", "unread"):
        assert left_out not in seen


UNFINISHED = [{"text": "read", "state": "done"}, {"text": "fix", "state": "doing"}]


async def test_done_with_an_unfinished_plan_is_refused_and_records_nothing(world):
    """#405: done puts an archive button on the card, so it is for a task that
    is over, and a plan with items left says it is not."""
    from aegis.ops import Caller

    a = await world.spawn()
    me = Caller("agent", a.log_id)
    await world.app.registry.call("plan.update", {"items": UNFINISHED}, me)
    with pytest.raises(OpError) as e:
        await world.app.registry.call(
            "turn.end", {"attention": "done", "line": "did it"}, me
        )
    assert "plan_update" in e.value.message and "review" in e.value.message
    assert a.standing.get("report") is None
    assert a.wire()["attention_line"] == ""


async def test_done_passes_once_every_plan_item_is_done(world):
    a = await world.spawn()
    items = [{"text": "read", "state": "done"}, {"text": "fix", "state": "done"}]
    await turn(a, mcp("plan_update", items=items))
    said = await turn(a, mcp("turn_end", attention="done", line="Fixed."))
    assert not said.startswith("mcp error"), said
    assert a.standing["report"]["attention"] == "done"


async def test_done_passes_without_a_plan(world):
    a = await world.spawn()
    said = await turn(a, mcp("turn_end", attention="done", line="Answered."))
    assert not said.startswith("mcp error"), said
    assert a.standing["report"]["attention"] == "done"


@pytest.mark.parametrize("attention", ["review", "needs_you"])
async def test_review_and_needs_you_pass_with_an_unfinished_plan(world, attention):
    a = await world.spawn()
    await turn(a, mcp("plan_update", items=UNFINISHED))
    said = await turn(a, mcp("turn_end", attention=attention, line="Step one."))
    assert not said.startswith("mcp error"), said
    assert a.wire()["attention"] == attention


async def test_done_with_a_live_monitor_is_refused_and_records_nothing(world):
    """#608: a live monitor will wake the session again, so its task is not over."""
    a = await world.spawn()
    await turn(
        a,
        mcp("monitor_start", description="w", done="false", progress=None, source=None),
    )
    said = await turn(a, mcp("turn_end", attention="done", line="Merged."))
    assert said.startswith("mcp error: still_waiting:") and "1 monitor" in said, said
    assert "review" in said and "monitor_cancel" in said, said
    assert a.standing.get("report") is None


async def test_review_passes_with_a_live_monitor(world):
    a = await world.spawn()
    await turn(
        a,
        mcp("monitor_start", description="w", done="false", progress=None, source=None),
    )
    said = await turn(a, mcp("turn_end", attention="review", line="CI runs."))
    assert not said.startswith("mcp error"), said
