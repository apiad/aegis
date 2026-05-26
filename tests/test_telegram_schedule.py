from __future__ import annotations

from types import SimpleNamespace

import pytest

from aegis.remote.config import RemoteSpec
from aegis.telegram.commands import COMMANDS


@pytest.fixture(autouse=True)
def _clean_registry():
    snap = dict(COMMANDS)
    yield
    COMMANDS.clear()
    COMMANDS.update(snap)


def _make_frontend(scheduler=None, remotes=None):
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

    class _Bridge: pass
    _Bridge.queue_manager = None
    _Bridge.scheduler = scheduler

    class _Cfg:
        def __init__(self, remotes=None):
            self.remotes = remotes or {}

    bot = _Bot()
    fe = TelegramFrontend(bot, _Mgr(), _Bridge(), _Cfg(remotes),
                          chat_id=42, auto_prompt="")
    return fe, bot


@pytest.mark.asyncio
async def test_schedule_list_empty_local():
    class _Sched:
        def snapshot(self): return []
    fe, bot = _make_frontend(scheduler=_Sched())
    await fe._command("/schedule list")
    assert "no schedules" in bot.sent[-1].lower()


@pytest.mark.asyncio
async def test_schedule_list_local_shows_entries():
    entries = [
        SimpleNamespace(name="nightly-build", source="pushed",
                         next_fire="2026-05-27T02:00:00Z",
                         fire_count=47, enabled=True),
        SimpleNamespace(name="weekly-report", source="inline",
                         next_fire="2026-05-31T08:00:00Z",
                         fire_count=12, enabled=True),
    ]
    class _Sched:
        def snapshot(self): return entries
    fe, bot = _make_frontend(scheduler=_Sched())
    await fe._command("/schedule list")
    out = bot.sent[-1]
    assert "nightly-build" in out
    assert "weekly-report" in out


@pytest.mark.asyncio
async def test_schedule_list_remote_routes_through_client(monkeypatch):
    captured = {}
    async def fake_list(spec):
        captured["spec"] = spec
        return {"schedules": [
            {"name": "remote-job", "source": "inline",
             "next_fire": "2026-05-27T05:00:00Z",
             "fire_count": 5, "enabled": True},
        ]}
    monkeypatch.setattr("aegis.remote.client.remote_schedule_list",
                        fake_list)

    fe, bot = _make_frontend(scheduler=None,
                              remotes={"vps": RemoteSpec(
                                  url="http://1.2.3.4:8556")})
    await fe._command("/schedule list @vps")
    assert captured["spec"].url == "http://1.2.3.4:8556"
    assert "remote-job" in bot.sent[-1]


@pytest.mark.asyncio
async def test_schedule_show_local_known():
    entry = SimpleNamespace(
        name="nb", source="pushed",
        spec={"workflow": "enqueue", "cron": "0 2 * * *"},
        next_fire="2026-05-27T02:00:00Z", fire_count=10, enabled=True)
    class _Sched:
        def get(self, name): return entry if name == "nb" else None
    fe, bot = _make_frontend(scheduler=_Sched())
    await fe._command("/schedule show nb")
    out = bot.sent[-1]
    assert "nb" in out
    assert "0 2 * * *" in out


@pytest.mark.asyncio
async def test_schedule_show_local_unknown():
    class _Sched:
        def get(self, name): return None
    fe, bot = _make_frontend(scheduler=_Sched())
    await fe._command("/schedule show ghost")
    assert "no such schedule" in bot.sent[-1].lower() or \
           "unknown" in bot.sent[-1].lower()


@pytest.mark.asyncio
async def test_schedule_show_missing_arg():
    class _Sched:
        def get(self, name): return None
    fe, bot = _make_frontend(scheduler=_Sched())
    await fe._command("/schedule show")
    assert "usage" in bot.sent[-1].lower()


@pytest.mark.asyncio
async def test_schedule_remote_unknown_peer():
    fe, bot = _make_frontend(remotes={"vps": RemoteSpec(
        url="http://1.2.3.4:8556")})
    await fe._command("/schedule list @nope")
    assert "unknown peer" in bot.sent[-1].lower()


@pytest.mark.asyncio
async def test_schedule_run_local():
    fired: list[str] = []
    class _Sched:
        def fire_now(self, name): fired.append(name)
        def get(self, name):
            return SimpleNamespace(name=name) if name == "nb" else None
    fe, bot = _make_frontend(scheduler=_Sched())
    await fe._command("/schedule run nb")
    assert fired == ["nb"]
    assert "fired" in bot.sent[-1].lower()


@pytest.mark.asyncio
async def test_schedule_run_unknown_schedule_errors():
    class _Sched:
        def fire_now(self, name): raise KeyError(name)
        def get(self, name): return None
    fe, bot = _make_frontend(scheduler=_Sched())
    await fe._command("/schedule run ghost")
    assert "no such schedule" in bot.sent[-1].lower() \
        or "unknown" in bot.sent[-1].lower() \
        or "error" in bot.sent[-1].lower()


@pytest.mark.asyncio
async def test_schedule_run_missing_arg():
    fe, bot = _make_frontend(scheduler=SimpleNamespace())
    await fe._command("/schedule run")
    assert "usage" in bot.sent[-1].lower()


@pytest.mark.asyncio
async def test_schedule_run_rejects_at_peer():
    fe, bot = _make_frontend(scheduler=SimpleNamespace(),
                              remotes={"vps": RemoteSpec(
                                  url="http://1.2.3.4:8556")})
    await fe._command("/schedule run nb @vps")
    out = bot.sent[-1].lower()
    assert "cross-host" in out or "local only" in out
