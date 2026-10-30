"""An agent's schedule tools across a trusted link (#875): ``name@server``. The
schedule lives and ticks on the far server, and the rule that a schedule an
agent makes waits for a person holds there, whichever way the call came."""

import json

import pytest

from aegis.ops import Caller, Principal

from .conftest import until
from .test_links import Node, mcp, turn
from .test_schedules_e2e import DONE, create, ended, frozen, runs, tick
from .test_trusted_links import trusted  # noqa: F401
from .test_workflow_store import BY, NAP

NEW = {"clock": {"every": "5m"}, "fire": {"agent": "opus"}, "prompt": "triage"}


def ok(said: str):
    assert said.startswith("mcp ok: "), said
    return json.loads(said.removeprefix("mcp ok: "))


def cards(s) -> list[dict]:
    return [e["detail"] for e in s.entries() if e["kind"] == "approval"]


async def made(here, name: str = "far-triage@beta", **kw):
    return ok(await turn(here, mcp("schedule_create", name=name, **NEW, **kw)))


async def approve(alpha, name: str = "far-triage", **kw) -> None:
    """A person's Unpause: the browser's call through the link, no agent named."""
    await alpha.app.links.get("beta").call("schedule.resume", {"name": name, **kw})


# -- where it lives -----------------------------------------------------------------
async def test_a_schedule_made_across_lives_on_the_far_server(trusted):  # noqa: F811
    alpha, beta = trusted
    here = await alpha.spawn()
    w = await made(here)
    assert w["name"] == "far-triage@beta" and w["server"] == "beta"
    assert "far-triage" not in alpha.app.schedules.items
    s = beta.app.schedules.items["far-triage"]
    assert s.owner["far"]["server"] == "alpha"
    assert s.owner["far"]["log_id"] == here.log_id
    assert s.owner["ceiling"] == "full"
    assert s.paused["by"] == "approval"
    assert w["owner_handle"] == f"{here.handle}@alpha"
    # A fire's directory is the far server's own, not the agent's here.
    assert s.d.fire.cwd == str(beta.app.roots.harness_cwd)


async def test_it_ticks_on_the_far_server_and_its_runs_start_there(trusted):  # noqa: F811
    alpha, beta = trusted
    here = await alpha.spawn()
    t = frozen(beta)
    ok(
        await turn(
            here, mcp("schedule_create", name="far-tick@beta", **NEW | {"prompt": DONE})
        )
    )
    await approve(alpha, "far-tick")
    await tick(beta, t)
    (fired,) = runs(beta, "far-tick", "fired")
    assert fired["log_id"] in beta.app.sessions.sessions
    assert fired["log_id"] not in alpha.app.sessions.sessions
    await ended(beta, "far-tick")
    assert ok(await turn(here, mcp("schedule_runs", name="far-tick@beta")))["total"]


async def test_the_card_is_under_the_call_here_and_names_the_far_server(trusted):  # noqa: F811
    alpha, beta = trusted
    here = await alpha.spawn()
    await made(here)
    assert cards(here) == [
        {
            "object": "schedule",
            "name": "far-triage",
            "action": "created",
            "server": "beta",
        }
    ]
    await approve(alpha)
    ok(await turn(here, mcp("schedule_update", name="far-triage@beta", **NEW)))
    assert cards(here)[-1]["action"] == "updated"
    assert beta.app.schedules.items["far-triage"].paused["by"] == "approval"


async def test_a_card_here_keeps_no_server_field(trusted):  # noqa: F811
    alpha, _ = trusted
    here = await alpha.spawn()
    ok(await turn(here, mcp("schedule_create", name="home-triage", **NEW)))
    assert cards(here) == [
        {"object": "schedule", "name": "home-triage", "action": "created"}
    ]


async def test_this_servers_own_name_addresses_this_server(trusted):  # noqa: F811
    alpha, _ = trusted
    here = await alpha.spawn()
    w = ok(await turn(here, mcp("schedule_create", name="home-triage@alpha", **NEW)))
    assert w["name"] == "home-triage" and "home-triage" in alpha.app.schedules.items
    assert ok(await turn(here, mcp("schedule_pause", name="home-triage@alpha")))
    assert ok(await turn(here, mcp("schedule_delete", name="home-triage@alpha"))) == {
        "deleted": "home-triage"
    }


