"""Copy buttons on messages, code blocks and tool output, read back from the
clipboard (#247).

Kept out of test_browser.py, whose server fixture it borrows; the page here is
its own, in a context allowed to read the clipboard."""

import pytest

from .test_browser import EXPECTED_CONSOLE, browser, server, spawn, turns_done  # noqa: F401

pytestmark = [pytest.mark.browser, pytest.mark.slow]


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


def copied(pg, button) -> str:
    """Click ``button`` on an empty clipboard; what it put there."""
    pg.evaluate("navigator.clipboard.writeText('')")
    button.hover()
    button.click()
    pg.wait_for_function(
        "b => b.dataset.state === 'done' || b.dataset.state === 'err'",
        arg=button.element_handle(),
    )
    assert button.get_attribute("data-state") == "done", button.get_attribute("title")
    return pg.evaluate("navigator.clipboard.readText()")


PROMPT = r"/md Run this:\n\n```py\nprint('hi')\n```\n\n**Done.**"
REPLY = "Run this:\n\n```py\nprint('hi')\n```\n\n**Done.**"


def test_a_message_and_its_code_block_copy_their_markdown_and_their_code(
    server,  # noqa: F811
    page,
):
    page.goto(server.url)
    spawn(page, PROMPT)
    prose = page.locator(".row.prose").last
    message = prose.locator(":scope > .body > .copy")
    code = prose.locator("pre > .copy")
    # Unobtrusive: nothing shows until the row is hovered.
    page.mouse.move(0, 0)
    assert message.evaluate("b => getComputedStyle(b).opacity") == "0"
    prose.hover()
    page.wait_for_function(  # it fades in
        "b => getComputedStyle(b).opacity === '1'", arg=message.element_handle()
    )
    icon = message.locator("svg")  # the icon's class is "copy" too: once, it hid
    assert icon.evaluate("s => getComputedStyle(s).opacity") == "1"
    assert icon.bounding_box()["width"] > 0

    assert copied(page, message) == REPLY, "the Markdown, not the rendered text"
    assert message.inner_text() == "copied"
    assert copied(page, code) == "print('hi')\n"
    user = page.locator(".row.user").first.locator(":scope > .body > .copy")
    assert copied(page, user) == PROMPT
    # The confirmation goes away.
    page.wait_for_function(
        "b => !b.dataset.state", arg=message.element_handle(), timeout=4000
    )
    assert page.errors == []


def test_a_collapsed_tool_row_copies_its_whole_output_and_stays_closed(
    server,  # noqa: F811
    page,
    tmp_path,
):
    """The row holds only the last 40 lines; the copy is all 100."""
    lines = [f"line {n}" for n in range(1, 101)]
    (tmp_path / "long.txt").write_text("\n".join(lines) + "\n")
    page.goto(server.url)
    spawn(page, f"/read {tmp_path / 'long.txt'}")
    tool = page.locator(".row.tool").first
    button = tool.locator(".line > .copy")
    assert copied(page, button) == "\n".join(lines) + "\n"
    assert tool.locator("details").evaluate("d => d.open") is False
    assert page.errors == []


def test_the_c_key_copies_the_selected_row(server, page):  # noqa: F811
    page.goto(server.url)
    spawn(page, PROMPT)
    page.locator("#entries").click(position={"x": 5, "y": 5})  # out of the composer
    page.keyboard.press("G")  # the last row: the turn's "done in" note
    page.keyboard.press("k")  # the reply
    assert page.evaluate(
        "document.querySelector('.row.sel')?.classList.contains('prose')"
    )
    page.evaluate("navigator.clipboard.writeText('')")
    page.keyboard.press("c")
    page.wait_for_function(
        "document.querySelector('.row.sel > .body > .copy').dataset.state === 'done'"
    )
    assert page.evaluate("navigator.clipboard.readText()") == REPLY
    # Tab reaches a copy button like any other button.
    page.locator(".row.prose").last.locator(":scope > .body > .copy").focus()
    page.wait_for_function("getComputedStyle(document.activeElement).opacity === '1'")
    assert page.errors == []
