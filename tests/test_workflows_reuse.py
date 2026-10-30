"""A step that runs in an earlier step's session (spec §04, §05)."""

import asyncio
import json

import pytest

from .conftest import until
from .test_agents import CONFIG, World, mcp, turn
from .test_workflows_e2e import ok
from .test_workflows_parallel import report


@pytest.fixture
async def world(tmp_path, fake_claude):
    (tmp_path / ".aegis.yaml").write_text(CONFIG + "workflows: {max_agents: 1}\n")
    w = await World(tmp_path, fake_claude).start()
    yield w
    await w.stop()


def reuse(first: str, second: str, third: str | None = None) -> str:
    more = (
        f"\n    - {{id: again, say: Once more., agent: {{session: fix, prompt: {json.dumps(third)}}}}}"
        if third
        else ""
    )
    return f"""id: reuse
title: Reuse
say: Write it, then fix it in the same session.
root:
  seq:
    - {{id: impl, say: Write it., agent: {{agent: opus, prompt: {json.dumps(first)}}}}}
    - {{id: fix, say: Fix it., agent: {{session: impl, prompt: {json.dumps(second)}}}}}{more}
  say: Both.
"""


def settled(wf, run: str) -> bool:
    """Ended, or a step failed: a failed step waits on the main agent."""
    st = wf.runs[run]
    return st.status != "running" or any(
        n.status == "failed" for n in st.nodes.values()
    )


async def run_it(world, text: str) -> str:
    a = await world.spawn()
    run = ok(await turn(a, mcp("workflow_arm", definition=text, args={})))["run"]
    await world.app.registry.call("workflow.approve", {"run": run})
    return run


async def test_the_second_step_runs_in_the_first_ones_session(world):
    wf = world.app.workflows
    run = await run_it(world, reuse(report(1, 0.2), report(2, 0.2), report(3, 0.2)))
    await until(lambda: settled(wf, run), timeout=20)
    st = wf.runs[run]
    assert st.status == "done", {k: (n.status, n.error) for k, n in st.nodes.items()}
    lids = {st.nodes[k].log_id for k in ("impl", "fix", "again")}
    assert len(lids) == 1
    # Archived after its last use, not before: the run is done and it is gone.
    assert next(iter(lids)) not in world.app.sessions.sessions


async def test_a_reused_session_takes_no_new_slot(world):
    # max_agents 1: a fresh session for fix would wait forever behind impl's.
    wf = world.app.workflows
    run = await run_it(world, reuse(report(1, 0.2), report(2, 0.2)))
    await until(lambda: settled(wf, run), timeout=20)
    st = wf.runs[run]
    assert st.status == "done", {k: (n.status, n.error) for k, n in st.nodes.items()}


async def test_a_result_from_the_reused_session_finishes_the_later_step(world):
    wf = world.app.workflows
    run = await run_it(world, reuse(report(1, 0.2), report(9, 0.2)))
    await until(lambda: settled(wf, run), timeout=20)
    st = wf.runs[run]
    assert st.status == "done", {k: (n.status, n.error) for k, n in st.nodes.items()}
    assert st.nodes["impl"].result == {"n": 1} and st.nodes["fix"].result == {"n": 9}


def steps(body: str) -> str:
    return f"id: reuse\ntitle: Reuse\nsay: Reuse.\nroot:\n  seq:\n{body}\n  say: All.\n"


IMPL = f"    - {{id: impl, say: Write it., agent: {{agent: opus, prompt: {json.dumps(report(1, 0.2))}}}}}\n"


async def test_a_loop_reuses_an_outside_session_in_every_round(world):
    # The first round must not close impl's session: round 2 runs in it too.
    fix = json.dumps(report(2, 0.2))
    text = steps(
        IMPL
        + "    - loop:\n"
        + "        max: 2\n"
        + f"        do: {{id: fix, say: Fix it., agent: {{session: impl, prompt: {fix}}}}}\n"
        + "      id: rounds\n"
        + "      say: Twice."
    )
    wf = world.app.workflows
    run = await run_it(world, text)
    await until(lambda: settled(wf, run), timeout=20)
    st = wf.runs[run]
    assert st.status == "done", {k: (n.status, n.error) for k, n in st.nodes.items()}
    lids = {st.nodes[k].log_id for k in ("impl", "rounds.1.fix", "rounds.2.fix")}
    assert len(lids) == 1 and next(iter(lids)) not in world.app.sessions.sessions


async def test_a_reusing_step_in_a_branch_not_taken_frees_its_slot(world):
    # max_agents 1: impl's session, kept for a fix that never runs, would
    # hold the only slot and later wait forever.
    fix = json.dumps(report(2, 0.2))
    later = json.dumps(report(3, 0.2))
    text = steps(
        IMPL
        + '    - {if: {when: {expr: "false"}, then: '
        + f"{{id: fix, say: Fix it., agent: {{session: impl, prompt: {fix}}}}}}}, "
        + "id: maybe, say: Maybe.}\n"
        + f"    - {{id: later, say: Then this., agent: {{agent: opus, prompt: {later}}}}}"
    )
    wf = world.app.workflows
    run = await run_it(world, text)
    await until(lambda: settled(wf, run), timeout=20)
    st = wf.runs[run]
    assert st.status == "done", {k: (n.status, n.error) for k, n in st.nodes.items()}
    assert st.nodes["impl"].log_id not in world.app.sessions.sessions


