"""Every artifact's bar has a Phone width toggle (#610).

On a desktop the person could not see how a page lays out on a phone. The
toggle narrows the frame to a phone's viewport, so the page's own media
queries answer, and turns it back. Kept out of test_browser.py, whose server
and artifact helpers it borrows."""

import re

import pytest
from playwright.sync_api import expect

from .test_browser import (  # noqa: F401
    _artifact,
    browser,
    page,
    server,
    spawn,
)

pytestmark = [pytest.mark.browser, pytest.mark.slow]

# `w` shows the page's own viewport width, as its media queries see it.
WIDTH = (
    '<span id="w"></span><button id="ok">ok</button>',
    "const show = () => (w.textContent = String(innerWidth));"
    "  show(); addEventListener('resize', show);"
    '  ok.onclick = () => aegis.submit({}, "ok");',
)


def test_phone_width_narrows_the_frame_and_turns_back(server, page):  # noqa: F811
    page.set_viewport_size({"width": 1400, "height": 900})
    page.goto(server.url)
    page.wait_for_selector("#a2[data-view=fleet]")
    spawn(page)
    aid, _ = _artifact(page, server, WIDTH, 0)
    width = page.frame_locator(f"iframe[data-artifact={aid}]").locator("#w")
    wide = int(width.inner_text())
    assert wide > 500
    btn = page.locator(".row.artifact .acard .btn.phone")
    btn.click()
    assert btn.get_attribute("aria-pressed") == "true"
    page.wait_for_function(
        f"document.querySelector('iframe[data-artifact={aid}]').clientWidth <= 390"
    )
    # The page hears its resize after the frame narrows; wait for it.
    expect(width).to_have_text(
        re.compile(r"^([0-9]{1,2}|[1-2][0-9]{2}|3[0-8][0-9]|390)$")
    )
    btn.click()
    assert btn.get_attribute("aria-pressed") == "false"
    page.wait_for_function(
        f"document.querySelector('iframe[data-artifact={aid}]').clientWidth > 500"
    )


def test_the_viewer_opens_a_page_at_the_width_its_card_chose(server, page):  # noqa: F811
    page.set_viewport_size({"width": 1400, "height": 900})
    page.goto(server.url)
    page.wait_for_selector("#a2[data-view=fleet]")
    spawn(page)
    aid, _ = _artifact(page, server, WIDTH, 0)
    page.locator(".row.artifact .acard .btn.phone").click()
    page.locator(".row.artifact .acard .btn.view").click()
    frame = f"#viewer iframe[data-artifact={aid}]"
    page.wait_for_selector(frame)
    btn = page.locator("#viewer .btn.phone")
    assert btn.get_attribute("aria-pressed") == "true"
    page.wait_for_function(f"document.querySelector('{frame}').clientWidth <= 390")
    btn.click()
    page.wait_for_function(f"document.querySelector('{frame}').clientWidth > 500")
