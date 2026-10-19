"""A selection in the transcript, quoted for the composer (#479): the clean
text of a range (quote.js), the selection bar, the row menus and the link to
a row. Every action starts with a draft in the composer and checks that it
survives, after the quote."""

import pytest

from .test_browser import EXPECTED_CONSOLE, beta_session, spawn, turns_done
from .test_browser import CONFIG, Linked, browser, btw_server, linked, server  # noqa: F401
from .test_park_browser import HIDE, PARKED, SHOW, park_server  # noqa: F401

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
    page.fill("#input", "later")
    last = page.locator(".row.prose .body").first.locator("p").last
    last.click(click_count=3)
    page.wait_for_selector(".sbar")
    page.click(".sbar [data-id=ask]")
    assert page.input_value("#input") == "> last part kiwi\n\nlater"
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


def test_new_session_opens_the_form_with_the_source_and_the_quote(server, page):  # noqa: F811
    page.goto(server.url)
    key = spawn(page, "/md alpha kiwi beta")
    handle = page.locator("#s-handle").inner_text()
    cwd = page.locator("#s-cwd").inner_text()
    # Half a prompt in the form, and a draft in the composer: both survive.
    page.click("#tab-add")
    page.fill("#sp-text", "half typed")
    page.click(f"#tablist .tab[data-id='{key}']")
    page.wait_for_selector("#a2[data-view=session]")
    page.fill("#input", "later")
    select(page, REPLY, "kiwi")
    page.click(".sbar [data-id=new]")
    page.wait_for_selector("#a2[data-view=spawn]")
    assert page.input_value("#sp-text") == f"From {handle}:\n> kiwi\n\nhalf typed"
    assert page.evaluate("document.querySelector('#sp-agent').value") == "opus"
    assert page.evaluate("document.querySelector('#sp-model').value") == "opus"
    assert page.input_value("#sp-cwd") == cwd
    page.click(f"#tablist .tab[data-id='{key}']")
    assert page.input_value("#input") == "later"
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
    server,  # noqa: F811
    page,
):
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
    page.fill("#input", "later")
    first.locator(".body").click(button="right")
    page.click(".cmenu [data-id=link]")
    link = page.evaluate("navigator.clipboard.readText()")
    assert page.input_value("#input") == "later"
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


def test_a_view_change_closes_the_bar_and_the_menu(server, page):  # noqa: F811
    # go() moves with pushState, which fires no hashchange.
    page.goto(server.url)
    key = spawn(page, "/md alpha kiwi beta")
    select(page, REPLY, "kiwi")
    page.wait_for_selector(".sbar")
    page.keyboard.press("Alt+BracketLeft")
    page.wait_for_selector("#a2[data-view=fleet]")
    assert page.locator(".sbar").count() == 0
    page.click(f"#tablist .tab[data-id='{key}']")
    page.wait_for_selector("#a2[data-view=session]")
    page.click(REPLY, button="right")
    page.wait_for_selector(".cmenu")
    page.evaluate("document.getElementById('tab-fleet').click()")
    page.wait_for_selector("#a2[data-view=fleet]")
    assert page.locator(".cmenu").count() == 0
    assert page.errors == []


def test_esc_closes_the_bar(server, page):  # noqa: F811
    page.goto(server.url)
    spawn(page, "/md alpha kiwi beta")
    select(page, REPLY, "kiwi")
    page.wait_for_selector(".sbar")
    page.keyboard.press("Escape")
    page.wait_for_selector(".sbar", state="detached")
    assert page.errors == []


def quote_body(pg, sel: str) -> str:
    return pg.evaluate(
        f"""async () => {{
          const {{ rangeText }} = await {QUOTE};
          const r = document.createRange();
          r.selectNodeContents([...document.querySelectorAll("{sel}")].at(-1));
          return rangeText(r);
        }}"""
    )


