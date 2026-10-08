import os
from pathlib import Path

import pytest

from aegis.meta import MetaStore
from aegis.claude.process import ControlError
from aegis.session import Host, Session, SpawnSpec
from aegis.transcript.entries import fold_records
from aegis.transcript.store import Store, read_store

from .conftest import cmdline, until


class Harness:
    def __init__(self, tmp_path: Path, fake: str, **kw):
        self.published: list[tuple[str, list[dict]]] = []
        self.path = tmp_path / "state" / "transcripts" / "log-abc.jsonl"
        self.metas = MetaStore(tmp_path / "state" / "sessions")
        self.fake = fake
        self.tmp_path = tmp_path
        self.kw = kw
        self.snapshot: list[dict] = []  # what a browser subscribed with
        self.session = self.make()

    def make(self, **meta) -> Session:
        """A Session over the same store, as a registry builds one at boot."""
        return Session(
            log_id="log-abc",
            spec=SpawnSpec("opus", "opus", "high", "full", self.tmp_path),
            handle="quiet-owl",
            store=Store(self.path),
            stderr_path=self.tmp_path / "state" / "stderr" / "log-abc.log",
            claude_bin=self.fake,
            publish=lambda ch, ops: self.published.append((ch, ops)),
            metas=self.metas,
            **meta,
            **self.kw,
        )

    def statuses(self) -> list[str]:
        """The states the sessions channel announced, repeats folded."""
        out: list[str] = []
        for ch, ops in self.published:
            if ch != "sessions":
                continue
            for op in ops:
                state = op["upsert"]["state"]
                if not out or out[-1] != state:
                    out.append(state)
        return out

    def kinds(self) -> list[tuple[str, str]]:
        return [(e["kind"], e["status"]) for e in self.session.entries()]

    def patches_rebuild_entries(self) -> bool:
        """What a browser that took ``snapshot`` and then every patch would hold."""
        shown: dict[str, dict] = {e["id"]: e for e in self.snapshot}
        for ch, ops in self.published:
            if ch != self.session.channel:
                continue
            for op in ops:
                if "upsert" in op:
                    shown[op["upsert"]["id"]] = op["upsert"]
                else:
                    shown.pop(op["remove"], None)
        return list(shown.values()) == self.session.entries()

    def refold_matches(self) -> bool:
        records, damaged = read_store(self.path)
        return (
            damaged == 0 and fold_records(records).entries() == self.session.entries()
        )


def tools(s: Session) -> list[dict]:
    return [e for e in s.entries() if e["kind"] == "tool"]


@pytest.fixture
async def h(tmp_path, fake_claude):
    harness = Harness(tmp_path, fake_claude)
    await harness.session.start()
    yield harness
    await harness.session.stop()
    assert harness.refold_matches(), "live entries differ from a fold of the store"
    assert harness.patches_rebuild_entries(), (
        "the published patches do not add up to the entries"
    )


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
    assert h.session.wire()["model"] == "fake-model"
    assert h.session.title == "hello" and h.session.resume_id


async def test_the_store_is_named_by_the_log_id(h):
    assert h.path.name == "log-abc.jsonl" and h.path.exists()


async def test_notices_never_start_a_turn(h):
    h.session._on_line('{"type":"system","subtype":"hook_started"}')
    h.session._on_line('{"type":"system","subtype":"thinking_tokens"}')
    assert h.session.status == "idle"


async def test_a_prompt_sent_mid_turn_is_echoed_in_order_and_one_result_ends_both(h):
    await h.session.send("/sleep 0.4")
    await until(lambda: tools(h.session), what="the tool call")
    await h.session.send("steer this")
    await until(lambda: h.session.status == "idle", what="idle")
    users = [e["md"] for e in h.session.entries() if e["kind"] == "user"]
    assert users == ["/sleep 0.4", "steer this"]
    assert h.statuses() == ["idle", "working", "idle"]


async def test_interrupt_ends_the_turn(h):
    await h.session.send("/sleep 5")
    await until(lambda: tools(h.session), what="the tool call")
    await h.session.interrupt()
    await until(lambda: h.session.status == "idle", what="idle after interrupt")
    assert h.session.entries()[-1]["summary"].startswith("interrupted")
    (tool,) = tools(h.session)
    assert tool["status"] == "err"


async def test_interrupt_when_idle_does_nothing(h):
    await h.session.interrupt()
    assert h.session.status == "idle"


async def test_an_unanswered_interrupt_is_an_error(tmp_path, fake_claude):
    h = Harness(tmp_path, fake_claude, interrupt_timeout=0.3)
    await h.session.start()
    try:
        await h.session.send("/deafsleep 2")
        await until(lambda: tools(h.session), what="the tool call")
        await h.session.interrupt()
        await until(lambda: h.session.status == "error", what="error")
        assert (
            h.session.entries()[-1]["summary"]
            == "the interrupt went unanswered for 0.3s"
        )
    finally:
        await h.session.stop()


async def test_a_dead_claude_leaves_the_session_stopped_and_the_next_prompt_resumes(h):
    await h.session.send("first prompt")
    await until(
        lambda: h.session.status == "idle" and h.session.cost_usd,
        what="the first result",
    )
    await h.session.send("/exit 3")
    await until(lambda: h.session.status == "stopped", what="stopped")
    last = h.session.entries()[-1]
    assert last["summary"] == "Claude Code exited with code 3"
    assert "fatal: something broke" in last["detail"]["tail"]
    await h.session.send("/recall")
    await until(lambda: h.session.status == "idle", what="the resumed turn")
    prose = [e["md"] for e in h.session.entries() if e["kind"] == "prose"]
    assert prose[-1] == "earlier: first prompt | /exit 3"


