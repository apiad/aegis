"""A Claude session's text streams into the browser as it is generated (#79).

Kept out of test_browser.py, whose fixtures it borrows, so the two can change
apart."""

import pytest

from .test_browser import browser, page, server, spawn, turns_done  # noqa: F401

pytestmark = [pytest.mark.browser, pytest.mark.slow]


def test_a_claude_session_draws_text_before_the_turn_ends(server, page):  # noqa: F811
    page.goto(server.url)
    spawn(page)
    # Subscribed before the stream starts: the text can only arrive as patches.
    page.fill("#input", "/stream 6")
    page.press("#input", "Enter")
    page.wait_for_function(
        "[...document.querySelectorAll('.row.prose .body')]"
        ".some(b => b.textContent.includes('chunk1'))"
    )
    assert "chunk6" not in page.inner_text("#entries"), "drawn while it streams"
    turns_done(page, 1)
    # The whole block replaced the streamed row: one row, the whole text.
    bodies = page.locator(".row.prose .body").all_inner_texts()
    assert [b.strip() for b in bodies] == ["chunk1 chunk2 chunk3 chunk4 chunk5 chunk6"]
    assert page.errors == []
