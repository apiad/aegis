"""Schedules end to end: the real app, runs on the fake claude, and a clock the
test moves forward instead of waiting for (schedules.py)."""

import asyncio
import time

import pytest

from aegis.ops import Caller
from aegis.schedule_log import Definition

from .conftest import until
from .test_agents import CONFIG, World, inbox, mcp, turn

DONE = mcp("turn_end", attention="done", line="did it")
ASKS = mcp("turn_end", attention="needs_you", line="which one?")


@pytest.fixture
async def world(tmp_path, fake_claude):
    (tmp_path / ".aegis.yaml").write_text(CONFIG)
    w = await World(tmp_path, fake_claude).start()
    yield w
    await w.stop()


def frozen(world) -> list[float]:
    """Stop the schedules' wall clock at now; the test moves t[0]."""
    t = [float(int(world.app.schedules.now()))]
    world.app.schedules.now = lambda: t[0]
    return t


def create(world, name: str, prompt: str, **kw) -> None:
    raw = {"clock": {"every": "5m"}, "fire": {"agent": "opus"}, "prompt": prompt} | kw
    world.app.schedules.create(
        name,
        Definition.model_validate(raw),
        {"kind": "user", "principal": world.app.user},
        world.root,
    )


async def tick(world, t: list[float], seconds: int = 300) -> None:
    t[0] += seconds
    await world.app.schedules.step()


def runs(world, name: str, ev: str) -> list[dict]:
    return [r for r in world.app.schedules.log.of(name) if r["ev"] == ev]


def outs(world, name: str) -> list[str]:
    return [r["out"] for r in runs(world, name, "tick")]


async def ended(world, name: str, n: int = 1) -> dict:
    await until(
        lambda: len(runs(world, name, "finished")) >= n, 10, f"{name}'s run {n} ending"
    )
    return runs(world, name, "finished")[n - 1]


# -- the clock and a new session per fire ------------------------------------------
async def test_an_every_schedule_fires_a_new_session_and_a_done_run_is_archived(world):
    t = frozen(world)
    create(world, "pr-triage", DONE)
    await tick(world, t, 299)
    assert outs(world, "pr-triage") == []
    await tick(world, t, 1)
    (fired,) = runs(world, "pr-triage", "fired")
    end = await ended(world, "pr-triage")
    assert end["attention"] == "done" and end["closed"] is True
    meta = world.app.sessions.archived[fired["log_id"]]
    assert meta["title"] == "pr-triage · run 1"
    assert meta["schedule"] == {"name": "pr-triage", "run": 1, "spawn": False}
    rows = world.app.sessions.archived_entries(fired["log_id"])
    assert any(
        e["kind"] == "inbox"
        and e["title"].startswith("schedule:pr-triage · run#1 · tick ")
        for e in rows
    )


async def test_a_run_that_asks_the_person_stays_open(world):
    t = frozen(world)
    create(world, "ask-me", ASKS)
    await tick(world, t)
    end = await ended(world, "ask-me")
    assert end["attention"] == "needs_you" and end["closed"] is False
    assert runs(world, "ask-me", "fired")[0]["log_id"] in world.app.sessions.sessions


async def test_wait_holds_one_tick_during_a_live_run_and_fires_it_after(world):
    t = frozen(world)
    create(world, "slow-one", "/busy 1")
    for _ in range(3):
        await tick(world, t)
    assert outs(world, "slow-one") == ["fired", "held", "held"]
    await until(lambda: len(runs(world, "slow-one", "fired")) == 2, 10, "the held tick")
    await ended(world, "slow-one", 2)
    assert len(runs(world, "slow-one", "fired")) == 2  # two held ticks, one fire


async def test_skip_drops_a_tick_during_a_live_run(world):
    t = frozen(world)
    create(world, "skip-one", "/busy 1", overlap="skip")
    await tick(world, t)
    await tick(world, t)
    assert outs(world, "skip-one") == ["fired", "skipped"]
    await ended(world, "skip-one")
    await asyncio.sleep(0.3)
    assert len(runs(world, "skip-one", "fired")) == 1


