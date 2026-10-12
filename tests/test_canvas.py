"""The canvas end to end: the fake claude calls the real tools over /mcp, and
the person's side is called through the registry as the browser would."""

import pytest

from aegis import layout

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