def test_a_tool_rows_quote_is_its_output_without_the_interface(server, page, tmp_path):  # noqa: F811
    f = tmp_path / "notes.txt"
    f.write_text("kiwi one\nkiwi two\n")
    page.goto(server.url)
    spawn(page, f"/read {f}")
    page.click(".row.tool .line")
    page.wait_for_selector(".row.tool .more .peekbar")
    page.click(".row.tool .peek")  # the file card and its "as of" caption
    page.wait_for_selector(".row.tool .peekcap")
    text = quote_body(page, ".row.tool .body")
    assert "kiwi one\nkiwi two" in text
    for word in ("Read", "Show the file", "as of", "lines", "Open"):
        assert word not in text, (word, text)
    # A Write's file view and an Edit's diff: no line numbers, one line each.
    page.fill("#input", f"/write {tmp_path / 'c.py'} => z = 9")
    page.press("#input", "Enter")
    turns_done(page, 2)
    page.locator(".row.tool .line").last.click()
    page.locator(".row.tool").last.locator(".lines, .diff").first.wait_for()
    text = quote_body(page, ".row.tool .body")
    assert "z = 9" in text and "1z" not in text, text
    (tmp_path / "b.py").write_text("x = 1\ny = 2\n")
    page.fill("#input", f"/edit {tmp_path / 'b.py'} y = 2 => y = 3")
    page.press("#input", "Enter")
    turns_done(page, 3)
    page.locator(".row.tool .line").last.click()
    page.locator(".row.tool").last.locator(".diff .unified").wait_for(state="attached")
    text = quote_body(page, ".row.tool .body")
    assert "−y = 2\n+y = 3" in text and "@@" not in text, text
    assert page.errors == []


def test_a_server_restart_leaves_no_menu_on_a_row_that_left(server, page):  # noqa: F811
    page.goto(server.url)
    spawn(page, "/md alpha kiwi beta")
    page.click(REPLY, button="right")
    page.wait_for_selector(".cmenu")
    server.stop()
    server.start()
    page.wait_for_function(
        "document.getElementById('conn-dot').className.includes('ready')", timeout=15000
    )
    page.wait_for_selector(REPLY)
    # Either closed, or about the row as it is drawn now: never a detached one.
    assert page.evaluate(
        "async () => { const c = (await import('/static/js/menu.js')).current();"
        " return !c || c.target.isConnected; }"
    )


def test_a_row_link_in_the_shown_session_reveals_it_and_tabs_still_cycle(server, page):  # noqa: F811
    page.goto(server.url)
    a = spawn(page, "/md alpha kiwi beta")
    b = spawn(page, "/md other")
    page.click(f"#tablist .tab[data-id='{a}']")
    page.wait_for_selector("#a2[data-view=session] .row.prose >> text=kiwi")
    rid = page.locator(".row.prose").first.get_attribute("data-id")
    page.evaluate(f"location.hash = '#s={a}&at={rid}'")
    page.wait_for_selector(f".row.sel[data-id='{rid}']")
    page.wait_for_function(f"location.hash === '#s={a}'")
    page.keyboard.press("Alt+BracketRight")
    page.wait_for_function(f"location.hash === '#s={b}'")
    assert page.errors == []


def test_a_linked_servers_rows_open_no_file_and_spawn_there(
    linked,  # noqa: F811
    browser,  # noqa: F811
    page,
    tmp_path,
):
    f = tmp_path / "far.txt"
    f.write_text("kiwi far\n")
    lid = beta_session(linked, browser, f"/read {f}")
    page.goto(linked.alpha.url)
    page.click(f"#tablist .tab[data-id='beta/{lid}']")
    page.wait_for_selector("#a2[data-view=session]")
    turns_done(page, 1)
    cwd = page.locator("#s-cwd").inner_text()
    page.click(".row.tool .line", button="right")
    assert menu_ids(page) == ["copyout", "reply", "link"]
    page.keyboard.press("Escape")
    select(page, REPLY, "Read")
    page.click(".sbar [data-id=new]")
    page.wait_for_selector("#a2[data-view=spawn]")
    page.wait_for_function("document.querySelector('#sp-server').value === 'beta'")
    page.wait_for_function("document.querySelector('#sp-agent').value === 'opus'")
    assert page.input_value("#sp-cwd") == cwd
    assert page.input_value("#sp-text").startswith("From ")
    assert not [e for e in page.errors if "server_offline" not in e]


