"""The two AppBridge implementations must hand `ask()` the same things.

`ask()` gives every optional parameter a default, so a bridge that forgets
one still compiles, still passes its tests, and still answers — it just
answers with the feature quietly switched off. That has happened three
times in `AegisApp.peer_ask` alone:

- `state_dir` / `source_log_id` missing → the peer is told the operator's
  transcript "could not be read", so the teaser silently never sends.
- `cc` missing → `--cc` parses, reaches the bridge, and is dropped.
- `source_session` missing → even a forwarded `cc` is inert, because
  `cc_into` returns early on a None session.

Every one of those was green on the web seam (`SessionManager.peer_ask`)
and broken in the TUI, which is the primary UI. So this pins the *class*
rather than the three instances: whatever `SessionManager` forwards,
`AegisApp` forwards. A new parameter added to only one of them fails here.

The bridges are called for real against a recording `ask`, not scanned as
source text — a substring assertion would pin the spelling of the call
rather than what actually reaches it.
"""
from __future__ import annotations

import inspect

import pytest

from aegis.core.manager import SessionManager
from aegis.tui.app import AegisApp


class _Recorder:
    """Stands in for `aegis.peer.ask` and keeps the kwargs it was given."""

    def __init__(self):
        self.kwargs: dict = {}

    async def __call__(self, **kwargs):
        self.kwargs = kwargs
        return object()


class _TuiStub:
    """The three attributes `AegisApp.peer_ask` reads. No panes, so the
    source/target lookups resolve to None — the parameter *names* are what
    is under test, not the values."""
    _panes: list = []
    _state_dir = "/tmp/aegis-test-state"


class _WebStub:
    """The equivalent for `SessionManager.peer_ask`."""
    _persist_dir = "/tmp/aegis-test-state"
    state_root = "/tmp/aegis-test-state"

    def get(self, handle):
        return None


async def _kwargs_from(bridge_method, stub, monkeypatch) -> set[str]:
    rec = _Recorder()
    monkeypatch.setattr("aegis.peer.ask", rec)
    await bridge_method(stub, "alpha", "beta", "is the build green?", cc=True)
    return set(rec.kwargs)


@pytest.mark.asyncio
async def test_peer_ask_bridges_forward_the_same_kwargs(monkeypatch):
    tui = await _kwargs_from(AegisApp.peer_ask, _TuiStub(), monkeypatch)
    web = await _kwargs_from(SessionManager.peer_ask, _WebStub(), monkeypatch)
    assert tui == web, (
        f"AegisApp.peer_ask and SessionManager.peer_ask disagree.\n"
        f"  only in SessionManager: {sorted(web - tui)}\n"
        f"  only in AegisApp:       {sorted(tui - web)}\n"
        f"Every optional parameter on ask() defaults, so a missing one "
        f"ships a silently degraded path rather than an error.")


@pytest.mark.asyncio
async def test_the_tui_bridge_forwards_every_optional_ask_parameter(
        monkeypatch):
    """Catches the other direction: a parameter `ask()` accepts that the
    primary UI never passes is a feature switched off in the primary UI."""
    from aegis.peer import ask
    optional = {
        name for name, p in inspect.signature(ask).parameters.items()
        if p.default is not inspect.Parameter.empty}
    forwarded = await _kwargs_from(AegisApp.peer_ask, _TuiStub(), monkeypatch)
    missing = optional - forwarded
    assert not missing, (
        f"ask() accepts {sorted(missing)}, which AegisApp.peer_ask does "
        f"not forward. Either wire them up or drop them from ask().")


@pytest.mark.asyncio
async def test_cc_reaches_ask_as_true(monkeypatch):
    """The concrete regression: `--cc` parsed, reached the bridge, and was
    dropped on the floor, so the TUI never cc'd anything."""
    rec = _Recorder()
    monkeypatch.setattr("aegis.peer.ask", rec)
    await AegisApp.peer_ask(_TuiStub(), "alpha", "beta", "q", cc=True)
    assert rec.kwargs["cc"] is True


@pytest.mark.parametrize("method", ["peer_ask", "read_peer", "side_note"])
def test_both_bridges_implement_the_same_peer_surface(method):
    """`read_peer` is resolved by getattr rather than the Protocol, so
    nothing else would notice the TUI missing it — the agent would just
    be told this frontend cannot read peer transcripts."""
    assert hasattr(AegisApp, method), f"AegisApp is missing {method}"
    assert hasattr(SessionManager, method), f"SessionManager is missing {method}"
