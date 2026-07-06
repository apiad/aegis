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
        # Default model dropdown selection (first claude-code entry,
        # "opus") is used — no manual model entry needed.
        await pilot.pause()
        await pilot.press("ctrl+s")  # save
        for _ in range(5):
            await pilot.pause()

    assert result and result[0] is True
    text = (tmp_path / ".aegis.yaml").read_text()
    assert "main:" in text
    assert "provider: claude-code" in text
    assert "model: opus" in text


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
        # Hit save with empty slug — modal must stay open (model has a
        # default selection from the registry, so it's never empty).
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


# --- Model picker (registry-backed) ---------------------------------

@pytest.mark.asyncio
async def test_add_agent_modal_picks_model_from_registry(
        tmp_path: Path) -> None:
    """Picking a non-default model from the dropdown writes that exact
    value to .aegis.yaml — no custom input needed."""
    from textual.widgets import Select
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
        app.screen.query_one("#agm-slug", Input).value = "fast"
        app.screen.query_one("#agm-model", Select).value = "haiku"
        await pilot.pause()
        await pilot.press("ctrl+s")
        for _ in range(5):
            await pilot.pause()

    text = (tmp_path / ".aegis.yaml").read_text()
    assert "model: haiku" in text


@pytest.mark.asyncio
async def test_add_agent_modal_provider_change_repopulates_model_select(
        tmp_path: Path) -> None:
    """Switching the provider Select replaces the model options with the
    new provider's registry entries."""
    from textual.widgets import Select
    from aegis.tui.config_panel import AddAgentModal

    class _Wrap(App):
        async def on_mount(self) -> None:
            self.push_screen(AddAgentModal(tmp_path))

    app = _Wrap()
    async with app.run_test() as pilot:
        await pilot.pause()
        provider = app.screen.query_one("#agm-provider", Select)
        model = app.screen.query_one("#agm-model", Select)
        # Switch to gemini — model Select must now offer gemini models.
        provider.value = "gemini"
        await pilot.pause()
        opts = [v for _label, v in model._options]  # (prompt, value)
        assert any("gemini" in o for o in opts if isinstance(o, str))
        # Switch to opencode — pick a Kimi entry to prove the slug shape.
        provider.value = "opencode"
        await pilot.pause()
        opts = [v for _label, v in model._options]
        assert any("kimi" in (o.lower() if isinstance(o, str) else "")
                   for o in opts)


@pytest.mark.asyncio
async def test_add_agent_modal_custom_model_writes_typed_value(
        tmp_path: Path) -> None:
    """Selecting <custom> reveals the input; the typed value is what
    lands in .aegis.yaml."""
    from textual.widgets import Input, Select
    from aegis.tui.config_panel import (
        AddAgentModal, CUSTOM_MODEL_OPTION,
    )

    result: list = []

    class _Wrap(App):
        async def on_mount(self) -> None:
            self.push_screen(
                AddAgentModal(tmp_path),
                callback=lambda r: (result.append(r), self.exit()))

    app = _Wrap()
    async with app.run_test() as pilot:
        await pilot.pause()
        app.screen.query_one("#agm-slug", Input).value = "weird"
        app.screen.query_one("#agm-model", Select).value = CUSTOM_MODEL_OPTION
        await pilot.pause()
        # Custom input is now visible.
        custom = app.screen.query_one("#agm-model-custom", Input)
        assert custom.has_class("-visible")
        custom.value = "experimental-model-7b"
        await pilot.pause()
        await pilot.press("ctrl+s")
        for _ in range(5):
            await pilot.pause()

    text = (tmp_path / ".aegis.yaml").read_text()
    assert "model: experimental-model-7b" in text


@pytest.mark.asyncio
async def test_add_agent_modal_custom_with_empty_input_blocks_save(
        tmp_path: Path) -> None:
    """If <custom> is picked but the input is empty, save must error
    rather than write 'model: <custom>'."""
    from textual.widgets import Input, Select
    from aegis.tui.config_panel import (
        AddAgentModal, CUSTOM_MODEL_OPTION,
    )

    result: list = []

    class _Wrap(App):
        async def on_mount(self) -> None:
            self.push_screen(
                AddAgentModal(tmp_path),
                callback=lambda r: result.append(r))

    app = _Wrap()
    async with app.run_test() as pilot:
        await pilot.pause()
        app.screen.query_one("#agm-slug", Input).value = "blank"
        app.screen.query_one("#agm-model", Select).value = CUSTOM_MODEL_OPTION
        await pilot.pause()
        await pilot.press("ctrl+s")
        await pilot.pause()
        # Save did not dismiss the modal (callback not invoked).
        assert result == []
        # .aegis.yaml was not written.
        assert not (tmp_path / ".aegis.yaml").exists()
        await pilot.press("escape")
        for _ in range(5):
            await pilot.pause()
    assert result == [False]
