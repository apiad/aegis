import asyncio
from pathlib import Path

import pytest

from aegis.terminal.manager import TerminalManager


@pytest.fixture
def state_dir(tmp_path: Path) -> Path:
    return tmp_path / "state"


@pytest.mark.asyncio
async def test_render_observer_sees_chunks_and_command_end(state_dir):
    events: list[tuple[str, dict]] = []

    def cb(kind: str, payload: dict) -> None:
        events.append((kind, payload))

    mgr = TerminalManager(state_dir=state_dir)
    await mgr.spawn(name="r", shell="/bin/bash")
    mgr.add_render_observer("r", cb)
    rec = await mgr.run("r", "echo hello", writer="human")
    assert rec.exit == 0
    kinds = [k for k, _ in events]
    assert "command_end" in kinds
    end_payload = [p for k, p in events if k == "command_end"][-1]
    assert end_payload["record"].cmd == "echo hello"
    # At least one chunk fired between the run and the prompt return.
    assert "chunk" in kinds
    await mgr.close("r")


@pytest.mark.asyncio
async def test_remove_render_observer_stops_firing(state_dir):
    seen: list[str] = []

    def cb(kind: str, payload: dict) -> None:
        seen.append(kind)

    mgr = TerminalManager(state_dir=state_dir)
    await mgr.spawn(name="x", shell="/bin/bash")
    mgr.add_render_observer("x", cb)
    await mgr.run("x", "true", writer="human")
    n_first = len(seen)
    assert n_first > 0
    mgr.remove_render_observer("x", cb)
    await mgr.run("x", "true", writer="human")
    # No additional events after removal.
    assert len(seen) == n_first
    await mgr.close("x")


@pytest.mark.asyncio
async def test_render_observer_unknown_terminal_errors(state_dir):
    from aegis.terminal.manager import TerminalNotFound
    mgr = TerminalManager(state_dir=state_dir)
    with pytest.raises(TerminalNotFound):
        mgr.add_render_observer("ghost", lambda *a: None)
