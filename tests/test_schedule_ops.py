"""The schedule operations: who may change what, the tools agents get, the
check tried without saving, the runs table and the channel."""

import json

import httpx
import pytest

from aegis.ops import Caller, OpError, Principal

from .conftest import until
from .test_agents import CONFIG, World, mcp, turn
from .test_schedules_e2e import DONE, create, ended, frozen, runs, tick

WRITER = (
    "  writer: {harness: claude-code, model: opus, effort: high, permission: write}\n"
)
NEW = {"clock": {"every": "5m"}, "fire": {"agent": "opus"}, "prompt": "triage"}
TOOLS = {
    "schedule_list",
    "schedule_runs",
    "schedule_create",
    "schedule_update",
    "schedule_delete",
    "schedule_pause",
    "schedule_resume",
    "schedule_fire",
    "schedule_test",
}


@pytest.fixture
async def world(tmp_path, fake_claude):
    (tmp_path / ".aegis.yaml").write_text(CONFIG.replace("queues:", WRITER + "queues:"))
    w = await World(tmp_path, fake_claude).start()
    yield w
    await w.stop()


def ok(said: str):
    assert said.startswith("mcp ok: "), said
    return json.loads(said.removeprefix("mcp ok: "))


async def test_agents_get_every_schedule_tool(world):
    async with httpx.AsyncClient() as c:
        r = await c.post(
            world.base + "/mcp",
            json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
            headers={"accept": "application/json, text/event-stream"},
        )
    names = {t["name"] for t in r.json()["result"]["tools"]}
    assert TOOLS <= names


async def test_an_agent_creates_lists_and_deletes_its_own_schedule(world):
    a = await world.spawn()
    ok(await turn(a, mcp("schedule_create", name="pr-triage", **NEW)))
    s = world.app.schedules.items["pr-triage"]
    assert s.owner == {
        "kind": "agent",
        "log_id": a.log_id,
        "principal": world.app.user,
        "ceiling": "full",
    }
    assert s.d.fire.cwd == str(a.spec.cwd)
    listed = ok(await turn(a, mcp("schedule_list")))
    assert [x["name"] for x in listed] == ["pr-triage"]
    ok(await turn(a, mcp("schedule_delete", name="pr-triage")))
    assert "pr-triage" not in world.app.schedules.items


async def test_an_agent_cannot_change_another_agents_schedule(world):
    a, b = await world.spawn(), await world.spawn()
    ok(await turn(a, mcp("schedule_create", name="a-owns", **NEW)))
    said = await turn(b, mcp("schedule_pause", name="a-owns"))
    assert "agents can change only the schedules they created" in said
    assert world.app.schedules.items["a-owns"].paused["by"] == "approval"


async def test_a_member_cannot_change_another_members_schedule(world):
    app = world.app
    for name in ("ben", "cara"):
        app.principals.add(name, "member")
    ben = Caller("user", principal=Principal("ben", "member"))
    cara = Caller("user", principal=Principal("cara", "member"))
    await app.registry.call("schedule.create", {"name": "ben-owns", **NEW}, ben)
    with pytest.raises(OpError) as e:
        await app.registry.call("schedule.pause", {"name": "ben-owns"}, cara)
    assert e.value.code == "not_owner"
    await app.registry.call("schedule.pause", {"name": "ben-owns"}, Caller("user"))
    assert app.schedules.items["ben-owns"].paused["by"] == "user"


async def test_an_agent_with_write_cannot_make_a_schedule_that_starts_full(world):
    r = await world.app.registry.call("session.spawn", {"agent": "writer"})
    w = world.session(r["log_id"])
    said = await turn(w, mcp("schedule_create", name="too-strong", **NEW))
    assert "cannot start opus, which runs with full" in said
    assert "too-strong" not in world.app.schedules.items


async def test_a_run_with_spawn_off_cannot_create_a_schedule(world):
    t = frozen(world)
    create(world, "no-nest", mcp("schedule_create", name="nested-one", **NEW))
    await tick(world, t)
    await ended(world, "no-nest")
    (fired,) = runs(world, "no-nest", "fired")
    s = world.session(fired["log_id"])
    said = [e["md"] for e in s.entries() if e["kind"] == "prose"][-1]
    assert "may not start other work (spawn: false)" in said
    assert "nested-one" not in world.app.schedules.items


