import json
import os
from pathlib import Path

import pytest

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
        self.log = tmp_path / "fake-codex.log"
        self.session = self.make()

    def make(self, **meta) -> Session:
        return Session(
            log_id="log-cx",
            spec=SpawnSpec(
                "cx", "openai/fake-pro", "high", "full", self.tmp_path, harness="codex"
            ),
            handle="quiet-okapi",
            store=Store(self.path),
            stderr_path=self.tmp_path / "state" / "stderr" / "log-cx.log",
            claude_bin="claude-unused",
            codex_bin=self.fake,
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
        await until(lambda: self.done_lines() > before and self.session.status == "idle",
                    timeout=5, what=f"{text!r}")  # fmt: skip

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
async def cx(tmp_path, fake_codex):
    h = CX(tmp_path, fake_codex)
    await h.session.start()
    yield h
    await h.session.stop()
    assert h.refold_matches(), "live entries differ from a fold of the store"
    assert h.patches_rebuild_entries(), "the published patches do not add up"


async def test_a_prompt_runs_a_turn_with_context_and_model(cx):
    await cx.turn("hello")
    s = cx.session
    assert cx.prose() == ["you said: hello"]
    assert s.context_tokens == 1030 and s.context_window == 258400
    assert s.model_id == "openai/fake-pro" and s.resume_id
    assert s.wire()["harness_label"] == "Codex"
    assert any(e["summary"] == "Codex 0.162.1 · openai/fake-pro" for e in s.entries())


async def test_text_streams_before_the_turn_ends(cx):
    await cx.session.send("/stream 4")
    await until(lambda: any("chunk1" in m for m in cx.prose()), what="the first chunk")
    assert not any("chunk4" in m for m in cx.prose()), "drawn while it streams"
    await until(lambda: cx.done_lines() == 1, timeout=5, what="the end")


async def test_no_delta_line_is_stored(cx):
    await cx.turn("/stream 3")
    stored = [
        json.loads(r["line"])["method"]
        for r in read_store(cx.path)[0]
        if r.get("src") == "codex"
    ]
    assert stored and not any(m.endswith("/delta") or "Delta" in m for m in stored)


async def test_interrupt_ends_the_turn_and_marks_the_call(cx):
    await cx.session.send("/sleep 5")
    await until(
        lambda: any(e["kind"] == "tool" for e in cx.session.entries()), what="the call"
    )
    await cx.session.interrupt()
    await until(lambda: cx.session.status == "idle", timeout=5, what="idle")
    (t,) = [e for e in cx.session.entries() if e["kind"] == "tool"]
    assert t["status"] == "err"


async def test_stop_and_resume_keep_the_conversation(cx):
    await cx.turn("remember PELICAN")
    sid = cx.session.resume_id
    await cx.session.stop()
    await cx.turn("/recall")
    assert cx.session.resume_id == sid and "PELICAN" in cx.prose()[-1]


async def test_a_resume_codex_lost_starts_anew_and_says_so(cx):
    await cx.turn("first")
    await cx.session.stop()
    cx.session.resume_id = "01a10000-0000-7000-8000-000000000000"
    await cx.turn("second")
    assert cx.session.resume_id != "01a10000-0000-7000-8000-000000000000"
    assert any(
        "no longer had this conversation" in e["summary"] for e in cx.session.entries()
    )


async def test_stop_leaves_nothing_the_child_started(cx):
    await cx.turn("hello")
    pids = cx.pids("START") + cx.pids("LEASE")
    await cx.session.stop()
    await until(
        lambda: not any(alive(p) for p in pids), timeout=8, what="every child gone"
    )


async def test_a_child_that_ignores_stdin_is_ended_by_its_group(
    tmp_path, fake_codex, monkeypatch
):
    monkeypatch.setenv("FAKE_CODEX_HANG", "1")
    monkeypatch.setattr(
        "aegis.codex.process.TERM_GRACE_S", 0.5
    )  # the gate caps a test at 3 s
    h = CX(tmp_path, fake_codex)
    await h.session.start()
    await h.turn("hello")
    pids = h.pids("START") + h.pids("LEASE")
    await h.session.stop()
    await until(
        lambda: not any(alive(p) for p in pids), timeout=15, what="the group gone"
    )


async def test_an_exit_kills_what_the_child_left_and_the_resume_works(cx):
    await cx.turn("remember OKAPI")
    lease = cx.pids("LEASE")[-1]
    await cx.session.send("/exit 3")
    await until(lambda: cx.session.status == "stopped", timeout=5, what="stopped")
    await until(lambda: not alive(lease), timeout=5, what="the lease holder gone")
    await cx.turn("/recall")
    assert "OKAPI" in cx.prose()[-1]


class TokenHost(Host):
    def spawn_args(self, session):
        return ("http://127.0.0.1:9/mcp", "tok-secret"), "primer"


async def test_the_token_reaches_the_child_only_by_its_environment(
    tmp_path, fake_codex
):
    h = CX(tmp_path, fake_codex)
    h.session = h.make(host=TokenHost())
    await h.session.start()
    await h.turn("/argv")
    assert "tok-secret" not in h.prose()[-1] and "AEGIS_SESSION_TOKEN" in h.prose()[-1]
    await h.turn("/env")
    assert h.prose()[-1] == "env: tok-secret"
    await h.session.stop()


async def test_a_request_from_codex_is_refused_and_the_turn_goes_on(cx):
    await cx.turn("/ask")
    assert cx.prose()[-1] == "asked"


async def test_a_line_over_64_kb_reaches_the_store(cx):
    await cx.turn("/big 200")
    (t,) = [e for e in cx.session.entries() if e["kind"] == "tool"]
    assert t["status"] == "ok" and cx.prose()[-1] == "big done"


async def test_a_subagent_shows_as_a_task_with_steps(cx):
    await cx.turn("/spawn")
    (t,) = [e for e in cx.session.entries() if e["title"] == "Task"]
    assert t["status"] == "ok" and t["detail"]["steps"] > 0
    assert cx.session.context_tokens == 1030  # the child's usage is not the context


async def test_an_exit_leaves_the_session_stopped(cx):
    await cx.session.send("/exit 1")
    await until(lambda: cx.session.status == "stopped", timeout=5, what="stopped")
    assert any("Codex exited with code 1" in e["summary"] for e in cx.session.entries())


async def test_a_prompt_sent_mid_turn_is_steered_into_the_same_turn(cx):
    await cx.session.send("/sleep 0.6")
    await until(
        lambda: any(e["kind"] == "tool" for e in cx.session.entries()), what="the call"
    )
    await cx.session.send("steer this")
    await until(
        lambda: cx.done_lines() == 1 and cx.session.status == "idle",
        timeout=5,
        what="idle",
    )
    assert [e["status"] for e in cx.session.entries() if e["kind"] == "user"] == [
        "ok",
        "ok",
    ]
    assert "also: steer this" in cx.prose()[-1]


async def test_a_steer_that_meets_an_ended_turn_starts_a_new_one(cx):
    await cx.turn("first")
    cx.session._proc._turn = "a-turn-that-ended"  # the race: our id is stale
    await cx.turn("second")
    assert cx.prose()[-1] == "you said: second"


async def test_model_effort_and_permission_apply_from_the_next_turn_without_a_restart(
    cx,
):
    pid = cx.session.pid
    await cx.session.configure(model="openai/fake-flash", permission="read")
    await cx.turn("/body")
    body = json.loads(cx.prose()[-1].removeprefix("body: "))
    assert (
        body["model"] == "fake-flash" and "effort" not in body
    )  # fake-flash has no efforts
    assert body["sandboxPolicy"] == {"type": "readOnly", "networkAccess": False}
    await cx.session.configure(model="openai/fake-pro", effort="low")
    await cx.turn("/body")
    body = json.loads(cx.prose()[-1].removeprefix("body: "))
    assert body["model"] == "fake-pro" and body["effort"] == "low"
    assert cx.session.pid == pid


async def test_a_new_provider_restarts_the_child_on_the_same_thread(cx):
    await cx.turn("remember HERON")
    sid, pid = cx.session.resume_id, cx.session.pid
    await cx.session.configure(model="other/fake-x")
    await cx.turn("/recall")
    assert cx.session.pid != pid and cx.session.resume_id == sid
    assert "HERON" in cx.prose()[-1]


async def test_a_skill_shows_as_typed_and_runs(cx):
    await cx.turn("/bash echo hi => hi")
    (u,) = [e for e in cx.session.entries() if e["kind"] == "user"]
    assert u["md"] == "/bash echo hi => hi"
    (t,) = [e for e in cx.session.entries() if e["kind"] == "tool"]
    assert t["status"] == "ok"


async def test_compact_and_review_are_codex_commands(cx):
    await cx.turn("hello")
    await cx.turn("/compact")
    await cx.turn("/review the README")
    users = [e["md"] for e in cx.session.entries() if e["kind"] == "user"]
    assert users == ["hello", "/compact", "/review the README"]
    assert cx.prose()[-1] == "you said: review: the README"


async def test_an_unknown_command_fails_the_send_and_ends_the_turn(cx):
    with pytest.raises(Exception):
        await cx.session.send("/nosuch")
    await until(lambda: cx.session.status == "idle", timeout=5, what="idle")


async def test_the_catalog_has_the_fakes_models_skills_and_codex_commands(cx):
    cat = await cx.session.catalog_task
    assert [m.value for m in cat.models][:2] == ["openai/fake-pro", "openai/fake-flash"]
    names = [c["name"] for c in cat.commands]
    assert names[:2] == ["compact", "review"] and "sleep" in names


async def test_a_free_model_shows_zero_cost(tmp_path, fake_codex):
    h = CX(tmp_path, fake_codex)
    h.session.spec = h.session.spec.__class__(
        "cx", "openrouter/fake:free", "", "full", tmp_path, harness="codex"
    )
    await h.session.start()
    await h.turn("hello")
    assert h.session.cost_usd == 0
    await h.session.stop()
