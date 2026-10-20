"""Pointing inside files and pages (#479, spec 07): a selection or a press in
the viewer, the Files view or a sandboxed page asks about that place, with
its locator in front of the quote, or collects comments that go out as one
message. Every action starts with a draft in the composer and checks that it
survives, after what was inserted."""

import json

import pytest

from .test_browser import EXPECTED_CONSOLE, spawn, turns_done
from .test_browser import browser, server  # noqa: F401

pytestmark = [pytest.mark.browser, pytest.mark.slow]

CODE = "".join(f"line{i} = {i}\n" for i in range(1, 9))


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


def send(pg, n, paths):
    pg.fill("#input", f"/mcp file_send {json.dumps({'paths': paths})}")
    pg.press("#input", "Enter")
    turns_done(pg, n)


def view(pg, ready: str) -> None:
    """Open the last sent file's card in the viewer and wait for `ready`."""
    pg.locator(".row.file .fbar .view").last.click()
    pg.wait_for_selector(f"#viewer {ready}")


def select_text(pg, sel: str, start: str, end: str | None = None) -> None:
    """Select from the first `start` to the end of the first `end` after it
    (or of `start`) in the text of `sel`, as a drag would."""
    pg.evaluate(
        """([sel, start, end]) => {
          const root = document.querySelector(sel);
          const text = root.textContent;
          const a = text.indexOf(start);
          const b = end ? text.indexOf(end, a) + end.length : a + start.length;
          if (a < 0 || b < a) throw new Error("no " + start);
          const r = document.createRange();
          const w = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
          let seen = 0, started = false;
          for (let n; (n = w.nextNode()); seen += n.data.length) {
            if (!started && a < seen + n.data.length) { r.setStart(n, a - seen); started = true; }
            if (started && b <= seen + n.data.length) { r.setEnd(n, b - seen); break; }
          }
          getSelection().removeAllRanges();
          getSelection().addRange(r);
        }""",
        [sel, start, end],
    )


def test_ask_on_lines_of_a_text_file_carries_path_and_line_numbers(
    server,  # noqa: F811
    page,
):
    (server.root / "src").mkdir()
    (server.root / "src" / "x.py").write_text(CODE)
    page.goto(server.url)
    key = spawn(page)
    send(page, 1, ["src/x.py"])
    page.fill("#input", "later")
    view(page, ".src code")
    select_text(page, "#viewer .src code", "line3", "= 4")
    page.wait_for_selector(".sbar")
    labels = page.locator(".sbar button").evaluate_all(
        "bs => bs.map(b => b.getAttribute('aria-label'))"
    )
    assert labels == ["Ask", "Ask aside", "New session", "Comment", "Copy"]
    page.click(".sbar [data-id=ask]")
    # The viewer closes over the composer, which holds the quote then the draft.
    page.wait_for_selector("#viewer", state="hidden")
    assert page.input_value("#input") == "`x.py:3-4`\n> line3 = 3\n> line4 = 4\n\nlater"

    # The same in the Files editor, by CodeMirror's line numbers; the quote
    # goes to the session last shown in this window.
    page.fill("#input", "later")
    page.goto(f"{server.url.split('#')[0]}#files=src/x.py:3")
    page.wait_for_selector(".v-files .cm-content")
    page.wait_for_function("document.activeElement?.closest('.cm-content')")
    page.keyboard.press("Shift+ArrowDown")
    page.keyboard.press("Shift+ArrowDown")
    page.wait_for_selector(".sbar")
    page.click(".sbar [data-id=ask]")
    page.wait_for_function(f"location.hash === '#s={key}'")
    assert page.input_value("#input") == (
        "`src/x.py:3-4`\n> line3 = 3\n> line4 = 4\n\nlater"
    )
    assert page.errors == []


