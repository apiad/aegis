"""Markdown in the transcript: tables that fit a phone, fences coloured by a
loader that fetches only what they need, task lists, the person's own
messages (#323)."""

import pytest

from .test_browser import EXPECTED_CONSOLE, browser, server, spawn, turns_done  # noqa: F401

pytestmark = [pytest.mark.browser, pytest.mark.slow]


@pytest.fixture
def make_page(browser):  # noqa: F811
    made = []

    def make(width: int):
        ctx = browser.new_context(viewport={"width": width, "height": 900})
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
        pg.fetched = []
        pg.on(
            "request",
            lambda r: (
                "/vendor/hljs/" in r.url
                and pg.fetched.append(r.url.split("/vendor/hljs/")[1])
            ),
        )
        made.append(ctx)
        return pg

    yield make
    for c in made:
        c.close()


def md(text: str) -> str:
    """The fake claude's /md answers with this text; it turns \\n back into newlines."""
    return "/md " + text.replace("\n", "\\n")


TABLE = (
    "| Run | Before (ms) | After (ms) | Notes |\n"
    "|---|--:|--:|---|\n"
    "| transcript, 5k rows | 2210 | 2190 | unchanged, out of scope |\n"
    "| cold boot | 1840 | 612 | lazy import of `markdown-it` |\n"
    "| a \\| b | | 3 | the header has fewer cells | extra |"
)

SPLIT_WORDS = """() => {
  const bad = [];
  for (const cell of document.querySelectorAll('.row.prose .md td, .row.prose .md th')) {
    const w = document.createTreeWalker(cell, NodeFilter.SHOW_TEXT);
    for (let n; (n = w.nextNode()); ) {
      for (const m of n.data.matchAll(/[^\\s-]+/g)) {
        const r = document.createRange();
        r.setStart(n, m.index);
        r.setEnd(n, m.index + m[0].length);
        if (r.getClientRects().length > 1) bad.push(m[0]);
      }
    }
  }
  return bad;
}"""


def test_a_table_on_a_phone_is_cards_and_breaks_no_word(server, make_page):  # noqa: F811
    page = make_page(390)
    page.goto(server.url)
    spawn(page, md(f"Measured:\n\n{TABLE}\n\nDone."))
    tr = page.locator(".row.prose .md tbody tr").first
    assert tr.evaluate("t => getComputedStyle(t).display") == "block", "a card"
    assert tr.locator("td").nth(1).get_attribute("data-label") == "Before (ms)"
    assert page.evaluate(SPLIT_WORDS) == []
    assert page.evaluate("document.documentElement.scrollWidth") <= 390
    assert page.errors == []


def test_a_wide_table_scrolls_with_its_first_column_pinned(server, make_page):  # noqa: F811
    page = make_page(1280)
    page.goto(server.url)
    cols = " | ".join(f"column {i}" for i in range(14))
    row = " | ".join(f"value {i}" for i in range(14))
    spawn(page, md(f"| {cols} |\n|{'---|' * 14}\n| {row} |"))
    tw = page.locator(".row.prose .md .tw").first
    first = tw.locator("td").first
    assert (
        tw.evaluate("t => getComputedStyle(t.querySelector('table')).display")
        == "table"
    )
    before = first.bounding_box()["x"]
    tw.evaluate("t => { t.scrollLeft = 300; }")
    page.wait_for_timeout(50)
    assert abs(first.bounding_box()["x"] - before) < 1, "the first column stays"
    assert page.evaluate(SPLIT_WORDS) == []
    assert page.errors == []


def test_a_python_fence_fetches_the_core_and_python_only(server, make_page):  # noqa: F811
    page = make_page(1280)
    page.goto(server.url)
    spawn(page, md("Here:\n\n```python\ndef f():\n    return 1\n```"))
    fence = page.locator(".row.prose .fence").first
    fence.locator(".hljs-keyword").first.wait_for()
    assert fence.locator(".fh .lang").inner_text().lower() == "python"
    assert sorted(page.fetched) == ["core.min.js", "languages/python.min.js"]
    assert page.errors == []


def test_a_transcript_with_no_code_fetches_no_highlighter(server, make_page):  # noqa: F811
    page = make_page(1280)
    page.goto(server.url)
    spawn(page, md("Plain words, **bold**, a list:\n\n- one\n- two"))
    page.wait_for_timeout(300)
    assert page.fetched == []
    assert page.errors == []


def test_a_reply_cut_inside_a_fence_shows_its_text_plain(server, make_page):  # noqa: F811
    page = make_page(1280)
    page.goto(server.url)
    spawn(page, md("Start:\n\n```python\nx = 1\ny = 2"))
    fence = page.locator(".row.prose .fence").first
    assert fence.get_attribute("data-open") is not None
    assert "y = 2" in fence.inner_text()
    page.wait_for_timeout(300)
    assert fence.locator("[class^=hljs-]").count() == 0
    assert page.errors == []


def test_a_fence_with_a_huge_line_scrolls_and_the_page_answers(server, make_page):  # noqa: F811
    page = make_page(1280)
    page.goto(server.url)
    spawn(page, md("```js\n" + "a=1;" * 75_000 + "\n```"))
    # Read the live block in one turn: the row is redrawn when it is read, and
    # a locator's evaluate can land on the row it replaced (0 by 0, detached).
    page.locator(".row.prose .fence pre").first.wait_for()
    assert page.evaluate(
        "(p => p.scrollWidth > p.clientWidth)(document.querySelector('.row.prose .fence pre'))"
    )
    assert page.evaluate("document.documentElement.scrollWidth") <= 1280
    assert page.errors == []


def test_task_lists_draw_checkboxes(server, make_page):  # noqa: F811
    page = make_page(1280)
    page.goto(server.url)
    spawn(page, md("Steps:\n\n- [x] failing first\n- [ ] passing on CI\n- plain item"))
    boxes = page.locator(".row.prose li.task input[type=checkbox]")
    assert boxes.count() == 2
    assert boxes.evaluate_all("bs => bs.map(b => b.checked && b.disabled)") == [
        True,
        False,
    ]
    assert "[x]" not in page.locator(".row.prose").last.inner_text()
    assert page.errors == []


def test_the_persons_message_is_markdown_with_its_line_breaks(server, make_page):  # noqa: F811
    page = make_page(1280)
    page.goto(server.url)
    spawn(page, "first line\nsecond line\n\n- a\n- `b`")
    body = page.locator(".row.user .md").first
    assert body.locator("br").count() == 1
    assert body.locator("li").count() == 2
    assert body.locator("li code").inner_text() == "b"
    assert page.errors == []