async def test_a_skipped_user_closes_the_session_it_kept(world):
    from aegis.ops import Caller

    # fix ends its turns with no result, fails, and is skipped: nothing
    # will run in impl's session again, so it closes and later gets the slot.
    later = json.dumps(report(3, 0.2))
    text = steps(
        IMPL
        + "    - {id: fix, say: Fix it., agent: {session: impl, prompt: hello}}\n"
        + f"    - {{id: later, say: Then this., agent: {{agent: opus, prompt: {later}}}}}"
    )
    wf = world.app.workflows
    a = await world.spawn()
    run = ok(await turn(a, mcp("workflow_arm", definition=text, args={})))["run"]
    await world.app.registry.call("workflow.approve", {"run": run})
    await until(lambda: "fix" in wf.runs[run].open, timeout=20)
    await world.app.registry.call(
        "workflow.answer",
        {"run": run, "to": "fix", "choice": "skip", "text": "Not needed."},
        Caller("agent", a.log_id),
    )
    await until(
        lambda: settled(wf, run) and wf.runs[run].status != "running", timeout=20
    )
    st = wf.runs[run]
    assert st.status == "done", {k: (n.status, n.error) for k, n in st.nodes.items()}
    assert st.nodes["impl"].log_id not in world.app.sessions.sessions


async def test_a_reused_session_names_the_step_running_in_it(world):
    wf = world.app.workflows
    run = await run_it(world, reuse(report(1, 0.2), report(2, 1.5)))
    await until(
        lambda: (
            (n := wf.runs[run].nodes.get("fix")) is not None and n.status == "running"
        ),
        timeout=20,
    )
    s = world.app.sessions.sessions[wf.runs[run].nodes["impl"].log_id]
    assert s.workflow["node"] == "fix" and s.wire()["workflow"]["node"] == "fix"
    await until(lambda: settled(wf, run), timeout=20)
    st = wf.runs[run]
    assert st.status == "done", {k: (n.status, n.error) for k, n in st.nodes.items()}


async def test_a_spent_steps_session_names_the_step_that_may_continue_it(world):
    from aegis.ops import OpError

    # impl is done and fix waits behind a pause: impl's session is held for
    # fix, and a person closing it is told so (#678).
    fix = json.dumps(report(2, 0.2))
    text = steps(
        IMPL
        + "    - {wait: 30s, id: hold, say: Hold.}\n"
        + f"    - {{id: fix, say: Fix it., agent: {{session: impl, prompt: {fix}}}}}"
    )
    wf = world.app.workflows
    run = await run_it(world, text)
    await until(lambda: wf.runs[run].nodes.get("hold"), timeout=20, what="the hold")
    lid = wf.runs[run].nodes["impl"].log_id
    assert wf.runs[run].nodes["impl"].status == "done"
    with pytest.raises(OpError) as e:
        await world.app.registry.call("session.close", {"log_id": lid})
    assert e.value.code == "in_workflow"
    assert "which is done, but fix may still continue" in e.value.message
    assert lid in world.app.sessions.sessions


async def test_a_spent_loop_is_done_and_frees_the_session_it_kept(world):
    # #741: each item's impl stayed open behind a loop that read running once
    # its rounds were spent, so the next item's fresh step never had room.
    r = json.dumps(report(1, 0.1))
    text = f"""id: spent
title: Spent
say: Each item writes, fixes in rounds, then has a fresh review.
limits: {{total: 1}}
root:
  map:
    over: [1, 2]
    as: x
    cap: 1
    do:
      seq:
        - {{id: impl, say: Write it., agent: {{agent: opus, prompt: {r}}}}}
        - loop:
            max: 2
            until: {{expr: "false"}}
            at_max: done
            do: {{id: fix, say: Fix it., agent: {{session: impl, prompt: {r}}}}}
          id: rounds
          say: Twice.
        - {{id: review, say: Review it., agent: {{agent: opus, prompt: {r}}}}}
      id: item
      say: One item.
  id: items
  say: Both.
"""
    wf = world.app.workflows
    run = await run_it(world, text)
    await until(lambda: settled(wf, run), timeout=20)
    st = wf.runs[run]
    assert st.status == "done", {k: (n.status, n.error) for k, n in st.nodes.items()}
    rows = {r["id"]: r["state"] for r in wf.wire(run)["tree"]}
    assert rows["items.0.rounds"] == rows["items.1.rounds"] == "done"
    assert st.nodes["items.0.impl"].log_id not in world.app.sessions.sessions


