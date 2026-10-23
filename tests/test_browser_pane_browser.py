"""A browser's tab in a real browser against a real aegis serve (#564)."""

import time
from pathlib import Path

import pytest

from .test_browser import browser  # noqa: F401
from .test_browsers import site  # noqa: F401
from .test_terminal_browser import CONFIG, TermServer

pytest.importorskip("playwright.sync_api")

pytestmark = [pytest.mark.browser, pytest.mark.slow]


@pytest.fixture
def server(tmp_path: Path, fake_claude: str, fake_opencode: str):
    (tmp_path / ".aegis.yaml").write_text(CONFIG)
    s = TermServer(tmp_path, fake_claude, fake_opencode).start()
    yield s
    s.stop()


def frame_drawn(page) -> None:
    """The first frame is drawn, and the frame size has settled: opening a
    browser fits its viewport to the tab, so the first frames may be of the
    old size."""
    page.wait_for_selector("#v-browser .br-wait", state="hidden", timeout=15_000)
    size = "(() => { const c = document.querySelector('#v-browser .br-screen');"
    size += " return c ? `${c.width}x${c.height}` : ''; })()"
    seen, deadline = None, time.time() + 10
    while time.time() < deadline:
        now = page.evaluate(size)
        if now and now != "0x0" and now == seen:
            return
        seen = now
        page.wait_for_timeout(600)


def tap(page, x: int, y: int) -> None:
    """Click the canvas where the page's point (x, y) is drawn."""
    r = page.evaluate(
        "(() => { const c = document.querySelector('#v-browser .br-screen');"
        " const b = c.getBoundingClientRect();"
        " return {l: b.left, t: b.top, sx: b.width / c.width, sy: b.height / c.height}; })()"
    )
    page.mouse.click(r["l"] + x * r["sx"], r["t"] + y * r["sy"])


def test_the_pane_streams_the_page_and_a_click_on_it_reaches_the_page(
    server,
    browser,  # noqa: F811
    site,  # noqa: F811
):
    card = server.call("browser.spawn", {"url": site})
    page = browser.new_page()
    page.goto(f"{server.url}#b={card['id']}")
    frame_drawn(page)
    assert page.locator("#v-browser .br-holder").inner_text() == "you"
    tap(page, 150, 120)
    page.wait_for_function(
        "document.querySelector('#tablist .tab.on')?.title.includes('clicked')",
        timeout=10_000,
    )


def test_the_pane_types_into_the_focused_field(server, browser, site):  # noqa: F811
    card = server.call("browser.spawn", {"url": site})
    page = browser.new_page()
    page.goto(f"{server.url}#b={card['id']}")
    frame_drawn(page)
    tap(page, 400, 315)  # the Email field, placed by the test page
    page.keyboard.type("ana")
    page.wait_for_function(
        "document.querySelector('#tablist .tab.on')?.title.includes('typed ana')",
        timeout=10_000,
    )
