"""Light-touch tests for the TerminalTab widget.

We instantiate the widget inside a minimal Textual host App and drive a
single command through TerminalManager, verifying the tab renders a
finalized block once the run completes. The bash-OSC133 integration is
real here so this is closer to an integration test, but it stays in
the hermetic suite because everything is sandboxed under tmp_path.
"""
from __future__ import annotations

from pathlib import Path

import pytest
from textual.app import App, ComposeResult
from textual.widgets import ContentSwitcher

from aegis.terminal.manager import TerminalManager
from aegis.tui.terminal_tab import TerminalTab
from aegis.tui.state import AgentState


class _Host(App):
    def __init__(self, tab: TerminalTab) -> None:
        super().__init__()
        self._tab = tab

    def compose(self) -> ComposeResult:
        cs = ContentSwitcher(id="cs")
        yield cs

    async def on_mount(self) -> None:
        cs = self.query_one("#cs", ContentSwitcher)
        await cs.mount(self._tab)
        cs.current = self._tab.id


@pytest.mark.asyncio
async def test_terminal_tab_mounts_and_finalizes_command(tmp_path: Path):
    mgr = TerminalManager(state_dir=tmp_path / "state")
    info = await mgr.spawn(name="t", shell="/bin/bash")
    tab = TerminalTab(mgr, info)
    app = _Host(tab)
    async with app.run_test() as pilot:
        await pilot.pause()
        # Drive a command through the manager; the tab should pick it up
        # via the render-observer hook and finalize the running block.
        await mgr.run("t", "echo hi", writer="human")
        for _ in range(10):
            await pilot.pause()
        # The tab should be back to ready (no command running).
        assert tab.state is AgentState.ready
    await mgr.close("t")


def test_terminal_tab_attrs_quack_like_pane(tmp_path: Path):
    """TerminalTab must expose handle, agent_slug, state, unseen, id so
    the AegisApp tabbar treats it like a ConversationPane."""
    import asyncio
    mgr = TerminalManager(state_dir=tmp_path / "state")

    async def _go():
        info = await mgr.spawn(name="quack", shell="/bin/bash")
        tab = TerminalTab(mgr, info)
        assert tab.handle == "quack"
        assert tab.agent_slug == "term"
        assert tab.state is AgentState.ready
        assert tab.unseen is False
        assert tab.id == "term-quack"
        await mgr.close("quack")

    asyncio.run(_go())
