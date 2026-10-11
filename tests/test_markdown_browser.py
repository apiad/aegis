"""Markdown in the transcript: tables that fit a phone, fences coloured by a
loader that fetches only what they need, task lists, the person's own
messages (#323)."""

import json

import pytest

from .test_browser import (  # noqa: F401
    CONFIG,
    EXPECTED_CONSOLE,
    FIXTURE,
    Server,
    browser,
    server,
    spawn,
    turns_done,
)
from .test_viewer_browser import send

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
    # The cells scrolled under the pinned one do not show through it on hover.
    first.hover()
    alpha = first.evaluate("""td => {
      const c = document.createElement('canvas').getContext('2d');
      c.fillStyle = getComputedStyle(td).backgroundColor;
      c.fillRect(0, 0, 1, 1);
      return c.getImageData(0, 0, 1, 1).data[3];
    }""")
    assert alpha == 255, alpha
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


QUOTED = "Quoted:\n\n> ```python\n> def f():\n>     return 1\n> ```"
NESTED = (
    "Steps:\n\n- one\n  - two\n\n    ```python\n    def f():\n        return 1\n    ```"
)


@pytest.mark.parametrize("text", [QUOTED, NESTED], ids=["blockquote", "nested-list"])
def test_a_fence_in_a_quote_or_a_nested_list_is_closed_and_coloured(
    server,  # noqa: F811
    make_page,
    text,
):
    page = make_page(1280)
    page.goto(server.url)
    spawn(page, md(text))
    fence = page.locator(".row.prose .fence").first
    fence.locator(".hljs-keyword").first.wait_for()
    assert fence.get_attribute("data-open") is None
    assert page.errors == []


def test_inline_code_draws_no_ligatures(server, make_page):  # noqa: F811
    page = make_page(1280)
    page.goto(server.url)
    spawn(page, md("Use `a -> b != c` here."))
    code = page.locator(".row.prose .md code").first
    assert code.evaluate("c => getComputedStyle(c).fontVariantLigatures") == "none"
    assert page.errors == []


# Reading back: each step scrolls the view up 150 px and measures how far the
# row in its middle moved. Rows above the view that were never drawn take their
# real heights as they near it (#157); the browser's scroll anchoring keeps the
# reader's place only while no row inside the scroller is a size container.
SCROLL_UP = """async () => {
  const tr = document.getElementById('tr');
  const frame = () => new Promise((r) => requestAnimationFrame(r));
  const settle = async () => { await frame(); await frame(); await new Promise((r) => setTimeout(r, 60)); };
  let steps = 0, off = 0;
  for (let i = 0; i < 120 && tr.scrollTop > 0; i++) {
    const box = tr.getBoundingClientRect();
    const ref = document.elementFromPoint(box.left + box.width / 2, box.top + box.height / 2)?.closest('#entries > .row');
    const rows = tr.querySelectorAll('#entries > .row').length;
    const before = ref?.getBoundingClientRect().top;
    tr.scrollBy(0, -150);
    await settle();
    if (!ref?.isConnected || tr.scrollTop < 400 || tr.querySelectorAll('#entries > .row').length !== rows) continue;
    steps++;
    if (Math.abs(ref.getBoundingClientRect().top - before - 150) > 20) off++;
  }
  return [steps, off];
}"""


def test_scrolling_up_through_tables_and_fences_keeps_the_readers_place(
    tmp_path,
    fake_claude,
    make_page,
):
    reply = {
        "type": "assistant",
        "message": {
            "content": [
                {
                    "type": "text",
                    "text": f"Measured:\n\n{TABLE}\n\n```python\ndef f():\n"
                    "    return 1\n```\n\nThe rest reads as before.",
                }
            ]
        },
    }
    lines = [r for r in map(json.loads, FIXTURE.open()) if r.get("src") == "claude"]
    lines.insert(-1, {"src": "claude", "line": json.dumps(reply)})
    store = tmp_path / "tables.jsonl"
    store.write_text("".join(json.dumps(r) + "\n" for r in lines))
    (tmp_path / ".aegis.yaml").write_text(CONFIG)
    s = Server(tmp_path, fake_claude)
    s.env = {"FAKE_CLAUDE_REPLAY": str(store), "FAKE_CLAUDE_REPEAT": "16"}
    s.start()
    try:
        page = make_page(1280)
        page.goto(s.url)
        spawn(page)
        page.fill("#input", "replay")
        page.press("#input", "Enter")
        page.wait_for_function(
            "document.getElementById('s-status').textContent.trim() === 'done'",
            timeout=60_000,
        )
        page.reload()
        page.wait_for_function("window.__a2snapshot && window.__a2snapshot.painted")
        page.wait_for_timeout(800)
        steps, off = page.evaluate(SCROLL_UP)
        # Before #323 about 5% of steps moved by more than 20 px (rows that
        # first draw in the same frame as the step); with every row a size
        # container about half did. A tenth leaves room for the first and fails
        # the second by five times.
        assert steps >= 60, steps
        assert off <= steps // 10, (off, steps)
        assert page.errors == []
    finally:
        s.stop()


def test_a_language_file_that_fails_to_load_leaves_code_plain_and_is_retried(
    server,  # noqa: F811
    make_page,
):
    python = "**/vendor/hljs/languages/python.min.js"
    (server.root / "code.py").write_text("def f():\n    return 1\n")
    page = make_page(1280)
    page.route(python, lambda r: r.fulfill(status=404))
    page.goto(server.url)
    spawn(page, md("```python\ndef f():\n    return 1\n```"))
    fence = page.locator(".row.prose .fence").last
    page.wait_for_function(
        "() => document.querySelector('.row.prose .fence')?.dataset.painted"
    )
    page.wait_for_timeout(300)
    assert fence.locator("[class^=hljs-]").count() == 0
    assert "return 1" in fence.inner_text()

    # The viewer draws the file plain instead of waiting on its colours.
    send(page, 2, ["code.py"])
    page.click(".row.file .fbar .view")
    page.locator("#viewer .code .src").wait_for()
    assert "return 1" in page.locator("#viewer .code .src").inner_text()
    assert page.locator("#viewer .vnote").count() == 0, "no 'loading…' left"
    page.keyboard.press("Escape")

    # Once the file can load, the next block asks again and is coloured.
    page.unroute(python)
    page.fill("#input", md("```python\ndef g():\n    return 2\n```"))
    page.press("#input", "Enter")
    turns_done(page, 3)
    page.locator(".row.prose .fence").last.locator(".hljs-keyword").first.wait_for()
    # Only the browser's own note of the 404s, nothing thrown.
    assert [e for e in page.errors if "404" not in e] == []