def test_a_long_press_on_text_on_a_phone_gives_the_bar(browser, server):  # noqa: F811
    ctx = browser.new_context(
        viewport={"width": 390, "height": 844}, has_touch=True, is_mobile=True
    )
    pg = ctx.new_page()
    try:
        pg.goto(server.url)
        spawn(pg, "/md alpha kiwi beta")
        box = pg.evaluate(
            """() => { const b = [...document.querySelectorAll('.row.prose .body')].at(-1);
              const w = document.createTreeWalker(b, NodeFilter.SHOW_TEXT);
              for (let n; (n = w.nextNode()); ) { const i = n.data.indexOf('kiwi'); if (i < 0) continue;
                const r = document.createRange(); r.setStart(n, i); r.setEnd(n, i + 4);
                const q = r.getBoundingClientRect(); return [q.x + q.width / 2, q.y + q.height / 2]; } }"""
        )
        cdp = ctx.new_cdp_session(pg)
        point = [{"x": box[0], "y": box[1]}]
        cdp.send(
            "Input.dispatchTouchEvent", {"type": "touchStart", "touchPoints": point}
        )
        pg.wait_for_selector(".cmenu")  # the long press opened the row's menu
        # The browser's own long press selects the word under the finger and
        # cancels the pointer; its contextmenu comes after, while still held.
        select(pg, REPLY, "kiwi")
        cdp.send("Input.dispatchTouchEvent", {"type": "touchCancel", "touchPoints": []})
        pg.wait_for_selector(".sbar")
        assert pg.locator(".cmenu").count() == 0
        pg.evaluate(
            "document.querySelector('.row.prose .body')"
            ".dispatchEvent(new MouseEvent('contextmenu', {bubbles: true, cancelable: true}))"
        )
        pg.wait_for_timeout(300)
        assert pg.locator(".cmenu").count() == 0 and pg.locator(".sbar").count() == 1
    finally:
        ctx.close()


def test_new_session_on_a_linked_server_takes_its_agents_picks(
    tmp_path,
    fake_claude,
    fake_opencode,
    browser,  # noqa: F811
):
    pair = Linked(tmp_path, fake_claude, fake_opencode)
    far = CONFIG.replace(
        "opus: {harness: claude-code, model: opus, effort: high, permission: full}",
        "opus: {harness: claude-code, model: sonnet, effort: low, permission: read}",
    )
    (pair.beta.root / ".aegis.yaml").write_text(far)
    pair.start()
    try:
        lid = beta_session(pair, browser, "/md alpha kiwi beta")
        errors: list = []
        from .test_browser import new_page, pick

        pg = new_page(browser, errors)
        pg.goto(pair.alpha.url)
        # An override left in the form from an earlier spawn here.
        pg.click("#tab-add")
        pg.wait_for_function("document.querySelector('#sp-agent').value !== ''")
        pick(pg, "#sp-effort", "max")
        pg.click(f"#tablist .tab[data-id='beta/{lid}']")
        pg.wait_for_selector("#a2[data-view=session]")
        turns_done(pg, 1)
        select(pg, REPLY, "kiwi")
        pg.click(".sbar [data-id=new]")
        pg.wait_for_selector("#a2[data-view=spawn]")
        pg.wait_for_function("document.querySelector('#sp-server').value === 'beta'")
        pg.wait_for_function("document.querySelector('#sp-effort').value === 'low'")
        picks = pg.evaluate(
            "['agent', 'model', 'effort', 'permission']"
            ".map(k => document.querySelector('#sp-' + k).value)"
        )
        assert picks == ["opus", "sonnet", "low", "read"]
    finally:
        pair.stop()


def test_a_drag_let_go_outside_the_transcript_gets_the_bar(server, page):  # noqa: F811
    # From the last row's text down into the scroller's empty space under the
    # list: one row selected, let go outside #entries.
    page.goto(server.url)
    spawn(page, "/md alpha kiwi beta")
    at = page.evaluate(
        """() => { const b = [...document.querySelectorAll('#entries > .row .body')].at(-1);
          const r = b.getBoundingClientRect(); return [r.x + 2, r.y + r.height / 2, r.bottom]; }"""
    )
    page.mouse.move(at[0], at[1])
    page.mouse.down()
    page.mouse.move(at[0] + 200, at[2] + 150, steps=8)
    page.mouse.up()
    assert page.evaluate(
        "document.elementFromPoint(%d, %d).closest('#entries') === null"
        % (at[0] + 200, at[2] + 150)
    )
    assert page.evaluate("getSelection().toString()").startswith("done in")
    page.wait_for_selector(".sbar")
    assert page.errors == []