async def test_list_and_runs_read_the_far_servers_schedules(trusted):  # noqa: F811
    alpha, beta = trusted
    here = await alpha.spawn()
    create(alpha, "near-one", "x")
    create(beta, "beta-own", "x")
    await made(here)
    listed = ok(await turn(here, mcp("schedule_list")))
    assert {(w["name"], w.get("server")) for w in listed} == {
        ("near-one", None),
        ("beta-own@beta", "beta"),
        ("far-triage@beta", "beta"),
    }
    rows = ok(await turn(here, mcp("schedule_runs", name="beta-own@beta")))["rows"]
    assert rows


async def test_a_person_and_a_link_list_this_server_alone(trusted):  # noqa: F811
    alpha, beta = trusted
    create(beta, "beta-own", "x")
    create(alpha, "near-one", "x")
    assert [w["name"] for w in await alpha.app.registry.call("schedule.list", {})] == [
        "near-one"
    ]
    far = await alpha.app.links.get("beta").call("schedule.list")
    assert [w["name"] for w in far] == ["beta-own"]


async def test_schedule_test_runs_on_the_far_server(trusted):  # noqa: F811
    alpha, beta = trusted
    here = await alpha.spawn()
    said = ok(
        await turn(
            here,
            mcp(
                "schedule_test",
                name="not-made-yet@beta",
                clock={"every": "5m"},
                check={"bash": "pwd"},
            ),
        )
    )
    assert len(said["next"]) == 3
    assert said["check"]["stdout"].strip() == str(beta.app.roots.harness_cwd.resolve())


async def test_an_owner_pauses_resumes_and_deletes_after_approval(trusted):  # noqa: F811
    alpha, beta = trusted
    here = await alpha.spawn()
    await made(here)
    await approve(alpha)
    far = "far-triage@beta"
    assert (
        ok(await turn(here, mcp("schedule_pause", name=far)))["paused"]["by"] == "user"
    )
    assert ok(await turn(here, mcp("schedule_resume", name=far)))["paused"] is None
    # A rename is the same agent: ownership goes by its log id, not its handle.
    await alpha.app.registry.call(
        "session.rename", {"log_id": here.log_id, "handle": "other-name"}
    )
    assert ok(await turn(here, mcp("schedule_pause", name=far)))["name"] == far
    assert ok(await turn(here, mcp("schedule_delete", name=far))) == {"deleted": far}
    assert "far-triage" not in beta.app.schedules.items


async def test_a_far_fire_starts_its_run_on_the_far_server(trusted):  # noqa: F811
    alpha, beta = trusted
    here = await alpha.spawn()
    ok(
        await turn(
            here, mcp("schedule_create", name="far-fire@beta", **NEW | {"prompt": DONE})
        )
    )
    await approve(alpha, "far-fire")
    said = ok(await turn(here, mcp("schedule_fire", name="far-fire@beta")))
    assert said["outcome"] == "fired" and said["name"] == "far-fire@beta"
    (fired,) = runs(beta, "far-fire", "fired")
    assert fired["log_id"] in beta.app.sessions.sessions


# -- the approval rule -----------------------------------------------------------------
async def test_an_agent_cannot_unpause_or_fire_across_a_link(trusted):  # noqa: F811
    """The security property: an agent's call across a link never stands in for
    a person's Unpause, by the tools, by naming itself, or by the raw link."""
    alpha, beta = trusted
    here = await alpha.spawn()
    await made(here)
    far = "far-triage@beta"
    for said in (
        await turn(here, mcp("schedule_resume", name=far)),
        await turn(here, mcp("schedule_fire", name=far)),
        # An agent naming someone else in the hidden field changes nothing:
        # this server writes it from the session that called.
        await turn(
            here,
            mcp(
                "schedule_resume",
                name=far,
                from_agent={
                    "handle": "a-person",
                    "server": "alpha",
                    "log_id": "x",
                    "permission": "full",
                },
            ),
        ),
    ):
        assert "needs_person" in said and "mcp ok" not in said, said
    s = beta.app.schedules.items["far-triage"]
    assert (
        s.paused["by"] == "approval"
        and beta.app.schedules.state["far-triage"].runs == 0
    )

    # What alpha sends, seen by beta's own rule, is refused there and not here.
    me = {
        "handle": here.handle,
        "server": "alpha",
        "log_id": here.log_id,
        "permission": "full",
    }
    r = await alpha.app.links.get("beta").call_raw(
        "schedule.resume", {"name": "far-triage", "from_agent": me}
    )
    assert r["error"]["code"] == "needs_person", r
    assert s.paused["by"] == "approval"

    # A person's approval is the one thing that lifts it, and an edit brings it back.
    await approve(alpha)
    assert s.name in beta.app.schedules.items
    assert beta.app.schedules.items["far-triage"].paused is None
    ok(await turn(here, mcp("schedule_update", name=far, **NEW)))
    assert beta.app.schedules.items["far-triage"].paused["by"] == "approval"
    said = await turn(here, mcp("schedule_resume", name=far))
    assert "needs_person" in said
    assert beta.app.schedules.items["far-triage"].paused["by"] == "approval"


