"""A workflow's card as a canvas item, on the server: it docks through the
layout exactly as a page or a file does (spec §07)."""

import pytest

from .test_agents import CONFIG, World, mcp, turn
from .test_workflows_e2e import RESULT, flow, ok


@pytest.fixture
async def world(tmp_path, fake_claude):
    (tmp_path / ".aegis.yaml").write_text(CONFIG)
    w = await World(tmp_path, fake_claude).start()
    yield w
    await w.stop()


def panes(layout: dict) -> list[dict]:
    return [p for w in layout["windows"] for c in w["columns"] for p in c["panes"]]


async def test_arm_with_dock_places_the_card_as_artifact_send_does(world):
    a = await world.spawn()
    out = ok(
        await turn(
            a,
            mcp("workflow_arm", definition=flow(RESULT, RESULT), args={}, dock="right"),
        )
    )
    assert "docked" not in out
    lay = ok(await turn(a, mcp("layout_read")))
    wf = [p for p in panes(lay) if p["id"] == f"wf-{out['run']}"]
    assert wf and wf[0]["kind"] == "workflow"
    assert wf[0]["title"] == "Two steps" and wf[0]["run"] == out["run"]


async def test_a_bad_dock_arms_nothing(world):
    a = await world.spawn()
    said = await turn(
        a,
        mcp(
            "workflow_arm",
            definition=flow(RESULT, RESULT),
            args={},
            dock="below:nothing",
        ),
    )
    assert said.startswith("mcp error") and "nothing" in said
    assert world.app.workflows.runs == {}


async def test_layout_set_takes_a_workflow_and_names_it_in_its_errors(world):
    a = await world.spawn()
    run = ok(
        await turn(a, mcp("workflow_arm", definition=flow(RESULT, RESULT), args={}))
    )["run"]
    one = {
        "n": 1,
        "columns": [
            {"panes": [{"id": "transcript"}]},
            {"panes": [{"id": f"wf-{run}"}]},
        ],
    }
    ok(await turn(a, mcp("layout_set", windows=[one])))
    assert f"wf-{run}" in [p["id"] for p in panes(a.layout)]
    one["columns"][1]["panes"][0]["id"] = "wf-nope"
    said = await turn(a, mcp("layout_set", windows=[one]))
    assert said.startswith("mcp error") and "workflow" in said


async def test_a_replacement_takes_the_docked_cards_place(world):
    a = await world.spawn()
    first = ok(
        await turn(
            a,
            mcp("workflow_arm", definition=flow(RESULT, RESULT), args={}, dock="right"),
        )
    )["run"]
    second = ok(
        await turn(a, mcp("workflow_arm", definition=flow(RESULT, RESULT), args={}))
    )["run"]
    ids = [p["id"] for p in panes(a.layout)]
    assert f"wf-{second}" in ids and f"wf-{first}" not in ids
    assert not a.dockable(f"wf-{first}")


async def test_a_person_docks_a_finished_run(world):
    a = await world.spawn()
    run = ok(
        await turn(a, mcp("workflow_arm", definition=flow(RESULT, RESULT), args={}))
    )["run"]
    await world.app.registry.call("workflow.reject", {"run": run})
    await world.app.registry.call(
        "layout.dock", {"log_id": a.log_id, "pane": f"wf-{run}", "where": "right"}
    )
    assert f"wf-{run}" in [p["id"] for p in panes(a.layout)]


MANY = """id: many
title: Many
say: Forty quick commands.
root:
  map: {over: "{{args.xs}}", as: x, cap: 40, do: {sh: "true", id: t, say: "Run {{x}}."}}
  id: m
  say: All of them.
args: {type: object, properties: {xs: {type: array}}, required: [xs]}
"""


async def test_a_large_map_builds_its_wire_a_few_times_not_per_record(
    world, monkeypatch
):
    from .conftest import until

    wf = world.app.workflows
    built = []
    real = wf.wire
    monkeypatch.setattr(wf, "wire", lambda run: built.append(run) or real(run))
    sent: list[dict] = []
    publish = world.app.publish

    def spy(channel, ops):
        if channel == "workflows":
            sent.extend(op["upsert"] for op in ops if "upsert" in op)
        return publish(channel, ops)

    monkeypatch.setattr(world.app, "publish", spy)
    a = await world.spawn()
    run = ok(
        await turn(a, mcp("workflow_arm", definition=MANY, args={"xs": list(range(40))}))
    )["run"]
    await world.app.registry.call("workflow.approve", {"run": run})
    await until(lambda: wf.runs[run].status == "done", timeout=20)
    await until(lambda: not wf._dirty, timeout=2)  # the last flush went out
    records = len((wf.dir / f"{run}.jsonl").read_text().splitlines())
    assert records > 80
    assert built.count(run) < records / 4, (built.count(run), records)
    # The browser still gets the end: the last publish carries "done".
    assert [w for w in sent if w["run"] == run][-1]["status"] == "done"
