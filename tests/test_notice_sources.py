"""Per-session notices: sources declared in .aegis.yaml push items about one
session, warnings ride on prompts and tool results once per level, and info
lines make one status block on prompts only (notices.py, notice_sources.py)."""

import pytest

from aegis import notice_sources
from aegis.config import NOTICES_DEFAULTS, notices_from
from aegis.notices import Item

from .test_notices import Stub

MON = {"stall_min": 15.0, "wake": True}


@pytest.fixture
def declared(monkeypatch):
    monkeypatch.setattr(
        notice_sources, "_SOURCES", {"monitors": dict(MON), "status": {"lines": 5}}
    )


def test_declared_sources_get_their_defaults(declared):
    out = notices_from(None)
    assert out["monitors"] == MON
    assert out["disk"] == NOTICES_DEFAULTS["disk"]


def test_a_source_turns_off_or_overrides_one_key(declared):
    assert notices_from({"monitors": False})["monitors"] is False
    assert notices_from({"monitors": True})["monitors"] == MON
    assert notices_from({"monitors": {"stall_min": 2}})["monitors"] == {
        "stall_min": 2,
        "wake": True,
    }
    assert (
        notices_from({"monitors": {"stall_min": 0.5}})["monitors"]["stall_min"] == 0.5
    )


def test_a_wrong_source_value_keeps_every_default(declared):
    for bad in ({"stall_min": 0}, {"stall_min": "x"}, {"wake": 1}, {"nope": 1}, 3):
        out = notices_from({"monitors": bad})
        assert out["monitors"] == MON, bad
        assert "notices.monitors" in out["error"], bad


def test_an_int_default_takes_only_an_int(declared):
    assert "error" in notices_from({"status": {"lines": 2.5}})
    assert notices_from({"status": {"lines": 2}})["status"] == {"lines": 2}


def test_an_undeclared_source_is_kept_and_listed(declared):
    out = notices_from({"deploys": {"x": 1}})
    assert out["deploys"] == {"x": 1} and out["undeclared"] == ["deploys"]
    assert "error" not in out


@pytest.fixture
def stub(tmp_path, declared):
    s = Stub(tmp_path)
    s.cfg = notices_from(None) | {"plan": {}, "queues": {}}
    return s


def W(
    text="monitor m5 stalled",
    key="m5:stalled",
    clear="monitor m5 moves again, or has ended",
):
    return Item(key, "warn", text, clear=clear)


def test_info_rides_on_sends_only_and_once_per_change(stub):
    n, a = stub.notices, stub.a
    n.set(a.log_id, "plan", [Item("progress", "info", "plan 3/7 · doing: x")])
    assert n.pending(a, "tool") == ""
    out = n.pending(a, "send")
    assert out.startswith("> from aegis:notice · status · info · ")
    assert out.endswith("\nplan 3/7 · doing: x")
    assert n.pending(a, "send") == ""
    n.set(a.log_id, "plan", [Item("progress", "info", "plan 4/7 · doing: y")])
    assert "plan 4/7" in n.pending(a, "send")


def test_a_warning_is_told_once_per_level_and_cleared_with_its_text(stub):
    n, a = stub.notices, stub.a
    n.set(a.log_id, "monitors", [W('monitor m5 "tests" at 40% for 16 min')])
    got = n.pending(a, "tool")
    assert "> from aegis:notice · monitors:m5:stalled · warn · " in got
    assert got.endswith('monitor m5 "tests" at 40% for 16 min')
    n.set(a.log_id, "monitors", [W('monitor m5 "tests" at 40% for 17 min')])
    assert n.pending(a, "tool") == ""
    n.set(
        a.log_id, "monitors", [Item("m5:stalled", "critical", "worse", clear=W().clear)]
    )
    assert "monitors:m5:stalled · critical" in n.pending(a, "tool")
    n.set(a.log_id, "monitors", [])
    cleared = n.pending(a, "send")
    assert "monitors:m5:stalled · clear" in cleared and "moves again" in cleared
    assert n.pending(a, "send") == ""
    assert n.pending(stub.b, "send") == ""  # another session was never told


