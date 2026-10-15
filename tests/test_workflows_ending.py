"""What a run's end does to the pool and to its card, against a real server
and fake agents: nothing stays queued on an ended run, and a node another run
queued behind it starts on the next pass, not the next timed one."""

import time

from aegis import workflows as engine

from .conftest import until
from .test_agents import CONFIG
from . import test_workflows_parallel
from .test_workflows_parallel import arm, leaf, report, script

world = test_workflows_parallel.world  # the fixture, shared

QUEUES = f"""
id: q
title: Q
say: One runs, one queues.
root:
  par:
    - {leaf("a", "A.", script(["sleep", 30]))}
    - {leaf("b", "B.", report())}
  say: Both.
"""


def queued(wf, run) -> list[str]:
    return [r["id"] for r in wf.wire(run)["tree"] if r["state"] == "queued"]


async def test_a_stopped_run_shows_no_queued_rows_before_or_after_a_restart(world):
    (world.root / ".aegis.yaml").write_text(CONFIG + "workflows: {max_agents: 1}\n")
    run = await arm(world, QUEUES)
    wf = world.app.workflows
    await until(lambda: queued(wf, run), timeout=20)
    await world.app.registry.call("workflow.stop", {"run": run})
    assert wf.wire(run)["status"] == "stopped"
    assert queued(wf, run) == []
    assert wf._queued.get(run, set()) == set()
    await world.restart()
    assert queued(world.app.workflows, run) == []


class _Deaf:
    """The engine as the registry sees it, minus its wakes: a turn ending
    (the interrupted node's, the owner's inbox turn) no longer starts a
    pass, directly or through the quota reading it triggers, so only the
    stop itself can. A stop whose node is idle and whose owner is closed
    ends no turn at all."""

    def __init__(self, wf) -> None:
        self._wf = wf

    def changed(self) -> None:
        pass

    def __getattr__(self, name):
        return getattr(self._wf, name)


async def test_a_queued_node_starts_on_the_pass_a_stop_wakes(world):
    (world.root / ".aegis.yaml").write_text(CONFIG + "workflows: {max_agents: 1}\n")
    first = await arm(world, QUEUES.replace("id: q\n", "id: first\n"))
    wf = world.app.workflows
    await until(lambda: queued(wf, first), timeout=20)  # a holds the only slot
    second = await arm(
        world,
        f"""
id: second
title: Second
say: One node behind the first run.
root: {leaf("c", "C.", report(wait=0.1))}
""",
    )
    await until(lambda: queued(wf, second) == ["c"], timeout=20)
    assert engine.PASS_S >= 30
    world.app.sessions.workflows = _Deaf(wf)
    world.app.sessions.quota = None
    await world.app.registry.call("workflow.stop", {"run": first})
    t0 = time.monotonic()
    await until(lambda: "c" in wf.runs[second].nodes, timeout=5)
    assert time.monotonic() - t0 < 5
