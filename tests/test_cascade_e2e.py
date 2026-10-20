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
    assert world.sends(s)[-1] == CONTINUE_PROMPT.format(error=error, model="opencode/b")
    prose = [e["md"] for e in s.entries() if e["kind"] == "prose"]
    assert prose[-1].startswith("you said: The previous model failed")
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
    assert t.result.startswith("you said: The previous model failed")
    assert world.session(t.worker).spec.model == "opencode/b"
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
