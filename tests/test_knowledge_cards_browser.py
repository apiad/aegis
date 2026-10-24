# ruff: noqa: F811  (pytest fixtures imported from other test modules)
"""The knowledge cards in a browser: Approve on an agent's proposal, Undo on
its change to an approved memory, at desktop and phone width
(knowledge_ops.py, #696)."""

import pytest

from .test_browser import browser, new_page, page, server, spawn, turns_done  # noqa: F401
from .test_canvas_browser import mcp

pytestmark = [pytest.mark.browser, pytest.mark.slow]

CARD = ".row.knowledge .kcard"
MEMORY = {
    "name": "merge-after-green",
    "description": "Merge a PR only after every check passed",
    "body": "The author merges without being asked.",
    "type": "feedback",
}


def stored(server, name: str) -> str | None:
    found = list(
        (server.root / ".aegis" / "state" / "knowledge").glob(f"*/memories/{name}.md")
    )
    return found[0].read_text() if found else None


def last_says(page, text: str) -> None:
    page.wait_for_function(
        "([s, t]) => [...document.querySelectorAll(s)].at(-1)?.textContent.includes(t)",
        arg=[CARD, text],
    )


def fits(page) -> None:
    page.set_viewport_size({"width": 390, "height": 800})
    width = page.evaluate("document.documentElement.scrollWidth")
    assert width <= 390, width
    page.set_viewport_size({"width": 1280, "height": 800})


def test_the_card_approves(server, page):
    page.goto(server.url)
    page.wait_for_selector("#a2[data-view=fleet]")
    spawn(page, "hello")
    mcp(page, 2, "memory_save", MEMORY)
    page.wait_for_selector(f"{CARD} button:has-text('Approve')")
    text = page.inner_text(CARD)
    assert "memory proposed by the agent · feedback · permanent" in text
    assert "merge-after-green" in text and "without being asked" in text
    assert "status: proposed" in stored(server, "merge-after-green")
    fits(page)
    page.click(f"{CARD} button:has-text('Approve')")
    last_says(page, "Approved")
    assert "status: approved" in stored(server, "merge-after-green")
    turns_done(page, 3)  # the wake ran a turn
    fits(page)
    assert page.errors == []


def test_the_card_undoes(server, page):
    page.goto(server.url)
    page.wait_for_selector("#a2[data-view=fleet]")
    spawn(page, "hello")
    mcp(page, 2, "memory_save", {**MEMORY, "asked": True})
    assert page.locator(CARD).count() == 0  # asked for: no card
    changed = {**MEMORY, "body": "Only the required checks."}
    mcp(page, 3, "memory_save", changed)
    page.wait_for_selector(f"{CARD} button:has-text('Undo')")
    assert "memory changed by the agent" in page.inner_text(CARD)
    assert "required checks" in stored(server, "merge-after-green")
    fits(page)
    page.click(f"{CARD} button:has-text('Undo')")
    last_says(page, "Undone")
    assert "without being asked" in stored(server, "merge-after-green")
    turns_done(page, 4)

    mcp(page, 5, "memory_delete", {"name": "merge-after-green"})
    last_says(page, "deleted by the agent")
    assert stored(server, "merge-after-green") is None
    page.locator(CARD).last.locator("button:has-text('Undo')").click()
    last_says(page, "Undone")
    assert "without being asked" in stored(server, "merge-after-green")
    fits(page)
    assert page.errors == []
