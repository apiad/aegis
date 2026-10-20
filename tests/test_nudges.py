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


async def test_a_silent_idle_session_is_asked_once_per_stretch(
    fast, world, monkeypatch
):
    # Shorter than `fast`'s, so the three idle stretches below fit the fast
    # lane's budget under load (#359); every wait is a multiple of it.
    monkeypatch.setattr(nudges, "IDLE_S", 0.2)
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
    await asyncio.sleep(3 * nudges.IDLE_S)
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
    # Each `/busy 0.4` works past it; halved from 0.6 and 0.8 for the fast
    # lane's budget under load (#359).
    monkeypatch.setattr(nudges, "PLAN_STALE_S", 0.3)
    a = await world.spawn()
    plan = [
        {"text": "build it", "state": "doing"},
        {"text": "ship it", "state": "pending"},
    ]
    await turn(a, mcp("plan_update", items=plan))
    await asyncio.sleep(0.15)
    assert inbox(a) == [], "a plan just written is not stale"
    await turn(a, "/busy 0.4")
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
    await turn(a, "/busy 0.4")
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


# -- the plan notice source ---------------------------------------------------------
from aegis.notice_sources import Item  # noqa: E402
from aegis.nudges import plan_items, stale_now  # noqa: E402


def test_stale_now_adds_the_running_work_stretch():
    st = {"clock": {"work_s": 100.0, "idle_s": 0.0, "at": 1000.0, "running": "work"},
          "plan_mark": 40.0}  # fmt: skip
    assert stale_now(st, 1100.0) == 160.0
    st["clock"]["running"] = "idle"
    assert stale_now(st, 1100.0) == 60.0
    assert stale_now({"clock": None, "plan_mark": None}, 1.0) == 0.0


def _plan(done=3, total=7, doing="write the stall check", work=300.0):
    items = [{"text": f"i{k}", "state": "done"} for k in range(done)]
    items.append({"text": doing, "state": "doing"})
    items += [{"text": f"p{k}", "state": "pending"} for k in range(total - done - 1)]
    return {"plan": items, "plan_mark": 0.0,
            "clock": {"work_s": work, "idle_s": 0.0, "at": 0.0, "running": "idle"}}  # fmt: skip


def test_plan_items():
    assert plan_items(_plan(), {"stale_min": 10.0}, 0.0) == [
        Item("progress", "info", "plan 3/7 · doing: write the stall check")
    ]
    assert plan_items(_plan(work=601.0), {"stale_min": 10.0}, 0.0)[1] == Item(
        "stale",
        "warn",
        "plan untouched for 10 min of work · update it with plan_update",
    )
    st = _plan()
    for i in st["plan"]:
        i["state"] = "done"
    assert plan_items(st, {"stale_min": 10.0}, 0.0) == []
    assert plan_items({"plan": [], "clock": None, "plan_mark": None}, {}, 0.0) == []


def test_a_plan_with_nothing_doing_names_the_next_item():
    st = _plan()
    st["plan"][3]["state"] = "pending"
    assert (
        plan_items(st, {"stale_min": 10.0}, 0.0)[0].text
        == "plan 3/7 · next: write the stall check"
    )


async def test_plan_update_never_answers_with_the_plan_warning(world):
    a = await world.spawn()
    await turn(a, mcp("plan_update", items=[{"text": "a", "state": "doing"}]))
    stale = Item("stale", "warn", "plan untouched for 12 min of work")
    world.app.notices.set(a.log_id, "plan", [stale])  # as a tick would
    said = await turn(a, mcp("plan_update", items=[{"text": "a", "state": "doing"}]))
    assert said.startswith("mcp ok: plan saved")
    assert "plan:stale" not in said
    assert "plan:stale" not in a.items_sent
