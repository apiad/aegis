import json
import os
from pathlib import Path

import pytest

from aegis.claude.process import ControlError
from aegis.harness import harness_for
from aegis.meta import MetaStore
from aegis.session import Host, Session, SpawnSpec
from aegis.transcript.entries import fold_records
from aegis.transcript.store import Store, read_store

from .conftest import until


class CX:
    def __init__(self, tmp_path: Path, fake: str):
        self.published: list[tuple[str, list[dict]]] = []
        self.path = tmp_path / "state" / "transcripts" / "log-cx.jsonl"
        self.metas = MetaStore(tmp_path / "state" / "sessions")
        self.tmp_path, self.fake = tmp_path, fake
        self.log = tmp_path / "fake-cursor.log"
        self.session = self.make()

    def make(self, **meta) -> Session:
        return Session(
            log_id="log-cx",
            spec=SpawnSpec(
                "cx", "fake-pro", "", "full", self.tmp_path, harness="cursor"
            ),
            handle="quiet-heron",
            store=Store(self.path),
            stderr_path=self.tmp_path / "state" / "stderr" / "log-cx.log",
            claude_bin="claude-unused",
            cursor_bin=self.fake,
            publish=lambda ch, ops: self.published.append((ch, ops)),
            metas=self.metas,
            **meta,
        )

    def prose(self) -> list[str]:
        return [e["md"] for e in self.session.entries() if e["kind"] == "prose"]

    def done_lines(self) -> int:
        return sum(
            e["summary"].startswith(("done in", "interrupted"))
            for e in self.session.entries()
        )

    async def turn(self, text: str) -> None:
        before = self.done_lines()
        await self.session.send(text)
        await until(
            lambda: self.done_lines() > before and self.session.status == "idle",
            timeout=5,
            what=f"{text!r}",
        )

    def pids(self, tag: str) -> list[int]:
        return [
            int(ln.split()[1])
            for ln in self.log.read_text().splitlines()
            if ln.startswith(tag + " ")
        ]

    def refold_matches(self) -> bool:
        records, damaged = read_store(self.path)
        return (
            damaged == 0 and fold_records(records).entries() == self.session.entries()
        )

    def patches_rebuild_entries(self) -> bool:
        shown: dict[str, dict] = {}
        for ch, ops in self.published:
            if ch != self.session.channel:
                continue
            for op in ops:
                if "upsert" in op:
                    shown[op["upsert"]["id"]] = op["upsert"]
                else:
                    shown.pop(op["remove"], None)
        return list(shown.values()) == self.session.snapshot()["entries"]


def alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    try:
        with open(f"/proc/{pid}/stat") as f:
            return f.read().split()[2] != "Z"
    except FileNotFoundError:
        return False


@pytest.fixture
async def cx(tmp_path, fake_cursor):
    h = CX(tmp_path, fake_cursor)
    await h.session.start()
    yield h
    await h.session.stop()
    assert h.refold_matches(), "live entries differ from a fold of the store"
    assert h.patches_rebuild_entries(), "the published patches do not add up"


async def test_a_prompt_runs_a_turn_with_the_model_and_the_session(cx):
    await cx.turn("hello")
    s = cx.session
    assert cx.prose() == ["you said: hello"]
    assert s.model_id == "fake-pro" and s.resume_id
    assert s.context_tokens is None  # initialize carries no token counts (measured)
    assert s.wire()["harness_label"] == "Cursor Agent"
    assert any(e["summary"] == "Cursor Agent · fake-pro" for e in s.entries())


async def test_text_streams_before_the_turn_ends(cx):
    await cx.session.send("/stream 4")
    await until(lambda: any("chunk1" in m for m in cx.prose()), what="the first chunk")
    assert not any("chunk4" in m for m in cx.prose()), "drawn while it streams"
    await until(lambda: cx.done_lines() == 1, timeout=5, what="the end")


async def test_the_store_keeps_every_chunk_because_cursor_never_repeats_the_text(cx):
    await cx.turn("/stream 3")
    records, damaged = read_store(cx.path)
    assert damaged == 0
    updates = []
    for r in records:
        if r.get("src") != "cursor":
            continue
        line = json.loads(r["line"])
        if line.get("method") == "session/update":
            updates.append(line["params"]["update"].get("sessionUpdate"))
    # 3 streamed chunks + "streamed 3" in two words: each line is the only
    # copy of its text (Cursor sends no closing update), so the store keeps them.
    assert updates.count("agent_message_chunk") == 5
    prose = [e["md"] for e in fold_records(records).entries() if e["kind"] == "prose"]
    assert prose == ["chunk1 chunk2 chunk3 streamed 3"]


