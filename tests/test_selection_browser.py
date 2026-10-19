"""A selection in the transcript, quoted for the composer (#479): the clean
text of a range (quote.js), the selection bar, the row menus and the link to
a row. Every action starts with a draft in the composer and checks that it
survives, after the quote."""

import pytest

from .test_browser import (
    EXPECTED_CONSOLE,
    spawn,
    turns_done,
)  # noqa: F401

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
    page.evaluate(
        f"async () => (await {QUOTE}).insertIntoComposer('> x', {{ prefix: '/nosuch\\n' }})"
    )
    page.wait_for_function(
        "document.getElementById('composer').classList.contains('bad')"
    )
    page.fill("#input", "hello")
    page.evaluate(
        f"async () => (await {QUOTE}).insertIntoComposer('> x', {{ prefix: '/btw\\n' }})"
    )
    # ...and /btw is not.
    page.wait_for_function(
        "!document.getElementById('composer').classList.contains('bad')"
    )
    assert page.input_value("#input") == "/btw\n> x\n\nhello"
    assert page.evaluate(
        "() => { const i = document.getElementById('input');"
        " return i.selectionStart === i.value.length && document.activeElement === i; }"
    )
    assert (
        page.evaluate(f"localStorage.getItem('aegis.draft.{key}')")
        == "/btw\n> x\n\nhello"
    )
    assert page.errors == []


REPLY = ".row.prose .body"


def select(pg, sel: str, word: str) -> None:
    """Select `word` in the last match of `sel` that holds it, as a drag would."""
    pg.evaluate(
        """([sel, word]) => {
          const body = [...document.querySelectorAll(sel)].filter((b) => b.textContent.includes(word)).at(-1);
          const w = document.createTreeWalker(body, NodeFilter.SHOW_TEXT);
          for (let n; (n = w.nextNode()); ) {
            const i = n.data.indexOf(word);
            if (i < 0) continue;
            const r = document.createRange();
            r.setStart(n, i);
            r.setEnd(n, i + word.length);
            getSelection().removeAllRanges();
            getSelection().addRange(r);
            return;
          }
          throw new Error("no " + word);
        }""",
        [sel, word],
    )


def bar_and_selection(pg) -> dict:
    return pg.evaluate(
        """() => {
          const b = document.querySelector('.sbar').getBoundingClientRect();
          const s = getSelection().getRangeAt(0).getClientRects()[0];
          return { barTop: b.top, barBottom: b.bottom, selTop: s.top, selBottom: s.bottom };
        }"""
    )


def labels(pg) -> list[str]:
    return [
        x.strip()
        for x in pg.locator(".sbar button").evaluate_all(
            "bs => bs.map(b => b.getAttribute('aria-label'))"
        )
    ]


def test_ask_and_ask_aside_with_a_draft(btw_server, page):  # noqa: F811
    page.goto(btw_server.url)
    spawn(page, "/md alpha kiwi beta")
    page.fill("#input", "later")
    select(page, REPLY, "kiwi")
    page.wait_for_selector(".sbar")
    assert labels(page) == ["Ask", "Ask aside", "New session", "Copy"]
    page.click(".sbar [data-id=ask]")
    page.wait_for_selector(".sbar", state="detached")
    assert page.input_value("#input") == "> kiwi\n\nlater"
    page.fill("#input", "later")
    select(page, REPLY, "kiwi")
    page.click(".sbar [data-id=aside]")
    assert page.input_value("#input") == "/btw\n> kiwi\n\nlater"
    page.press("#input", "Enter")
    page.wait_for_selector(".row.btw .hd .q", timeout=8000)
    assert "kiwi" in page.locator(".row.btw .hd .q").inner_text()
    # Copy takes the clean text; with Shift, the quote.
    select(page, REPLY, "kiwi")
    page.click(".sbar [data-id=copy]")
    assert page.evaluate("navigator.clipboard.readText()") == "kiwi"
    select(page, REPLY, "kiwi")
    page.click(".sbar [data-id=copy]", modifiers=["Shift"])
    assert page.evaluate("navigator.clipboard.readText()") == "> kiwi"
    assert page.errors == []


