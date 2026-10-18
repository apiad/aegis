import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest

from aegis.config import NOTICES_DEFAULTS, notices_from
from aegis.notices import Notices, cpu_pct, next_level, psi, read_signals
from aegis.ops import Caller

from .test_agents import world  # noqa: F401

DISK = NOTICES_DEFAULTS["disk"]


def test_a_level_turns_on_escalates_and_clears_with_a_gap():
    assert next_level(None, 89, DISK) is None
    assert next_level(None, 90, DISK) == "warn"
    assert next_level("warn", 86, DISK) == "warn"  # above clear: held
    assert next_level("warn", 97, DISK) == "critical"
    assert next_level("critical", 92, DISK) == "critical"  # never steps down
    assert next_level("critical", 84, DISK) is None
    assert next_level(None, 98, DISK) == "critical"


def test_cpu_has_no_critical():
    cpu = NOTICES_DEFAULTS["cpu"]
    assert next_level(None, 99, cpu) == "warn"
    assert next_level("warn", 29, cpu) is None


def test_psi_parses_and_cpu_falls_back_to_load():
    p = psi(
        "some avg10=67.69 avg60=62.36 avg300=63.95 total=1\n"
        "full avg10=0.00 avg60=0.00 avg300=0.00 total=0"
    )
    assert p["some"]["avg60"] == 62.36
    assert cpu_pct(p, None, 4) == 62.36
    assert cpu_pct(None, "8.00 7.00 6.00 3/900 1", 4) == 50.0  # load 2x CPUs reads 50
    assert cpu_pct(None, None, 4) is None
    assert psi("garbage") is None


def test_a_host_without_proc_reads_no_memory_or_cpu(tmp_path):
    r = read_signals([tmp_path], proc=tmp_path / "noproc")
    assert r["memory"] is None and r["cpu"] is None
    assert r["disk"]["pct"] >= 0


def test_the_notices_block_falls_back_and_turns_kinds_off():
    assert notices_from(None) == NOTICES_DEFAULTS
    got = notices_from({"disk": {"on": 80}, "cpu": False})
    assert got["disk"] == {"on": 80, "critical": 97, "clear": 85}
    assert got["cpu"] is False
    bad = notices_from({"disk": {"onn": 1}})
    assert "error" in bad and bad["disk"] == NOTICES_DEFAULTS["disk"]


class Metas:
    def __init__(self):
        self.writes = 0
        self.fail = False

    def write_soon(self, log_id, meta):
        if self.fail:
            raise OSError(28, "No space left on device")
        self.writes += 1


def session(log_id, *, owner=None, worker=None, spawned_by=None, spawned_from=None):
    return SimpleNamespace(
        log_id=log_id,
        handle=log_id,
        spec=SimpleNamespace(
            harness="claude-code",
            owner=owner,
            spawned_by=spawned_by,
            spawned_from=spawned_from,
        ),
        worker=worker,
        schedule=None,
        archived=False,
        notices_sent={},
        _metas=Metas(),
        meta=lambda: {},
    )


class Stub:
    """An app with two person-opened sessions and a queue worker, and a
    reading the test sets."""

    def __init__(self, tmp_path: Path):
        self.reading = {"disk": None, "memory": None, "cpu": None}
        self.cfg = notices_from(None)
        self.toasts: list[tuple[str, str]] = []
        self.a, self.b = session("a"), session("b")
        self.w = session("w", worker={"queue": "general"})
        self.roots = SimpleNamespace(config_root=tmp_path)
        self.config = SimpleNamespace(current=lambda: SimpleNamespace(notices=self.cfg))
        self.quota = None
        self.sessions = SimpleNamespace(
            sessions={s.log_id: s for s in (self.a, self.b, self.w)},
            workflow_lock=lambda s: None,
            cgroup_dir=lambda s: None,
        )
        self.now = 0.0
        self.notices = Notices(
            self, read=lambda paths: self.reading, clock=lambda: self.now
        )

    def toast(self, text, kind="info", source=None):
        self.toasts.append((text, kind))
        return 1

    def disk(self, pct):
        self.reading = self.reading | {
            "disk": {"pct": pct, "free_gb": 4.8, "total_gb": 75}
        }
        self.now += 10  # past the cache


@pytest.fixture
def stub(tmp_path):
    return Stub(tmp_path)


def test_each_session_gets_a_notice_once_and_a_clear_once(stub):
    n = stub.notices
    stub.disk(94)
    got = n.pending(stub.a)
    assert "> from aegis:notice · disk · warn · " in got
    assert "The disk holding aegis is 94% full: 4.8 GB free of 75.0 GB" in got
    assert got.endswith("aegis tells you once, and again when it clears.")
    assert n.pending(stub.a) == ""
    assert stub.a.notices_sent == {"disk": "warn"}
    stub.disk(80)
    got = n.pending(stub.a)
    assert "aegis:notice · disk · clear" in got
    assert got.endswith("The disk holding aegis is back to 80% full. Work as usual.")
    assert n.pending(stub.a) == "" and stub.a.notices_sent == {}
    assert n.pending(stub.w) == ""  # never told, so owed no clear


def test_an_escalation_is_sent_again(stub):
    n = stub.notices
    stub.disk(94)
    assert "disk · warn" in n.pending(stub.a)
    stub.disk(98)
    got = n.pending(stub.a)
    assert "disk · critical" in got and "This is critical." in got
    assert n.pending(stub.a) == ""
    stub.disk(92)  # never steps down to warn
    assert n.pending(stub.a) == ""