async def test_interrupt_ends_the_turn_and_marks_the_call(cx):
    await cx.session.send("/sleep 5")
    await until(
        lambda: any(e["kind"] == "tool" for e in cx.session.entries()), what="the call"
    )
    await cx.session.interrupt()
    await until(lambda: cx.session.status == "idle", timeout=5, what="idle")
    (t,) = [e for e in cx.session.entries() if e["kind"] == "tool"]
    assert t["status"] == "err"
    assert cx.done_lines() == 1  # the interrupted turn still gets its line


async def test_stop_and_resume_keep_the_conversation(cx):
    await cx.turn("remember PELICAN")
    sid = cx.session.resume_id
    await cx.session.stop()
    await cx.turn("/recall")
    assert cx.session.resume_id == sid and "PELICAN" in cx.prose()[-1]


async def test_a_resume_replays_nothing_the_store_already_has(cx):
    """A real ``session/load`` re-announces the conversation as whole-text
    chunks before it answers (measured 2026-10-19); the store already holds
    those lines, so a resume that replays them adds no entry and no line."""
    await cx.turn("remember PELICAN")

    def shown() -> list:
        return [
            (e["kind"], e.get("md"))
            for e in cx.session.entries()
            if e["kind"] in ("prose", "thinking", "tool", "user")
        ]

    md = shown()
    records, _ = read_store(cx.path)
    chunks = [str(r) for r in records if "message_chunk" in str(r)]

    await cx.session.stop()
    await cx.session.start()  # spawn and load, before any prompt

    # spawn and session lines of its own are not replay; history is
    assert shown() == md
    records, _ = read_store(cx.path)
    assert [str(r) for r in records if "message_chunk" in str(r)] == chunks


async def test_a_resume_cursor_lost_starts_anew_and_says_so(cx):
    await cx.turn("first")
    await cx.session.stop()
    cx.session.resume_id = "01a10000-0000-7000-8000-000000000000"
    await cx.turn("second")
    assert cx.session.resume_id != "01a10000-0000-7000-8000-000000000000"
    assert any(
        "no longer had this conversation" in e["summary"] for e in cx.session.entries()
    )


async def test_permission_requests_are_answered_by_the_level(cx):
    await cx.turn("/ask")
    assert cx.prose()[-1] == "permission execute: allow-once"
    await cx.session.configure(permission="write")
    await cx.turn("/ask")
    assert cx.prose()[-1] == "permission execute: reject-once"
    await cx.turn("/ask edit")
    assert cx.prose()[-1] == "permission edit: allow-once"
    await cx.session.configure(permission="read")
    await cx.turn("/ask edit")
    assert cx.prose()[-1] == "permission edit: reject-once"
    await cx.session.configure(permission="auto")
    await cx.turn("/ask")
    assert cx.prose()[-1] == "permission execute: allow-once"
    # never "always": that option would outlive the session in Cursor's config
    assert all("always" not in m for m in cx.prose())


async def test_model_and_permission_apply_to_the_live_child(cx):
    pid = cx.session.pid
    await cx.session.configure(model="fake-flash")
    await cx.turn("/model")
    assert cx.prose()[-1] == "model: fake-flash mode: agent"
    # the configured value is named at once; the resolved id waits for the
    # next init, as it does for every harness (#243)
    assert cx.session.wire()["model"] == "fake-flash"
    assert cx.session.model_id is None
    await cx.session.configure(permission="read")
    await cx.turn("/model")
    assert cx.prose()[-1] == "model: fake-flash mode: plan"
    assert cx.session.pid == pid  # a setting, not a restart
    # the next open's session line is the init that names the resolved id
    await cx.session.stop()
    await cx.turn("/model")
    assert cx.session.model_id == "fake-flash" and cx.session.pid != pid


async def test_an_effort_is_refused_unless_the_model_carries_it(cx):
    with pytest.raises(ControlError, match="carries the effort"):
        await cx.session.configure(effort="high")
    await cx.session.configure(model="fake-reason")
    await cx.session.configure(effort="high")  # the id already carries it
    assert cx.session.spec.effort == "high"


async def test_a_model_the_catalog_does_not_know_is_refused_as_cursor_refuses_it(cx):
    with pytest.raises(ControlError, match="Invalid model value"):
        await cx.session.configure(model="nope")


async def test_the_catalog_lists_the_models_commands_and_efforts(cx):
    cat = await cx.session.catalog_task
    assert [m.value for m in cat.models] == ["fake-pro", "fake-flash", "fake-reason"]
    assert cat.models[2].resolved == "fake-reason[thinking=true,effort=high]"
    assert cat.models[2].efforts == ("high",)
    assert [c["name"] for c in cat.commands] == ["sleep", "stream"]