def test_ask_in_a_markdown_file_names_the_nearest_heading(server, page):  # noqa: F811
    (server.root / "notes.md").write_text(
        "# Notes\n\nintro\n\n## Setup\n\nrun kiwi here\n"
    )
    page.goto(server.url)
    spawn(page)
    send(page, 1, ["notes.md"])
    page.fill("#input", "later")
    view(page, ".doc h2")
    select_text(page, "#viewer .doc", "kiwi")
    page.wait_for_selector(".sbar")
    page.click(".sbar [data-id=aside]")
    assert page.input_value("#input") == "/btw\n`notes.md` § Setup\n> kiwi\n\nlater"

    # A right-click on a block with nothing selected asks about that block.
    page.fill("#input", "later")
    view(page, ".doc h2")
    page.locator("#viewer .doc p").first.click(button="right")
    page.wait_for_selector(".cmenu [data-id=ask]")
    page.click(".cmenu [data-id=ask]")
    assert page.input_value("#input") == "`notes.md` § Notes\n> intro\n\nlater"
    assert page.errors == []


def comment(pg, sel: str, start: str, note: str) -> None:
    select_text(pg, sel, start)
    pg.wait_for_selector(".sbar [data-id=comment]")
    pg.click(".sbar [data-id=comment]")
    pg.keyboard.type(note)  # the new comment's note has the focus


def test_three_comments_become_one_message_and_vanish(server, page):  # noqa: F811
    (server.root / "x.py").write_text(CODE)
    page.goto(server.url)
    spawn(page)
    send(page, 1, ["x.py"])
    page.fill("#input", "later")
    view(page, ".src code")
    code = "#viewer .src code"
    comment(page, code, "line2 = 2", "first")
    comment(page, code, "line7 = 7", "third")
    # A right-click on a line with nothing selected comments on that line.
    page.evaluate("getSelection().removeAllRanges()")
    box = page.evaluate(
        """() => {
          const code = document.querySelector('#viewer .src code');
          const w = document.createTreeWalker(code, NodeFilter.SHOW_TEXT);
          for (let n; (n = w.nextNode()); ) {
            const i = n.data.indexOf('line5');
            if (i < 0) continue;
            const r = document.createRange();
            r.setStart(n, i);
            r.setEnd(n, i + 3);
            const b = r.getBoundingClientRect();
            return { x: b.left + b.width / 2, y: b.top + b.height / 2 };
          }
        }"""
    )
    page.mouse.click(box["x"], box["y"], button="right")
    page.click(".cmenu [data-id=comment]")
    page.keyboard.type("second")
    tray = page.locator("#viewer .ctray")
    assert tray.locator(".cn").inner_text() == "3 comments"
    assert page.locator("#viewer .pin").count() == 3
    # Removing one and making it again keeps the order of making.
    tray.locator("li").nth(2).locator(".rm").click()
    assert tray.locator(".cn").inner_text() == "2 comments"
    page.evaluate("getSelection().removeAllRanges()")
    page.mouse.click(box["x"], box["y"], button="right")
    page.click(".cmenu [data-id=comment]")
    page.keyboard.type("second")

    tray.locator("[data-act=send]").click()
    page.wait_for_selector("#viewer", state="hidden")
    assert page.input_value("#input") == (
        "Comments on x.py:\n"
        "1. `x.py:2`\n   > line2 = 2\n   first\n"
        "2. `x.py:7`\n   > line7 = 7\n   third\n"
        "3. `x.py:5`\n   > line5 = 5\n   second"
        "\n\nlater"
    )
    assert (
        page.locator(".row.user").count() == 1
    )  # only the file_send; nothing went out
    view(page, ".src code")
    assert page.locator("#viewer .ctray").is_hidden()
    assert page.locator("#viewer .pin").count() == 0
    assert page.errors == []


def test_unsent_comments_survive_a_reload_in_that_browser(server, page):  # noqa: F811
    (server.root / "x.py").write_text(CODE)
    page.goto(server.url)
    key = spawn(page)
    send(page, 1, ["x.py"])
    view(page, ".src code")
    comment(page, "#viewer .src code", "line4 = 4", "kept")
    page.reload()
    page.wait_for_function(f"location.hash === '#s={key}'")
    view(page, ".src code")
    tray = page.locator("#viewer .ctray")
    assert tray.locator(".cn").inner_text() == "1 comment"
    assert tray.locator(".note").input_value() == "kept"
    page.wait_for_selector("#viewer .pin[data-n='1']", state="attached")
    tray.locator("[data-act=discard]").click()
    assert tray.is_hidden()
    page.reload()
    view(page, ".src code")
    assert page.locator("#viewer .ctray").is_hidden()
    assert page.errors == []