def stuck_wakes(main) -> list[str]:
    return [
        e["md"]
        for e in main.entries()
        if e["kind"] == "inbox" and " · stuck · " in e.get("md", "")
    ]


def later_step(r: str, wrap: bool = False) -> str:
    """impl, a fresh review, then fix in impl's session: review needs the slot
    only fix can free, and fix runs after it (#823)."""
    steps = f"""seq:
    - {{id: impl, say: Write it., agent: {{agent: opus, prompt: {r}}}}}
    - {{id: review, say: Review it., agent: {{agent: opus, prompt: {r}}}}}
    - {{id: fix, say: Fix it., agent: {{session: impl, prompt: {r}}}}}
  id: item
  say: One item."""
    if wrap:
        do = steps.replace("\n", "\n    ")
        steps = f"""map:
    over: [1]
    as: x
    do:
      {do}
  id: items
  say: All."""
    return f"""id: later
title: Later
say: Write, review, fix.
limits: {{total: 1}}
root:
  {steps}
"""


@pytest.mark.parametrize("wrap", [False, True], ids=["seq", "map"])
async def test_a_step_waiting_on_room_nobody_can_free_is_reported(
    world, monkeypatch, wrap
):
    monkeypatch.setattr("aegis.workflows.STUCK_S", 0.2)
    wf = world.app.workflows
    run = await run_it(world, later_step(json.dumps(report(1, 0.1)), wrap))
    st = wf.runs[run]
    main = wf.owner(st)
    await until(lambda: stuck_wakes(main), timeout=15)
    prefix = "items.0." if wrap else ""
    holder = world.app.sessions.sessions[st.nodes[prefix + "impl"].log_id]
    md = stuck_wakes(main)[0]
    assert "Review it." in md and holder.handle in md
    assert "workflow_edit" in md and "workflow_stop" in md
    assert any("Stuck" in e["text"] for e in st.story)
    # A notice: told, it leaves nothing open, and the run still waits.
    assert st.status == "running" and not st.open
    rows = {r["id"]: r["state"] for r in wf.wire(run)["tree"]}
    assert rows[prefix + "review"] == "queued"
    # Told once, however many passes find it the same.
    for _ in range(3):
        wf.changed()
        await wf.step()
    assert len(stuck_wakes(main)) == 1


async def test_a_step_waiting_on_room_an_agent_will_free_is_not_reported(
    world, monkeypatch
):
    monkeypatch.setattr("aegis.workflows.STUCK_S", 0.2)
    r, slow = json.dumps(report(1, 0.1)), json.dumps(report(2, 1.5))
    text = f"""id: busy
title: Busy
say: Two at once, one slot.
limits: {{total: 1}}
root:
  par:
    - {{id: a, say: First., agent: {{agent: opus, prompt: {slow}}}}}
    - {{id: b, say: Second., agent: {{agent: opus, prompt: {r}}}}}
  id: both
  say: Both.
"""
    wf = world.app.workflows
    run = await run_it(world, text)
    await until(lambda: settled(wf, run), timeout=20)
    st = wf.runs[run]
    assert st.status == "done", {k: (n.status, n.error) for k, n in st.nodes.items()}
    assert not stuck_wakes(wf.owner(st))


async def test_a_stuck_report_is_not_told_again_after_a_restart(world, monkeypatch):
    monkeypatch.setattr("aegis.workflows.STUCK_S", 0.2)
    wf = world.app.workflows
    run = await run_it(world, later_step(json.dumps(report(1, 0.1))))
    await until(lambda: stuck_wakes(wf.owner(wf.runs[run])), timeout=15)
    await world.restart()
    wf = world.app.workflows
    await wf.step()
    await asyncio.sleep(0.4)  # past STUCK_S: a new reading would be told by now
    await wf.step()
    assert len(stuck_wakes(wf.owner(wf.runs[run]))) == 1


async def test_raising_the_limits_of_a_stuck_run_lets_it_finish(tmp_path, fake_claude):
    # What the report tells the main agent to do has to work.
    (tmp_path / ".aegis.yaml").write_text(CONFIG + "workflows: {max_agents: 2}\n")
    world = await World(tmp_path, fake_claude).start()
    try:
        r = json.dumps(report(1, 0.1))
        wf = world.app.workflows
        text = later_step(r)
        run = await run_it(world, text)
        st = wf.runs[run]
        main = wf.owner(st)
        await until(lambda: stuck_wakes(main), timeout=15)
        await until(lambda: main.status == "idle", timeout=10)
        raised = text.replace("limits: {total: 1}", "limits: {total: 2}")
        ok(await turn(main, mcp("workflow_edit", run=run, definition=raised)))
        await world.app.registry.call("workflow.edit.apply", {"run": run, "v": 2})
        await until(lambda: settled(wf, run), timeout=20)
        assert st.status == "done", {
            k: (n.status, n.error) for k, n in st.nodes.items()
        }
    finally:
        await world.stop()