class TokenHost(Host):
    def spawn_args(self, session):
        return ("http://127.0.0.1:9/mcp", "tok-secret"), "primer"


async def test_the_token_travels_in_mcp_servers_the_argv_and_the_env(
    tmp_path, fake_cursor
):
    h = CX(tmp_path, fake_cursor)
    h.session = h.make(host=TokenHost())
    await h.session.start()
    await h.turn("/servers")
    servers = h.prose()[-1].removeprefix("servers: ")
    assert "http://127.0.0.1:9/mcp" in servers and "X-Aegis-Session" in servers
    assert "tok-secret" in servers  # a header of session/new's mcpServers
    await h.turn("/argv")
    assert "tok-secret" not in h.prose()[-1] and '"acp"' in h.prose()[-1]
    await h.turn("/env")
    assert h.prose()[-1] == "env: tok-secret"
    await h.session.stop()


async def test_a_prompt_sent_mid_turn_waits_and_runs_when_the_turn_ends(cx):
    await cx.session.send("/sleep 0.6")
    await until(
        lambda: any(e["kind"] == "tool" for e in cx.session.entries()), what="the call"
    )
    await cx.session.send("/stream 5")
    # queued, not sent: the running turn is still the only one
    assert cx.done_lines() == 0
    await until(lambda: cx.done_lines() == 1, timeout=5, what="the first turn")
    await until(
        lambda: any("chunk1" in m for m in cx.prose()),
        timeout=5,
        what="the queued turn streaming",
    )
    # its echo, not a send, flips the session back to working (a second
    # prompt sent mid-turn would cancel the one running; measured)
    assert cx.session.status == "working"
    await until(
        lambda: cx.done_lines() == 2 and cx.session.status == "idle",
        timeout=8,
        what="both turns read",
    )
    assert [e["status"] for e in cx.session.entries() if e["kind"] == "user"] == [
        "ok",
        "ok",
    ]
    assert cx.prose()[-1] == "chunk1 chunk2 chunk3 chunk4 chunk5 streamed 5"


async def test_a_line_over_the_stdio_limit_reaches_the_store(cx):
    await cx.turn("/big 200")  # one 200 KB rawOutput on one line
    (t,) = [e for e in cx.session.entries() if e["kind"] == "tool"]
    assert t["status"] == "ok"
    assert (t["detail"]["tail"] or "").endswith("x")  # capped, but the end
    records, damaged = read_store(cx.path)
    assert damaged == 0
    assert any(len(r.get("line") or "") >= 200 * 1024 for r in records)
    assert cx.prose()[-1] == "big done"


async def test_an_exit_leaves_the_session_stopped(cx):
    await cx.session.send("/exit 1")
    await until(lambda: cx.session.status == "stopped", timeout=5, what="stopped")
    assert any(
        e["summary"] == "Cursor Agent exited with code 1" for e in cx.session.entries()
    )


async def test_stop_leaves_no_child_the_fake_started(cx):
    await cx.turn("hello")
    pids = cx.pids("START")
    await cx.session.stop()
    await until(
        lambda: not any(alive(p) for p in pids), timeout=8, what="every child gone"
    )


async def test_a_child_that_ignores_stdin_is_ended_by_its_group(
    tmp_path, fake_cursor, monkeypatch
):
    monkeypatch.setenv("FAKE_CURSOR_HANG", "1")
    monkeypatch.setattr(
        "aegis.cursor.process.TERM_GRACE_S", 0.5
    )  # the gate caps a test at 3 s
    h = CX(tmp_path, fake_cursor)
    await h.session.start()
    await h.turn("hello")
    pids = h.pids("START")
    await h.session.stop()
    await until(
        lambda: not any(alive(p) for p in pids), timeout=8, what="the group gone"
    )


async def test_the_title_reaches_the_session(cx):
    await cx.turn("/title Fix the door")
    assert cx.session.title == "Fix the door"


async def test_a_probe_reads_the_catalog_with_no_turn(tmp_path, fake_cursor):
    h = harness_for(
        "cursor", "claude-unused", "opencode-unused", "codex-unused", fake_cursor
    )
    cat = await h.probe(
        SpawnSpec("probe", "", "", "read", tmp_path, harness="cursor"),
        tmp_path / "probe-stderr.log",
    )
    assert [m.value for m in cat.models] == ["fake-pro", "fake-flash", "fake-reason"]
    assert [c["name"] for c in cat.commands] == ["sleep", "stream"]
