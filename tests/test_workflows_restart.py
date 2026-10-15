"""Spec §12: stop the server during each kind of node and check the restart
table in §05, completed in the slice 3 plan."""

import asyncio
import contextlib
import json
import time

import pytest

from aegis import restarts
from aegis.workflow_log import RunLog, fold
from aegis.workflows import CUT

from .conftest import until
from .test_workflows_parallel import report
from .test_workflows_steps import ended, flow, run, verdict, world  # noqa: F401


def lines(w, run_id: str) -> list[dict]:
    p = w.app.roots.state_root / "workflow-runs" / f"{run_id}.jsonl"
    return [json.loads(x) for x in p.read_text().splitlines()]


async def running(w, run_id: str, node: str, also=lambda n: True):
    wf = w.app.workflows
    await until(
        lambda: (
            (n := wf.runs[run_id].nodes.get(node)) is not None
            and n.status == "running"
            and also(n)
        ),
        timeout=15,
    )


async def test_an_agent_continues_after_a_restart(world):  # noqa: F811
    run_id = await run(
        world,
        flow(
            f"{{agent: {{agent: opus, prompt: {json.dumps(report(wait=3))}}}, id: a, say: Work.}}"
        ),
    )
    await running(world, run_id, "a")
    await world.restart()
    # The session continues after the boot. The fake harness does not take
    # its script up again, so the test reports for it, as the agent would.
    wf = world.app.workflows
    s = world.app.sessions.sessions[wf.runs[run_id].nodes["a"].log_id]
    wf.result(s, {"n": 7}, "Did it after the restart.")
    wf.changed()
    st = await ended(world, run_id, timeout=40)
    assert st.status == "done", {k: (n.status, n.error) for k, n in st.nodes.items()}
    assert sum(r.get("ev") == "node_started" for r in lines(world, run_id)) == 1


async def test_a_judge_continues_after_a_restart(world):  # noqa: F811
    j = json.dumps(verdict(True, wait=3))
    run_id = await run(
        world,
        flow(f"{{check: {{judge: {{agent: opus, prompt: {j}}}}}, id: c, say: Judge.}}"),
    )
    await running(world, run_id, "c", lambda n: n.judges.get("", {}).get("log_id"))
    await world.restart()
    wf = world.app.workflows  # the judge reports for its continued session
    s = world.app.sessions.sessions[wf.runs[run_id].nodes["c"].judges[""]["log_id"]]
    wf.result(s, {"pass": True, "why": "It holds."}, "It holds.")
    wf.changed()
    st = await ended(world, run_id, timeout=40)
    assert st.status == "done", {k: (n.status, n.error) for k, n in st.nodes.items()}
    assert sum(r.get("ev") == "judge_started" for r in lines(world, run_id)) == 1


async def test_a_running_sh_with_no_task_is_cut_and_not_retried(world):  # noqa: F811
    # The rule of the slow test below, without a restart: fold the log as
    # boot does, with the sh running and no task in this process.
    wf = world.app.workflows
    run_id = await run(world, flow('{sh: "sleep 30", id: s, retries: 2, say: Slow.}'))
    await running(world, run_id, "s", lambda n: n.pid)
    async with wf._lock:
        task = wf._tasks[(run_id, "s")]
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task
        wf.runs[run_id] = fold(RunLog(wf.dir / f"{run_id}.jsonl").read())
    assert wf.runs[run_id].nodes["s"].status == "running"
    await wf.step()
    st = wf.runs[run_id]
    assert st.status == "failed" and st.nodes["s"].error == CUT, st.nodes["s"]
    assert st.nodes["s"].attempts == 0
    assert sum(r.get("ev") == "node_started" for r in lines(world, run_id)) == 1


@pytest.mark.slow
async def test_an_sh_cut_by_a_restart_is_never_run_again(world):  # noqa: F811
    marker = world.root / "ran"
    run_id = await run(
        world, flow(f'{{sh: "sleep 4 && touch {marker}", id: s, say: Slow.}}')
    )
    await running(world, run_id, "s", lambda n: n.pid)
    pid = world.app.workflows.runs[run_id].nodes["s"].pid
    await world.restart()
    st = await ended(world, run_id)
    assert st.status == "failed" and st.nodes["s"].error.startswith("cut by restart")
    assert restarts.started_at(pid) is None
    await asyncio.sleep(5)
    assert not marker.exists()


