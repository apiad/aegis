# ruff: noqa: F811  (pytest fixtures imported from other test modules)
"""An agent's browser docked beside its transcript, in a real browser: the
stream draws in the pane, and the handover banner's Give back wakes the agent
(#564, browserview.js and canvas.js)."""

import pytest

from .test_browser import browser, page, server, spawn, turns_done  # noqa: F401
from .test_browsers import site  # noqa: F401
from .test_canvas_browser import mcp

pytestmark = [pytest.mark.browser, pytest.mark.slow]

PANE = "#canvas .pane.docked[data-pane^='b-'] .br-view"


def test_a_docked_browser_streams_and_its_banner_gives_the_page_back(
    server, page, site
):
    page.goto(server.url)
    page.wait_for_selector("#a2[data-view=fleet]")
    spawn(page)
    mcp(page, 1, "browser_open", {"url": site})
    pane = page.locator(PANE)
    pane.wait_for(timeout=10_000)
    pane.locator(".br-wait").wait_for(state="hidden", timeout=15_000)
    assert pane.locator(".br-holder").inner_text() == "agent"
    assert pane.locator(".br-take").is_visible()
    mcp(page, 2, "browser_handover", {"reason": "Tick the box"})
    pane.locator(".br-ask").wait_for(timeout=10_000)
    assert "Tick the box" in pane.locator(".br-ask").inner_text()
    assert pane.locator(".br-holder").inner_text() == "you"
    pane.locator(".br-ask-back").click()
    # Give back wakes the agent: its inbox turn runs and ends.
    turns_done(page, 3)
    pane.locator(".br-ask").wait_for(state="hidden")
    assert pane.locator(".br-holder").inner_text() == "agent"
    assert not page.errors
