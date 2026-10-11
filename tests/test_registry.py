import json
import os
import signal
import time
from pathlib import Path

import pytest

import aegis.meta
from aegis import restarts
import aegis.registry
from aegis.registry import Registry
from aegis.roots import make_roots
from aegis.session import SpawnSpec

from .conftest import argv_of, until


class World:
    """One server's state directory, booted as many times as a test likes."""

    def __init__(self, tmp_path: Path, fake: str):
        (tmp_path / ".aegis.yaml").write_text("agents: {}\n")
        self.roots = make_roots(tmp_path, None)
        self.fake = fake
        self.published: list[tuple[str, list]] = []

    def registry(
        self, prev: restarts.Previous = restarts.CLEAN, bin: str | None = None
    ) -> Registry:
        self.published.clear()
        r = Registry(
            self.roots,
            lambda ch, ops: self.published.append((ch, ops)),
            bin or self.fake,
        )
        r.boot(prev)
        return r

    def spec(self) -> SpawnSpec:
        return SpawnSpec("opus", "opus", "high", "full", self.roots.config_root)

    def stores(self) -> dict[str, bytes]:
        d = self.roots.state_root / "transcripts"
        return {p.name: p.read_bytes() for p in sorted(d.glob("*.jsonl"))}


@pytest.fixture
def world(tmp_path, fake_claude):
    return World(tmp_path, fake_claude)


async def turn(s, text):
    await s.send(text)
    await until(lambda: s.status == "idle" and s.cost_usd, what=f"the turn {text!r}")


async def test_booting_idle_sessions_writes_and_publishes_nothing(world):
    r = world.registry()
    a, b = await r.spawn(world.spec()), await r.spawn(world.spec())
    await turn(a, "one")
    await turn(b, "two")
    await r.shutdown()
    before = world.stores()
    r2 = world.registry()
    assert world.stores() == before, "boot wrote to a store"
    assert world.published == [], "boot published"
    assert [s.status for s in r2.open_sessions()] == ["stopped", "stopped"]
    assert {s.handle for s in r2.open_sessions()} == {a.handle, b.handle}
    assert [s.title for s in r2.open_sessions()] == ["one", "two"]


async def test_a_session_working_at_shutdown_is_marked_once(world):
    r = world.registry()
    s = await r.spawn(world.spec())
    await s.send("/sleep 5")
    await until(lambda: any(e["kind"] == "tool" for e in s.entries()), what="the call")
    await r.shutdown()
    r2 = world.registry()
    (s2,) = r2.open_sessions()
    assert s2.entries()[-1]["summary"] == "the server stopped during a turn"
    await r2.shutdown()
    before = world.stores()
    r3 = world.registry()
    assert world.stores() == before
    (s3,) = r3.open_sessions()
    marks = [
        e for e in s3.entries() if e["summary"] == "the server stopped during a turn"
    ]
    assert len(marks) == 1


async def test_a_turn_cut_by_a_restart_boots_as_an_error(world):
    r = world.registry()
    s = await r.spawn(world.spec())
    await s.send("/sleep 5")
    await until(lambda: any(e["kind"] == "tool" for e in s.entries()), what="the call")
    await r.shutdown()
    r2 = world.registry()
    (s2,) = r2.open_sessions()
    assert s2.wire()["attention"] == "error"
    assert s2.wire()["attention_line"] == "the server stopped during a turn"
    await r2.shutdown()


async def test_boot_takes_the_standing_from_the_meta(world):
    sessions = world.roots.state_root / "sessions"
    sessions.mkdir(parents=True)
    standing = {
        "plan": [{"text": "write the test", "state": "doing"}],
        "did": "",
        "report": None,
        "turn_error": "",
    }
    meta = {
        "log_id": "l000",
        "handle": "h-0",
        "archived": False,
        "created_at": 0,
        "last_activity": 0,
        "profile": "opus",
        "cwd": str(world.roots.config_root),
        "standing": standing,
    }
    (sessions / "l000.json").write_text(json.dumps(meta))
    r = world.registry()
    (s,) = r.open_sessions()
    assert s.wire()["plan_now"] == "write the test"
    assert s.wire()["plan_clock"] is None


