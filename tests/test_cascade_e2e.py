"""The fallback inside a turn, in the real app with the fake OpenCode: an
agent whose cascade is opencode/a, opencode/b, opencode/c, and models the fake
answers with Zen's 429 (FAKE_OPENCODE_EXHAUSTED)."""

import asyncio
from collections import Counter

import pytest

from aegis.session import CONTINUE_PROMPT
from aegis.transcript.store import read_store

from .conftest import until
from .test_agents import World

MODELS = ["opencode/a", "opencode/b", "opencode/c"]
CONFIG = f"""\
default_agent: free
agents:
  free:
    harness: opencode
    models: [{", ".join(MODELS)}]
    effort: high
    permission: full
queues:
  general: {{agent: free, max_parallel: 1}}
"""


def model_of(world, log_id: str) -> str:
    """A worker's model, read from its meta: a finished worker is archived at
    once, so it may be gone from the open sessions by the time a test looks."""
    metas, _ = world.app.sessions.metas.read_all()
    return next(m["model"] for m in metas if m.get("log_id") == log_id)


class CascadeWorld(World):
    async def start(self) -> "CascadeWorld":
        await super().start()
        self.ended: Counter = Counter()
        reg = self.app.sessions
        inner = reg.turn_ended

        def counted(s):
            self.ended[s.log_id] += 1
            inner(s)

        reg.turn_ended = counted
        return self

    async def spawn_free(self, prompt: str | None = "hello"):
        p = {"agent": "free"} | ({"prompt": prompt} if prompt else {})
        r = await self.app.registry.call("session.spawn", p)
        return self.session(r["log_id"])

    async def turn_ended(self, s, n: int = 1) -> None:
        await until(
            lambda: self.ended[s.log_id] >= n and s.status == "idle",
            timeout=10,
            what="the turn to end",
        )

    def rows(self, s) -> list[str]:
        return [e["summary"] for e in s.entries() if e["kind"] == "system"]

    def sends(self, s) -> list[str]:
        return [
            r["text"]
            for r in read_store(s.store.path)[0]
            if r.get("kind") == "send" and isinstance(r.get("text"), str)
        ]


@pytest.fixture
async def world(tmp_path, fake_claude, fake_opencode):
    (tmp_path / ".aegis.yaml").write_text(CONFIG)
    w = await CascadeWorld(tmp_path, fake_claude, fake_opencode).start()
    yield w
    await w.stop()


def exhaust(monkeypatch, *models: str) -> None:
    monkeypatch.setenv("FAKE_OPENCODE_EXHAUSTED", ",".join(models))


async def test_a_failed_turn_falls_to_the_next_model_and_finishes(world, monkeypatch):
    exhaust(monkeypatch, "opencode/a")
    s = await world.spawn_free()
    await world.turn_ended(s)
    assert s.spec.model == "opencode/b"
    rows = world.rows(s)
    assert any(r.startswith("cascade: opencode/a failed") for r in rows), rows
    assert world.ended[s.log_id] == 1
    assert s.meta()["cascade"] == {"models": MODELS, "at": 1}
    error = "APIError: 429 FreeUsageLimitError: free usage limit reached"
    sent = world.sends(s)[-1]
    assert sent.startswith("> from aegis:cascade · ")
    assert sent.endswith("\n" + CONTINUE_PROMPT.format(error=error, model="opencode/b"))
    # New spawns skip the model that fell.
    assert world.app.cooling.until("opencode", "opencode/a", s.created_at) is not None


async def test_every_model_failing_exhausts_the_cascade(world, monkeypatch):
    exhaust(monkeypatch, *MODELS)
    s = await world.spawn_free()
    await world.turn_ended(s)
    rows = world.rows(s)
    assert sum(r.startswith("cascade exhausted:") for r in rows) == 1, rows
    assert s.spec.model == "opencode/c"
    assert world.ended[s.log_id] == 1
    errors = [e for e in s.entries() if e["kind"] == "error"]
    assert errors and errors[-1]["summary"].startswith("turn failed")
    assert s.meta()["cascade"] == {"models": MODELS, "at": 2, "exhausted": True}
    assert s.wire()["cascade"]["exhausted"] is True


async def test_a_queue_worker_that_falls_completes_once_with_the_last_answer(
    world, monkeypatch
):
    exhaust(monkeypatch, "opencode/a")
    q = world.app.queues
    t = await asyncio.wait_for(
        q.enqueue("general", "hello", False, None, str(world.root)), 3
    )
    await until(lambda: t.status != "pending" and t.status != "running", timeout=10)
    assert t.status == "completed", t.result
    assert t.result.startswith("you said: > from aegis:cascade")
    assert model_of(world, t.worker) == "opencode/b"
    assert world.ended[t.worker] == 1