def test_the_bar_survives_a_streaming_reply(server, page):  # noqa: F811
    page.goto(server.url)
    # A transcript taller than the view, so each new row scrolls it.
    spawn(page, "/md " + "\\n\\n".join(f"line {i}" for i in range(40)))
    page.fill("#input", "/md alpha kiwi beta")
    page.press("#input", "Enter")
    turns_done(page, 2)
    page.fill("#input", "/stream 10")
    page.press("#input", "Enter")
    page.wait_for_selector(".row.prose .body >> text=chunk1")
    select(page, REPLY, "kiwi")
    page.wait_for_selector(".sbar")
    before = bar_and_selection(page)
    page.evaluate(
        "window.__scrolls = 0;"
        " document.getElementById('tr').addEventListener('scroll', () => window.__scrolls++)"
    )
    turns_done(page, 3)  # the ten chunks and the turn's end landed
    page.wait_for_timeout(300)
    assert page.locator(".sbar").count() == 1
    after = bar_and_selection(page)
    assert (
        page.evaluate("window.__scrolls") > 0
    )  # the transcript scrolled itself under it
    assert (
        abs(
            (after["selTop"] - after["barBottom"])
            - (before["selTop"] - before["barBottom"])
        )
        < 2
    )
    # A wheel scroll by the person closes it.
    page.mouse.move(600, 300)
    page.mouse.wheel(0, -300)
    page.wait_for_selector(".sbar", state="detached")
    assert page.errors == []


def test_a_triple_click_on_the_last_paragraph_gets_the_bar(server, page):  # noqa: F811
    page.goto(server.url)
    spawn(page, "/md first part\\n\\nlast part kiwi")
    page.fill("#input", "/md next")
    page.press("#input", "Enter")
    turns_done(page, 2)
    last = page.locator(".row.prose .body").first.locator("p").last
    last.click(click_count=3)
    page.wait_for_selector(".sbar")
    page.click(".sbar [data-id=ask]")
    assert page.input_value("#input") == "> last part kiwi\n\n"
    assert page.errors == []


def test_a_selection_across_two_rows_gets_no_bar(server, page):  # noqa: F811
    page.goto(server.url)
    spawn(page, "/md alpha kiwi beta")
    page.evaluate("""() => {
      const r = document.createRange();
      r.setStart(document.querySelector('.row.user .body'), 0);
      r.setEnd(document.querySelector('.row.prose .body'), 1);
      getSelection().removeAllRanges(); getSelection().addRange(r);
    }""")
    page.wait_for_timeout(600)
    assert page.locator(".sbar").count() == 0
    select(page, REPLY, "kiwi")  # the control: one row's selection does get it
    page.wait_for_selector(".sbar")
    assert page.errors == []


def test_new_session_opens_the_form_with_the_source_and_the_quote(
    server, page, tmp_path
):  # noqa: F811
    page.goto(server.url)
    spawn(page, "/md alpha kiwi beta")
    handle = page.locator("#s-handle").inner_text()
    cwd = page.locator("#s-cwd").inner_text()
    select(page, REPLY, "kiwi")
    page.click(".sbar [data-id=new]")
    page.wait_for_selector("#a2[data-view=spawn]")
    assert page.input_value("#sp-text") == f"From {handle}:\n> kiwi\n\n"
    assert page.evaluate("document.querySelector('#sp-agent').value") == "opus"
    assert page.input_value("#sp-cwd") == cwd
    assert page.errors == []


def test_on_a_phone_the_bar_sits_under_the_selection_with_icons_only(browser, server):  # noqa: F811
    ctx = browser.new_context(
        viewport={"width": 390, "height": 844}, has_touch=True, is_mobile=True
    )
    pg = ctx.new_page()
    try:
        pg.goto(server.url)
        assert pg.evaluate("matchMedia('(pointer: coarse)').matches")
        spawn(pg, "/md alpha kiwi beta")
        pg.fill("#input", "later")
        select(pg, REPLY, "kiwi")
        pg.wait_for_selector(".sbar")
        at = bar_and_selection(pg)
        assert at["barTop"] >= at["selBottom"]
        assert pg.locator(".sbar .lb").first.is_hidden()
        assert pg.locator(".sbar button").first.bounding_box()["height"] >= 44
        pg.tap(".sbar [data-id=ask]")
        assert pg.input_value("#input") == "> kiwi\n\nlater"
    finally:
        ctx.close()


def menu_ids(pg) -> list[str]:
    pg.wait_for_selector(".cmenu")
    return pg.locator(".cmenu [role=menuitem]").evaluate_all(
        "bs => bs.map(b => b.dataset.id)"
    )


