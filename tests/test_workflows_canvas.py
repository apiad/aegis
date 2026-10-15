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
