import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from aegis.config import NOTICES_DEFAULTS, notices_from
from aegis.notices import Notices, cpu_pct, next_level, psi, read_signals
from aegis.ops import Caller
from aegis.transcript.entries import NOTICE_HEADER, split_notices
from aegis.transcript.store import read_store

from .conftest import until
from .test_agents import CONFIG, World, inbox, mcp, turn, world  # noqa: F401
from .test_fold import Rec, run

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
    assert got["disk"] == {"on": 80, "critical": 87, "clear": 75}
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


def session(
    log_id,
    *,
    owner=None,
    worker=None,
    spawned_by=None,
    spawned_from=None,
    harness="claude-code",
    model="opus",
):
    return SimpleNamespace(
        log_id=log_id,
        handle=log_id,
        spec=SimpleNamespace(
            harness=harness,
            model=model,
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


async def test_pending_from_tasks_on_the_loop_gives_each_block_once(stub):
    stub.disk(94)

    async def call():
        await asyncio.sleep(0)
        return stub.notices.pending(stub.a)

    got = await asyncio.gather(*(call() for _ in range(5)))
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
        self.state = "ok"
        self.missing = False

    def snapshot(self):
        if self.missing:
            return {"providers": []}
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
                 "state": self.state, "windows": [window]},
                {"name": "opencode-go", "label": "OpenCode Go",
                 "harness": "opencode", "state": "ok", "windows": [window]},
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


def test_a_kinds_on_alone_derives_clear_and_critical():
    got = notices_from({"cpu": {"on": 40}, "memory": {"on": 92}})
    assert got["cpu"] == {"on": 40, "clear": 20}
    assert got["memory"] == {"on": 92, "critical": 97, "clear": 87, "psi_full": 5}
    assert notices_from({"disk": {"on": 95}})["disk"]["critical"] == 100  # capped


def test_a_block_whose_levels_cross_keeps_the_defaults():
    for raw in (
        {"disk": {"clear": 95}},
        {"disk": {"on": 90, "critical": 80}},
        {"memory": {"critical": 50}},
        {"cpu": {"clear": 60}},
    ):
        got = notices_from(raw)
        assert "error" in got, raw
        assert got["disk"] == NOTICES_DEFAULTS["disk"]


def fresh(stub):
    """The same app after a restart: a new Notices over the restored sessions."""
    stub.notices = Notices(
        stub, read=lambda paths: stub.reading, clock=lambda: stub.now
    )
    return stub.notices


def test_a_restart_does_not_repeat_a_critical_or_toast_again(stub):
    stub.a.notices_sent = {"disk": "critical"}
    stub.disk(93)
    n = fresh(stub)
    assert n.pending(stub.a) == ""
    assert stub.a.notices_sent == {"disk": "critical"}
    n.levels()
    assert stub.toasts == []


def test_a_restart_does_not_clear_a_warn_above_clear(stub):
    stub.a.notices_sent = {"disk": "warn"}
    stub.disk(87)
    n = fresh(stub)
    assert n.pending(stub.a) == ""
    assert stub.a.notices_sent == {"disk": "warn"}
    assert "disk · warn" in n.pending(stub.b)


def test_quota_keeps_its_level_while_the_reading_is_not_fresh(stub):
    n = stub.notices
    stub.quota = Quota("warning")
    assert "quota:claude · warn" in n.pending(stub.a)
    stub.quota.severity = "normal"
    for state in ("stale", "failed"):
        stub.quota.state = state
        assert n.pending(stub.a) == "", state
    stub.quota.state = "ok"
    stub.quota.missing = True
    assert n.pending(stub.a) == ""
    stub.quota.missing = False
    assert "The Claude quota is back on pace." in n.pending(stub.a)


def test_quota_reaches_only_sessions_that_draw_on_that_pool(stub):
    n = stub.notices
    stub.quota = Quota("warning")
    zen = session("z", harness="opencode", model="zen/big")
    go = session("g", harness="opencode", model="opencode-go/kimi")
    stub.sessions.sessions |= {"z": zen, "g": go}
    assert n.pending(zen) == ""
    got = n.pending(go)
    assert "quota:opencode-go · warn" in got and "quota:claude" not in got


def test_a_kind_turned_off_mid_episode_says_so_instead_of_a_reading(stub):
    n = stub.notices
    stub.quota = Quota("warning")
    stub.reading = stub.reading | {"cpu": {"pct": 70.0}}
    stub.disk(94)
    n.pending(stub.a)
    stub.cfg = notices_from(
        {"disk": False, "cpu": False, "memory": False, "quota": False}
    )
    got = n.pending(stub.a)
    assert "aegis no longer watches the disk." in got
    assert "aegis no longer watches the CPU." in got
    assert "aegis no longer watches the Claude quota." in got
    assert "back to" not in got and "no longer saturated" not in got


def test_a_failed_read_keeps_the_episode(stub):
    n = stub.notices
    stub.disk(94)
    n.pending(stub.a)
    stub.reading = stub.reading | {"disk": None}
    stub.now += 10
    assert n.pending(stub.a) == ""
    assert stub.a.notices_sent == {"disk": "warn"}
    assert "The disk holding aegis is 94% full" in n.pending(stub.b)


def test_a_closed_asker_hands_over_to_a_session_already_told(stub):
    n = stub.notices
    stub.disk(94)
    n.pending(stub.a)
    assert "Another session is asking the person" in n.pending(stub.b)
    n.pending(stub.w)
    stub.a.archived = True
    got = n.pending(stub.b)
    assert "disk · warn" in got and "ask the person what to do" in got
    assert n.pending(stub.w) == ""  # a worker never becomes the asker
    assert n.pending(stub.b) == ""


def memory(stub, pct, psi_full):
    stub.reading = stub.reading | {
        "memory": {"pct": pct, "avail_gb": 3.0, "total_gb": 7.6, "psi_full": psi_full}
    }
    stub.now += 10


def test_a_memory_episode_on_pressure_holds_until_half_the_threshold(stub):
    n = stub.notices
    memory(stub, 60, 8.0)
    assert "memory · warn" in n.pending(stub.a)
    memory(stub, 60, 3.0)  # at or above 5 / 2: held
    assert n.pending(stub.a) == ""
    memory(stub, 60, 2.0)
    assert "Memory is back to 60% used." in n.pending(stub.a)


def test_a_memory_episode_on_percent_ends_below_clear_whatever_the_pressure(stub):
    n = stub.notices
    memory(stub, 92, 3.0)
    assert "memory · warn" in n.pending(stub.a)
    memory(stub, 80, 3.0)
    assert "Memory is back to 80% used." in n.pending(stub.a)


# -- delivery, end to end ---------------------------------------------------------
# The disk notice fires on any host and never toasts.
NOTICED = CONFIG + (
    "notices: {disk: {on: 1, critical: 100, clear: 0}, memory: false,"
    " cpu: false, quota: false}\n"
)
DISK_WARN = NOTICE_HEADER + "disk · warn"


@pytest.fixture
async def noticed(tmp_path, fake_claude):
    (tmp_path / ".aegis.yaml").write_text(NOTICED)
    w = await World(tmp_path, fake_claude).start()
    yield w
    await w.stop()


def sent(app, log_id) -> list[str]:
    records, _ = read_store(app.sessions.store_path(log_id))
    return [r["text"] for r in records if r.get("kind") == "send"]


async def test_the_first_send_carries_the_notice_and_the_second_does_not(noticed):
    s = await noticed.spawn()
    await turn(s, "add the retry")
    await turn(s, "and a test")
    first, second = sent(noticed.app, s.log_id)
    assert first.startswith("add the retry\n\n" + DISK_WARN)
    assert "ask the person what to do" in first  # the first person-opened asks
    assert second == "and a test"


async def test_a_tool_result_carries_the_notice_when_no_send_came_first(noticed):
    s = await noticed.spawn()
    said = await turn(s, mcp("plan_update", items=[{"text": "a", "state": "doing"}]))
    assert said.startswith("mcp ok: plan saved")
    assert DISK_WARN in said
    assert DISK_WARN not in await turn(s, mcp("session_list"))
    assert sent(noticed.app, s.log_id)[0].startswith("/mcp plan_update")


async def test_a_slash_command_goes_unchanged_and_the_next_prompt_carries_it(noticed):
    s = await noticed.spawn()
    await turn(s, "/sh true")
    await turn(s, "hello")
    command, prompt = sent(noticed.app, s.log_id)
    assert command == "/sh true"
    assert prompt.startswith("hello\n\n" + DISK_WARN)


async def test_a_worker_gets_no_asker_line(noticed):
    a = await noticed.spawn()
    said = await turn(a, mcp("queue_enqueue", queue="general", payload="summarize"))
    task_id = json.loads(said.removeprefix("mcp ok: ").split("\n\n")[0])["task_id"]
    await until(lambda: inbox(a), timeout=12, what="the callback")
    first = sent(noticed.app, noticed.app.queues.tasks[task_id].worker)[0]
    assert first.startswith("summarize\n\n" + DISK_WARN)
    assert "Do not ask anyone" in first and "ask the person" not in first


async def test_a_restart_does_not_repeat_a_notice(noticed):
    s = await noticed.spawn()
    await turn(s, "add the retry")
    await noticed.restart()
    s = noticed.session(s.log_id)
    await turn(s, "carry on")
    assert sent(noticed.app, s.log_id)[-1] == "carry on"


# -- the transcript ---------------------------------------------------------------
BLOCK = (
    NOTICE_HEADER + "disk · warn · 2026-10-18T10:00:00Z\n"
    "The disk holding aegis is 95% full: 3.9 GB free of 75 GB, measured just now."
)
CLEAR = NOTICE_HEADER + "disk · clear · 2026-10-18T11:00:00Z\nThe disk is back."


def test_split_notices_takes_every_block_off_the_end():
    assert split_notices("hello") == ("hello", [])
    assert split_notices(f"hello\n\n{BLOCK}\n\n{CLEAR}") == ("hello", [BLOCK, CLEAR])
    assert split_notices(BLOCK) == (BLOCK, [])  # a bare block is no carrier


def test_a_prompt_with_a_notice_folds_to_the_prompt_and_an_inbox_row():
    r = Rec()
    r.own("send", text=f"add the retry\n\n{BLOCK}")
    r.echo(f"add the retry\n\n{BLOCK}")
    f, ops = run(r)
    assert ops[0][0]["upsert"]["md"] == "add the retry"  # the pending row too
    user, notice = f.entries()
    assert (user["kind"], user["md"]) == ("user", "add the retry")
    assert notice["kind"] == "inbox" and notice["id"] == f"{user['id']}:notice:0"
    assert notice["title"].startswith("aegis:notice · disk · warn")
    assert notice["md"] == BLOCK


def test_a_tool_result_with_a_notice_keeps_the_notice_out_of_its_row():
    r = Rec()
    r.call("t1", "mcp__aegis__session_list", {})
    r.output("t1", f'[{{"handle": "a"}}]\n\n{BLOCK}')
    tool, notice = run(r)[0].entries()
    assert tool["detail"]["result"] == "1 item"
    assert NOTICE_HEADER not in tool["detail"]["tail"]
    assert (notice["kind"], notice["id"], notice["md"]) == (
        "inbox",
        "t1:notice:0",
        BLOCK,
    )


def test_a_monitor_wake_with_a_notice_is_two_inbox_rows():
    wake = "> from monitor:01M4 · ok · 2026-10-18T10:00:00Z\npytest finished"
    r = Rec()
    r.own("send", text=f"{wake}\n\n{BLOCK}")
    r.echo(f"{wake}\n\n{BLOCK}")
    woke, notice = run(r)[0].entries()
    assert (woke["kind"], woke["md"]) == ("inbox", wake)
    assert woke["title"].startswith("monitor:01M4")
    assert notice["kind"] == "inbox" and notice["md"] == BLOCK