def close_menu(pg) -> None:
    pg.keyboard.press("Escape")
    pg.wait_for_selector(".cmenu", state="detached")


def test_row_menus_by_kind(server, page, tmp_path):  # noqa: F811
    (tmp_path / "notes.txt").write_text("kiwi notes\n")
    page.goto(server.url)
    spawn(page, f"/read {tmp_path / 'notes.txt'}")
    page.fill("#input", "later")
    page.click(".row.prose .body", button="right")
    assert menu_ids(page) == ["reply", "new", "copy", "link"]
    assert page.locator(".cmenu .grp").count() == 1
    page.click(".cmenu [data-id=reply]")
    assert page.input_value("#input") == "> Read it.\n\nlater"
    page.fill("#input", "later")
    page.click(".row.user .body", button="right")
    assert menu_ids(page) == ["edit", "new", "copy", "link"]
    page.click(".cmenu [data-id=edit]")
    assert page.input_value("#input") == f"/read {tmp_path / 'notes.txt'}\n\nlater"
    page.click(".row.tool .line", button="right")
    assert menu_ids(page) == ["open", "copyout", "reply", "link"]
    page.click(".cmenu [data-id=open]")
    page.wait_for_selector("#viewer:not([hidden]) .fn >> text=notes.txt")
    page.keyboard.press("Escape")
    page.wait_for_selector("#viewer", state="hidden")
    # A right-click inside a selection puts the bar's items on top.
    select(page, REPLY, "Read")
    page.wait_for_selector(".sbar")
    box = page.evaluate(
        "(() => { const r = getSelection().getRangeAt(0).getBoundingClientRect(); return [r.x + r.width / 2, r.y + r.height / 2]; })()"
    )
    page.mouse.click(box[0], box[1], button="right")
    assert menu_ids(page) == [
        "ask",
        "aside",
        "new",
        "copy",
        "reply",
        "new",
        "copy",
        "link",
    ]
    assert page.locator(".sbar").count() == 0  # one at a time
    close_menu(page)
    assert page.errors == []


def test_a_menu_stays_through_an_in_place_update_and_closes_when_its_row_goes(
    server, page
):  # noqa: F811
    page.goto(server.url)
    spawn(page)
    page.fill("#input", "/stream 12")
    page.press("#input", "Enter")
    page.wait_for_selector(".row.prose .body >> text=chunk1")
    streaming = page.locator(".row.prose").last
    sid = streaming.get_attribute("data-id")
    streaming.locator(".body").click(button="right")
    page.wait_for_selector(".cmenu")
    # Each delta redraws the row as a new node; the menu stays, about it.
    page.wait_for_selector(".row.prose .body >> text=chunk6")
    assert page.locator(".cmenu").count() == 1
    assert page.evaluate("document.activeElement.closest('.cmenu') !== null")
    # The whole block replaces the streamed entry: its row goes, and the menu with it.
    page.wait_for_function(
        f"!document.querySelector('.row[data-id=\"{sid}\"]')", timeout=10000
    )
    page.wait_for_selector(".cmenu", state="detached", timeout=2000)
    assert page.errors == []


def test_copy_link_opens_the_session_at_that_row(server, page):  # noqa: F811
    page.goto(server.url)
    key = spawn(page, "/md alpha kiwi beta")
    for i in range(30):
        page.fill("#input", f"/md filler {i}")
        page.press("#input", "Enter")
        turns_done(page, i + 2)
    first = page.locator(".row.prose").first
    rid = first.get_attribute("data-id")
    page.evaluate(f"document.querySelector('.row[data-id=\"{rid}\"]').scrollIntoView()")
    first.locator(".body").click(button="right")
    page.click(".cmenu [data-id=link]")
    link = page.evaluate("navigator.clipboard.readText()")
    # The link carries no token: a browser that signed in already opens it.
    origin = server.url.split("?")[0].split("#")[0]
    assert link == f"{origin}#s={key}&at={rid}"
    pg = page.context.new_page()
    try:
        pg.goto(link)
        pg.wait_for_selector(f".row.sel[data-id='{rid}']")
        assert pg.evaluate(
            f"""() => {{ const r = document.querySelector('.row[data-id="{rid}"]').getBoundingClientRect();
              const v = document.getElementById('tr').getBoundingClientRect();
              return r.bottom > v.top && r.top < v.bottom; }}"""
        )
    finally:
        pg.close()
    assert page.errors == []
