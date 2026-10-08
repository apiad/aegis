import asyncio
import os
from pathlib import Path

import pytest

from aegis.meta import MetaStore
from aegis.session import Session, SpawnSpec
from aegis.transcript.entries import fold_records
from aegis.transcript.store import Store, read_store
from aegis.transcript.wire import wire

from .conftest import until


class OC:
    def __init__(self, tmp_path: Path, fake: str):
        self.published: list[tuple[str, list[dict]]] = []
        self.path = tmp_path / "state" / "transcripts" / "log-oc.jsonl"
        self.metas = MetaStore(tmp_path / "state" / "sessions")
        self.tmp_path, self.fake = tmp_path, fake
        self.session = self.make()

    def make(self, **meta) -> Session:
        return Session(
            log_id="log-oc",
            spec=SpawnSpec(
                "deepseek",
                "opencode-go/fake-pro",
                "high",
                "full",
                self.tmp_path,
                harness="opencode",
            ),  # fmt: skip
            handle="quiet-okapi",
            store=Store(self.path),
            stderr_path=self.tmp_path / "state" / "stderr" / "log-oc.log",
            claude_bin="claude-unused",
            opencode_bin=self.fake,
            publish=lambda ch, ops: self.published.append((ch, ops)),
            metas=self.metas,
            **meta,
        )

    def statuses(self) -> list[str]:
        out: list[str] = []
        for ch, ops in self.published:
            if ch == "sessions":
                for op in ops:
                    st = op["upsert"]["state"]
                    if not out or out[-1] != st:
                        out.append(st)
        return out

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
            timeout=5, what=f"{text!r}",
        )  # fmt: skip

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
        return list(shown.values()) == [wire(e) for e in self.session.entries()]


@pytest.fixture
async def oc(tmp_path, fake_opencode):
    h = OC(tmp_path, fake_opencode)
    await h.session.start()
    yield h
    await h.session.stop()
    assert h.refold_matches(), "live entries differ from a fold of the store"
    assert h.patches_rebuild_entries(), "the published patches do not add up"


async def test_a_prompt_runs_a_turn_with_cost_context_title_and_model(oc):
    await oc.turn("hello")
    s = oc.session
    assert oc.statuses() == ["idle", "working", "idle"]
    assert oc.prose() == ["you said: hello"]
    assert s.cost_usd == pytest.approx(0.002) and s.context_tokens == 1030
    assert s.context_window == 1000000 and s.model_id == "opencode-go/fake-pro"
    assert s.resume_id and s.resume_id.startswith("ses_")
    assert s.title == "Fake title: hello"
    assert s.wire()["harness_label"] == "OpenCode"
    # OpenCode names the model once prompted, so this line follows the echo.
    assert "OpenCode 1.18.31 · opencode-go/fake-pro" in [
        e["summary"] for e in s.entries()
    ]


async def test_text_streams_before_the_turn_ends(oc):
    await oc.session.send("/stream 4")
    await until(lambda: any("chunk1" in m for m in oc.prose()), what="the first chunk")
    assert not any("chunk4" in m for m in oc.prose()), "drawn while it streams"
    await until(lambda: oc.done_lines() == 1, timeout=5, what="the end")
    assert oc.prose() == ["chunk1 chunk2 chunk3 chunk4 "]


async def test_a_prompt_sent_mid_turn_is_answered_in_the_same_turn(oc):
    await oc.session.send("/sleep 0.6")
    await until(
        lambda: any(e["kind"] == "tool" for e in oc.session.entries()), what="the call"
    )
    await oc.session.send("steer this")
    await until(
        lambda: oc.done_lines() == 1 and oc.session.status == "idle",
        timeout=5,
        what="idle",
    )
    assert [e["status"] for e in oc.session.entries() if e["kind"] == "user"] == [
        "ok",
        "ok",
    ]
    assert "also: steer this" in oc.prose()[-1]


async def test_an_inbox_message_waits_for_the_turn_and_arrives_as_its_own(oc):
    await oc.session.send("/sleep 0.5")
    await until(lambda: oc.session.status == "working", what="working")
    await oc.session.deliver("> from monitor:m1 · done", "the build finished")
    assert len(oc.session.held) == 1
    await until(
        lambda: (
            any(e["kind"] == "inbox" for e in oc.session.entries())
            and oc.session.status == "idle"
        ),
        timeout=5,
        what="the inbox turn",
    )
    assert oc.session.held == [] and oc.done_lines() == 2


async def test_interrupt_marks_the_call_interrupted_and_it_stays_so(oc):
    await oc.session.send("/sleep 5")
    await until(
        lambda: any(e["kind"] == "tool" for e in oc.session.entries()), what="the call"
    )
    await oc.session.interrupt()
    await until(lambda: oc.session.status == "idle", timeout=5, what="idle")
    await oc.turn("after")  # the late tool part has long arrived
    (t,) = [e for e in oc.session.entries() if e["kind"] == "tool"]
    assert (t["status"], t["detail"]["result"]) == ("err", "interrupted")


