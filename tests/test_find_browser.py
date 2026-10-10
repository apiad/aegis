"""The find bar: a search over the open transcript's entries, not its DOM
(#250). Kept out of test_browser.py, whose server fixture it borrows."""

import pytest

from .test_browser import browser, page, server, spawn, turns_done  # noqa: F401

pytestmark = [pytest.mark.browser, pytest.mark.slow]

COUNT = "document.querySelector('#find-n').textContent"
# The current match's text, and the row it is in.
NOW = """() => {
  const h = CSS.highlights.get('find-now');
  const r = h && [...h][0];
  return r ? [r.toString(), r.startContainer.parentElement.closest('.row').classList[1]] : null;
}"""


def counted(pg, text: str) -> None:
    pg.wait_for_function(f"{COUNT} === {text!r}", timeout=4000)


def test_find_reaches_a_folded_collapsed_row_steps_and_closes_with_esc(
    server,  # noqa: F811
    page,  # noqa: F811
    tmp_path,
):
    # The tool's output is the only place the word is outside a message: the
    # row is closed, and the wire leaves the output on the server.
    (tmp_path / "notes.txt").write_text("first\nsecond Needle\nthird\n")
    page.goto(server.url)
    spawn(page, f"/read {tmp_path / 'notes.txt'}")
    page.fill("#input", "/md a needle in prose")
    page.press("#input", "Enter")
    turns_done(page, 2)
    tool = page.locator(".row.tool").first
    assert "Needle" not in tool.inner_html()
    assert tool.locator("details").evaluate("d => d.open") is False

    # From the message box, Ctrl+F is the browser's.
    page.focus("#input")
    page.keyboard.press("Control+f")
    assert page.locator("#find").is_hidden()

    page.locator("#entries").click(position={"x": 5, "y": 5})  # out of the composer
    page.keyboard.press("z")  # fold the tool calls: the row shows its run's line
    assert tool.locator("details").is_hidden()
    page.keyboard.press("Control+f")
    assert page.locator("#find").is_visible()
    assert page.evaluate("document.activeElement.id") == "find-q"

    page.keyboard.type("needle")
    # The second prompt, the tool's output and the reply. It starts at the
    # last one: nothing matches below the row in view.
    counted(page, "3 of 3")
    assert page.evaluate(NOW) == ["needle", "prose"]

    page.keyboard.press("Shift+Enter")
    counted(page, "2 of 3")
    page.keyboard.press("Shift+Enter")
    counted(page, "1 of 3")
    # The run opened, the row's details opened, and its output came from the
    # server with the match highlighted in it.
    assert tool.locator("details").is_visible()
    assert tool.locator("details").evaluate("d => d.open") is True
    page.wait_for_function(f"({NOW})()?.[0] === 'Needle'", timeout=4000)
    assert page.evaluate(NOW)[1] == "tool"
    assert page.evaluate(
        "CSS.highlights.get('find-now').values().next().value"
        ".startContainer.parentElement.matches('pre.out')"
    )
    assert page.evaluate(
        "document.querySelector('.row.sel') === document.querySelector('.row.tool')"
    )
    # The other matches are painted too.
    assert page.evaluate("CSS.highlights.get('find').size") >= 2

    page.keyboard.press("Enter")
    counted(page, "2 of 3")
    page.keyboard.press("Enter")
    page.keyboard.press("Enter")
    counted(page, "1 of 3")  # it wraps
    page.click("#find-prev")
    counted(page, "3 of 3")

    page.fill("#find-q", "no such text")
    counted(page, "no matches")
    assert page.locator("#find-prev").is_disabled()

    # Esc closes it, clears the paint and gives the focus back to the
    # transcript, without interrupting the agent.
    page.keyboard.press("Escape")
    assert page.locator("#find").is_hidden()
    assert page.evaluate("document.activeElement.id") == "tr"
    assert page.evaluate("CSS.highlights.size") == 0
    assert page.locator(".row.sys .body", has_text="interrupted").count() == 0
    # Opened again, it searches what it held.
    page.keyboard.press("Control+f")
    assert page.input_value("#find-q") == "no such text"
    assert page.errors == []
