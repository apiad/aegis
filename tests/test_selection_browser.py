"""A selection in the transcript, quoted for the composer (#479): the clean
text of a range (quote.js), the selection bar, the row menus and the link to
a row. Every action starts with a draft in the composer and checks that it
survives, after the quote."""

import pytest

from .test_browser import EXPECTED_CONSOLE, browser, server, spawn, turns_done  # noqa: F401

pytestmark = [pytest.mark.browser, pytest.mark.slow]

QUOTE = "import('/static/js/quote.js')"


@pytest.fixture
def page(browser):  # noqa: F811
    ctx = browser.new_context(
        viewport={"width": 1280, "height": 800},
        permissions=["clipboard-read", "clipboard-write"],
    )
    pg = ctx.new_page()
    pg.errors = []
    pg.on("pageerror", lambda e: pg.errors.append(str(e)))
    pg.on(
        "console",
        lambda m: (
            m.type == "error"
            and not any(x in m.text for x in EXPECTED_CONSOLE)
            and pg.errors.append(m.text)
        ),
    )
    yield pg
    ctx.close()


def test_clean_text_drops_the_interface_and_quotes_every_line(server, page):  # noqa: F811
    page.goto(server.url)
    spawn(page, "/md before\\n```python\\nx = 1\\n```\\nafter")
    reply = ".row.prose .body"
    page.wait_for_selector(f"{reply} .fence .copy")
    page.click(f"{reply} .fence .copy")  # the button now reads "copied"
    page.wait_for_selector(f"{reply} .fence .copy[data-state=done]")
    text = page.evaluate(
        f"""async () => {{
          const {{ rangeText }} = await {QUOTE};
          const r = document.createRange();
          r.selectNodeContents(document.querySelector("{reply}"));
          return rangeText(r);
        }}"""
    )
    assert "before" in text and "x = 1" in text and "after" in text
    assert "python" not in text and "copied" not in text
    assert page.evaluate(f"async () => (await {QUOTE}).quoted('a\\nb')") == "> a\n> b"
    assert page.errors == []


def test_insert_keeps_the_draft_after_the_quote_and_runs_the_input_path(server, page):  # noqa: F811
    page.goto(server.url)
    key = spawn(page)
    # The composer's own input path runs: an unknown command is marked...
    page.evaluate(f"async () => (await {QUOTE}).insertIntoComposer('> x', {{ prefix: '/nosuch\\n' }})")
    page.wait_for_function("document.getElementById('composer').classList.contains('bad')")
    page.fill("#input", "hello")
    page.evaluate(
        f"async () => (await {QUOTE}).insertIntoComposer('> x', {{ prefix: '/btw\\n' }})"
    )
    # ...and /btw is not.
    page.wait_for_function("!document.getElementById('composer').classList.contains('bad')")
    assert page.input_value("#input") == "/btw\n> x\n\nhello"
    assert page.evaluate(
        "() => { const i = document.getElementById('input');"
        " return i.selectionStart === i.value.length && document.activeElement === i; }"
    )
    assert page.evaluate(f"localStorage.getItem('aegis.draft.{key}')") == "/btw\n> x\n\nhello"
    assert page.errors == []