async def test_boot_takes_the_read_state_from_the_meta(world):
    sessions = world.roots.state_root / "sessions"
    sessions.mkdir(parents=True)
    meta = {
        "log_id": "l000",
        "handle": "h-0",
        "archived": False,
        "created_at": 0,
        "last_activity": 0,
        "profile": "opus",
        "cwd": str(world.roots.config_root),
        "unread": ["e5.0"],
        "last_read_at": 1234.0,
    }
    (sessions / "l000.json").write_text(json.dumps(meta))
    r = world.registry()
    (s,) = r.open_sessions()
    assert s.unread == {"e5.0"}
    assert s.wire()["unread"] == 1
    assert s.wire()["last_read_at"] == 1234.0


async def test_boot_reads_no_store_when_every_meta_is_there(world, monkeypatch):
    sessions = world.roots.state_root / "sessions"
    sessions.mkdir(parents=True)
    for i in range(300):
        meta = {
            "log_id": f"l{i:03}",
            "handle": f"h-{i}",
            "archived": i >= 20,
            "created_at": i,
            "last_activity": i,
            "profile": "opus",
            "cwd": str(world.roots.config_root),
        }
        (sessions / f"l{i:03}.json").write_text(json.dumps(meta))

    def boom(*a, **k):
        raise AssertionError("boot read a store")

    monkeypatch.setattr(aegis.meta, "read_store", boom)
    monkeypatch.setattr(aegis.registry, "read_store", boom)
    r = world.registry()
    assert len(r.open_sessions()) == 20 and len(r.archived) == 280


async def test_missing_and_damaged_metas_are_rebuilt_from_their_stores(world):
    r = world.registry()
    a, b = await r.spawn(world.spec()), await r.spawn(world.spec())
    await turn(a, "alpha prompt")
    await turn(b, "beta prompt")
    await r.shutdown()
    r.metas.path(a.log_id).unlink()
    r.metas.path(b.log_id).write_text("{broken")
    junk = world.roots.state_root / "transcripts" / "junk.jsonl"
    junk.write_text("not a store\n")
    r2 = world.registry()
    titles = sorted(m["title"] for m in r2.archived.values())
    assert titles == ["alpha prompt", "beta prompt"]
    assert all(m["resume_id"] for m in r2.archived.values())
    assert "junk" not in r2.archived and "junk" not in r2.sessions
    reopened = r2.reopen(a.log_id)
    await reopened.send("/recall")
    await until(lambda: reopened.status == "idle", what="the resumed turn")
    assert reopened.entries()[-2]["md"] == "earlier: alpha prompt"
    await r2.shutdown()


async def test_handles_never_repeat_across_archive_and_restart(world):
    r = world.registry()
    handles = set()
    for _ in range(4):
        s = await r.spawn(world.spec())
        handles.add(s.handle)
        await r.close(s.log_id)
    await r.shutdown()
    r2 = world.registry()
    s = await r2.spawn(world.spec())
    assert s.handle not in handles and len(handles) == 4
    await r2.shutdown()


async def test_archive_filters_and_pages(world):
    sessions = world.roots.state_root / "sessions"
    sessions.mkdir(parents=True)
    for i in range(10):
        meta = {
            "log_id": f"l{i}",
            "handle": f"h-{i}",
            "archived": True,
            "last_activity": float(i),
            "title": "deploy fix" if i % 2 else "write docs",
            "cwd": "/w",
        }
        (sessions / f"l{i}.json").write_text(json.dumps(meta))
    r = world.registry()
    items, total, last = r.archive(None, 3, None)
    assert [m["log_id"] for m in items] == ["l9", "l8", "l7"] and total == 10
    assert [m["log_id"] for m in r.archive(None, 3, last)[0]] == ["l6", "l5", "l4"]
    items, total, _ = r.archive("DEPLOY", 50, None)
    assert [m["log_id"] for m in items] == ["l9", "l7", "l5", "l3", "l1"]
    assert total == 5