async def test_parallel_starts_a_second_session_while_the_first_works(world):
    t = frozen(world)
    create(world, "two-at-once", "/busy 1", overlap="parallel")
    await tick(world, t)
    await tick(world, t)
    a, b = runs(world, "two-at-once", "fired")
    assert a["log_id"] != b["log_id"]
    assert len(world.app.schedules.state["two-at-once"].live) == 2


async def test_a_run_whose_tab_a_person_closes_ends_and_lets_a_held_tick_go(world):
    t = frozen(world)
    create(world, "closed-early", "/busy 5")
    await tick(world, t)
    await tick(world, t)
    (fired,) = runs(world, "closed-early", "fired")
    await world.app.sessions.close(fired["log_id"])
    await ended(world, "closed-early")
    await until(
        lambda: len(runs(world, "closed-early", "fired")) == 2, 10, "the held tick"
    )


# -- the check -------------------------------------------------------------------------
async def test_a_check_fires_on_new_stdout_and_passes_it_in_the_prompt(world, tmp_path):
    t = frozen(world)
    f = tmp_path / "prs.txt"
    f.write_text("#241 Schedules view\n")
    create(world, "pr-watch", DONE, check={"bash": f"test -s {f} && cat {f}"})
    await tick(world, t)
    (fired,) = runs(world, "pr-watch", "fired")
    assert fired["stdout"] == "#241 Schedules view\n"
    await ended(world, "pr-watch")
    (row,) = [
        e
        for e in world.app.sessions.archived_entries(fired["log_id"])
        if e["kind"] == "inbox"
    ]
    assert "```text\n#241 Schedules view\n```" in row["md"]
    await tick(world, t)  # the same stdout
    f.write_text("")
    await tick(world, t)  # exit 1: not yet
    f.write_text("#241 Schedules view\n#242 Docs\n")
    await tick(world, t)
    ticks = runs(world, "pr-watch", "tick")
    assert [(r["out"], r.get("rc")) for r in ticks[1:3]] == [("quiet", 0), ("quiet", 1)]
    assert ticks[3]["out"] == "fired"


async def test_three_errors_in_a_row_pause_the_schedule_and_raise_a_toast(world):
    t = frozen(world)
    toasts = []
    world.app.toast = lambda text, kind="info", source=None: toasts.append((text, kind))
    create(world, "main-red", DONE, check={"bash": "echo 'gh: HTTP 502' >&2; exit 4"})
    for _ in range(4):
        await tick(world, t)
    assert outs(world, "main-red") == ["error", "error", "error"]
    assert world.app.schedules.items["main-red"].paused["by"] == "errors"
    assert toasts == [
        (
            "Schedule main-red is paused: 3 errors in a row, the last: gh: HTTP 502",
            "error",
        )
    ]


async def test_a_missing_command_is_an_error_not_a_quiet_tick(world):
    t = frozen(world)
    create(world, "typo-check", DONE, check={"bash": "no-such-command-here"})
    await tick(world, t)
    (r,) = runs(world, "typo-check", "tick")
    assert (r["out"], r["rc"]) == ("error", 127) and "not found" in r["err"]


# -- an existing session ------------------------------------------------------------------
async def test_an_idle_session_gets_the_prompt_in_its_inbox_at_once(world):
    t = frozen(world)
    a = await world.spawn()
    await turn(a, "hello")
    create(
        world,
        "nudge-me",
        "keep going",
        fire={"target": "existing", "session": a.handle},
    )
    assert world.app.schedules.items["nudge-me"].d.fire.session == a.log_id
    await tick(world, t)
    await until(lambda: inbox(a), 10, "the inbox row")
    assert inbox(a)[0]["title"].startswith("schedule:nudge-me · run#1 · tick ")


async def test_a_working_session_gets_the_prompt_when_its_turn_ends(world):
    t = frozen(world)
    a = await world.spawn()
    await a.send("/busy 1")
    await until(lambda: a.status == "working", 5, "the turn")
    create(
        world,
        "wait-turn",
        "keep going",
        fire={"target": "existing", "session": a.log_id},
    )
    await tick(world, t)
    assert a.held and not inbox(a)
    await until(lambda: inbox(a), 10, "the held message")