async def test_a_2mb_line_is_stored_and_its_entry_carries_only_the_tail(h):
    await h.session.send("/big")
    await until(lambda: h.session.status == "idle", what="idle")
    (tool,) = tools(h.session)
    assert tool["status"] == "ok" and tool["detail"]["result"] == "30000 lines"
    assert len(tool["detail"]["tail"].encode()) <= 8192 + 3
    assert h.path.stat().st_size > 2_000_000


async def test_stop_ends_the_child_and_keeps_the_session(h):
    pid = h.session.pid
    assert pid is not None
    await h.session.stop()
    assert (
        h.session.status == "stopped"
        and h.session.entries()[-1]["summary"] == "stopped"
    )
    with pytest.raises(ProcessLookupError):
        os.kill(pid, 0)


async def test_a_stopped_session_resumes_with_its_context_in_the_same_store(h):
    await h.session.send("remember PELICAN")
    await until(
        lambda: h.session.status == "idle" and h.session.cost_usd,
        what="the first result",
    )
    first_id = h.session.resume_id
    await h.session.stop()
    await h.session.send("/recall")
    await until(lambda: h.session.status == "idle", what="the resumed turn")
    assert h.session.resume_id == first_id
    summaries = [e["summary"] for e in h.session.entries() if e["kind"] == "system"]
    assert "stopped" in summaries and "resumed" in summaries
    prose = [e["md"] for e in h.session.entries() if e["kind"] == "prose"]
    assert prose[-1] == "earlier: remember PELICAN"


async def test_a_session_rebuilt_from_its_meta_resumes(h):
    await h.session.send("before the restart")
    await until(
        lambda: h.session.status == "idle" and h.session.cost_usd, what="the result"
    )
    await h.session.shutdown()
    kept = {k: v for k, v in h.session.meta().items() if k in ("resume_id", "title")}
    h.published.clear()
    reborn = h.session = h.make(**kept)
    assert reborn.status == "stopped" and not reborn.running
    h.snapshot = reborn.entries()  # a browser subscribes after the restart
    await reborn.send("/recall")
    await until(lambda: reborn.status == "idle", what="the resumed turn")
    prose = [e["md"] for e in reborn.entries() if e["kind"] == "prose"]
    assert prose[-1] == "earlier: before the restart"


async def test_a_missing_binary_on_resume_leaves_it_stopped(h):
    await h.session.stop()
    h.session._claude_bin = "/no/such/claude"
    with pytest.raises(FileNotFoundError):
        await h.session.send("hello")
    assert h.session.status == "stopped" and not h.session.running
    h.session._claude_bin = h.fake


async def test_interrupt_on_a_stopped_session_does_nothing(h):
    await h.session.stop()
    await h.session.interrupt()
    assert h.session.status == "stopped"


async def test_a_missing_binary_raises_on_start(tmp_path):
    h = Harness(tmp_path, str(tmp_path / "no-such-claude"))
    with pytest.raises(FileNotFoundError):
        await h.session.start()


async def test_a_process_start_fetches_the_catalog_and_tells_the_host(
    tmp_path, fake_claude
):
    seen = []

    class Spy(Host):
        def catalog_ready(self, session, catalog):
            seen.append(catalog)

    h = Harness(tmp_path, fake_claude, host=Spy())
    await h.session.start()
    try:
        cat = await h.session.catalog_task
        assert cat is not None and cat.has("hello") and seen == [cat]
    finally:
        await h.session.stop()


async def test_configure_live_switches_the_process_and_the_spec(h):
    await h.session.send("hi")
    await until(
        lambda: h.session.status == "idle" and h.session.cost_usd, what="the turn"
    )
    await h.session.configure(model="sonnet", effort="max", permission="read")
    s = h.session
    assert (s.spec.model, s.spec.effort, s.spec.permission) == ("sonnet", "max", "read")
    assert s.wire()["model"] == "sonnet"
    await s.send("again")
    await until(lambda: s.model_id == "fake-sonnet", what="the new model's init")
    assert [e["summary"] for e in s.entries() if " → " in e["summary"]] == [
        "model → sonnet · effort → max · permission → read"
    ]


async def test_configure_on_a_stopped_session_starts_nothing_and_the_next_start_uses_it(
    h,
):
    await h.session.stop()
    await h.session.configure(model="haiku")
    assert h.session.pid is None
    assert h.session.entries()[-1]["summary"] == "model → haiku (when it resumes)"
    await h.session.send("hello")
    argv = await cmdline(h.session.pid)
    assert argv[argv.index(b"--model") + 1] == b"haiku"


async def test_a_refused_change_keeps_what_applied_before_it(h):
    with pytest.raises(ControlError):
        await h.session.configure(model="haiku", effort="low")  # no effort on haiku
    assert (h.session.spec.model, h.session.spec.effort) == ("haiku", "high")


async def test_a_slash_command_does_not_title_the_session(h):
    await h.session.send("/context")
    await until(lambda: h.session.status == "idle", what="/context")
    assert h.session.title == ""
    await h.session.send("fix the parser")
    assert h.session.title == "fix the parser"
