"""Toasts (#329): App.toast publishes on the toasts channel, and the toast tool is
how an agent raises one. The channel's snapshot is always empty, so a browser
that subscribes later never replays an old toast."""

import json

from .test_agents import mcp, turn, world  # noqa: F401  (world is a fixture)


def listen(app) -> list[dict]:
    frames: list[dict] = []
    app.channels.subscribe("toasts", frames.append)
    return frames


def toasts(frames: list[dict]) -> list[dict]:
    return [op["toast"] for f in frames if f["t"] == "patch" for op in f["ops"]]


async def test_app_toast_publishes_one_op_and_the_snapshot_stays_empty(world):  # noqa: F811
    frames = listen(world.app)
    assert frames == [{"t": "snapshot", "channel": "toasts", "seq": 0, "data": []}]
    assert world.app.toast("Copied the path") == 1
    (t,) = toasts(frames)
    assert (t["text"], t["kind"], t.get("from")) == ("Copied the path", "info", None)
    later = listen(world.app)
    assert later[0]["data"] == [] and toasts(later) == []


async def test_an_agent_toast_names_its_session_and_says_how_many_saw_it(world):  # noqa: F811
    s = await world.spawn()
    said = await turn(s, mcp("toast", text="Spec is ready", kind="error"))
    assert said.startswith("mcp ok") and json.loads(said.split(": ", 1)[1]) == {
        "shown": 0
    }
    frames = listen(world.app)
    said = await turn(s, mcp("toast", text="Spec is ready"))
    assert json.loads(said.split(": ", 1)[1]) == {"shown": 1}
    (t,) = toasts(frames)
    assert t["text"] == "Spec is ready" and t["kind"] == "info"
    assert t["from"] == {"handle": s.handle, "id": s.log_id}


async def test_empty_multiline_and_long_text_are_refused(world):  # noqa: F811
    s = await world.spawn()
    frames = listen(world.app)
    for text in ("", "two\nlines", "x" * 201):
        said = await turn(s, mcp("toast", text=text))
        assert said.startswith("mcp error"), said
    assert toasts(frames) == []


async def test_the_seventh_toast_in_a_minute_is_refused(world):  # noqa: F811
    s = await world.spawn()
    for i in range(6):
        said = await turn(s, mcp("toast", text=f"step {i}"))
        assert said.startswith("mcp ok"), said
    said = await turn(s, mcp("toast", text="step 6"))
    assert said.startswith("mcp error: rate_limited"), said


async def test_a_read_session_can_raise_a_toast(world):  # noqa: F811
    r = await world.app.registry.call("session.spawn", {"agent": "reviewer"})
    s = world.session(r["log_id"])
    said = await turn(s, mcp("toast", text="Found two problems"))
    assert said.startswith("mcp ok"), said