async def test_a_renamed_target_still_gets_its_fires(world):
    t = frozen(world)
    a = await world.spawn()
    await turn(a, "hello")
    create(
        world,
        "follow-me",
        "keep going",
        fire={"target": "existing", "session": a.handle},
    )
    world.app.sessions.rename(a.log_id, "renamed-one", None)
    await tick(world, t)
    await until(lambda: inbox(a), 10, "the inbox row")


async def test_an_archived_target_pauses_the_schedule_at_once(world):
    t = frozen(world)
    a = await world.spawn()
    create(
        world,
        "dead-end",
        "keep going",
        fire={"target": "existing", "session": a.log_id},
    )
    await world.app.sessions.close(a.log_id)
    await tick(world, t)
    (r,) = runs(world, "dead-end", "tick")
    assert r["out"] == "error" and "is archived" in r["err"]
    assert world.app.schedules.items["dead-end"].paused["by"] == "errors"


async def test_a_closed_quota_gate_holds_the_tick_and_fires_it_when_it_opens(world):
    from .test_quota_gate_app import LINES, seed

    app = world.app
    app.principals.add("ben", "member")
    await app.registry.call(
        "token.policy", {"name": "ben", "policy": LINES}, Caller("user")
    )
    t = frozen(world)
    seed(world, session=63)
    app.schedules.create(
        "ben-brief",
        Definition.model_validate(
            {"clock": {"every": "5m"}, "fire": {"agent": "opus"}, "prompt": DONE}
        ),
        {"kind": "user", "principal": "ben"},
        world.root,
    )
    await tick(world, t)
    (held,) = runs(world, "ben-brief", "tick")
    assert (held["out"], held["why"]) == ("held", "quota")
    seed(world, session=40)
    await app.schedules.step()
    assert len(runs(world, "ben-brief", "fired")) == 1


def gauge(world, session=10.0, weekly_projected=None) -> None:
    """Every reading says Claude's 5 hours are at ``session``% and the week is
    projected to reach ``weekly_projected``% at its reset."""
    now = time.time()

    def window(kind, percent, projected, span):
        return {
            "kind": kind,
            "label": kind,
            "percent": percent,
            "projected": projected,
            "resets_at": now + span,
        }

    world.app.quota.snapshot = lambda: {
        "providers": [
            {
                "name": "claude",
                "label": "Claude",
                "harness": "claude-code",
                "state": "ok",
                "read_at": now - 30,
                "windows": [
                    window("session", session, None, 3600),
                    window("weekly_all", 20.0, weekly_projected, 86400),
                ],
            }
        ]
    }


async def test_a_week_off_pace_holds_a_schedule_by_default_until_it_is_back(world):
    t = frozen(world)
    gauge(world, weekly_projected=130)
    create(world, "pace-me", DONE)
    await tick(world, t)
    (held,) = runs(world, "pace-me", "tick")
    assert (held["out"], held["why"]) == ("held", "quota")
    assert "on pace for 130%" in held["line"]
    gauge(world, weekly_projected=90)
    world.app.schedules.quota_opened()
    await world.app.schedules.step()
    assert len(runs(world, "pace-me", "fired")) == 1


async def test_a_launch_line_holds_a_fire_into_an_existing_session(world):
    t = frozen(world)
    a = await world.spawn()
    await turn(a, "hello")
    gauge(world, session=63)
    create(
        world,
        "under-half",
        "keep going",
        fire={"target": "existing", "session": a.log_id},
        quota={"claude": {"session": 50}},
    )
    await tick(world, t)
    assert outs(world, "under-half") == ["held"] and not inbox(a)
    gauge(world, session=40)
    await world.app.schedules.step()
    await until(lambda: inbox(a), 10, "the released message")


async def test_an_empty_quota_sets_no_line(world):
    t = frozen(world)
    gauge(world, session=99, weekly_projected=400)
    create(world, "no-limit", DONE, quota={})
    await tick(world, t)
    assert len(runs(world, "no-limit", "fired")) == 1