async def test_a_fallen_session_resumes_on_its_lower_model_after_restart(
    world, monkeypatch
):
    exhaust(monkeypatch, "opencode/a")
    s = await world.spawn_free()
    await world.turn_ended(s)
    log_id = s.log_id
    await world.restart()
    s = world.session(log_id)
    assert s.spec.model == "opencode/b"
    assert s.spec.cascade == tuple(MODELS)
    assert s.meta()["cascade"] == {"models": MODELS, "at": 1}


async def test_model_command_moves_the_cascade_position(world, monkeypatch):
    exhaust(monkeypatch, "opencode/b", "opencode/z")
    s = await world.spawn_free()
    await world.turn_ended(s)
    assert s.spec.model == "opencode/a"
    await s.configure(model="opencode/b")
    await s.send("again")
    await world.turn_ended(s, 2)
    assert s.spec.model == "opencode/c"
    assert s.meta()["cascade"]["at"] == 2
    await s.configure(model="opencode/z")
    assert s.meta()["cascade"] == {"models": MODELS, "at": None}
    await s.send("once more")
    await world.turn_ended(s, 3)
    assert s.spec.model == "opencode/z"
    assert not any("opencode/z failed" in r for r in world.rows(s))
    assert s.entries()[-1]["summary"].startswith("turn failed")


async def test_interrupt_during_fallback_sends_no_continue(world, monkeypatch):
    exhaust(monkeypatch, "opencode/a")
    s = await world.spawn_free(None)
    hold = asyncio.Event()
    configure = s.configure

    async def held(**kw):
        await hold.wait()
        await configure(**kw)

    s.configure = held
    await s.send("start")
    await until(lambda: s._falling is not None, what="the fall to start")
    await s.interrupt()
    hold.set()
    await world.turn_ended(s)
    assert not any(t.startswith("The previous model") for t in world.sends(s))
    assert world.rows(s)[-1] == "cascade: interrupted before continuing on opencode/b"
    assert s.spec.model == "opencode/b"
    assert world.ended[s.log_id] == 1


async def test_a_context_error_does_not_fall(world, monkeypatch):
    exhaust(monkeypatch)
    s = await world.spawn_free("OVERFLOW")
    await world.turn_ended(s)
    assert s.spec.model == "opencode/a"
    assert not any(r.startswith("cascade") for r in world.rows(s))
    assert s.entries()[-1]["summary"].startswith("turn failed (ContextOverflowError")


def hold_configure(s) -> asyncio.Event:
    """Hold the fall's configure until the returned event is set."""
    hold = asyncio.Event()
    configure = s.configure

    async def held(**kw):
        await hold.wait()
        await configure(**kw)

    s.configure = held
    return hold


def continued(world, s) -> bool:
    return any("The previous model failed" in t for t in world.sends(s))


async def settle() -> None:
    for _ in range(20):
        await asyncio.sleep(0.02)


async def test_stop_during_fallback_cancels_the_fall(world, monkeypatch):
    exhaust(monkeypatch, "opencode/a")
    s = await world.spawn_free(None)
    hold = hold_configure(s)
    await s.send("start")
    await until(lambda: s._falling is not None, what="the fall to start")
    await s.stop()
    hold.set()
    await settle()
    assert s.status == "asleep" and not s.running
    assert not continued(world, s)
    assert world.rows(s)[-1] == "cascade: stopped before continuing on opencode/b"
    assert world.ended[s.log_id] == 1


async def test_close_during_fallback_cancels_the_fall(world, monkeypatch):
    exhaust(monkeypatch, "opencode/a")
    s = await world.spawn_free(None)
    hold = hold_configure(s)
    await s.send("start")
    await until(lambda: s._falling is not None, what="the fall to start")
    await world.app.sessions.close(s.log_id)
    hold.set()
    await settle()
    assert s.archived and not s.running
    assert not continued(world, s)
    assert world.ended[s.log_id] == 1


