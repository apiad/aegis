"""Tests for the TUI ConfigPanel (read-only surface — slice 6)."""
from __future__ import annotations

from pathlib import Path

import pytest
from textual.app import App, ComposeResult
from textual.widgets import ContentSwitcher

from aegis.tui.config_panel import ConfigPanel


def _seed(p: Path, body: str) -> None:
    (p / ".aegis.yaml").write_text(body)


_FULL = (
    "default_agent: main\n"
    "agents:\n"
    "  main:\n"
    "    provider: claude-code\n"
    "    model: opus\n"
    "    effort: high\n"
    "    permission: auto\n"
    "  fast:\n"
    "    provider: gemini\n"
    "    model: gemini-3-flash-preview\n"
    "    permission: full\n"
    "queues:\n"
    "  impl:\n"
    "    agent: main\n"
    "    max_parallel: 2\n"
    "telegram:\n"
    "  chat_id: 42\n"
)


class _Host(App):
    def __init__(self, root: Path) -> None:
        super().__init__()
        self._root = root

    def compose(self) -> ComposeResult:
        yield ContentSwitcher(id="cs")

    async def on_mount(self) -> None:
        cs = self.query_one("#cs", ContentSwitcher)
        panel = ConfigPanel(self._root)
        await cs.mount(panel)
        cs.current = panel.id


@pytest.mark.asyncio
async def test_config_panel_mounts(tmp_path: Path) -> None:
    _seed(tmp_path, _FULL)
    app = _Host(tmp_path)
    async with app.run_test() as pilot:
        await pilot.pause()
        # Tab metadata that the TabBar machinery reads.
        panel = app.query_one(ConfigPanel)
        assert panel.handle == "config"
        assert panel.agent_slug == "config"
        assert panel.id is not None


@pytest.mark.asyncio
async def test_config_panel_renders_agents(tmp_path: Path) -> None:
    _seed(tmp_path, _FULL)
    app = _Host(tmp_path)
    async with app.run_test() as pilot:
        await pilot.pause()
        text = app.query_one(ConfigPanel).rendered_text()
    assert "main" in text
    assert "fast" in text
    assert "claude-code" in text
    assert "gemini" in text


@pytest.mark.asyncio
async def test_config_panel_renders_queues(tmp_path: Path) -> None:
    _seed(tmp_path, _FULL)
    app = _Host(tmp_path)
    async with app.run_test() as pilot:
        await pilot.pause()
        text = app.query_one(ConfigPanel).rendered_text()
    assert "impl" in text


@pytest.mark.asyncio
async def test_config_panel_renders_telegram(tmp_path: Path) -> None:
    _seed(tmp_path, _FULL)
    app = _Host(tmp_path)
    async with app.run_test() as pilot:
        await pilot.pause()
        text = app.query_one(ConfigPanel).rendered_text()
    assert "42" in text  # chat_id


@pytest.mark.asyncio
async def test_config_panel_empty_state(tmp_path: Path) -> None:
    """Empty directory (no .aegis.yaml) shows a hint, not a crash."""
    app = _Host(tmp_path)
    async with app.run_test() as pilot:
        await pilot.pause()
        text = app.query_one(ConfigPanel).rendered_text()
    assert "no .aegis.yaml" in text.lower() or "no agents" in text.lower()


# --- AddAgentModal --------------------------------------------------

@pytest.mark.asyncio
async def test_add_agent_modal_writes_through_edit_helpers(
        tmp_path: Path) -> None:
    """Driving the modal to completion calls add_agent and refreshes."""
    from aegis.tui.config_panel import AddAgentModal

    result: list = []

    class _Wrap(App):
        async def on_mount(self) -> None:
            self.push_screen(
                AddAgentModal(tmp_path),
                callback=lambda r: (result.append(r), self.exit()))

    app = _Wrap()
    async with app.run_test() as pilot:
        await pilot.pause()
        from textual.widgets import Input
        inputs = {inp.id: inp for inp in app.screen.query(Input)}
        inputs["agm-slug"].value = "main"
        inputs["agm-model"].value = "opus"
        await pilot.pause()
        await pilot.press("ctrl+s")  # save
        for _ in range(5):
            await pilot.pause()

    assert result and result[0] is True
    text = (tmp_path / ".aegis.yaml").read_text()
    assert "main:" in text
    assert "provider: claude-code" in text


@pytest.mark.asyncio
async def test_add_agent_modal_validates_missing_fields(
        tmp_path: Path) -> None:
    from aegis.tui.config_panel import AddAgentModal

    result: list = []

    class _Wrap(App):
        async def on_mount(self) -> None:
            self.push_screen(
                AddAgentModal(tmp_path),
                callback=lambda r: result.append(r))

    app = _Wrap()
    async with app.run_test() as pilot:
        await pilot.pause()
        # Hit save with empty slug/model — modal must stay open.
        await pilot.press("ctrl+s")
        await pilot.pause()
        # Modal remains on screen → callback not yet fired.
        assert result == []
        # Cancel cleanly.
        await pilot.press("escape")
        for _ in range(5):
            await pilot.pause()
    # Cancel returns False.
    assert result == [False]


@pytest.mark.asyncio
async def test_config_panel_a_keybinding_opens_modal(tmp_path: Path) -> None:
    """Pressing `a` while focused on ConfigPanel pushes the modal."""
    from aegis.tui.config_panel import AddAgentModal
    _seed(tmp_path, _FULL)
    app = _Host(tmp_path)
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("a")
        await pilot.pause()
        # Top screen on the stack is the modal.
        assert isinstance(app.screen, AddAgentModal)
