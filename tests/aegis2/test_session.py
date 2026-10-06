from pathlib import Path

import pytest

from aegis2.claude.process import build_argv
from aegis2.session import Session, SpawnSpec
from aegis2.transcript.entries import fold_records
from aegis2.transcript.store import Store, read_store

from .conftest import until


class Harness:
    def __init__(self, tmp_path: Path, fake: str, **kw):
        self.published: list[tuple[str, list[dict]]] = []
        self.path = tmp_path / "state" / "transcripts" / "log-abc.jsonl"
        self.session = Session(
            log_id="log-abc",
            spec=SpawnSpec("opus", "opus", "high", "full", tmp_path),
            store=Store(self.path),
            argv=build_argv(fake, "opus", "high", "full"),
            stderr_path=tmp_path / "state" / "stderr" / "log-abc.log",
            publish=lambda ch, ops: self.published.append((ch, ops)),
            **kw,
        )

    def statuses(self) -> list[str]:
        return [
            op["set"]["status"]
            for ch, ops in self.published
            if ch == "session"
            for op in ops
            if "status" in op["set"]
        ]

    def kinds(self) -> list[tuple[str, str]]:
        return [(e["kind"], e["status"]) for e in self.session.entries()]

    def refold_matches(self) -> bool:
        records, damaged = read_store(self.path)
        return (
            damaged == 0 and fold_records(records).entries() == self.session.entries()
        )


@pytest.fixture
async def h(tmp_path, fake_claude):
    harness = Harness(tmp_path, fake_claude)
    await harness.session.start()
    yield harness
    await harness.session.close()
    assert harness.refold_matches(), "live entries differ from a fold of the store"


async def test_a_prompt_runs_a_turn(h):
    await h.session.send("hello")
    await until(
        lambda: h.session.status == "idle" and h.session.cost_usd, what="the result"
    )
    assert h.statuses() == ["idle", "working", "idle"]
    kinds = h.kinds()
    assert ("user", "ok") in kinds and ("prose", "ok") in kinds
    assert not [k for k in kinds if k[1] == "pending"]
    assert h.session.context_window == 200000 and h.session.context_tokens == 1030
    assert h.session.meta()["model"] == "fake-model"


async def test_the_store_is_named_by_the_log_id(h):
    assert h.path.name == "log-abc.jsonl" and h.path.exists()


async def test_notices_never_start_a_turn(h):
    await h.session.send("/notice")
    await until(lambda: len(read_store(h.path)[0]) >= 6, what="the notices")
    # The send itself moved it to working; a fresh session idles through notices.
    h.session.status = "idle"
    h.session._on_line('{"type":"system","subtype":"hook_started"}')
    h.session._on_line('{"type":"system","subtype":"thinking_tokens"}')
    assert h.session.status == "idle"


async def test_a_prompt_sent_mid_turn_is_echoed_in_order_and_one_result_ends_both(h):
    await h.session.send("/sleep 0.4")
    await until(
        lambda: any(e["kind"] == "tool" for e in h.session.entries()),
        what="the tool call",
    )
    await h.session.send("steer this")
    await until(lambda: h.session.status == "idle", what="idle")
    users = [e["md"] for e in h.session.entries() if e["kind"] == "user"]
    assert users == ["/sleep 0.4", "steer this"]
    assert h.statuses().count("idle") == 2  # start, then the one result


async def test_interrupt_ends_the_turn(h):
    await h.session.send("/sleep 5")
    await until(
        lambda: any(e["kind"] == "tool" for e in h.session.entries()),
        what="the tool call",
    )
    await h.session.interrupt()
    await until(lambda: h.session.status == "idle", what="idle after interrupt")
    assert h.session.entries()[-1]["summary"].startswith("interrupted")


async def test_interrupt_when_idle_does_nothing(h):
    await h.session.interrupt()
    assert h.session.status == "idle"


async def test_an_unanswered_interrupt_is_an_error(tmp_path, fake_claude):
    h = Harness(tmp_path, fake_claude, interrupt_timeout=0.3)
    await h.session.start()
    try:
        await h.session.send("/deafsleep 2")
        await until(
            lambda: any(e["kind"] == "tool" for e in h.session.entries()),
            what="the tool call",
        )
        await h.session.interrupt()
        await until(lambda: h.session.status == "error", what="error")
        assert (
            h.session.entries()[-1]["summary"]
            == "the interrupt went unanswered for 0.3s"
        )
    finally:
        await h.session.close()


async def test_a_dead_claude_is_an_error_with_its_stderr(h):
    await h.session.send("/exit 3")
    await until(lambda: h.session.status == "error", what="error")
    last = h.session.entries()[-1]
    assert last["summary"] == "claude exited with code 3"
    assert "fatal: something broke" in last["detail"]["tail"]


async def test_a_2mb_line_is_stored_and_its_entry_carries_only_the_tail(h):
    await h.session.send("/big")
    await until(lambda: h.session.status == "idle", what="idle")
    (tool,) = [e for e in h.session.entries() if e["kind"] == "tool"]
    assert tool["status"] == "ok" and tool["detail"]["result"] == "30000 lines"
    assert len(tool["detail"]["tail"].encode()) <= 8192 + 3
    assert h.path.stat().st_size > 2_000_000


async def test_close_ends_the_child(tmp_path, fake_claude):
    import os

    h = Harness(tmp_path, fake_claude)
    await h.session.start()
    pid = h.session.pid
    await h.session.close()
    assert h.session.status == "closed"
    with pytest.raises(ProcessLookupError):
        os.kill(pid, 0)


async def test_a_missing_binary_raises_on_start(tmp_path):
    h = Harness(tmp_path, str(tmp_path / "no-such-claude"))
    with pytest.raises(FileNotFoundError):
        await h.session.start()