def test_an_item_without_clear_text_ends_silently(stub):
    n, a = stub.notices, stub.a
    n.set(a.log_id, "plan", [Item("stale", "warn", "plan untouched")])
    assert "plan:stale" in n.pending(a, "tool")
    n.set(a.log_id, "plan", [])
    assert n.pending(a, "send") == ""
    assert "plan:stale" not in a.items_sent
    n.set(a.log_id, "plan", [Item("stale", "warn", "plan untouched")])
    assert "plan:stale" in n.pending(a, "tool")  # on again: told again


def test_warnings_come_before_the_status_block(stub):
    n, a = stub.notices, stub.a
    n.set(a.log_id, "plan", [Item("progress", "info", "plan 1/2")])
    n.set(a.log_id, "monitors", [W()])
    out = n.pending(a, "send")
    assert out.index("monitors:m5:stalled") < out.index("status · info")


def test_the_status_block_caps_its_lines_and_cuts_long_text(stub):
    n, a = stub.notices, stub.a
    n.set(a.log_id, "plan", [Item(f"i{k}", "info", f"line {k}") for k in range(7)])
    lines = n.pending(a, "send").splitlines()[1:]
    assert lines == [f"line {k}" for k in range(5)] + ["… and 2 more"]
    n.set(a.log_id, "plan", [Item("long", "info", "x" * 300)])
    (line,) = n.pending(a, "send").splitlines()[1:]
    assert len(line) == 160 and line.endswith("…")


def test_lines_follow_the_order_sources_first_set_items(stub):
    n, a = stub.notices, stub.a
    n.set(a.log_id, "queues", [Item("out", "info", "queue line")])
    n.set(a.log_id, "plan", [Item("progress", "info", "plan line")])
    n.set(a.log_id, "queues", [Item("out", "info", "queue line 2")])
    assert n.pending(a, "send").splitlines()[1:] == ["queue line 2", "plan line"]


def test_the_last_info_item_gone_owes_one_clear(stub):
    n, a = stub.notices, stub.a
    n.set(a.log_id, "plan", [Item("progress", "info", "plan 1/2")])
    n.pending(a, "send")
    n.set(a.log_id, "plan", [])
    got = n.pending(a, "send")
    assert "status · clear" in got
    assert got.endswith(
        "nothing tracked: no plan items open, no monitors, no tasks out"
    )
    assert n.pending(a, "send") == ""


def test_a_source_turned_off_sets_nothing_and_clears_what_it_had(stub):
    n, a = stub.notices, stub.a
    n.set(a.log_id, "monitors", [W()])
    assert "monitors:m5:stalled" in n.pending(a, "tool")
    stub.cfg = stub.cfg | {"monitors": False}
    n.set(a.log_id, "monitors", [W()])
    assert "monitors:m5:stalled · clear" in n.pending(a, "send")


def test_status_false_drops_the_block_but_not_warnings(stub):
    n, a = stub.notices, stub.a
    stub.cfg = stub.cfg | {"status": False}
    n.set(a.log_id, "plan", [Item("progress", "info", "plan 1/2")])
    n.set(a.log_id, "monitors", [W()])
    out = n.pending(a, "send")
    assert "monitors:m5:stalled" in out and "status · info" not in out


def test_drop_source_clears_its_items_for_every_session(stub):
    n = stub.notices
    n.set(stub.a.log_id, "monitors", [W()])
    n.set(stub.b.log_id, "monitors", [W()])
    n.pending(stub.a, "tool"), n.pending(stub.b, "tool")
    n.drop_source("monitors")
    assert "clear" in n.pending(stub.a, "send") and "clear" in n.pending(stub.b, "send")


def test_forget_retells_everything_per_session_and_leaves_host_kinds(stub):
    n, a = stub.notices, stub.a
    stub.disk(94)
    n.set(a.log_id, "plan", [Item("progress", "info", "plan 1/2")])
    n.set(a.log_id, "monitors", [W()])
    n.pending(a, "send")
    n.forget(a.log_id)
    out = n.pending(a, "send")
    assert "monitors:m5:stalled" in out and "status · info" in out
    assert "disk" not in out and a.notices_sent == {"disk": "warn"}


