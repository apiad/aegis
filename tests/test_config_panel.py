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
