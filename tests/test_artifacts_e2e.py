"""Artifacts end to end: the fake claude calls the real tools over /mcp, and
the person's side is called through the registry as the browser would."""

import asyncio
import json

import httpx
import pytest

from aegis import artifacts
from aegis.ops import Caller, OpError

from .conftest import until
from .test_agents import CONFIG, World, inbox, mcp, turn


@pytest.fixture
async def world(tmp_path, fake_claude):
    (tmp_path / ".aegis.yaml").write_text(CONFIG)
    w = await World(tmp_path, fake_claude).start()
    yield w
    await w.stop()


def ok(said: str) -> dict:
    assert said.startswith("mcp ok: "), said
    return json.loads(said.removeprefix("mcp ok: "))


def arts(s):
    return [e for e in s.entries() if e["kind"] == "artifact"]


PAGE = (
    '<script src="/static/js/artifact.js"></script><button id="b">B</button>'
    '<script>aegis.ready(() => { b.onclick = () => aegis.submit({p: "b"}, "Picked B"); });</script>'
)


async def test_create_send_read_update_close(world, tmp_path):
    a = await world.spawn()
    made = ok(
        await turn(
            a, mcp("artifact_create", title="Pick", caption="Pick one", state={"n": 0})
        )
    )
    assert made["id"].startswith("art-") and made["html"] == artifacts.skeleton("Pick")
    path = tmp_path / ".aegis" / "state" / "artifacts" / made["id"] / "index.html"
    assert made["path"] == str(path) and path.read_text() == made["html"]
    assert arts(a) == []  # a draft is not in the transcript

    path.write_text(PAGE)
    sent = ok(await turn(a, mcp("artifact_send", id=made["id"])))
    assert sent["started"] is None and sent["url"].startswith(
        "/files/"
    )  # nobody is watching
    (e,) = arts(a)
    assert (
        e["status"] == "live"
        and e["md"] == "Pick one"
        and e["detail"]["state"] == {"n": 0}
    )
    async with httpx.AsyncClient() as c:
        r = await c.get(world.base + sent["url"])
    assert (
        r.text == PAGE
        and r.headers["content-security-policy"] == "sandbox allow-scripts"
    )

    assert ok(await turn(a, mcp("artifact_read", id=made["id"]))) == {
        "status": "live",
        "state": {"n": 0},
        "events": [],
        "submitted": None,
        "label": None,
    }
    upd = ok(
        await turn(
            a, mcp("artifact_update", id=made["id"], state={"n": 2}, caption="now")
        )
    )
    assert upd["url"] == sent["url"]
    assert arts(a)[0]["detail"]["state"] == {"n": 2} and arts(a)[0]["md"] == "now"

    path.write_text(PAGE.replace("B</button>", "C</button>"))
    re = ok(await turn(a, mcp("artifact_update", id=made["id"], resend=True)))
    assert re["url"] != sent["url"] and arts(a)[0]["detail"]["url"] == re["url"]
    assert arts(a)[0]["detail"]["state"] == {"n": 2}  # carried over

    assert (
        await turn(a, mcp("artifact_close", id=made["id"], label="never mind"))
        == "mcp ok: closed"
    )
    assert (
        arts(a)[0]["status"] == "closed"
        and arts(a)[0]["detail"]["label"] == "never mind"
    )
    said = await turn(a, mcp("artifact_update", id=made["id"], state={}))
    assert said.startswith("mcp error: not_live")


async def test_a_page_that_cannot_answer_is_refused_and_nothing_lands(world, tmp_path):
    a = await world.spawn()
    made = ok(await turn(a, mcp("artifact_create", title="T")))
    path = tmp_path / ".aegis" / "state" / "artifacts" / made["id"] / "index.html"
    path.write_text("<h1>hi</h1>")
    said = await turn(a, mcp("artifact_send", id=made["id"]))
    assert said.startswith("mcp error: no_script") and arts(a) == []
    path.write_text('<script src="/static/js/artifact.js"></script><h1>hi</h1>')
    said = await turn(a, mcp("artifact_send", id=made["id"]))
    assert said.startswith("mcp error: no_answer") and arts(a) == []
    said = await turn(a, mcp("artifact_send", id="art-00000000"))
    assert said.startswith("mcp error: no_artifact")


