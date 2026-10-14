"""The terminals registry: spawn, cards, the channel, close, restart, boot."""

import asyncio

import pytest

from aegis import terminals as terminals_mod
from aegis.ops import OpError
from aegis.terminals import Terminals

from .conftest import until


def profile(cmd, restart="no"):
    return {"cmd": cmd, "cwd": ".", "restart": restart, "env": {}, "access": {}}


PROFILES = {
    "echo": profile("cat"),
    "svc": profile("echo up; sleep 100", restart="always"),
    "missing": profile("no-such-binary-xyz"),
    "stubborn": profile("trap '' HUP; echo ready; sleep 100"),
    "broken": {"error": "cmd is missing"},
}


class Bus:
    def __init__(self):
        self.msgs: list[tuple[str, list[dict]]] = []

    def __call__(self, channel, ops):
        self.msgs.append((channel, ops))

    def text(self, tid) -> str:
        return "".join(
            o.get("o", "") for c, ops in self.msgs if c == f"term:{tid}" for o in ops
        )

    def cards(self, tid) -> list[dict]:
        return [
            o["upsert"]
            for c, ops in self.msgs
            if c == "sessions"
            for o in ops
            if "upsert" in o and o["upsert"]["id"] == tid
        ]


def registry(tmp_path) -> tuple[Terminals, Bus]:
    bus = Bus()
    return Terminals(tmp_path / "state", tmp_path, lambda: PROFILES, bus), bus


@pytest.fixture
async def reg(tmp_path):
    r, bus = registry(tmp_path)
    r.bus = bus
    yield r
    await r.shutdown()


async def test_spawn_publishes_a_card_and_output(reg):
    t = await reg.spawn("echo", owner="ana")
    assert t.id.startswith("t-") and t.meta["name"] == "echo"
    t.input("ping\r")
    await until(lambda: "ping" in reg.bus.text(t.id), what="echo")
    card = reg.bus.cards(t.id)[-1]
    assert card["kind"] == "term" and card["log_id"] == t.id
    assert card["owner"] == "ana" and card["title"] == "cat"
    assert card["state"] == "running" and card["mark"] == "running"
    assert (reg.dir / t.id / "meta.json").exists()


async def test_names_are_unique_among_open_terminals(reg):
    a = await reg.spawn("echo", owner="ana")
    b = await reg.spawn("echo", owner="ana")
    assert (a.meta["name"], b.meta["name"]) == ("echo", "echo-2")


async def test_a_bad_or_unknown_profile_is_refused(reg):
    with pytest.raises(OpError) as e:
        await reg.spawn("broken", owner="ana")
    assert e.value.code == "bad_profile" and "cmd is missing" in str(e.value)
    with pytest.raises(OpError) as e:
        await reg.spawn("nope", owner="ana")
    assert e.value.code == "no_profile"


async def test_a_command_that_is_not_found_shows_as_a_failed_terminal(reg):
    t = await reg.spawn("missing", owner="ana")
    await asyncio.wait_for(t.exited.wait(), 3)
    card = reg.bus.cards(t.id)[-1]
    assert card["state"] == "exited" and card["code"] == 127
    assert card["mark"] == "failed"
    assert "not found" in reg.bus.text(t.id)


async def test_the_channel_sends_a_tail_or_a_delta(reg):
    t = await reg.spawn("echo", owner="ana")
    t.input("one\r")
    await until(lambda: "one" in reg.bus.text(t.id), what="one")
    cold = reg.channel(t.id, None)()
    assert "one" in cold["tail"] and "delta" not in cold
    assert (cold["cols"], cold["rows"], cold["state"]) == (120, 40, "running")
    held = cold["rev"]
    t.input("two\r")
    await until(lambda: "two" in reg.bus.text(t.id), what="two")
    warm = reg.channel(t.id, held)()
    assert "tail" not in warm
    assert "two" in "".join(o.get("o", "") for o in warm["delta"])
    assert all(o["rev"] > held for o in warm["delta"])
    assert reg.channel("t-20200101-000000-000000", None) is None


async def test_close_archives_and_ends_a_program_that_ignores_sighup(reg, monkeypatch):
    monkeypatch.setattr(terminals_mod, "CLOSE_GRACE_S", 0.3)
    t = await reg.spawn("stubborn", owner="ana")
    await until(lambda: "ready" in reg.bus.text(t.id), what="ready")
    await reg.close(t.id)
    assert t.id not in reg.open and reg.archived[t.id]["reason"] == "closed"
    assert ("sessions", [{"remove": t.id}]) in reg.bus.msgs
    await asyncio.wait_for(t.exited.wait(), 2)
    assert reg.channel(t.id, None)()["state"] == "ended"
    assert [c["id"] for c in reg.closed()] == [t.id]


async def test_restart_keeps_the_id_and_the_output(reg):
    t = await reg.spawn("missing", owner="ana")
    await asyncio.wait_for(t.exited.wait(), 3)
    before = t.store.rev
    await reg.restart(t.id)
    assert reg.open[t.id] is t and t.running and t.store.rev > before


async def test_boot_starts_services_again_and_ends_the_rest(tmp_path):
    r, bus = registry(tmp_path)
    shell = await r.spawn("echo", owner="ana")
    svc = await r.spawn("svc", owner="ana")
    shell.input("before\r")
    await until(lambda: "before" in bus.text(shell.id), what="before")
    await r.shutdown()
    again, _ = registry(tmp_path)
    await again.boot()
    try:
        ended = again.open[shell.id].meta
        assert (ended["state"], ended["reason"]) == ("ended", "server_stopped")
        assert "before" in again.channel(shell.id, None)()["tail"]
        assert again.open[svc.id].running
    finally:
        await again.shutdown()
