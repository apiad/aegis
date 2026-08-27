"""A handle minted twice takes the TUI down.

``ConversationPane`` keys its Textual DOM id on the handle
(``id=f"pane-{handle}"``), and Textual ids are immutable and unique within
a parent. The moment two live panes carry one handle, the second
``cs.mount(pane)`` raises ``DuplicateIds`` out of a worker and the app dies
— the crash Alex hits on ``/spawn``.

Every mint site fed ``generate_name`` a ``taken`` set built from the handles
panes/sessions carry *right now*. That is not the set of occupied names:

- a **renamed** session frees its birth name from ``taken`` while its DOM id
  ``pane-<birth-name>`` stays occupied for the pane's whole life;
- a **closed** session frees its name for immediate reissue, which merges it
  with the dead session's monitors, claims and history rows.

``generate_name`` prefers unused adjectives and laureates, so a collision
only surfaces once the fresh pool thins — which is exactly why this shows up
as *sometimes*, on a long-lived aegis with many renamed tabs. The tests
below shrink the name pool so exhaustion is reached in a few mints and the
failure is deterministic rather than a coin flip.
"""
from __future__ import annotations

import pytest

from aegis.config import Agent
from aegis.core.manager import SessionManager
from aegis.events import AssistantText, Result
from aegis.tui import names as names_mod
from aegis.tui.app import AegisApp

# Four alliterating pairs total: agile-adleman, apt-adleman, agile-abel,
# apt-abel. Exhaustion — and therefore any reuse of a retired name — is
# reached within four mints.
TINY_LAUREATES = ("adleman", "abel")
TINY_ADJECTIVES = {"a": ("agile", "apt")}


@pytest.fixture
def tiny_pool(monkeypatch):
    monkeypatch.setattr(names_mod, "LAUREATES", TINY_LAUREATES)
    monkeypatch.setattr(names_mod, "ADJECTIVES_BY_LETTER", TINY_ADJECTIVES)


class FakeHarness:
    session_id = None

    async def start(self): ...
    async def send(self, t): ...
    async def close(self): ...

    async def events(self):
        if False:
            yield


def _mgr() -> SessionManager:
    return SessionManager(
        {"default": object()}, "default",
        make_session=lambda profile, url, handle, **kw: FakeHarness(),
        mcp=None)


# --- serve / web path (SessionManager) ------------------------------------

@pytest.mark.asyncio
async def test_manager_never_reissues_a_renamed_sessions_birth_handle(
        tiny_pool):
    m = _mgr()
    birth = m._sync_spawn("default").handle
    await m.rename_handle(birth, "lucid-river")

    minted = [m._sync_spawn("default").handle for _ in range(6)]
    assert birth not in minted
    assert len(minted) == len(set(minted))


@pytest.mark.asyncio
async def test_manager_never_reissues_a_closed_sessions_handle(tiny_pool):
    m = _mgr()
    s = m._sync_spawn("default")
    dead = s.handle
    m._sessions.remove(s)

    minted = [m._sync_spawn("default").handle for _ in range(6)]
    assert dead not in minted


@pytest.mark.asyncio
async def test_manager_never_reissues_a_rename_target(tiny_pool):
    """A name a session renamed *into* stays occupied after that session
    renames again or dies. The targets are drawn from the mintable pool on
    purpose — a target the generator could never produce would pass this
    test without testing anything."""
    m = _mgr()
    s = m._sync_spawn("default")
    birth = s.handle
    target = next(f"{a}-{last}" for last in TINY_LAUREATES
                  for a in TINY_ADJECTIVES["a"]
                  if f"{a}-{last}" != birth)
    await m.rename_handle(birth, target)
    await m.rename_handle(target, "lucid-river")
    m._sessions.remove(s)

    minted = [m._sync_spawn("default").handle for _ in range(6)]
    assert birth not in minted
    assert target not in minted