def test_a_bar_and_a_menu_whose_row_scrolled_away_close(server, page):  # noqa: F811
    page.goto(server.url)
    key = spawn(page, "/md alpha kiwi beta")
    other = page.context.new_page()  # sends, so this page's selection stays
    other.goto(f"{server.url.split('#')[0]}#s={key}")
    other.wait_for_selector("#a2[data-view=session]")
    tall = "/md " + "\\n\\n".join(f"line {i}" for i in range(60))
    select(page, REPLY, "kiwi")
    page.wait_for_selector(".sbar")
    other.fill("#input", tall)
    other.press("#input", "Enter")
    page.wait_for_selector(".row.prose >> text=line 59")
    page.wait_for_selector(".sbar", state="detached")
    # The same for a row's menu, opened on a reply the next one pushes away.
    other.fill("#input", "/md short reply")
    other.press("#input", "Enter")
    page.wait_for_selector(".row.prose >> text=short reply")
    page.locator(".row.prose .body").last.click(button="right")
    page.wait_for_selector(".cmenu")
    other.fill("#input", tall.replace("line", "row"))
    other.press("#input", "Enter")
    page.wait_for_selector(".row.prose >> text=row 59")
    page.wait_for_selector(".cmenu", state="detached")
    other.close()
    assert page.errors == []


def test_another_members_session_offers_no_composer_items(server, page, browser):  # noqa: F811
    from .test_browser import member_page

    page.goto(server.url)
    key = spawn(page, "/md alpha kiwi beta")
    pg, base = member_page(browser, server)
    pg.goto(f"{base}#s={key}")
    pg.wait_for_selector(".row.prose .body")
    select(pg, REPLY, "kiwi")
    pg.wait_for_selector(".sbar")
    assert [b.get_attribute("data-id") for b in pg.locator(".sbar button").all()] == [
        "new",
        "copy",
    ]
    pg.click(".row.prose .body", button="right")
    assert menu_ids(pg) == ["new", "copy", "link"]
    close_menu(pg)
    pg.click(".row.user .body", button="right")
    assert menu_ids(pg) == ["new", "copy", "link"]


def test_a_full_answer_after_a_restart_leaves_no_menu_on_a_detached_row(server, page):  # noqa: F811
    page.goto(server.url)
    key = spawn(page, "/md alpha kiwi beta")
    page.evaluate("window.__row = document.querySelector('.row.prose')")
    page.click(REPLY, button="right")
    page.wait_for_selector(".cmenu")
    server.stop()
    # A store shorter than the page's revision: the server answers in full.
    store = server.root / ".aegis" / "state" / "transcripts" / f"{key}.jsonl"
    lines = store.read_text().splitlines(keepends=True)
    store.write_text("".join(lines[:-1]))
    server.start()
    page.wait_for_function(
        "!window.__row.isConnected", timeout=15000
    )  # snapshot() redrew
    assert page.evaluate(
        "async () => { const c = (await import('/static/js/menu.js')).current();"
        " return !c || c.target.isConnected; }"
    )


def test_a_quote_into_a_parked_sessions_composer_wakes_it(park_server, page):  # noqa: F811
    page.goto(park_server.url)
    spawn(page, '/mcp turn_end {"attention": "done", "line": "finished"}')
    turns_done(page, 1)
    page.evaluate(HIDE)
    page.wait_for_function(PARKED, timeout=8000)
    page.evaluate(SHOW)
    page.evaluate(f"async () => (await {QUOTE}).insertIntoComposer('> x')")
    page.wait_for_function(
        "() => document.querySelector('#input').placeholder.startsWith('Message the agent')",
        timeout=8000,
    )
    assert page.input_value("#input") == "> x\n\n"
