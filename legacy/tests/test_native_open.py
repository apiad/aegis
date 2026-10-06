"""Handing a token to the desktop (alt+click on a transcript block)."""
from __future__ import annotations

from pathlib import Path

from aegis.tui.native_open import (
    is_url, native_open_command, refuse_reason,
)


def test_picks_the_platform_opener(monkeypatch):
    monkeypatch.setattr("sys.platform", "linux")
    monkeypatch.setattr("shutil.which",
                        lambda exe: "/usr/bin/xdg-open"
                        if exe == "xdg-open" else None)
    assert native_open_command("x.py") == ["/usr/bin/xdg-open", "x.py"]

    monkeypatch.setattr("sys.platform", "darwin")
    monkeypatch.setattr("shutil.which",
                        lambda exe: "/usr/bin/open" if exe == "open" else None)
    assert native_open_command("x.py") == ["/usr/bin/open", "x.py"]


def test_no_opener_on_a_headless_box(monkeypatch):
    """Bare SSH with no xdg-open: say so rather than fail silently."""
    monkeypatch.setattr("sys.platform", "linux")
    monkeypatch.setattr("shutil.which", lambda exe: None)
    assert native_open_command("x.py") is None


def test_urls_are_recognised():
    assert is_url("https://example.com/x")
    assert is_url("http://localhost:8080")
    assert not is_url("src/aegis/app.py")
    assert not is_url("notes.md")


def test_desktop_files_are_refused(tmp_path: Path):
    """`open` on a .desktop file *launches* it. Every other handler at
    worst shows content; this one executes, and the path came out of
    agent output."""
    launcher = tmp_path / "evil.desktop"
    launcher.write_text("[Desktop Entry]\nExec=rm -rf ~\n")
    assert refuse_reason(launcher) is not None
    assert ".desktop" in refuse_reason(launcher)


def test_ordinary_files_and_directories_are_allowed(tmp_path: Path):
    f = tmp_path / "notes.md"
    f.write_text("x")
    assert refuse_reason(f) is None
    assert refuse_reason(tmp_path) is None      # opens the file manager


# --------------------------------------------------------------------------
# The gesture, end to end on a transcript block
# --------------------------------------------------------------------------
import pytest                                            # noqa: E402
from textual.app import App, ComposeResult               # noqa: E402
from textual.events import Click                         # noqa: E402

from aegis.tui.pane import CopyableBlock                  # noqa: E402


def _click(widget, *, ctrl=False, meta=False):
    return Click(widget=widget, x=0, y=0, delta_x=0, delta_y=0, button=1,
                 shift=False, meta=meta, ctrl=ctrl)


class _BlockApp(App):
    def __init__(self, payload: str) -> None:
        super().__init__()
        self._payload = payload
        self.opened_natively: list[str] = []
        self.notices: list[str] = []

    def compose(self) -> ComposeResult:
        yield CopyableBlock(self._payload, self._payload)

    def notify(self, message, *a, **kw):                  # noqa: D102
        self.notices.append(str(message))


@pytest.mark.asyncio
async def test_alt_click_hands_the_file_to_the_desktop(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "notes.md").write_text("x")

    opened: list[str] = []
    monkeypatch.setattr("aegis.tui.native_open.open_native",
                        lambda target: opened.append(target))

    app = _BlockApp("see `notes.md` for the details")
    async with app.run_test() as pilot:
        block = app.query_one(CopyableBlock)
        block.on_click(_click(block, meta=True))
        await pilot.pause()
        await pilot.pause()
    assert opened == [str(tmp_path / "notes.md")]


@pytest.mark.asyncio
async def test_alt_click_opens_a_url(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    opened: list[str] = []
    monkeypatch.setattr("aegis.tui.native_open.open_native",
                        lambda target: opened.append(target))

    app = _BlockApp("released at `https://example.com/v1`")
    async with app.run_test() as pilot:
        block = app.query_one(CopyableBlock)
        block.on_click(_click(block, meta=True))
        await pilot.pause()
        await pilot.pause()
    assert opened == ["https://example.com/v1"]


@pytest.mark.asyncio
async def test_alt_click_refuses_a_desktop_file(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "evil.desktop").write_text("[Desktop Entry]\nExec=rm -rf ~\n")
    opened: list[str] = []
    monkeypatch.setattr("aegis.tui.native_open.open_native",
                        lambda target: opened.append(target))

    app = _BlockApp("run `evil.desktop` now")
    async with app.run_test() as pilot:
        block = app.query_one(CopyableBlock)
        block.on_click(_click(block, meta=True))
        await pilot.pause()
        await pilot.pause()
    assert opened == []
    assert any(".desktop" in n for n in app.notices)


@pytest.mark.asyncio
async def test_plain_click_still_copies(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "notes.md").write_text("x")
    opened: list[str] = []
    monkeypatch.setattr("aegis.tui.native_open.open_native",
                        lambda target: opened.append(target))
    copied: list[str] = []

    app = _BlockApp("see `notes.md`")
    async with app.run_test() as pilot:
        monkeypatch.setattr(type(app), "copy_to_clipboard",
                            lambda self, text: copied.append(text))
        block = app.query_one(CopyableBlock)
        block.on_click(_click(block))
        await pilot.pause()
    assert copied == ["see `notes.md`"]
    assert opened == []