# -- children ------------------------------------------------------------------------------
async def test_a_run_with_spawn_off_may_not_start_other_work(world):
    t = frozen(world)
    create(world, "no-kids", mcp("session_spawn", agent="opus", prompt="hi"))
    create(world, "no-tasks", mcp("queue_enqueue", queue="general", payload="hi"))
    await tick(world, t)
    for name in ("no-kids", "no-tasks"):
        await ended(world, name)
        (fired,) = runs(world, name, "fired")
        s = world.app.sessions.sessions[fired["log_id"]]
        said = [e["md"] for e in s.entries() if e["kind"] == "prose"][-1]
        assert f"run 1 of {name} may not start other work (spawn: false)" in said
    assert not world.app.queues.tasks


async def test_with_spawn_on_a_working_child_keeps_the_run_live(world):
    t = frozen(world)
    reg = world.app.sessions
    create(
        world,
        "with-kids",
        mcp("session_spawn", agent="opus", prompt="/busy 1"),
        fire={"agent": "opus", "spawn": True},
    )
    await tick(world, t)
    (fired,) = runs(world, "with-kids", "fired")
    run = reg.sessions[fired["log_id"]]

    def kids():
        return [o for o in reg.sessions.values() if o.spec.spawned_by == run.log_id]

    await until(lambda: run.status == "idle" and kids(), 10, "the child")
    (kid,) = kids()
    assert kid.schedule == {"name": "with-kids", "run": 1, "spawn": True}
    assert not runs(world, "with-kids", "finished")  # the child is still working
    end = await ended(world, "with-kids")
    assert kid.log_id in reg.sessions  # no turn_end of its own: kept
    assert end["cost_usd"] == round((run.cost_usd or 0) + (kid.cost_usd or 0), 4)


async def test_a_child_closes_by_its_own_turn_end(world):
    t = frozen(world)
    reg = world.app.sessions
    create(
        world,
        "done-kid",
        mcp("session_spawn", agent="opus", prompt=DONE),
        fire={"agent": "opus", "spawn": True},
    )
    await tick(world, t)
    (fired,) = runs(world, "done-kid", "fired")
    end = await ended(world, "done-kid")
    kids = [m for m in reg.archived.values() if m.get("spawned_by") == fired["log_id"]]
    assert len(kids) == 1  # its turn_end said done
    assert end["closed"] is False  # the run itself ended with no turn_end


# -- restarts and sleep ---------------------------------------------------------------------
async def test_three_missed_ticks_fire_once_after_a_restart(world):
    t = frozen(world)
    create(world, "catch-up", DONE)
    await world.restart()
    frozen(world)[0] = t[0] + 3 * 300 + 10
    await world.app.schedules.step()
    assert [(r["out"], r.get("n")) for r in runs(world, "catch-up", "tick")] == [
        ("missed", 3),
        ("fired", None),
    ]


async def test_a_machine_that_slept_eight_hours_fires_once(world):
    t = frozen(world)
    create(world, "slept-through", DONE)
    await tick(world, t, 8 * 3600)
    assert [(r["out"], r.get("n")) for r in runs(world, "slept-through", "tick")] == [
        ("missed", 96),
        ("fired", None),
    ]


async def test_a_live_run_and_a_held_tick_survive_a_restart(world):
    t = frozen(world)
    create(world, "long-run", "/busy 3")
    await tick(world, t)
    await tick(world, t)
    await world.restart()
    frozen(world)
    st = world.app.schedules.state["long-run"]
    assert list(st.live) == [1] and st.held == "overlap"
    await until(lambda: len(runs(world, "long-run", "fired")) == 2, 15, "the held tick")


async def test_the_clock_task_wakes_at_least_once_a_minute(world):
    t = frozen(world)
    assert world.app.schedules.wait_s() == 60.0
    create(world, "soon-one", DONE)
    assert world.app.schedules.wait_s() == 60.0
    t[0] += 270
    assert world.app.schedules.wait_s() == pytest.approx(30, abs=1)