async def test_schedule_test_gives_the_next_ticks_and_what_the_check_would_do(world):
    call = world.app.registry.call
    r = await call(
        "schedule.test", {"clock": {"every": "5m"}, "check": {"bash": "echo hi"}}
    )
    assert len(r["next"]) == 3
    assert (r["check"]["rc"], r["check"]["stdout"], r["check"]["would_fire"]) == (
        0,
        "hi\n",
        True,
    )
    r = await call(
        "schedule.test", {"clock": {"every": "5m"}, "check": {"bash": "exit 1"}}
    )
    assert (r["check"]["would_fire"], r["check"]["why"]) == (False, "exit 1: not yet")
    r = await call("schedule.test", {"clock": {"every": "5x"}})
    assert "write a number and m, h or d" in r["error"]


async def test_schedule_runs_pages_newest_first_and_hides_quiet_ticks(world, tmp_path):
    t = frozen(world)
    f = tmp_path / "x"
    f.write_text("a")
    create(world, "pages-one", DONE, check={"bash": f"cat {f}"})
    await tick(world, t)
    await ended(world, "pages-one")
    await tick(world, t)
    await tick(world, t)
    call = world.app.registry.call
    r = await call("schedule.runs", {"name": "pages-one"})
    assert [x["kind"] for x in r["rows"]] == ["fired", "created"] and r["quiet"] == 1
    fired = r["rows"][0]
    assert (fired["attention"], fired["closed"], fired["open"]) == ("done", True, False)
    r = await call("schedule.runs", {"name": "pages-one", "quiet": True})
    assert [x["kind"] for x in r["rows"]] == ["quiet", "fired", "created"]
    assert r["rows"][0]["n"] == 2


async def test_the_schedules_channel_carries_every_schedule(world):
    create(world, "on-channel", DONE)
    snap = world.app._resolve("schedules")()
    assert [s["name"] for s in snap] == ["on-channel"] and snap[0]["next"]


async def test_a_schedule_deleted_mid_run_leaves_its_run_alone(world):
    t = frozen(world)
    create(world, "gone-soon", "/busy 1")
    await tick(world, t)
    (fired,) = runs(world, "gone-soon", "fired")
    await world.app.registry.call("schedule.delete", {"name": "gone-soon"})
    s = world.session(fired["log_id"])
    await until(lambda: s.status == "idle", 10, "the run's turn")
    assert fired["log_id"] in world.app.sessions.sessions


async def test_an_agents_schedule_is_born_paused_until_a_person_unpauses_it(world):
    a = await world.spawn()
    ok(await turn(a, mcp("schedule_create", name="nightly-tests", **NEW)))
    s = world.app.schedules.items["nightly-tests"]
    assert s.paused["by"] == "approval"
    said = await turn(a, mcp("schedule_resume", name="nightly-tests"))
    assert "waits for a person's approval" in said
    said = await turn(a, mcp("schedule_fire", name="nightly-tests"))
    assert "waits for a person's approval" in said
    assert world.app.schedules.items["nightly-tests"].paused["by"] == "approval"
    await world.app.registry.call("schedule.resume", {"name": "nightly-tests"})
    assert world.app.schedules.items["nightly-tests"].paused is None


async def test_an_agents_edit_pauses_an_approved_schedule_again(world):
    a = await world.spawn()
    ok(await turn(a, mcp("schedule_create", name="nightly-tests", **NEW)))
    await world.app.registry.call("schedule.resume", {"name": "nightly-tests"})
    ok(
        await turn(
            a,
            mcp(
                "schedule_update", name="nightly-tests", **(NEW | {"prompt": "rm -rf"})
            ),
        )
    )
    assert world.app.schedules.items["nightly-tests"].paused["by"] == "approval"


async def test_a_persons_schedule_is_born_running(world):
    await world.app.registry.call("schedule.create", {"name": "by-hand", **NEW})
    assert world.app.schedules.items["by-hand"].paused is None


async def test_creating_a_schedule_puts_an_approval_card_under_the_call(world):
    a = await world.spawn()
    ok(await turn(a, mcp("schedule_create", name="nightly-tests", **NEW)))
    cards = [e for e in a.entries() if e["kind"] == "approval"]
    assert [c["detail"]["name"] for c in cards] == ["nightly-tests"]
