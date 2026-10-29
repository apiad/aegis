"""An untested harness version in the browser (#446): the dialog before a
person's spawn, start once or always, and the Settings rows."""

# ruff: noqa: F811  (pytest fixtures imported from test_browser)

from pathlib import Path

import pytest

from .test_browser import (  # noqa: F401
    CONFIG,
    SETTINGS_CONFIG,
    Server,
    browser,
    online,
    open_settings,
    page,
)

pytestmark = [pytest.mark.browser, pytest.mark.slow]


def start(tmp_path: Path, fake_claude: str, fake_opencode: str, text: str) -> Server:
    (tmp_path / ".aegis.yaml").write_text(text)
    s = Server(tmp_path, fake_claude, fake_opencode)
    s.env["AEGIS_FAKE_HARNESS_VERSION"] = "99.0.0"
    return s.start()


@pytest.fixture
def server(tmp_path: Path, fake_claude: str, fake_opencode: str):
    s = start(tmp_path, fake_claude, fake_opencode, CONFIG)
    yield s
    s.stop()


def open_spawn(pg) -> None:
    online(pg)
    pg.click("#tab-add")
    pg.wait_for_selector("#a2[data-view=spawn]")
    pg.wait_for_function("document.querySelector('#sp-agent').value !== ''")


def test_the_dialog_names_version_path_and_command_then_starts_once(
    server,
    page,
    fake_claude,
):
    page.goto(server.url)
    page.wait_for_selector("#a2[data-view=fleet]")
    open_spawn(page)
    page.fill("#sp-text", "hello")
    page.click("#sp-go")
    page.wait_for_selector("#chooser:not([hidden])")
    text = page.inner_text("#chooser")
    assert "Claude Code 99.0.0 is untested" in text
    assert str(Path(fake_claude).resolve()) in text
    assert "npm install -g @anthropic-ai/claude-code@" in text
    assert "tested with" in text
    page.click("#chooser button[data-value=once]")
    page.wait_for_selector("#a2[data-view=session]")
    page.wait_for_function(
        "[...document.querySelectorAll('#entries .body')]"
        ".some((e) => e.textContent.includes('99.0.0 is newer'))"
    )
    # The panel's harness row names the version in the warning colour (#456).
    page.wait_for_selector("#s-harness.warn", state="attached")
    assert page.text_content("#s-harness") == "Claude Code 99.0.0"
    assert page.errors == []


def test_always_saves_the_version_and_stops_asking(server, page):
    page.goto(server.url)
    page.wait_for_selector("#a2[data-view=fleet]")
    open_spawn(page)
    page.click("#sp-go")
    page.wait_for_selector("#chooser:not([hidden])")
    page.click("#chooser button[data-value=always]")
    page.wait_for_selector("#a2[data-view=session]")
    assert "99.0.0" in (server.root / ".aegis.yaml").read_text()
    open_spawn(page)
    page.click("#sp-go")
    page.wait_for_function("location.hash.startsWith('#s=')")
    page.wait_for_timeout(500)
    assert page.locator("#chooser:not([hidden])").count() == 0
    assert page.errors == []


def test_cancel_keeps_the_typed_text(server, page):
    page.goto(server.url)
    page.wait_for_selector("#a2[data-view=fleet]")
    open_spawn(page)
    page.fill("#sp-text", "keep me")
    page.click("#sp-go")
    page.wait_for_selector("#chooser:not([hidden])")
    page.keyboard.press("Escape")
    page.wait_for_selector("#chooser", state="hidden")
    assert page.input_value("#sp-text") == "keep me"
    assert page.errors == []


def test_settings_marks_the_harness_and_lists_accepted(
    tmp_path,
    fake_claude,
    fake_opencode,
    page,
):
    s = start(tmp_path, fake_claude, fake_opencode, SETTINGS_CONFIG)
    try:
        open_settings(page, s.url)
        page.wait_for_selector(".set-vtag.warn[data-harness=claude-code]")
        tag = page.locator(".set-vtag[data-harness=claude-code]")
        assert tag.locator("summary").inner_text() == "newer than tested"
        tag.locator("summary").click()
        assert "npm install -g" in tag.inner_text()
        assert page.locator("#set-untested").count() == 1
        assert "No version accepted" in page.inner_text("#set-accepted")
    finally:
        s.stop()


def test_the_dialog_fits_a_phone(server, page):
    page.set_viewport_size({"width": 390, "height": 800})
    page.goto(server.url)
    page.wait_for_selector("#a2[data-view=fleet]")
    open_spawn(page)
    page.click("#sp-go")
    page.wait_for_selector("#chooser:not([hidden])")
    wide = page.evaluate(
        "[...document.querySelectorAll('#chooser *')]"
        ".filter((e) => e.getBoundingClientRect().right > 391).length"
    )
    assert wide == 0


def test_start_once_on_a_stopped_session_clears_the_composer(
    tmp_path, fake_claude, fake_opencode, page
):
    (tmp_path / ".aegis.yaml").write_text(CONFIG)
    s = Server(tmp_path, fake_claude, fake_opencode).start()
    try:
        page.goto(s.url)
        page.wait_for_selector("#a2[data-view=fleet]")
        open_spawn(page)
        page.click("#sp-go")
        page.wait_for_selector("#a2[data-view=session]")
        page.fill("#input", "/stop")
        page.keyboard.press("Enter")
        page.wait_for_function("document.querySelector('#input').value === ''")
        # The harness is upgraded under the stopped session.
        script = Path(fake_claude)
        text = script.read_text()
        script.write_text(
            text.replace("\nexec ", "\nexport AEGIS_FAKE_HARNESS_VERSION=99.0.0\nexec ")
        )
        page.fill("#input", "hello again")
        page.keyboard.press("Enter")
        page.wait_for_selector("#chooser:not([hidden])")
        page.click("#chooser button[data-value=once]")
        page.wait_for_function("document.querySelector('#input').value === ''")
        page.wait_for_function(
            "[...document.querySelectorAll('#entries .body')]"
            ".some((e) => e.textContent.includes('99.0.0 is newer'))"
        )
        assert page.errors == []
    finally:
        s.stop()