@pytest.mark.slow
async def test_an_sh_a_crash_left_running_is_ended_at_boot(world, monkeypatch):  # noqa: F811
    marker = world.root / "ran"
    # Longer than the 2 s + 2 s a cancelled task waits on its group.
    run_id = await run(
        world, flow(f'{{sh: "sleep 10 && touch {marker}", id: s, say: Slow.}}')
    )
    await running(world, run_id, "s", lambda n: n.pid)
    pid = world.app.workflows.runs[run_id].nodes["s"].pid
    # A crash: the server goes and its command lives on. Its task is still
    # cancelled, so nothing of the old server writes to the log afterwards.
    monkeypatch.setattr("os.killpg", lambda *a: None)
    await world.stop()
    assert restarts.started_at(pid) is not None
    monkeypatch.undo()
    await world.start()
    assert restarts.started_at(pid) is None
    st = await ended(world, run_id)
    assert st.nodes["s"].error.startswith("cut by restart")
    await asyncio.sleep(7)
    assert not marker.exists()


@pytest.mark.slow
async def test_an_sh_check_runs_its_probe_again(world):  # noqa: F811
    run_id = await run(
        world,
        flow(
            '{check: {sh: "test -f flag", every: 5s}, id: c, say: Wait for the flag.}'
        ),
    )
    await running(world, run_id, "c")
    await world.restart()
    (world.root / "flag").write_text("")
    st = await ended(world, run_id, timeout=20)
    assert st.status == "done", {k: (n.status, n.error) for k, n in st.nodes.items()}
    assert sum(r.get("ev") == "node_started" for r in lines(world, run_id)) == 1


@pytest.mark.slow
async def test_a_wait_resumes_against_wall_time(world):  # noqa: F811
    t0 = time.time()
    run_id = await run(world, flow("{wait: 4s, id: w, say: Pause.}"))
    await running(world, run_id, "w")
    await world.restart()
    st = await ended(world, run_id, timeout=20)
    took = st.nodes["w"].ended - t0
    assert st.status == "done" and 4 <= took < 12


@pytest.mark.slow
async def test_an_if_deciding_asks_its_probe_again_and_logs_one_branch(world):  # noqa: F811
    run_id = await run(
        world,
        flow(
            '{if: {when: {sh: "sleep 2; true"}, then: {wait: 1s, say: Then.}}, id: i, say: Decide.}'
        ),
    )
    await asyncio.sleep(0.5)  # the probe is in flight
    await world.restart()
    st = await ended(world, run_id, timeout=30)
    assert st.status == "done" and st.nodes["i"].took == "then"
    assert sum(r.get("ev") == "branch" for r in lines(world, run_id)) == 1


@pytest.mark.slow
async def test_a_loop_keeps_its_round_through_a_restart(world):  # noqa: F811
    run_id = await run(
        world,
        flow(
            "{loop: {max: 3, do: {wait: 2s, id: w, say: Pause.}}, id: l, say: Three pauses.}"
        ),
    )
    await until(
        lambda: world.app.workflows.runs[run_id].nodes.get("l.2.w") is not None,
        timeout=20,
    )
    await world.restart()
    st = await ended(world, run_id, timeout=30)
    assert st.status == "done" and st.nodes["l"].round == 3
    assert sum(r.get("ev") == "loop_started" for r in lines(world, run_id)) == 1


@pytest.mark.slow
async def test_a_deadline_that_passed_while_down_fires_at_boot(world):  # noqa: F811
    run_id = await run(world, flow("{wait: 1h, id: w, deadline: 3s, say: Too long.}"))
    await running(world, run_id, "w")
    await world.stop()
    await asyncio.sleep(4)
    await world.start()
    st = await ended(world, run_id, timeout=10)
    assert st.status == "failed" and "deadline" in st.nodes["w"].error


@pytest.mark.slow
async def test_a_retry_count_survives_a_restart(world):  # noqa: F811
    run_id = await run(
        world,
        flow(
            '{sh: "echo x >> tries && sleep 2 && test $(wc -l < tries) -ge 3", id: f, retries: 3, say: Flaky.}'
        ),
    )
    await running(world, run_id, "f", lambda n: n.attempts >= 1 and n.pid)
    await world.restart()  # cuts attempt 2: a cut sh is not retried
    st = await ended(world, run_id, timeout=20)
    assert st.nodes["f"].attempts >= 1 and st.nodes["f"].error.startswith(
        "cut by restart"
    )


