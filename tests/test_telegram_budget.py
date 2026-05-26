from __future__ import annotations

from decimal import Decimal
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from aegis.budget.budgets import Budget
from aegis.budget.windows import parse_window
from aegis.remote.config import RemoteSpec
from aegis.telegram.commands import COMMANDS


@pytest.fixture(autouse=True)
def _clean_registry():
    snap = dict(COMMANDS)
    yield
    COMMANDS.clear()
    COMMANDS.update(snap)


def _make_frontend(queues=None, remotes=None):
    import tempfile
    from pathlib import Path
    from aegis.telegram.frontend import TelegramFrontend
    state_dir = Path(tempfile.mkdtemp())

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
        def __init__(self, queues): self._queues = queues or {}
        def _load_recent_jsonl(self, queue, max_age): return []

    class _Bridge:
        def __init__(self, qm): self.queue_manager = qm
        scheduler = None

    class _Cfg:
        def __init__(self, remotes=None): self.remotes = remotes or {}

    bot = _Bot()
    bot.sent = []  # instance-level list, not class-level
    fe = TelegramFrontend(
        bot, _Mgr(), _Bridge(_QM(queues)), _Cfg(remotes),
        chat_id=42, auto_prompt="", state_dir=state_dir)
    return fe, bot


def _q(name, budgets=None):
    """Make a Queue dataclass instance with optional budgets list."""
    from aegis.queue.schema import Queue
    return Queue(name=name, agent_profile="opus", max_parallel=1,
                 provider="claude-code", model="opus",
                 budgets=budgets or [])


@pytest.mark.asyncio
async def test_budget_list_no_queues():
    fe, bot = _make_frontend(queues={})
    await fe._command("/budget list")
    assert "no queues" in bot.sent[-1].lower() \
        or bot.sent[-1].strip().endswith("```")


@pytest.mark.asyncio
async def test_budget_list_local_summarizes_per_queue():
    fe, bot = _make_frontend(queues={
        "impl": _q("impl", budgets=[
            Budget(constraint="usd", limit=Decimal("1.00"),
                   window_str="1h", window=parse_window("1h"))
        ]),
        "fast": _q("fast"),  # no budget
    })
    await fe._command("/budget list")
    out = bot.sent[-1]
    assert "impl" in out
    assert "fast" in out


@pytest.mark.asyncio
async def test_budget_show_local_no_budget():
    fe, bot = _make_frontend(queues={"fast": _q("fast")})
    await fe._command("/budget show fast")
    out = bot.sent[-1].lower()
    assert "no budget" in out or "no budgets" in out


@pytest.mark.asyncio
async def test_budget_show_unknown_queue():
    fe, bot = _make_frontend(queues={})
    await fe._command("/budget show ghost")
    assert "unknown queue" in bot.sent[-1].lower() \
        or "no such queue" in bot.sent[-1].lower()


@pytest.mark.asyncio
async def test_budget_show_missing_arg():
    fe, bot = _make_frontend(queues={})
    await fe._command("/budget show")
    assert "usage" in bot.sent[-1].lower()


@pytest.mark.asyncio
async def test_budget_list_remote_routes(monkeypatch):
    captured = {}
    async def fake_list(spec):
        captured["spec"] = spec
        return {"queues": [
            {"name": "impl", "budgets_count": 1, "status": "ok",
             "binding": "$0.30/$1.00 1h", "unblock_at": None},
        ]}
    monkeypatch.setattr("aegis.remote.client.remote_budget_list",
                        fake_list)
    fe, bot = _make_frontend(remotes={
        "vps": RemoteSpec(url="http://1.2.3.4:8556"),
    })
    await fe._command("/budget list @vps")
    assert captured["spec"].url == "http://1.2.3.4:8556"
    assert "impl" in bot.sent[-1]


@pytest.mark.asyncio
async def test_budget_show_remote_routes(monkeypatch):
    captured = {}
    async def fake_show(spec, queue):
        captured["queue"] = queue
        return {"name": "impl", "allowed": True, "checks": [
            {"constraint": "usd", "limit": "1.00", "spent": "0.30",
             "window": "1h", "allowed": True, "headroom": "0.70"},
        ], "blocked_by": [], "unblock_at": None}
    monkeypatch.setattr("aegis.remote.client.remote_budget_show",
                        fake_show)
    fe, bot = _make_frontend(remotes={
        "vps": RemoteSpec(url="http://1.2.3.4:8556"),
    })
    await fe._command("/budget show impl @vps")
    assert captured["queue"] == "impl"
    assert "0.30" in bot.sent[-1]