async def test_the_way_back_keeps_the_approval_rule_too(trusted):  # noqa: F811
    alpha, beta = trusted
    there = await beta.spawn()
    w = ok(await turn(there, mcp("schedule_create", name="far-triage@alpha", **NEW)))
    # This server's browser cannot follow alpha's schedules: the reply says where.
    assert "Schedules view" in w["note"]
    assert alpha.app.schedules.items["far-triage"].paused["by"] == "approval"
    for tool in ("schedule_resume", "schedule_fire"):
        said = await turn(there, mcp(tool, name="far-triage@alpha"))
        assert "needs_person" in said, said
    assert alpha.app.schedules.items["far-triage"].paused["by"] == "approval"


async def test_only_a_trusted_link_names_a_far_agent(trusted):  # noqa: F811
    """On its own server an agent cannot pose as another server's agent, to
    take over a schedule that agent made or to look like the link."""
    alpha, beta = trusted
    here, local = await alpha.spawn(), await beta.spawn()
    await made(here)
    await approve(alpha)
    forged = {
        "handle": here.handle,
        "server": "alpha",
        "log_id": here.log_id,
        "permission": "full",
    }
    for call in (
        mcp("schedule_pause", name="far-triage", from_agent=forged),
        mcp("schedule_create", name="posing-one", **NEW, from_agent=forged),
    ):
        said = await turn(local, call)
        assert "not_a_link" in said, (call, said)
    assert "posing-one" not in beta.app.schedules.items
    # The link cannot speak for another server's agent either.
    r = await alpha.app.links.get("beta").call_raw(
        "schedule.pause",
        {"name": "far-triage", "from_agent": forged | {"server": "gamma"}},
    )
    assert r["error"]["code"] == "not_a_link", r


async def test_an_agent_changes_only_the_far_schedules_it_made(trusted):  # noqa: F811
    alpha, beta = trusted
    here, other, local = await alpha.spawn(), await alpha.spawn(), await beta.spawn()
    await made(here)
    create(beta, "beta-own", "x")
    far = "far-triage@beta"
    for said in (
        await turn(other, mcp("schedule_pause", name=far)),
        await turn(here, mcp("schedule_pause", name="beta-own@beta")),
        await turn(here, mcp("schedule_delete", name="beta-own@beta")),
        await turn(local, mcp("schedule_delete", name="far-triage")),
    ):
        assert "not_yours" in said, said
    assert {"far-triage", "beta-own"} <= set(beta.app.schedules.items)


async def test_a_far_schedule_that_fires_a_workflow_stays_refused(trusted):  # noqa: F811
    alpha, beta = trusted
    beta.app.saved.save(NAP, beta.app.workflows._agents(), BY)
    here = await alpha.spawn()
    w = ok(
        await turn(
            here,
            mcp(
                "schedule_create",
                name="far-nap@beta",
                **NEW | {"prompt": "/workflow nap a second"},
            ),
        )
    )
    await approve(alpha, "far-nap", workflow=w["workflow"]["sha"])
    assert beta.app.schedules.items["far-nap"].paused is None
    said = await turn(here, mcp("schedule_fire", name="far-nap@beta"))
    assert "needs_person" in said and "fires a workflow" in said, said
    assert beta.app.schedules.state["far-nap"].runs == 0


async def test_a_far_schedule_cannot_outrank_the_agent_that_made_it(trusted):  # noqa: F811
    alpha, beta = trusted
    here = await alpha.spawn(permission="write")
    said = await turn(here, mcp("schedule_create", name="too-strong@beta", **NEW))
    assert "not_allowed" in said, said
    assert "too-strong" not in beta.app.schedules.items


