"""The nudges, against the real app and the fake claude, with the clocks
shortened: a silent idle stretch is asked about once, a stale plan is reminded
once per plan, and nothing that explains the silence gets either."""

import asyncio

import pytest

from aegis import nudges
from aegis.nudges import due
from aegis.transcript.entries import fold_records
from aegis.transcript.store import read_store

from .conftest import until
from .test_agents import CONFIG, World, hold, inbox, mcp, turn


def nudges_of(s, kind: str) -> list[dict]:
    return [e for e in inbox(s) if e["title"].startswith(f"aegis:nudge · {kind} · ")]


def refolded(s) -> dict:
    return fold_records(read_store(s.store.path)[0]).standing


@pytest.fixture
async def world(tmp_path, fake_claude):
    (tmp_path / ".aegis.yaml").write_text(CONFIG)
    w = await World(tmp_path, fake_claude).start()
    yield w
    await w.stop()


@pytest.fixture
def fast(monkeypatch):
    """Before `world`: its loop reads EVERY_S as it goes to sleep."""
    monkeypatch.setattr(nudges, "EVERY_S", 0.1)
    monkeypatch.setattr(nudges, "IDLE_S", 0.5)
    monkeypatch.setattr(nudges, "PLAN_STALE_S", 3600.0)


async def test_a_silent_idle_session_is_asked_once_per_stretch(fast, world):
    a = await world.spawn()
    await turn(a, "write the summary")
    await until(lambda: nudges_of(a, "idle"), timeout=5, what="the idle nudge")
    (n,) = nudges_of(a, "idle")
    assert "What are you doing: done, needs you, or something else?" in n["md"]
    assert not any(
        "aegis:nudge" in (e.get("md") or "") for e in a.entries() if e["kind"] == "user"
    )
    # The fake answers the nudge with a turn that says nothing: that is not a
    # new stretch, so there is no second nudge.
    await until(lambda: a.status == "idle", timeout=5, what="the nudge's turn")
    await asyncio.sleep(1.5)
    assert len(nudges_of(a, "idle")) == 1
    # The person writes again: a new stretch, asked once more.
    await turn(a, "and the tests")
    await until(
        lambda: len(nudges_of(a, "idle")) == 2, timeout=5, what="a second stretch"
    )
    await until(lambda: a.status == "idle", timeout=5, what="the second nudge's turn")
    assert refolded(a)["nudged"] == a.standing["nudged"] == ["idle"]


@pytest.mark.parametrize("attention", ["done", "review", "needs_you"])
async def test_a_turn_end_suppresses_the_idle_nudge(fast, world, attention):
    a = await world.spawn()
    await turn(a, mcp("turn_end", attention=attention, line="Said."))
    await asyncio.sleep(1)
    assert inbox(a) == []


async def test_a_session_waiting_on_a_monitor_is_not_nudged(fast, world):
    a = await world.spawn()
    await hold(a)
    await asyncio.sleep(1)
    assert a.wire()["attention"] == "waiting" and inbox(a) == []


async def test_a_stale_plan_is_reminded_once_per_plan(fast, world, monkeypatch):
    monkeypatch.setattr(nudges, "IDLE_S", 3600.0)
    monkeypatch.setattr(nudges, "PLAN_STALE_S", 0.6)
    a = await world.spawn()
    plan = [
        {"text": "build it", "state": "doing"},
        {"text": "ship it", "state": "pending"},
    ]
    await turn(a, mcp("plan_update", items=plan))
    await asyncio.sleep(0.3)
    assert inbox(a) == [], "a plan just written is not stale"
    await turn(a, "/sleep 0.8")
    await until(lambda: nudges_of(a, "plan"), timeout=5, what="the plan nudge")
    assert "Update it with plan_update, or carry on" in nudges_of(a, "plan")[0]["md"]
    # The fake answers with a turn that leaves the plan as it was: still
    # stale, and not reminded again.
    await until(lambda: a.status == "idle", timeout=5, what="the nudge's turn")
    await asyncio.sleep(0.4)
    assert len(nudges_of(a, "plan")) == 1, "the same plan was reminded twice"
    # A plan record re-arms it: work past the threshold again earns one more.
    plan[0]["state"] = "done"
    await turn(a, mcp("plan_update", items=plan))
    await turn(a, "/sleep 0.8")
    await until(
        lambda: len(nudges_of(a, "plan")) == 2, timeout=5, what="the re-armed nudge"
    )
    await until(lambda: a.status == "idle", timeout=5, what="the last turn")
    assert refolded(a)["plan_mark"] == a.standing["plan_mark"]


def _standing(how="silent", at=0.0, report=None, **kw) -> dict:
    return {
        "ended": {"at": at, "how": how},
        "report": report,
        "plan": [],
        "clock": None,
        "plan_mark": None,
        "nudged": [],
        **kw,
    }


def test_due_leaves_alone_what_explains_the_silence():
    ok = dict(attention="done", worker=False, idle=True, now=10_000.0)
    assert due(_standing(), **ok) == "idle"
    for how in ("interrupted", "error", "command"):
        assert due(_standing(how=how), **ok) is None, how
    for attention in ("needs_you", "waiting", "working", "error"):
        assert due(_standing(), **{**ok, "attention": attention}) is None
    assert due(_standing(), **{**ok, "worker": True}) is None
    assert due(_standing(), **{**ok, "idle": False}) is None
    assert due(_standing(nudged=["idle"]), **ok) is None
    assert due({**_standing(), "ended": None}, **ok) is None
    # A meta written before the nudges has none of their keys.
    assert due({"plan": [], "report": None}, **ok) is None


def test_due_measures_a_stale_plan_in_work_time():
    ok = dict(attention="review", worker=False, idle=True, now=0.0)
    stale = _standing(
        how="reported",
        plan=[{"text": "a", "state": "doing"}],
        clock={"work_s": 2000.0, "idle_s": 99999.0, "at": 0.0, "running": "idle"},
        plan_mark=1000.0,
    )
    assert due(stale, **ok) == "plan"
    assert due({**stale, "plan_mark": 1200.0}, **ok) is None
    assert due({**stale, "nudged": ["plan"]}, **ok) is None
    assert due({**stale, "plan": [{"text": "a", "state": "done"}]}, **ok) is None