@pytest.mark.asyncio
async def test_manager_rename_onto_a_retired_handle_is_refused():
    """Renaming *into* a birth name that a live pane's DOM id still holds is
    the same collision arriving through the other door."""
    m = _mgr()
    a = m._sync_spawn("default")
    birth = a.handle
    await m.rename_handle(birth, "lucid-river")
    b = m._sync_spawn("default")

    res = await m.rename_handle(b.handle, birth)
    assert "error" in res
    assert b.handle != birth


@pytest.mark.asyncio
async def test_manager_rename_can_reclaim_your_own_birth_handle():
    """Retiring a name must not lock a session out of undoing its own
    rename — the pane's DOM id is still that name."""
    m = _mgr()
    s = m._sync_spawn("default")
    birth = s.handle
    await m.rename_handle(birth, "lucid-river")
    res = await m.rename_handle("lucid-river", birth)
    assert res.get("ok") is True
    assert s.handle == birth


# --- TUI path (AegisApp) --------------------------------------------------

def _agent():
    return Agent(harness="claude-code", model="opus",
                 effort="high", permission="auto")


class FakeSession:
    session_id = None

    def __init__(self):
        self.sent = []

    async def start(self): ...
    async def send(self, text): self.sent.append(text)
    async def close(self): ...

    async def events(self):
        yield AssistantText("ok")
        yield Result(duration_ms=1, is_error=False)


class FakeMCP:
    url = "http://127.0.0.1:0/mcp/"

    def bind(self, bridge): self.bound = bridge
    async def start(self): ...
    async def stop(self): ...


def _factory(agent, mcp_url, handle, **kw):
    return FakeSession()


def _app(tmp_path):
    return AegisApp({"default": _agent()}, "default", _factory, FakeMCP(),
                    cwd=str(tmp_path))


@pytest.mark.asyncio
async def test_tui_spawn_after_rename_does_not_collide_with_the_dom_id(
        tmp_path, monkeypatch, tiny_pool):
    """The crash, end to end. A regression fails as a Textual DuplicateIds
    exception out of mount, not merely as an assert."""
    monkeypatch.chdir(tmp_path)
    async with _app(tmp_path).run_test() as pilot:
        app = pilot.app
        await pilot.pause()
        first = app._panes[0]
        birth = first.handle
        assert first.id == f"pane-{birth}"
        await app.rename_handle(birth, "lucid-river")

        for _ in range(5):
            await app._spawn("default", foreground=False)
        await pilot.pause()

        assert birth not in [p.handle for p in app._panes]
        ids = [p.id for p in app._panes]
        assert len(ids) == len(set(ids))


@pytest.mark.asyncio
async def test_tui_spawn_never_reuses_a_closed_panes_handle(
        tmp_path, monkeypatch, tiny_pool):
    monkeypatch.chdir(tmp_path)
    async with _app(tmp_path).run_test() as pilot:
        app = pilot.app
        await pilot.pause()
        dead = app._panes[0].handle
        await app._spawn("default", foreground=True)
        await app._close_pane(app._panes[0])
        await pilot.pause()

        for _ in range(5):
            await app._spawn("default", foreground=False)
        await pilot.pause()

        assert dead not in [p.handle for p in app._panes]


@pytest.mark.asyncio
async def test_tui_bridge_spawn_shares_the_registry(
        tmp_path, monkeypatch, tiny_pool):
    """aegis_spawn / queue workers mint through _SessionManagerAdapter, a
    different code path from `/spawn`. It must consult the same authority."""
    monkeypatch.chdir(tmp_path)
    async with _app(tmp_path).run_test() as pilot:
        app = pilot.app
        await pilot.pause()
        birth = app._panes[0].handle
        await app.rename_handle(birth, "lucid-river")

        from aegis.tui.app import _SessionManagerAdapter
        bridge = _SessionManagerAdapter(app)
        for _ in range(5):
            bridge.spawn("default")
        await pilot.pause()

        handles = [p.handle for p in app._panes]
        assert birth not in handles
        assert len(handles) == len(set(handles))