def test_per_session_keys_never_touch_host_episodes(stub, tmp_path):
    n, a = stub.notices, stub.a
    n.set(a.log_id, "monitors", [W()])
    n.pending(a, "tool")
    assert a.items_sent == {"monitors:m5:stalled": "warn"} and a.notices_sent == {}
    fresh = Stub(tmp_path)  # a restart: the meta kept items_sent
    fresh.cfg = stub.cfg
    fresh.a.items_sent = dict(a.items_sent)
    assert "monitors:m5:stalled" not in fresh.notices.levels()
    assert "disk" not in fresh.notices.pending(fresh.a, "send")
    assert fresh.toasts == []


def test_the_core_names_no_per_session_source():
    import inspect
    from pathlib import Path

    import aegis
    from aegis import config

    root = Path(aegis.__file__).parent
    texts = {f: (root / f).read_text() for f in ("notices.py", "notice_sources.py")}
    texts["config.notices_from"] = inspect.getsource(config.notices_from)
    texts["config._source_from"] = inspect.getsource(config._source_from)
    for f, text in texts.items():
        for name in ('"plan"', '"monitors"', '"queues"'):
            assert name not in text, (f, name)


def test_doctor_warns_on_an_undeclared_source_and_errs_on_a_wrong_one(declared):
    from aegis.doctor import _notices

    (f,) = _notices(notices_from({"deploys": {"x": 1}}))
    assert (f.level, f.where) == ("warn", "notices")
    assert (
        "notices.deploys: no installed source declares it; it is ignored" in f.message
    )
    (f,) = _notices(notices_from({"monitors": {"stall_min": 0}}))
    assert f.level == "error" and "notices.monitors.stall_min" in f.message
    assert _notices(notices_from(None)) == []


# -- the wake: end to end on the fake claude ---------------------------------------
import asyncio  # noqa: E402

from .conftest import until  # noqa: E402
from .test_agents import CONFIG, World, inbox, turn  # noqa: E402

STALL = Item(
    "m5:stalled", "warn", "monitor m5 stalled", clear="m5 moves again", wake=True
)


def wakes(s, key: str) -> list[dict]:
    return [e for e in inbox(s) if e["title"].startswith(f"aegis:notice · {key} · ")]


@pytest.fixture
async def world(tmp_path, fake_claude):
    (tmp_path / ".aegis.yaml").write_text(CONFIG)
    w = await World(tmp_path, fake_claude).start()
    yield w
    await w.stop()


async def test_a_wake_item_reaches_an_idle_session_once(world):
    a = await world.spawn()
    await turn(a, "arm it")
    n = world.app.notices
    n.set(a.log_id, "monitors", [STALL])
    await until(lambda: wakes(a, "monitors:m5:stalled"), timeout=5, what="the wake")
    await until(lambda: a.status == "idle", timeout=5, what="the woken turn")
    n.set(a.log_id, "monitors", [STALL])  # still stalled
    await asyncio.sleep(0.5)
    assert len(wakes(a, "monitors:m5:stalled")) == 1
    n.set(a.log_id, "monitors", [])  # it moved
    n.set(a.log_id, "monitors", [STALL])  # and stalled anew
    await until(
        lambda: len(wakes(a, "monitors:m5:stalled")) == 2,
        timeout=5,
        what="the second wake",
    )


async def test_a_wake_item_set_mid_turn_comes_at_the_turn_end(world):
    a = await world.spawn()
    await a.send("/busy 1")
    await until(lambda: a.status == "working", timeout=5, what="the busy turn")
    world.app.notices.set(a.log_id, "monitors", [STALL])
    await asyncio.sleep(0.3)
    assert wakes(a, "monitors:m5:stalled") == []  # not while it works
    await until(
        lambda: wakes(a, "monitors:m5:stalled"), timeout=8, what="the wake at the end"
    )
    await until(lambda: a.status == "idle", timeout=5, what="the woken turn")
    assert len(wakes(a, "monitors:m5:stalled")) == 1