async def test_a_failed_probe_deletes_the_snapshot_and_lands_nothing(
    world, tmp_path, monkeypatch
):
    monkeypatch.setattr(artifacts, "PROBE_TIMEOUT_S", 2.0)
    a = await world.spawn()
    made = ok(await turn(a, mcp("artifact_create", title="T")))
    (
        tmp_path / ".aegis" / "state" / "artifacts" / made["id"] / "index.html"
    ).write_text(PAGE)
    probes = []
    original = world.app.channels.publish

    def publish(channel, ops):
        probes.extend(op["probe"] for op in ops if "probe" in op)
        original(channel, ops)

    world.app.channels.publish = publish
    # A subscriber, as a browser with the tab open would be.
    world.app.channels.subscribe(a.channel, lambda msg: None)
    await a.send(mcp("artifact_send", id=made["id"]))
    await until(lambda: probes, what="the probe request")
    (p,) = probes
    assert p["artifact_id"] == made["id"] and p["url"].startswith("/files/")
    async with httpx.AsyncClient() as c:
        assert (await c.get(world.base + p["url"])).status_code == 200
    await world.app.registry.call(
        "artifact.probed",
        {
            "log_id": a.log_id,
            "probe_id": p["id"],
            "started": False,
            "message": "ReferenceError: d3 is not defined",
            "stack": "at index.html:14",
        },
    )
    await until(lambda: a.status == "idle", what="the turn")
    said = [e["md"] for e in a.entries() if e["kind"] == "prose"][-1]
    assert (
        said.startswith("mcp error: page_error")
        and "d3 is not defined" in said
        and "index.html:14" in said
    )
    assert arts(a) == []
    async with httpx.AsyncClient() as c:
        assert (
            await c.get(world.base + p["url"])
        ).status_code == 404  # the snapshot is gone
    assert (
        await world.app.registry.call(
            "artifact.probed",
            {"log_id": a.log_id, "probe_id": p["id"], "started": True},
        )
        == "dropped"
    )


async def landed(world, tmp_path, page=PAGE):
    a = await world.spawn()
    made = ok(await turn(a, mcp("artifact_create", title="T")))
    (
        tmp_path / ".aegis" / "state" / "artifacts" / made["id"] / "index.html"
    ).write_text(page)
    ok(await turn(a, mcp("artifact_send", id=made["id"])))
    return a, made["id"]


async def test_a_submit_during_a_resend_probe_wins_and_the_new_page_is_dropped(
    world, tmp_path, monkeypatch
):
    monkeypatch.setattr(artifacts, "PROBE_TIMEOUT_S", 2.0)
    a, aid = await landed(world, tmp_path)
    (tmp_path / ".aegis" / "state" / "artifacts" / aid / "index.html").write_text(
        PAGE.replace("B<", "C<")
    )
    world.app.channels.subscribe(
        a.channel, lambda msg: None
    )  # a browser, so the probe waits
    await a.send(mcp("artifact_update", id=aid, resend=True))
    await until(lambda: a.status == "working", what="the resend")
    await asyncio.sleep(0.2)
    await world.app.registry.call(
        "artifact.submit",
        {
            "log_id": a.log_id,
            "artifact_id": aid,
            "data": {"p": "b"},
            "label": "Picked B",
        },
    )
    await until(
        lambda: a.status == "idle" and len(inbox(a)) == 1,
        timeout=10,
        what="the late submit and the refused resend",
    )
    said = [e["md"] for e in a.entries() if e["kind"] == "prose"][-2]
    assert said.startswith("mcp error: not_live"), said
    (e,) = arts(a)
    assert e["status"] == "submitted"
    assert (
        len([p for p in (tmp_path / ".aegis" / "state" / "files").iterdir()]) == 1
    )  # the new snapshot is gone


async def test_a_submit_wakes_the_agent_and_a_second_is_refused(world, tmp_path):
    a, aid = await landed(world, tmp_path)
    reg = world.app.registry
    await reg.call(
        "artifact.state", {"log_id": a.log_id, "artifact_id": aid, "state": {"n": 1}}
    )
    await reg.call(
        "artifact.submit",
        {
            "log_id": a.log_id,
            "artifact_id": aid,
            "data": {"p": "b"},
            "label": "Picked B",
        },
    )
    await until(lambda: inbox(a) and a.status == "idle", what="the wake")
    (m,) = inbox(a)
    assert m["title"].startswith(f"artifact:{aid} · submit")
    assert 'Picked B\n```json\n{"p": "b"}\n```' in m["md"]
    assert arts(a)[0]["status"] == "submitted" and arts(a)[0]["detail"]["state"] == {
        "n": 1
    }
    with pytest.raises(OpError) as e:
        await reg.call(
            "artifact.submit",
            {"log_id": a.log_id, "artifact_id": aid, "data": {}, "label": "x"},
        )
    assert e.value.code == "not_live"
    with pytest.raises(OpError) as e:
        await reg.call(
            "artifact.state", {"log_id": a.log_id, "artifact_id": aid, "state": {}}
        )
    assert e.value.code == "not_live"