async def test_a_failed_spawn_leaves_nothing_on_disk(world, tmp_path):
    world.fake = str(tmp_path / "no-claude")
    r = world.registry()
    with pytest.raises(FileNotFoundError):
        await r.spawn(world.spec())
    assert r.open_sessions() == [] and world.stores() == {}
    assert not list((world.roots.state_root / "sessions").glob("*.json"))


async def test_the_priming_reaches_every_start_from_the_record_not_the_config(world):
    # The World's .aegis.yaml has no agents at all: a resume that read the
    # priming from the config would lose it.
    r = world.registry()
    spec = SpawnSpec(
        "rev", "opus", "max", "read", world.roots.config_root, priming="You review."
    )
    s = await r.spawn(spec)
    argv = await argv_of(s)
    assert argv[argv.index("--append-system-prompt") + 1] == "You review."
    await r.shutdown()
    r2 = world.registry()
    (s2,) = r2.open_sessions()
    assert s2.spec.priming == "You review."
    argv = await argv_of(s2)
    assert "--resume" in argv
    assert argv[argv.index("--append-system-prompt") + 1] == "You review."
    await r2.shutdown()


async def test_no_priming_and_no_mcp_means_no_system_prompt(world):
    r = world.registry()
    s = await r.spawn(world.spec())
    assert "--append-system-prompt" not in await argv_of(s)
    await r.shutdown()


async def test_a_spawn_records_its_agent_overrides_and_spawner(world):
    r = world.registry()
    spec = SpawnSpec(
        "opus",
        "sonnet",
        "high",
        "full",
        world.roots.config_root,
        overridden=("model",),
        spawned_by="parent-log",
        priming="secret text",
    )
    s = await r.spawn(spec)
    w = s.wire()
    assert (w["agent"], w["harness"], w["overridden"], w["spawned_by"]) == (
        "opus",
        "claude-code",
        ["model"],
        "parent-log",
    )
    assert "priming" not in w and s.meta()["priming"] == "secret text"
    assert s.entries()[0]["summary"].startswith("spawned opus* · sonnet")
    await r.shutdown()


async def test_a_meta_from_before_agents_boots_with_its_agent_name(world):
    sessions = world.roots.state_root / "sessions"
    sessions.mkdir(parents=True)
    old = {
        "log_id": "old",
        "handle": "old-one",
        "created_at": 1,
        "last_activity": 1,
        "profile": "opus",
        "model": "opus",
        "effort": "high",
        "permission": "full",
        "cwd": str(world.roots.config_root),
    }
    (sessions / "old.json").write_text(json.dumps(old))
    r = world.registry()
    (s,) = r.open_sessions()
    assert (s.spec.agent, s.spec.harness, s.spec.priming) == (
        "opus",
        "claude-code",
        None,
    )
    assert s.wire()["agent"] == "opus"
    # Closed, it is found in the archive by its agent's name (Step 9).
    await r.close("old")
    assert [m["log_id"] for m in r.archive("opus", 10, None)[0]] == ["old"]


async def test_the_priming_never_leaves_the_server_from_the_archive(world):
    r = world.registry()
    spec = SpawnSpec(
        "rev", "opus", "max", "read", world.roots.config_root, priming="secret text"
    )
    s = await r.spawn(spec)
    await r.close(s.log_id)
    (listed,) = r.archive(None, 10, None)[0]
    assert listed["agent"] == "rev" and "priming" not in listed
    assert "priming" not in r.rename(s.log_id, None, "renamed")
    # The stored meta keeps it, so a reopened session resumes with it.
    assert r.archived[s.log_id]["priming"] == "secret text"


