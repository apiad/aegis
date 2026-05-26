from __future__ import annotations

import pytest

from aegis.queue.schema import Queue, Task
from aegis.remote.config import RemoteSpec
from aegis.telegram.commands import COMMANDS


@pytest.fixture(autouse=True)
def _clean_registry():
    snap = dict(COMMANDS)
    yield
    COMMANDS.clear()
    COMMANDS.update(snap)


def _q(name, max_parallel=1):
    return Queue(name=name, agent_profile="opus",
                 max_parallel=max_parallel, provider="claude-code",
                 model="opus", budgets=[])


def _task(tid="t1", queue="impl", status="pending", payload="x"):
    return Task(id=tid, queue=queue, payload=payload,
                enqueued_by="agent:p",
                enqueued_at="2026-05-26T10:00:00Z",
                callback=False, status=status)


def _make_frontend(queues=None, pending=None, inflight=None, tmp_path=None,
                   remotes=None):
    from aegis.telegram.frontend import TelegramFrontend

    class _Bot:
        sent: list[str] = []
        async def send_message(self, chat, text, markdown=False):
            self.sent.append(text); return 1
        async def edit_message(self, *a, **k): return None

    class _Mgr:
        def list_sessions(self): return []
        def list_agents(self): return []
        def get(self, handle): return None

    class _QM:
        def __init__(self, queues, pending, inflight, tmp_path):
            self._queues = queues or {}
            self._pending = pending or {n: [] for n in self._queues}
            self._inflight = inflight or {n: [] for n in self._queues}
            self._all = {}  # task-id -> Task; populate per test as needed
            self._state_dir = tmp_path
        def _load_recent_jsonl(self, queue, max_age): return []

    class _Bridge:
        def __init__(self, qm):
            self.queue_manager = qm
        scheduler = None

    class _Cfg:
        def __init__(self, remotes=None): self.remotes = remotes or {}

    bot = _Bot()
    bot.sent = []  # reset per-instance to avoid class-level list pollution
    fe = TelegramFrontend(
        bot, _Mgr(),
        _Bridge(_QM(queues, pending, inflight, tmp_path)),
        _Cfg(remotes), chat_id=42, auto_prompt="")
    return fe, bot


@pytest.mark.asyncio
async def test_queue_list_empty():
    fe, bot = _make_frontend(queues={})
    await fe._command("/queue list")
    assert "no queues" in bot.sent[-1].lower()


@pytest.mark.asyncio
async def test_queue_list_shows_every_queue():
    fe, bot = _make_frontend(queues={
        "impl": _q("impl"), "fast": _q("fast"),
    })
    await fe._command("/queue list")
    out = bot.sent[-1]
    assert "impl" in out
    assert "fast" in out


@pytest.mark.asyncio
async def test_queue_list_at_peer_rejects():
    fe, bot = _make_frontend(queues={}, remotes={
        "vps": RemoteSpec(url="http://1.2.3.4:8556"),
    })
    await fe._command("/queue list @vps")
    out = bot.sent[-1].lower()
    assert "cross-host" in out or "local only" in out


@pytest.mark.asyncio
async def test_queue_show_unknown():
    fe, bot = _make_frontend(queues={})
    await fe._command("/queue show ghost")
    assert "unknown queue" in bot.sent[-1].lower() \
        or "no such queue" in bot.sent[-1].lower()


@pytest.mark.asyncio
async def test_queue_show_local(tmp_path):
    fe, bot = _make_frontend(
        queues={"impl": _q("impl")},
        pending={"impl": [_task(tid="p1", payload="pending one")]},
        inflight={"impl": [_task(tid="i1", status="dispatched",
                                  payload="in flight one")]},
        tmp_path=tmp_path)
    await fe._command("/queue show impl")
    out = bot.sent[-1]
    assert "impl" in out
    assert "p1" in out or "pending" in out.lower()
    assert "i1" in out or "in flight" in out.lower()


@pytest.mark.asyncio
async def test_queue_show_missing_arg():
    fe, bot = _make_frontend(queues={"impl": _q("impl")})
    await fe._command("/queue show")
    assert "usage" in bot.sent[-1].lower()