@pytest.mark.slow
async def test_a_cut_sh_whose_deadline_passed_while_down_is_not_run_again(world):  # noqa: F811
    run_id = await run(
        world,
        flow(
            '{sh: "echo x >> tries && sleep 30", id: s, deadline: 3s, retries: 1, say: Slow.}'
        ),
    )
    await running(world, run_id, "s", lambda n: n.pid)
    await world.stop()
    await asyncio.sleep(3.5)
    await world.start()
    st = await ended(world, run_id, timeout=10)
    assert st.status == "failed" and st.nodes["s"].error.startswith("cut by restart")
    assert sum(r.get("ev") == "node_started" for r in lines(world, run_id)) == 1
    assert (world.root / "tries").read_text().count("x") == 1


@pytest.mark.slow
async def test_a_cut_sh_past_the_default_limit_is_not_run_again(world, monkeypatch):  # noqa: F811
    monkeypatch.setattr("aegis.workflows.SH_LIMIT_S", 3.0)
    run_id = await run(
        world,
        flow('{sh: "echo x >> tries && sleep 30", id: s, retries: 1, say: Slow.}'),
    )
    await running(world, run_id, "s", lambda n: n.pid)
    await world.stop()
    await asyncio.sleep(3.5)
    await world.start()
    st = await ended(world, run_id, timeout=10)
    assert st.status == "failed" and st.nodes["s"].error.startswith("cut by restart")
    assert (world.root / "tries").read_text().count("x") == 1


def _log_path(w, run_id: str):
    return w.app.roots.state_root / "workflow-runs" / f"{run_id}.jsonl"


@pytest.mark.slow
async def test_a_crash_right_after_a_loop_starts_keeps_round_one(world):  # noqa: F811
    run_id = await run(
        world,
        flow("{loop: {max: 2, do: {wait: 1s, id: w, say: Pause.}}, id: l, say: Two.}"),
    )
    await running(world, run_id, "l")
    await world.stop()
    # As a crash leaves it: the loop's start is the last record written.
    recs = lines(world, run_id)
    cut = next(i for i, r in enumerate(recs) if r.get("ev") == "loop_started")
    _log_path(world, run_id).write_text(
        "".join(json.dumps(r) + "\n" for r in recs[: cut + 1])
    )
    await world.start()
    st = await ended(world, run_id, timeout=20)
    assert st.status == "done", {k: (n.status, n.error) for k, n in st.nodes.items()}
    assert sorted(k for k in st.nodes if k.endswith(".w")) == ["l.1.w", "l.2.w"]


async def test_a_crash_between_a_failure_and_its_retry_still_retries(world):  # noqa: F811
    run_id = await run(
        world,
        flow(
            '{sh: "echo x >> tries && (test $(wc -l < tries) -ge 2 || sleep 30)", id: s, retries: 1, say: Flaky.}'
        ),
    )
    await running(world, run_id, "s", lambda n: n.pid)
    await world.stop()
    # As a crash leaves it: the failure that retries, and nothing after.
    rec = {"t": time.time(), "ev": "failed", "node": "s", "error": "exit 1"}
    with _log_path(world, run_id).open("a") as f:
        f.write(json.dumps(rec | {"retrying": 2, "of": 2}) + "\n")
    await world.start()
    st = await ended(world, run_id, timeout=20)
    assert st.status == "done", st.nodes["s"].error
    assert st.nodes["s"].attempts == 1


@pytest.mark.slow
async def test_boot_ends_a_group_whose_leader_is_gone(world, monkeypatch):  # noqa: F811
    import os
    import signal

    from .test_workflows_steps import MEMBER

    run_id = await run(world, flow(f"{{sh: {json.dumps(MEMBER)}, id: s, say: Stub.}}"))
    await running(world, run_id, "s", lambda n: n.pid)
    await until(lambda: (world.root / "member").exists(), timeout=5)
    leader = world.app.workflows.runs[run_id].nodes["s"].pid
    member = int((world.root / "member").read_text())
    monkeypatch.setattr("os.killpg", lambda *a: None)  # a crash: nothing ends it
    await world.stop()
    monkeypatch.undo()
    os.kill(leader, signal.SIGKILL)  # the leader went; its member lives on
    await until(lambda: restarts.started_at(leader) is None, timeout=5)
    assert restarts.started_at(member) is not None
    await world.start()
    await until(lambda: restarts.started_at(member) is None, timeout=5)
    st = await ended(world, run_id)
    assert st.nodes["s"].error.startswith("cut by restart")
