"""The canvas end to end: the fake claude calls the real tools over /mcp, and
the person's side is called through the registry as the browser would."""

import pytest

from aegis import layout
from aegis.ops import OpError

from .test_agents import CONFIG, World, mcp, turn
from .test_artifacts_e2e import PAGE, ok


@pytest.fixture
async def world(tmp_path, fake_claude):
    (tmp_path / ".aegis.yaml").write_text(CONFIG)
    w = await World(tmp_path, fake_claude).start()
    yield w
    await w.stop()


async def live_page(world, a, tmp_path):
    made = ok(await turn(a, mcp("artifact_create", title="Knobs")))
    (
        tmp_path / ".aegis" / "state" / "artifacts" / made["id"] / "index.html"
    ).write_text(PAGE)
    ok(await turn(a, mcp("artifact_send", id=made["id"])))
    return made["id"]


async def test_a_session_writes_a_layout_and_its_card_carries_it(world, tmp_path):
    a = await world.spawn()
    aid = await live_page(world, a, tmp_path)
    assert (
        a.dockable(aid)
        and not a.dockable("art-nope")
        and not a.dockable("f-000000000000")
    )
    assert a.layout == layout.DEFAULT
    assert world.app.sessions.card(a)["layout"] is None
    new = layout.place(a.layout, aid, "right", a.dockable)
    a.set_layout(new, "person")
    assert a.standing["layout"] == new
    assert world.app.sessions.card(a)["layout"] == new


async def person(world, op, **params):
    return await world.app.registry.call(op, params)


async def test_the_person_docks_moves_resizes_and_undocks(world, tmp_path):
    a = await world.spawn()
    aid = await live_page(world, a, tmp_path)
    lay = await person(world, "layout.dock", log_id=a.log_id, pane=aid, where="left")
    assert layout.ids(lay) == [aid, "transcript"]
    lay = await person(
        world, "layout.resize", log_id=a.log_id, n=1, widths=[1, 3], heights=None
    )
    assert [c["w"] for c in lay["windows"][0]["columns"]] == [0.25, 0.75]
    lay = await person(world, "layout.undock", log_id=a.log_id, pane=aid)
    assert layout.ids(lay) == ["transcript"]
    with pytest.raises(OpError) as e:
        await person(world, "layout.undock", log_id=a.log_id, pane="transcript")
    assert e.value.code == "bad_params"
    with pytest.raises(OpError) as e:
        await person(
            world, "layout.dock", log_id=a.log_id, pane="art-nope", where="right"
        )
    assert e.value.code == "bad_params" and "no pane art-nope" in e.value.message


async def test_a_persons_drop_keeps_what_the_agent_docked_a_moment_before(
    world, tmp_path
):
    a = await world.spawn()
    one = await live_page(world, a, tmp_path)
    two = await live_page(world, a, tmp_path)
    windows = [
        {
            "n": 1,
            "columns": [{"panes": [{"id": "transcript"}]}, {"panes": [{"id": one}]}],
        }
    ]
    ok(await turn(a, mcp("layout_set", windows=windows)))
    lay = await person(
        world, "layout.dock", log_id=a.log_id, pane=two, where=f"below:{one}"
    )
    assert layout.ids(lay) == ["transcript", one, two]


async def test_the_agent_reads_and_sets_its_canvas(world, tmp_path):
    a = await world.spawn()
    aid = await live_page(world, a, tmp_path)
    windows = [
        {"n": 1, "columns": [{"panes": [{"id": "transcript"}]}]},
        {"n": 2, "columns": [{"panes": [{"id": aid}]}]},
    ]
    ok(await turn(a, mcp("layout_set", windows=windows)))
    got = ok(await turn(a, mcp("layout_read")))
    assert got["windows"][1]["columns"][0]["panes"][0] == {
        "id": aid,
        "h": 1.0,
        "kind": "artifact",
        "title": "Knobs",
    }
    assert got["windows"][0]["columns"][0]["panes"][0]["kind"] == "transcript"
    said = await turn(a, mcp("layout_set", windows=[{"n": 1, "columns": []}]))
    assert "the transcript must be in the layout" in said


async def test_the_canvas_survives_a_restart(world, tmp_path):
    a = await world.spawn()
    one = await live_page(world, a, tmp_path)
    two = await live_page(world, a, tmp_path)
    await person(world, "layout.dock", log_id=a.log_id, pane=one, where="right")
    await person(world, "layout.dock", log_id=a.log_id, pane=two, where="right")
    assert await turn(a, mcp("artifact_close", id=one)) == "mcp ok: closed"
    before = a.standing["layout"]
    assert layout.ids(before) == ["transcript", two]
    await world.restart()
    assert world.session(a.log_id).standing["layout"] == before


async def test_layout_ops_check_ownership(world):
    for name in ("layout.dock", "layout.undock", "layout.resize"):
        assert world.app.registry._ops[name].owned == "log_id"
    assert world.app.registry._ops["layout.read"].agent
    assert world.app.registry._ops["layout.set"].agent


async def test_one_file_by_a_relative_and_an_absolute_path_is_one_key(world, tmp_path):
    a = await world.spawn()
    (a.spec.cwd / "r.md").write_text("one")
    ok(await turn(a, mcp("file_send", paths=["r.md"])))
    (a.spec.cwd / "r.md").write_text("two")
    ok(await turn(a, mcp("file_send", paths=[str(a.spec.cwd / "r.md")])))
    files = [
        f for e in a.entries() if e["kind"] == "file" for f in e["detail"]["files"]
    ]
    assert files[0]["key"] == files[1]["key"] and [f["version"] for f in files] == [
        1,
        2,
    ]
    assert files[0]["replaced"] == 2
    assert a.dockable(files[0]["key"])
    # One path twice in one send, however it is spelled, is refused before
    # anything is sent.
    (a.spec.cwd / "sub").mkdir()
    said = await turn(
        a, mcp("file_send", paths=["sub/../r.md", str(a.spec.cwd / "r.md")])
    )
    assert said.startswith("mcp error: bad_params") and "r.md" in said, said
    assert len([e for e in a.entries() if e["kind"] == "file"]) == 2


async def test_a_malformed_layout_set_is_bad_params_not_an_internal_error(world):
    a = await world.spawn()
    for windows in (
        [{"n": 1, "columns": ["transcript"]}],
        [{"n": 1, "columns": [{"panes": ["transcript"]}]}],
    ):
        said = await turn(a, mcp("layout_set", windows=windows))
        assert said.startswith("mcp error: bad_params"), said