def test_a_meta_from_before_the_rename_still_resumes(world):
    from aegis.meta import MetaStore

    MetaStore(world.roots.state_root / "sessions").write(
        {
            "log_id": "old-1",
            "handle": "old-owl",
            "agent": "opus",
            "harness": "claude-code",
            "model": "opus",
            "effort": "high",
            "permission": "full",
            "cwd": str(world.roots.config_root),
            "claude_session_id": "cs-old",
            "created_at": 1.0,
        }
    )
    r = world.registry()
    assert r.sessions["old-1"].resume_id == "cs-old"
    assert r.sessions["old-1"].meta()["resume_id"] == "cs-old"


async def test_a_spawn_whose_harness_fails_to_start_leaves_nothing(
    tmp_path, fake_opencode, monkeypatch
):
    (tmp_path / ".aegis.yaml").write_text("agents: {}\n")
    monkeypatch.setenv("FAKE_OPENCODE_DIE", "1")
    published = []
    r = Registry(
        make_roots(tmp_path, None),
        lambda ch, ops: published.append((ch, ops)),
        "claude",
        opencode_bin=fake_opencode,
    )
    r.boot()
    spec = SpawnSpec(
        "d", "opencode-go/fake-pro", "high", "full", tmp_path, harness="opencode"
    )
    with pytest.raises(OSError):
        await r.spawn(spec)
    assert r.sessions == {}
    assert not list((r.roots.state_root / "transcripts").glob("*.jsonl"))


async def test_the_archive_list_ships_no_standing_or_priming(world):
    sessions = world.roots.state_root / "sessions"
    sessions.mkdir(parents=True)
    meta = {
        "log_id": "l000",
        "handle": "h-0",
        "archived": True,
        "created_at": 0,
        "last_activity": 0,
        "profile": "opus",
        "cwd": str(world.roots.config_root),
        "priming": "You review.",
        "standing": {"plan": [{"text": "x", "state": "doing"}], "did": ""},
    }
    (sessions / "l000.json").write_text(json.dumps(meta))
    r = world.registry()
    (m,) = r.archive(None, 10, None)[0]
    assert m["log_id"] == "l000"
    assert "standing" not in m and "priming" not in m


async def test_the_meta_names_the_running_harness_and_its_last_exit(world):
    r = world.registry()
    s = await r.spawn(world.spec())
    m = json.loads(r.metas.path(s.log_id).read_text())
    assert m["pid"] == s.pid and m["pid_started"] == restarts.started_at(s.pid)
    await s.send("/exit 3")
    await until(lambda: s.status == "stopped", what="the exit")
    m = json.loads(r.metas.path(s.log_id).read_text())
    assert m["pid"] is None and m["last_exit"]["code"] == 3 and m["last_exit"]["at"] > 0
    await r.shutdown()


async def test_a_finished_turn_clears_the_restart_count(world):
    r = world.registry()
    s = await r.spawn(world.spec())
    s.restart_continues = 2
    await turn(s, "hello")
    assert s.restart_continues == 0
    assert json.loads(r.metas.path(s.log_id).read_text())["restart_continues"] == 0
    await r.shutdown()


def crash(r):
    for s in r.sessions.values():
        s._stopping = True
    r.metas.flush()


async def mid_turn(world, r):
    s = await r.spawn(world.spec())
    await s.send("/sleep 30")
    await until(lambda: any(e["kind"] == "tool" for e in s.entries()), what="the call")
    return s


def said(s, text):
    return any(text in (e.get("md") or "") for e in s.entries() if e["kind"] == "inbox")


async def test_a_turn_a_clean_restart_cut_continues_and_finishes(world):
    r = world.registry()
    s = await mid_turn(world, r)
    await r.shutdown()
    r2 = world.registry()
    assert r2.continuing == {s.log_id: False}
    await r2.continue_after_boot()
    s2 = r2.sessions[s.log_id]
    await until(
        lambda: s2.status == "idle" and said(s2, restarts.RESTARTED),
        timeout=8,
        what="the continued turn",
    )
    assert s2.restart_continues == 0
    await r2.shutdown()