async def test_an_emit_wakes_the_agent_and_leaves_the_page_live(world, tmp_path):
    a, aid = await landed(world, tmp_path)
    await world.app.registry.call(
        "artifact.emit",
        {"log_id": a.log_id, "artifact_id": aid, "name": "hover", "data": [1, 2]},
    )
    await until(lambda: inbox(a) and a.status == "idle", what="the wake")
    assert (
        inbox(a)[0]["title"].startswith(f"artifact:{aid} · hover")
        and "[1, 2]" in inbox(a)[0]["md"]
    )
    assert arts(a)[0]["status"] == "live"
    assert (
        ok(await turn(a, mcp("artifact_read", id=aid)))["events"][0]["name"] == "hover"
    )


async def test_caps_are_enforced_before_anything_reaches_the_agent(
    world, tmp_path, monkeypatch
):
    monkeypatch.setattr(artifacts, "MAX_STATE_BYTES", 32)
    a, aid = await landed(world, tmp_path)
    reg = world.app.registry
    for op, extra in (
        ("artifact.state", {"state": "x" * 40}),
        ("artifact.emit", {"name": "e", "data": "x" * 40}),
    ):
        with pytest.raises(OpError) as e:
            await reg.call(op, {"log_id": a.log_id, "artifact_id": aid, **extra})
        assert e.value.code == "too_large"
    with pytest.raises(OpError) as e:
        await reg.call(
            "artifact.emit",
            {"log_id": a.log_id, "artifact_id": aid, "name": "Bad Name"},
        )
    assert e.value.code == "bad_name"
    with pytest.raises(OpError) as e:
        await reg.call(
            "artifact.submit", {"log_id": a.log_id, "artifact_id": aid, "label": "a\nb"}
        )
    assert e.value.code == "bad_params"
    with pytest.raises(OpError) as e:
        await reg.call(
            "artifact.state",
            {"log_id": a.log_id, "artifact_id": "art-00000000", "state": 1},
        )
    assert e.value.code == "no_artifact"
    with pytest.raises(OpError) as e:
        await reg.call(
            "artifact.state",
            {"log_id": a.log_id, "artifact_id": aid, "state": 1},
            Caller("agent", a.log_id),
        )
    assert e.value.code == "not_for_agents"
    assert inbox(a) == []


async def test_errors_wake_once_per_turn(world, tmp_path):
    a, aid = await landed(world, tmp_path)
    reg = world.app.registry
    assert (
        await reg.call(
            "artifact.error",
            {
                "log_id": a.log_id,
                "artifact_id": aid,
                "message": "boom",
                "stack": "at x:1",
            },
        )
        == "ok"
    )
    assert (
        await reg.call(
            "artifact.error",
            {"log_id": a.log_id, "artifact_id": aid, "message": "boom2", "stack": ""},
        )
        == "dropped"
    )
    await until(lambda: inbox(a) and a.status == "idle", what="the wake")
    (m,) = inbox(a)
    assert (
        m["title"].startswith(f"artifact:{aid} · error")
        and "boom\n```\nat x:1\n```" in m["md"]
    )
    assert (
        await reg.call(
            "artifact.error",
            {"log_id": a.log_id, "artifact_id": aid, "message": "boom3", "stack": ""},
        )
        == "ok"
    )


async def test_a_long_error_wakes_the_agent_truncated(world, tmp_path):
    a, aid = await landed(world, tmp_path)
    assert (
        await world.app.registry.call(
            "artifact.error",
            {"log_id": a.log_id, "artifact_id": aid, "message": "x" * 10_000},
        )
        == "ok"
    )
    await until(lambda: inbox(a) and a.status == "idle", what="the wake")
    (m,) = inbox(a)
    assert m["title"].startswith(f"artifact:{aid} · error")
    assert len(m["md"]) < 5000


async def test_a_restart_forgets_drafts_but_a_live_page_can_still_be_resent(
    world, tmp_path
):
    a, aid = await landed(world, tmp_path)
    draft = ok(await turn(a, mcp("artifact_create", title="D")))
    await world.restart()
    a = world.session(a.log_id)
    said = await turn(a, mcp("artifact_send", id=draft["id"]))
    assert said.startswith("mcp error: no_artifact")  # the board forgot the draft
    (tmp_path / ".aegis" / "state" / "artifacts" / aid / "index.html").write_text(
        PAGE.replace("B<", "Z<")
    )
    re = ok(
        await turn(a, mcp("artifact_update", id=aid, resend=True))
    )  # its working copy survived
    assert arts(a)[0]["detail"]["url"] == re["url"]