async def test_a_run_with_spawn_off_cannot_make_one_across(trusted):  # noqa: F811
    alpha, beta = trusted
    t = frozen(alpha)
    create(alpha, "no-nest", mcp("schedule_create", name="nested-one@beta", **NEW))
    await tick(alpha, t)
    await ended(alpha, "no-nest")
    (fired,) = runs(alpha, "no-nest", "fired")
    s = alpha.app.sessions.sessions[fired["log_id"]]
    await until(lambda: s.status == "idle", 10, "the run's turn")
    said = [e["md"] for e in s.entries() if e["kind"] == "prose"][-1]
    assert "spawn: false" in said, said
    assert "nested-one" not in beta.app.schedules.items


# -- who may not -----------------------------------------------------------------------
TOOLS = (
    mcp("schedule_create", name="far-triage@beta", **NEW),
    mcp("schedule_update", name="far-triage@beta", **NEW),
    mcp("schedule_runs", name="far-triage@beta"),
    mcp("schedule_pause", name="far-triage@beta"),
    mcp("schedule_resume", name="far-triage@beta"),
    mcp("schedule_delete", name="far-triage@beta"),
    mcp("schedule_fire", name="far-triage@beta"),
    mcp("schedule_test", name="far-triage@beta", clock={"every": "5m"}),
)


@pytest.fixture
async def plain(tmp_path, fake_claude):
    beta = await Node(tmp_path / "beta", "beta", fake_claude).start()
    alpha = await Node(tmp_path / "alpha", "alpha", fake_claude).start()
    alpha.app.links.add("beta", beta.base, beta.token)
    await until(lambda: alpha.app.links.get("beta").state == "linked")
    yield alpha, beta
    await alpha.stop()
    await beta.stop()


async def test_a_plain_link_refuses_every_schedule_tool(plain):
    alpha, beta = plain
    here = await alpha.spawn()
    create(beta, "beta-own", "x")
    for call in TOOLS:
        said = await turn(here, call)
        assert "not_across_links" in said, (call, said)
    assert list(beta.app.schedules.items) == ["beta-own"]
    assert not cards(here)
    assert ok(await turn(here, mcp("schedule_list"))) == []


async def test_a_members_agent_is_refused_and_lists_no_far_schedule(trusted):  # noqa: F811
    alpha, beta = trusted
    create(beta, "beta-own", "x")
    alpha.app.principals.add("ben", "member")
    alpha.app.principals.set_default_policy(
        {"claude": {"mode": "open"}, "opencode-go": {"mode": "open"}}
    )
    r = await alpha.app.registry.call(
        "session.spawn",
        {"agent": "opus"},
        Caller("user", principal=Principal("ben", "member")),
    )
    here = alpha.app.sessions.sessions[r["log_id"]]
    for call in TOOLS:
        said = await turn(here, call)
        assert "not_for_members" in said, (call, said)
    assert ok(await turn(here, mcp("schedule_list"))) == []
    assert list(beta.app.schedules.items) == ["beta-own"]


async def test_nothing_relays_to_a_third_server(trusted, tmp_path, fake_claude):  # noqa: F811
    alpha, beta = trusted
    gamma = await Node(tmp_path / "gamma", "gamma", fake_claude).start()
    try:
        alpha.app.links.add("gamma", gamma.base, gamma.token, trusted=True)
        await until(
            lambda: (
                (link := alpha.app.links.get("gamma")).state == "linked"
                and link.granted
            )
        )
        create(gamma, "gamma-own", "x")
        # Beta's call down the way back asks alpha to go on to gamma.
        back = beta.app.links.get("alpha")
        for op, params in (
            ("schedule.pause", {"name": "gamma-own@gamma"}),
            ("schedule.create", {"name": "made-here@gamma", **NEW}),
            ("schedule.runs", {"name": "gamma-own@gamma"}),
        ):
            r = await back.call_raw(op, params)
            assert r["error"]["code"] == "not_relayed", (op, r)
        assert gamma.app.schedules.items["gamma-own"].paused is None
        assert list(gamma.app.schedules.items) == ["gamma-own"]
        # Listing over the link stops at the server asked.
        assert await back.call("schedule.list") == []
    finally:
        await gamma.stop()