async def test_a_harness_that_failed_on_its_own_is_not_continued(world):
    r = world.registry()
    s = await r.spawn(world.spec())
    await s.send("/exit 1")
    await until(lambda: s.status == "stopped", what="the exit")
    await r.shutdown()
    r2 = world.registry(restarts.Previous(True, time.time()))
    assert r2.continuing == {} and r2.declined == set()
    await r2.shutdown()


async def test_an_exit_by_sigterm_just_before_a_clean_stop_counts_as_the_stop(world):
    r = world.registry()
    s = await mid_turn(world, r)
    os.kill(s.pid, signal.SIGTERM)
    await until(lambda: s.status == "stopped", what="the SIGTERM exit")
    await r.shutdown()
    # Long before the stop: not the stop's doing.
    far = world.registry(restarts.Previous(True, time.time() + 60))
    assert far.continuing == {}
    await far.shutdown()
    r2 = world.registry(restarts.Previous(True, time.time()))
    assert r2.continuing == {s.log_id: False}
    await r2.shutdown()


async def test_after_a_crash_the_leftover_is_ended_and_the_turn_continues(world):
    r = world.registry()
    s = await mid_turn(world, r)
    pid = s.pid
    crash(r)
    r2 = world.registry(restarts.Previous(False))
    await until(lambda: restarts.started_at(pid) is None, what="the leftover's end")
    assert r2.continuing == {s.log_id: True}
    s2 = r2.sessions[s.log_id]
    assert s2.entries()[-1]["summary"] == "aegis stopped unexpectedly during a turn"
    await r2.continue_after_boot()
    await until(
        lambda: s2.status == "idle" and said(s2, restarts.CRASHED),
        timeout=8,
        what="the continued turn",
    )
    await r2.shutdown()


@pytest.mark.parametrize(
    "prev,count",
    [(restarts.Previous(False), 1), (restarts.CLEAN, 2)],
    ids=["second crash", "third restart"],
)
async def test_a_turn_cut_too_often_waits_for_a_person_with_its_messages(
    world, prev, count
):
    r = world.registry()
    s = await mid_turn(world, r)
    pid = s.pid
    await s.deliver("> from agent:peer · 2026-10-10T12:00:00Z", "the held body")
    if prev.clean:
        await r.shutdown()
    else:
        crash(r)
    meta = json.loads(r.metas.path(s.log_id).read_text())
    meta["restart_continues"] = count
    r.metas.write(meta)
    r2 = world.registry(prev)
    await until(lambda: restarts.started_at(pid) is None, what="no harness left")
    assert r2.continuing == {} and r2.declined == {s.log_id}
    s2 = r2.sessions[s.log_id]
    assert s2.wire()["attention_line"] == restarts.DECLINED
    await r2.continue_after_boot()
    assert s2.status == "stopped" and [m["body"] for m in s2.held] == ["the held body"]
    await r2.shutdown()


async def test_held_messages_go_out_with_the_continue(world):
    r = world.registry()
    s = await mid_turn(world, r)
    await s.deliver("> from agent:peer · 2026-10-10T12:00:00Z", "the held body")
    await r.shutdown()
    r2 = world.registry()
    await r2.continue_after_boot()
    s2 = r2.sessions[s.log_id]
    await until(
        lambda: (
            s2.status == "idle"
            and said(s2, "the held body")
            and said(s2, restarts.RESTARTED)
        ),
        timeout=8,
        what="one turn with both",
    )
    assert s2.held == []
    await r2.shutdown()


async def test_a_continue_that_cannot_start_keeps_its_messages(world):
    r = world.registry()
    a = await mid_turn(world, r)
    b = await mid_turn(world, r)
    await r.shutdown()
    r2 = world.registry(bin=str(world.roots.state_root / "no-such-claude"))
    await r2.continue_after_boot()  # raises nothing, and tries both
    for s in (r2.sessions[a.log_id], r2.sessions[b.log_id]):
        assert s.status == "stopped"
        assert [m["body"] for m in s.held] == [restarts.RESTARTED]
    await r2.shutdown()
