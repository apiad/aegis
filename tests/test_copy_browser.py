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


def test_a_reply_copies_its_code_but_has_no_button_for_itself(
    server,  # noqa: F811
    page,
):
    page.goto(server.url)
    spawn(page, PROMPT)
    prose = page.locator(".row.prose").last
    assert prose.locator(":scope > .body > .copy").count() == 0, "no whole-reply button"
    code = prose.locator(".fence .copy")
    page.mouse.move(0, 0)
    assert code.evaluate("b => getComputedStyle(b).opacity") == "0"
    prose.locator(".fence").hover()
    page.wait_for_function(
        "b => getComputedStyle(b).opacity === '1'", arg=code.element_handle()
    )
    assert copied(page, code) == "print('hi')\n"
    user = page.locator(".row.user").first.locator(":scope > .body > .copy")
    assert copied(page, user) == PROMPT, "the person's message keeps its button"
    assert page.errors == []


TABLE = "| a | b |\n|---|--:|\n| x | 1 |"


def test_a_table_copies_its_markdown_and_its_cells(server, page):  # noqa: F811
    page.goto(server.url)
    spawn(page, "/md Numbers:\\n\\n" + TABLE.replace("\n", "\\n") + "\\n\\nDone.")
    blk = page.locator(".row.prose .tblk").last
    button = blk.locator(":scope > .copy")
    page.evaluate("navigator.clipboard.writeText('')")
    blk.hover()
    button.click()
    page.wait_for_function(
        "b => b.dataset.state === 'done'", arg=button.element_handle()
    )
    got = page.evaluate(
        """async () => {
          const [item] = await navigator.clipboard.read();
          return {
            text: await (await item.getType('text/plain')).text(),
            html: await (await item.getType('text/html')).text(),
          };
        }"""
    )
    assert got["text"] == TABLE, "the lines the agent wrote"
    assert "<table" in got["html"] and "data-label" not in got["html"]
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


def test_the_c_key_copies_the_selected_reply_and_marks_its_glyph(server, page):  # noqa: F811
    page.goto(server.url)
    spawn(page, PROMPT)
    page.locator("#entries").click(position={"x": 5, "y": 5})
    page.keyboard.press("G")
    page.keyboard.press("k")
    assert page.evaluate(
        "document.querySelector('.row.sel')?.classList.contains('prose')"
    )
    page.evaluate("navigator.clipboard.writeText('')")
    page.keyboard.press("c")
    page.wait_for_function(
        "document.querySelector('.row.sel > .g')?.dataset.copied === 'done'"
    )
    assert page.evaluate("navigator.clipboard.readText()") == REPLY
    stops = page.evaluate(
        "[...document.querySelectorAll('button.copy')].map(b => b.tabIndex)"
    )
    assert stops and set(stops) == {-1}