def test_one_asker_per_episode_and_owner(stub):
    n = stub.notices
    stub.disk(94)
    assert "ask the person what to do" in n.pending(stub.a)
    assert "Another session is asking the person" in n.pending(stub.b)
    w = n.pending(stub.w)
    assert "Do not ask anyone" in w and "ask the person" not in w
    other = session("c", owner="someone-else")
    stub.sessions.sessions["c"] = other
    assert "ask the person what to do" in n.pending(other)


def test_a_closed_asker_hands_over(stub):
    n = stub.notices
    stub.disk(94)
    assert "ask the person what to do" in n.pending(stub.a)
    stub.a.archived = True
    assert "ask the person what to do" in n.pending(stub.b)
    c = session("c")
    stub.sessions.sessions["c"] = c
    assert "Another session is asking the person" in n.pending(c)


def test_an_ended_episode_drops_its_asker(stub):
    n = stub.notices
    stub.disk(94)
    n.pending(stub.a)
    stub.disk(80)
    n.levels()
    stub.disk(94)
    assert "ask the person what to do" in n.pending(stub.b)


def test_spawned_and_cpu_never_ask(stub):
    n = stub.notices
    child = session("k", spawned_by="a")
    far = session("f", spawned_from="person@elsewhere")
    stub.sessions.sessions |= {"k": child, "f": far}
    stub.reading = stub.reading | {"cpu": {"pct": 70.0}}
    stub.disk(94)
    assert "say so in your result to the agent that started you" in n.pending(child)
    assert "say so in your result to the agent that started you" in n.pending(far)
    got = n.pending(stub.a)
    assert "disk · warn" in got and "cpu · warn" in got
    cpu = got[got.index("cpu · warn") :]
    assert "Another session is asking the person" in cpu


async def test_concurrent_pending_gives_each_block_once(stub):
    stub.disk(94)
    got = await asyncio.gather(
        *(asyncio.to_thread(stub.notices.pending, stub.a) for _ in range(5))
    )
    assert len([g for g in got if g]) == 1


def test_a_failed_meta_write_still_delivers(stub):
    stub.a._metas.fail = True
    stub.disk(94)
    assert "disk · warn" in stub.notices.pending(stub.a)
    assert stub.a.notices_sent == {"disk": "warn"}
    assert stub.notices.pending(stub.a) == ""


def test_a_delivery_records_the_session_meta(stub):
    stub.disk(94)
    stub.notices.pending(stub.a)
    stub.notices.pending(stub.a)
    assert stub.a._metas.writes == 1


def test_critical_toasts_once_per_episode(stub):
    n = stub.notices
    stub.disk(98)
    n.levels()
    n.levels()
    assert stub.toasts == [("aegis: disk is critical: 98% full, 4.8 GB free", "error")]
    stub.disk(80)
    n.levels()
    stub.disk(98)
    n.levels()
    assert len(stub.toasts) == 2


def test_a_kind_set_false_never_fires(stub):
    stub.cfg = notices_from({"disk": False})
    stub.disk(99)
    assert stub.notices.pending(stub.a) == "" and not stub.toasts


def test_memory_turns_on_under_pressure_and_names_the_sessions_own(stub, tmp_path):
    (tmp_path / "memory.current").write_text(str(2 * 1024**3))
    stub.sessions.cgroup_dir = lambda s: tmp_path
    stub.reading = stub.reading | {
        "memory": {"pct": 60, "avail_gb": 3.0, "total_gb": 7.6, "psi_full": 8.0}
    }
    got = stub.notices.pending(stub.a)
    assert "Memory is 60% used: 3.0 GB available of 7.6 GB" in got
    assert "Your session's processes hold 2.0 GB." in got


class Quota:
    def __init__(self, severity):
        self.severity = severity

    def snapshot(self):
        window = {
            "kind": "week",
            "label": "week",
            "percent": 70,
            "severity": self.severity,
            "projected": 112,
        }
        return {
            "providers": [
                {"name": "claude", "label": "Claude", "harness": "claude-code",
                 "windows": [window]},
                {"name": "opencode-go", "label": "OpenCode Go",
                 "harness": "opencode", "windows": [window]},
            ]
        }  # fmt: skip


def test_quota_reaches_only_its_harness_and_never_steps_down(stub):
    n = stub.notices
    stub.quota = Quota("warning")
    got = n.pending(stub.a)
    assert "quota:claude · warn" in got and "opencode" not in got
    assert "The Claude quota is at 70% of its week window, on pace for 112%." in got
    stub.quota.severity = "critical"
    got = n.pending(stub.a)
    assert "quota:claude · critical" in got
    assert "Tell the person before you enqueue anything." in got
    stub.quota.severity = "warning"
    assert n.pending(stub.a) == ""
    stub.quota.severity = "normal"
    assert "The Claude quota is back on pace." in n.pending(stub.a)


async def test_a_spawn_through_a_link_is_never_the_asker(world):  # noqa: F811
    r = await world.app.registry.call(
        "session.spawn", {"agent": "opus"}, Caller("user", link="elsewhere")
    )
    s = world.app.sessions.sessions[r["log_id"]]
    assert s.spec.spawned_from == "person@elsewhere"
    assert world.app.notices.role(s, "disk") == "spawned"
    here = await world.spawn()
    assert world.app.notices.role(here, "disk") == "asker"
