# ruff: noqa: F811  (pytest fixtures imported from other test modules)
"""The config card in a browser: the diff, Apply writes the suggested text,
Not now writes nothing (config_suggest.py, #612)."""

import pytest

from .test_browser import browser, new_page, page, server, spawn, turns_done  # noqa: F401
from .test_canvas_browser import mcp

pytestmark = [pytest.mark.browser, pytest.mark.slow]

CARD = ".row.config .ccard"
NOTE = "# a queue for tests\n"


def suggested(page, server, n: int) -> str:
    text = (server.root / ".aegis.yaml").read_text() + NOTE
    mcp(page, n, "config_suggest", {"yaml": text, "why": "a queue for tests"})
    page.wait_for_selector(f"{CARD} button:has-text('Apply')")
    return text


def test_apply_writes_the_text_and_not_now_writes_nothing(server, page):
    page.goto(server.url)
    page.wait_for_selector("#a2[data-view=fleet]")
    spawn(page, "hello")
    text = suggested(page, server, 2)
    card = page.inner_text(CARD)
    assert "a queue for tests" in card and "+# a queue for tests" in card
    page.click(f"{CARD} button:has-text('Apply')")
    page.wait_for_function(
        "s => document.querySelector(s)?.textContent.includes('Applied')", arg=CARD
    )
    assert (server.root / ".aegis.yaml").read_text() == text
    turns_done(page, 3)  # the wake ran a turn

    before = (server.root / ".aegis.yaml").read_text()
    (server.root / ".aegis.yaml").write_text(before.replace(NOTE, ""))
    suggested(page, server, 4)
    later = page.locator(CARD).last
    later.locator("button:has-text('Not now')").click()
    page.wait_for_function(
        "s => [...document.querySelectorAll(s)].at(-1).textContent.includes('Declined')",
        arg=CARD,
    )
    assert NOTE not in (server.root / ".aegis.yaml").read_text()
    page.set_viewport_size({"width": 390, "height": 800})
    width = page.evaluate("document.documentElement.scrollWidth")
    assert width <= 390, width
    assert page.errors == []
