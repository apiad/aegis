# ruff: noqa: F811  (pytest fixtures imported from other test modules)
"""Secret cards in a browser: reveal once, then "already viewed" on a reload
and in another browser; a requested secret typed into its card lands in the
file; both through a linked server too (secret_ops.py)."""

import secrets

import pytest

from .test_browser import (  # noqa: F401
    beta_session,
    browser,
    linked,
    new_page,
    page,
    server,
    spawn,
    turns_done,
)
from .test_canvas_browser import mcp

pytestmark = [pytest.mark.browser, pytest.mark.slow]

CARD = ".row.secret .scard"
ASK = ".row.secret_request .scard"


def reveal_then_gone(page, browser, url):
    page.click(f"{CARD} button:has-text('Reveal')")
    page.wait_for_selector(f"{CARD} .sbody.val")
    shown = page.inner_text(f"{CARD} .sbody.val")
    errors: list = []
    other = new_page(browser, errors)
    other.goto(url)
    other.wait_for_selector(f"{CARD}.gone")
    assert "Already viewed" in other.inner_text(CARD)
    other.close()
    assert not errors
    return shown


def provide(page, value):
    page.fill(f"{ASK} input[type=password]", value)
    page.click(f"{ASK} button:has-text('Provide')")
    # On a linked server the line can land a render after the class.
    page.wait_for_selector(f"{ASK}.gone:has-text('Provided:')")


@pytest.mark.quarantine("#584")
def test_a_secret_is_revealed_once_and_a_request_writes_its_file(server, page, browser):  # noqa: F811
    page.goto(server.url)
    page.wait_for_selector("#a2[data-view=fleet]")
    sid = spawn(page)
    value = secrets.token_urlsafe(24)
    (server.root / "pw").write_text(value + "\n")
    mcp(
        page, 1, "secret_send", {"path": str(server.root / "pw"), "caption": "db admin"}
    )
    page.wait_for_selector(CARD)
    assert value not in page.content()  # before the reveal, nowhere in the page
    shown = reveal_then_gone(page, browser, f"{server.url}#s={sid}")
    assert shown.startswith(value)
    page.reload()
    page.wait_for_selector(f"{CARD}.gone")
    assert "Already viewed" in page.inner_text(CARD) and value not in page.content()

    mcp(
        page,
        2,
        "secret_request",
        {"path": str(server.root / "tok"), "prompt": "A GitHub token"},
    )
    page.wait_for_selector(f"{ASK} input[type=password]")
    assert "A GitHub token" in page.inner_text(ASK)
    typed = secrets.token_urlsafe(24)
    provide(page, typed)
    assert (server.root / "tok").read_text() == typed
    turns_done(page, 3)  # the wake ran a turn
    assert typed not in page.content()
    page.set_viewport_size({"width": 390, "height": 800})
    width = page.evaluate("document.documentElement.scrollWidth")
    assert width <= 390, width
    assert page.errors == []


@pytest.mark.quarantine("#571")
def test_secret_cards_work_on_a_linked_servers_session(linked, browser, page):  # noqa: F811
    lid = beta_session(linked, browser)
    root = linked.beta.root
    value = secrets.token_urlsafe(24)
    (root / "pw").write_text(value)
    page.goto(f"{linked.alpha.url}#s=beta/{lid}")
    page.wait_for_selector("#a2[data-view=session]")
    turns_done(page, 1)
    mcp(page, 2, "secret_send", {"path": str(root / "pw")})
    page.wait_for_selector(CARD)
    assert reveal_then_gone(
        page, browser, f"{linked.alpha.url}#s=beta/{lid}"
    ).startswith(value)
    mcp(page, 3, "secret_request", {"path": str(root / "tok"), "prompt": "p"})
    page.wait_for_selector(f"{ASK} input[type=password]")
    typed = secrets.token_urlsafe(24)
    provide(page, typed)
    assert (root / "tok").read_text() == typed
    assert page.errors == []