async def test_stop_and_resume_keep_the_conversation(oc):
    await oc.turn("remember PELICAN")
    sid = oc.session.resume_id
    await oc.session.stop()
    await oc.turn("/recall")
    assert oc.session.resume_id == sid and "PELICAN" in oc.prose()[-1]


async def test_a_resume_opencode_lost_starts_anew_and_says_so(oc):
    await oc.turn("first")
    await oc.session.stop()
    oc.session.resume_id = "ses_gone"
    await oc.turn("second")
    assert oc.session.resume_id not in ("ses_gone", None)
    assert any(
        "no longer had this conversation" in e["summary"] for e in oc.session.entries()
    )


async def test_model_and_effort_apply_from_the_next_prompt(oc):
    await oc.session.configure(model="opencode-go/fake-flash", effort="low")
    await oc.turn("hello")
    await oc.turn("/body")
    assert (
        '"modelID": "fake-flash"' in oc.prose()[-1]
        and '"variant": "low"' in oc.prose()[-1]
    )
    assert oc.session.context_window == 500000


async def test_a_permission_change_restarts_the_child_before_the_next_prompt(oc):
    pid = oc.session.pid
    await oc.session.configure(permission="read")
    await oc.turn("/config")
    assert oc.session.pid != pid and '"bash": "deny"' in oc.prose()[-1]


async def test_a_title_someone_set_is_kept(oc):
    oc.session.title_set = True
    oc.session._set(title="mine")
    await oc.turn("hello")
    assert oc.session.title == "mine"


async def test_a_command_shows_as_typed_and_an_unknown_one_ends_the_turn(oc):
    await oc.turn("/hello the okapi")
    users = [e for e in oc.session.entries() if e["kind"] == "user"]
    assert users[-1]["md"] == "/hello the okapi"
    assert users[-1]["detail"]["tail"] == "Say hello to the okapi."
    await oc.session.send("/nope x")
    await until(lambda: oc.session.status == "idle", what="idle after the refusal")
    assert any(
        e["kind"] == "error" and "/nope failed" in e["summary"]
        for e in oc.session.entries()
    )


async def test_an_exit_leaves_the_session_stopped(oc):
    await oc.session.send("/exit 3")
    await until(lambda: oc.session.status == "stopped", timeout=5, what="stopped")
    assert oc.session.entries()[-1]["summary"] == "OpenCode exited with code 3"


def _children(log: Path) -> list[int]:
    return [
        int(x.split()[1])
        for x in log.read_text().splitlines()
        if x.startswith("START ")
    ]


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return Path(f"/proc/{pid}/status").read_text().find("State:\tZ") < 0


async def test_two_sends_to_a_stopped_session_start_one_child_and_stop_ends_it(
    oc, tmp_path, monkeypatch
):
    await oc.session.stop()
    log = tmp_path / "fake.log"
    monkeypatch.setenv("FAKE_OPENCODE_LOG", str(log))
    await asyncio.gather(oc.session.send("one"), oc.session.send("two"))
    await until(lambda: oc.session.status == "idle", timeout=5, what="idle")
    await oc.session.stop()
    pids = _children(log)
    assert len(pids) == 1, pids
    await until(lambda: not any(_alive(p) for p in pids), what="no child left")


async def test_a_task_stores_no_delta_lines(oc):
    await oc.turn("/task")
    assert any(
        e["kind"] == "tool" and e["title"] == "Task" for e in oc.session.entries()
    )
    assert "message.part.delta" not in oc.path.read_text()


async def test_a_failed_prompt_ends_the_turn_and_says_why(oc):
    with pytest.raises(Exception):
        await oc.session.send("FAIL500")
    assert oc.session.status == "idle"
    assert any(
        e["kind"] == "error" and "500" in e["summary"] for e in oc.session.entries()
    )
    await oc.turn("still alive")


async def test_a_restart_that_fails_leaves_the_session_stopped(oc):
    await oc.turn("hello")
    await oc.session.configure(permission="read")
    oc.session._proc._bin = "/no/such/opencode"
    with pytest.raises(Exception):
        await oc.session.send("again")
    assert oc.session.status == "stopped" and not oc.session.running
    oc.session.harness.bin = oc.fake
    await oc.turn("after")


async def test_an_interrupt_that_cannot_reach_the_child_still_times_out(
    tmp_path, fake_opencode
):
    h = OC(tmp_path, fake_opencode)
    h.session = h.make(interrupt_timeout=0.3)
    await h.session.start()
    try:
        await h.session.send("/sleep 3")
        await until(lambda: h.session.status == "working", what="working")

        async def refuse():
            raise ConnectionResetError("gone")

        h.session._proc.interrupt = refuse
        with pytest.raises(ConnectionResetError):
            await h.session.interrupt()
        await until(lambda: h.session.status == "error", what="the deadline")
    finally:
        await h.session.stop()