async def test_a_warning_without_wake_never_starts_a_turn(world):
    a = await world.spawn()
    await turn(a, "arm it")
    quiet = Item("m5:stalled", "warn", "monitor m5 stalled")
    world.app.notices.set(a.log_id, "monitors", [quiet])
    await asyncio.sleep(0.5)
    assert inbox(a) == [] and a.status == "idle"


async def test_a_clear_never_wakes(world):
    a = await world.spawn()
    await turn(a, "arm it")
    n = world.app.notices
    n.set(a.log_id, "monitors", [STALL])
    await until(lambda: wakes(a, "monitors:m5:stalled"), timeout=5, what="the wake")
    await until(lambda: a.status == "idle", timeout=5, what="the woken turn")
    n.set(a.log_id, "monitors", [])
    await asyncio.sleep(0.5)
    assert len(inbox(a)) == 1 and a.status == "idle"


async def test_a_wake_for_an_archived_session_is_dropped(world):
    a = await world.spawn()
    await turn(a, "arm it")
    await world.app.registry.call("session.close", {"log_id": a.log_id})
    world.app.notices.set(a.log_id, "monitors", [STALL])
    await asyncio.sleep(0.5)
    assert wakes(a, "monitors:m5:stalled") == []


async def test_monitors_wake_false_lets_warnings_wait(world, monkeypatch):
    a = await world.spawn()
    await turn(a, "arm it")
    monkeypatch.setattr(world.app.notices, "cfg", lambda source: {"wake": False})
    world.app.notices.set(a.log_id, "monitors", [STALL])
    await asyncio.sleep(0.5)
    assert inbox(a) == []


# -- review fixes ------------------------------------------------------------------
def test_a_prompt_while_booting_carries_no_session_notices_and_forgets_nothing(
    stub, tmp_path
):
    n, a = stub.notices, stub.a
    stall = Item("m5:stalled", "warn", "monitor m5 stalled", clear="m5 moves again")
    n.set(a.log_id, "plan", [Item("progress", "info", "plan 1/2")])
    n.set(a.log_id, "monitors", [stall])
    n.pending(a, "send")
    told = dict(a.items_sent)
    fresh = Stub(tmp_path)  # a restart: the meta kept items_sent
    fresh.cfg = stub.cfg
    fresh.a.items_sent = dict(told)
    fresh.notices.boot_begin()
    assert fresh.notices.pending(fresh.a, "send") == ""  # the continue prompt
    assert fresh.a.items_sent == told
    fresh.notices.set(a.log_id, "plan", [Item("progress", "info", "plan 1/2")])
    fresh.notices.set(a.log_id, "monitors", [stall])
    fresh.notices.boot_end()
    assert fresh.notices.pending(fresh.a, "send") == ""  # nothing repeated or cleared


def test_the_status_source_is_declared_without_importing_notices():
    import subprocess
    import sys

    code = "from aegis.config import notices_from; print(notices_from(None)['status'])"
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
    assert out.stdout.strip() == "{'lines': 5}", out.stderr


async def test_several_wake_items_come_as_one_inbox_message(world):
    a = await world.spawn()
    await turn(a, "arm it")
    both = [
        Item("m5:stalled", "warn", "monitor m5 stalled", wake=True),
        Item("m5:overdue", "warn", "monitor m5 overdue", wake=True),
    ]
    world.app.notices.set(a.log_id, "monitors", both)
    await until(lambda: inbox(a), timeout=5, what="the wake")
    await until(lambda: a.status == "idle", timeout=5, what="the woken turn")
    await asyncio.sleep(0.3)
    rows = [e for e in inbox(a) if "monitors:m5" in e["title"]]
    # One delivery: the fold draws its second notice as a row of the same event.
    assert len({e["id"].split(":")[0] for e in rows}) == 1, [e["id"] for e in rows]
    md = " ".join(e["md"] for e in rows)
    assert "m5 stalled" in md and "m5 overdue" in md