@pytest.mark.slow
async def test_shutdown_during_fallback_continues_the_worker_at_boot(
    world, monkeypatch
):
    exhaust(monkeypatch, "opencode/a")
    hold = asyncio.Event()
    from aegis.session import Session

    configure = Session.configure

    async def held(self, **kw):
        if kw.get("model"):
            await hold.wait()
        await configure(self, **kw)

    monkeypatch.setattr(Session, "configure", held)
    q = world.app.queues
    t = await asyncio.wait_for(
        q.enqueue("general", "hello", False, None, str(world.root)), 3
    )
    await until(lambda: t.worker is not None, what="a worker")
    s = world.session(t.worker)
    await until(lambda: s._falling is not None, what="the fall to start")
    end_process = s._end_process

    async def ending():
        # The switch answers while the server is going down.
        await end_process()
        hold.set()
        await settle()

    s._end_process = ending
    ended = world.ended
    await world.stop()
    await settle()
    assert not s.running
    assert not continued(world, s)
    assert ended[s.log_id] == 0
    assert t.status == "running"
    exhaust(monkeypatch)
    from aegis.registry import Registry

    cont, loaded = Registry.continue_after_boot, []

    async def continuing(self):
        # A worker that ends its turn during the continue finds its task (#505).
        loaded.append(t.id in world.app.queues.tasks)
        await cont(self)

    monkeypatch.setattr(Registry, "continue_after_boot", continuing)
    await world.start()
    assert loaded == [True]
    # Continued, then completed, though the instant fake ends the turn the
    # held flush started before its send returns (#536).
    await until(
        lambda: world.app.queues.tasks[t.id].status == "completed",
        what="the task to complete",
    )
    sends = [
        r.get("text", "")
        for r in read_store(world.app.sessions.store_path(t.worker))[0]
        if r.get("kind") == "send"
    ]
    assert any(x.startswith("> from aegis:restart") for x in sends), sends


async def test_a_task_completes_when_the_flushed_turn_ends_before_its_send_returns(
    world, monkeypatch
):
    # #536: the turn a held flush starts ends while _flush_held still awaits
    # its send, so the turn's end finds the worker busy; the task completes
    # once the flush clears.
    from aegis.session import Session

    send, held, flushed = Session._send, [], []

    async def slow(self, text, *a, **kw):
        if self.worker and not held:
            # The prompt's turn ends with a message held: _turn_over flushes it.
            held.append(text)
            self._hold({"header": "from test", "body": "held", "ts": 0.0})
        await send(self, text, *a, **kw)
        if self._flushing and "from test" in text and not flushed:
            flushed.append(text)
            await until(lambda: self.status == "idle", what="the flushed turn")

    monkeypatch.setattr(Session, "_send", slow)
    q = world.app.queues
    t = await asyncio.wait_for(
        q.enqueue("general", "hello", False, None, str(world.root)), 3
    )
    await until(lambda: bool(flushed), what="the held flush")
    await until(lambda: t.status == "completed", what="the task to complete")


async def test_a_continue_that_fails_to_send_ends_the_turn_once(world, monkeypatch):
    exhaust(monkeypatch, "opencode/a")
    s = await world.spawn_free(None)
    await s.ensure_running()
    proc = s._proc
    send, calls = proc.send, []

    async def broken(text):
        calls.append(text)
        if len(calls) > 1:
            raise BrokenPipeError("gone")
        await send(text)

    proc.send = broken
    await s.send("start")
    await until(lambda: len(calls) > 1 and s.status == "idle", what="the failed send")
    await settle()
    assert world.ended[s.log_id] == 1


async def test_a_refused_switch_says_so_and_ends_the_turn(world, monkeypatch):
    from aegis.ops import OpError

    exhaust(monkeypatch, "opencode/a")
    s = await world.spawn_free(None)
    configure = s.configure

    async def refused(**kw):
        if kw.get("model"):
            raise OpError("control", "set_model refused")
        await configure(**kw)

    s.configure = refused
    await s.send("start")
    await world.turn_ended(s)
    await settle()
    assert s.spec.model == "opencode/a"
    rows = world.rows(s)
    assert not any("Continuing on" in r for r in rows), rows
    assert rows[-1].startswith("cascade: opencode/a failed")
    assert rows[-1].endswith("could not switch to opencode/b: set_model refused.")
    assert world.ended[s.log_id] == 1


async def test_an_exhausted_cascade_still_says_so_after_restart(world, monkeypatch):
    exhaust(monkeypatch, *MODELS)
    s = await world.spawn_free()
    await world.turn_ended(s)
    log_id = s.log_id
    await world.restart()
    s = world.session(log_id)
    assert s.wire()["cascade"]["exhausted"] is True


async def test_the_continue_prompt_is_aegis_not_the_person(world, monkeypatch):
    exhaust(monkeypatch, "opencode/a")
    s = await world.spawn_free()
    await world.turn_ended(s)
    users = [e["md"] for e in s.entries() if e["kind"] == "user"]
    assert users == ["hello"], users
    inbox = [e for e in s.entries() if e["kind"] == "inbox"]
    assert inbox and inbox[-1]["title"].startswith("aegis:cascade · ")
