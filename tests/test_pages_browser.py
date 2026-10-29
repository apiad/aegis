"""Pointing inside files and pages (#479, spec 07): a selection or a press in
the viewer, the Files view or a sandboxed page asks about that place, with
its locator in front of the quote, or collects comments that go out as one
message. Every action starts with a draft in the composer and checks that it
survives, after what was inserted."""

import json

import pytest

from .test_browser import EXPECTED_CONSOLE, _artifact, spawn, turns_done
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


def pick(pg, item: str, where: str = ".cmenu") -> None:
    """Pick a menu or bar item as a person would, once a page's has settled
    (a page-proposed menu or bar takes no click in its first 500 ms)."""
    pg.wait_for_selector(f"{where} [data-id={item}]")
    pg.wait_for_timeout(550)
    pg.click(f"{where} [data-id={item}]")


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
    pick(page, "ask")
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
    pick(page, "comment")
    page.keyboard.type("second")
    tray = page.locator("#viewer .ctray")
    assert tray.locator(".cn").inner_text() == "3 comments"
    # The note is themed like aegis's other boxes, not the browser's white.
    assert page.evaluate(
        """() => {
          const probe = document.createElement("div");
          probe.style.background = "var(--bg)";
          document.querySelector("#a2").append(probe);
          const want = getComputedStyle(probe).backgroundColor;
          probe.remove();
          const note = document.querySelector("#viewer .ctray .note");
          return getComputedStyle(note).backgroundColor === want;
        }"""
    )
    assert page.locator("#viewer .pin").count() == 3
    # Removing one and making it again keeps the order of making.
    tray.locator("li").nth(2).locator(".rm").click()
    assert tray.locator(".cn").inner_text() == "2 comments"
    page.evaluate("getSelection().removeAllRanges()")
    page.mouse.click(box["x"], box["y"], button="right")
    pick(page, "comment")
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


PAGE = "<html><body><h2>Setup</h2><p id=p1>kiwi</p><p>plum</p></body></html>"


def frame_of(pg, sel: str):
    """The frame drawn by the iframe at `sel`, once its page has loaded."""
    handle = pg.wait_for_selector(sel)
    frame = handle.content_frame()
    frame.wait_for_load_state("load")
    return frame


def test_a_right_click_in_a_sent_page_asks_about_that_element(server, page):  # noqa: F811
    (server.root / "page.html").write_text(PAGE)
    page.goto(server.url)
    spawn(page)
    send(page, 1, ["page.html"])
    page.fill("#input", "later")
    view(page, "iframe")
    frame = frame_of(page, "#viewer iframe")
    box = frame.locator("#p1").bounding_box()  # in the page's coordinates
    frame.locator("#p1").click(button="right")
    page.wait_for_selector(".cmenu [data-id=ask]")
    # The host's menu, over the frame where the click was.
    menu = page.locator(".cmenu").bounding_box()
    assert abs(menu["x"] - (box["x"] + box["width"] / 2)) < 4
    assert abs(menu["y"] - (box["y"] + box["height"] / 2)) < 4
    # The element is outlined while the menu is open, and not after.
    outline = "document.getElementById('p1').style.outline"
    assert "solid" in frame.evaluate(outline)
    # A page's menu takes no focus: a key never picks one of its items.
    assert page.evaluate("document.activeElement.closest('.cmenu')") is None
    page.keyboard.press("Enter")
    assert page.input_value("#input") == "later"
    page.keyboard.press("Escape")
    page.wait_for_selector(".cmenu", state="detached")
    frame.wait_for_function(f"{outline} === ''")  # the host's message lands after
    frame.locator("#p1").click(button="right")
    pick(page, "ask")
    page.wait_for_selector("#viewer", state="hidden")
    assert page.input_value("#input") == "`page.html` § Setup › #p1\n> kiwi\n\nlater"
    assert page.errors == []


def test_comments_in_a_page_and_an_artifact(server, page):  # noqa: F811
    (server.root / "page.html").write_text(PAGE)
    page.goto(server.url)
    spawn(page)
    send(page, 1, ["page.html"])
    page.fill("#input", "later")
    view(page, "iframe")
    frame = frame_of(page, "#viewer iframe")
    frame.locator("#p1").click(button="right")
    pick(page, "comment")
    page.keyboard.type("one")
    # A selection in the page gets the host's bar, over it.
    frame.locator("p:not([id])").dblclick(
        position={"x": 8, "y": 6}
    )  # a selection made by hand
    pick(page, "comment", ".sbar")
    page.keyboard.type("two")
    page.locator("#viewer .ctray [data-act=send]").click()
    assert page.input_value("#input") == (
        "Comments on page.html:\n"
        "1. § Setup › #p1\n   > kiwi\n   one\n"
        "2. § Setup › p:nth-of-type(2)\n   > plum\n   two"
        "\n\nlater"
    )

    # An artifact's page: its id names it.
    art, _ = _artifact(
        page,
        server,
        ("<h3>Pick</h3><p id=q>mango</p>", "aegis.state({});"),
        1,
    )
    page.fill("#input", "")
    page.locator(f".row.artifact [data-artifact='{art}']").first.wait_for()
    page.locator(".row.artifact .fbar .view").last.click()
    frame = frame_of(page, "#viewer iframe[data-artifact]")
    frame.locator("#q").click(button="right")
    pick(page, "comment")
    page.keyboard.type("ripe")
    page.locator("#viewer .ctray [data-act=send]").click()
    assert page.input_value("#input") == (
        f"Comments on {art}:\n1. § Pick › #q\n   > mango\n   ripe\n\n"
    )
    assert page.errors == []


def test_a_page_with_a_strict_csp_still_works_and_has_no_menu(server, page):  # noqa: F811
    (server.root / "strict.html").write_text(
        "<html><head><meta http-equiv='Content-Security-Policy'"
        " content=\"script-src 'unsafe-inline'\"></head><body><p id=p1>kiwi</p>"
        "<p id=ok></p><script>ok.textContent = 'ran'</script></body></html>"
    )
    page.errors_ok = True
    page.goto(server.url)
    spawn(page)
    send(page, 1, ["strict.html"])
    view(page, "iframe")
    frame = frame_of(page, "#viewer iframe")
    assert frame.locator("#ok").inner_text() == "ran"
    frame.locator("#p1").click(button="right")
    page.wait_for_timeout(500)
    assert page.locator(".cmenu").count() == 0
    # The browser reports the blocked script; nothing else went wrong.
    assert [e for e in page.errors if "pagepoint.js" not in e] == []


def test_a_long_press_inside_a_page_on_a_phone(server, page, browser):  # noqa: F811
    (server.root / "page.html").write_text(PAGE)
    page.goto(server.url)
    key = spawn(page)
    send(page, 1, ["page.html"])
    ctx = browser.new_context(
        viewport={"width": 390, "height": 844}, has_touch=True, is_mobile=True
    )
    phone = ctx.new_page()
    phone.goto(f"{server.url.split('#')[0]}#s={key}")
    frame = frame_of(phone, ".row.file iframe")
    frame.locator("#p1").scroll_into_view_if_needed()
    box = frame.locator("#p1").bounding_box()
    point = {"x": box["x"] + 8, "y": box["y"] + box["height"] / 2}
    cdp = ctx.new_cdp_session(phone)
    cdp.send("Input.dispatchTouchEvent", {"type": "touchStart", "touchPoints": [point]})
    phone.wait_for_selector(".cmenu [data-id=ask]", timeout=3000)
    cdp.send("Input.dispatchTouchEvent", {"type": "touchEnd", "touchPoints": []})
    phone.wait_for_timeout(300)
    assert phone.locator(".cmenu").count() == 1  # the release did not close it
    pick(phone, "ask")
    assert phone.input_value("#input").startswith("`page.html` § Setup › #p1\n> kiwi")
    ctx.close()
    assert page.errors == []


def test_a_page_message_without_a_gesture_does_nothing(server, page):  # noqa: F811
    """A page's script can post aegis/point at any time; with no press in the
    frame no menu opens, so the composer keeps the focus and the typing."""
    (server.root / "page.html").write_text(PAGE)
    page.goto(server.url)
    spawn(page)
    send(page, 1, ["page.html"])
    frame = frame_of(page, ".row.file iframe")
    page.click("#input")
    page.keyboard.type("hello")
    # The pointer resting over the page is no press in it.
    box = page.locator(".row.file iframe").bounding_box()
    page.mouse.move(box["x"] + 40, box["y"] + 40)
    for kind in ("menu", "select"):
        frame.evaluate(
            """(kind) => parent.postMessage({ jsonrpc: "2.0", method: "aegis/point", params: {
              kind, x: 5, y: 5, rect: { left: 0, top: 0, right: 9, bottom: 9 },
              locator: { css: "#p1" }, text: "made up" } }, "*")""",
            kind,
        )
    page.wait_for_timeout(400)
    assert page.locator(".cmenu, .sbar").count() == 0
    page.keyboard.type(" there")
    assert page.input_value("#input") == "hello there"
    assert page.errors == []


FORM = (
    "<html><body><h2>Form</h2><p id=p1>kiwi</p><input id=f value=typed>"
    "<textarea id=t>a draft</textarea><a id=a href='#x'>a link</a>"
    "<img id=i width=40 height=40 src='data:image/gif;base64,R0lGODlhAQABAAAAACw='>"
    # Registered after load, so it runs after pagepoint's window listener.
    "<script>addEventListener('load', () => addEventListener('contextmenu', (e) =>"
    " (document.title = String(e.defaultPrevented))))</script></body></html>"
)


def test_a_right_click_on_a_field_or_an_image_keeps_the_browsers_menu(
    server,  # noqa: F811
    page,
):
    (server.root / "form.html").write_text(FORM)
    page.goto(server.url)
    spawn(page)
    send(page, 1, ["form.html"])
    view(page, "iframe")
    frame = frame_of(page, "#viewer iframe")
    for sel in ("#f", "#t", "#a", "#i"):
        frame.locator(sel).click(button="right")
        page.wait_for_timeout(300)
        assert page.locator(".cmenu").count() == 0, sel
        assert frame.evaluate("document.title") == "false", sel
    frame.locator("#p1").click(button="right")
    page.wait_for_selector(".cmenu [data-id=ask]")
    assert frame.evaluate("document.title") == "true"
    assert page.errors == []


def test_a_scroll_inside_the_page_closes_its_menu_and_bar(server, page):  # noqa: F811
    tall = PAGE.replace("</body>", "<div style='height:3000px'></div></body>")
    (server.root / "page.html").write_text(tall)
    page.goto(server.url)
    spawn(page)
    send(page, 1, ["page.html"])
    view(page, "iframe")
    frame = frame_of(page, "#viewer iframe")
    frame.locator("#p1").click(button="right")
    page.wait_for_selector(".cmenu")
    frame.evaluate("scrollBy(0, 40)")
    page.wait_for_selector(".cmenu", state="detached")
    frame.evaluate("scrollTo(0, 0)")
    page.wait_for_timeout(300)  # that scroll's own clear lands first
    frame.locator("p:not([id])").dblclick(position={"x": 8, "y": 6})
    page.wait_for_selector(".sbar")
    frame.evaluate("scrollBy(0, 40)")
    page.wait_for_selector(".sbar", state="detached")
    assert page.errors == []


def test_comments_on_a_page_in_files_go_to_the_last_session(server, page):  # noqa: F811
    (server.root / "page.html").write_text(PAGE)
    page.goto(server.url)
    key = spawn(page)
    page.fill("#input", "later")
    page.goto(f"{server.url.split('#')[0]}#files=page.html")
    frame = frame_of(page, ".v-files .fview iframe")
    frame.locator("#p1").click(button="right")
    pick(page, "comment")
    page.keyboard.type("ripe")
    page.locator(".v-files .ctray [data-act=send]").click()
    page.wait_for_function(f"location.hash === '#s={key}'")
    assert page.input_value("#input") == (
        "Comments on page.html:\n1. § Setup › #p1\n   > kiwi\n   ripe\n\nlater"
    )
    assert page.errors == []


TRAP = (
    "<html><body><h2>x</h2><button id=b style='margin:80px'>press</button>"
    "<script>b.addEventListener('mousedown', (e) => parent.postMessage({"
    " jsonrpc: '2.0', method: 'aegis/point', params: { kind: 'menu',"
    " x: e.clientX - 20, y: e.clientY - 12, locator: { heading: 'x' },"
    " text: 'made up' } }, '*'))</script></body></html>"
)


def test_a_page_cannot_put_ask_under_the_next_click(server, page):  # noqa: F811
    """The page chose where its menu opens: a click aimed at the page that
    lands on an item right after takes nothing."""
    (server.root / "trap.html").write_text(TRAP)
    page.goto(server.url)
    spawn(page)
    send(page, 1, ["trap.html"])
    page.fill("#input", "later")
    view(page, "iframe")
    frame = frame_of(page, "#viewer iframe")
    box = frame.locator("#b").bounding_box()
    at = (box["x"] + box["width"] / 2, box["y"] + box["height"] / 2)
    page.mouse.click(*at)
    page.wait_for_selector(".cmenu")
    page.mouse.click(*at)  # the second click of the two, on the host's menu
    page.wait_for_timeout(200)
    assert page.locator("#viewer").is_visible()
    page.keyboard.press("Escape")
    assert page.evaluate("document.getElementById('input').value") == "later"
    assert page.errors == []


def test_a_press_elsewhere_in_the_page_closes_its_menu(server, page):  # noqa: F811
    (server.root / "page.html").write_text(PAGE)
    page.goto(server.url)
    spawn(page)
    send(page, 1, ["page.html"])
    view(page, "iframe")
    frame = frame_of(page, "#viewer iframe")
    frame.locator("#p1").click(button="right")
    page.wait_for_selector(".cmenu")
    box = page.locator("#viewer iframe").bounding_box()
    page.mouse.click(box["x"] + box["width"] - 10, box["y"] + box["height"] - 10)
    page.wait_for_selector(".cmenu", state="detached")
    frame.wait_for_function("document.getElementById('p1').style.outline === ''")
    assert page.errors == []


def test_files_send_cancelled_at_unsaved_edits_keeps_the_comments(
    server,  # noqa: F811
    page,
):
    (server.root / "notes.md").write_text("# Notes\n\n## Setup\n\nrun kiwi here\n")
    page.goto(server.url)
    key = spawn(page)
    page.goto(f"{server.url.split('#')[0]}#files=notes.md")
    page.wait_for_selector(".v-files .fview .md h2")
    page.locator(".v-files .fview .md p").last.click(button="right")
    pick(page, "comment")
    page.keyboard.type("ripe")
    page.click(".v-files .fstrip [data-mode=src]")
    page.click(".v-files .cm-content")
    page.keyboard.press("Control+End")
    page.keyboard.type("more\n")
    page.wait_for_selector(".fbar .dirty:not([hidden])")
    tray = page.locator(".v-files .ctray")
    tray.locator("[data-act=send]").click()
    page.click(".dialog .btn[data-value=cancel]")
    assert tray.locator(".cn").inner_text() == "1 comment"
    assert page.evaluate("location.hash") == "#files=notes.md"
    # Discard the edits this time: the message lands, and the tray empties.
    tray.locator("[data-act=send]").click()
    page.click(".dialog .btn[data-value=discard]")
    page.wait_for_function(f"location.hash === '#s={key}'")
    assert page.input_value("#input").startswith(
        "Comments on notes.md:\n1. `notes.md` § Setup\n   > run kiwi here\n   ripe"
    )
    assert (
        page.evaluate(
            "Object.keys(localStorage).filter(k => k.startsWith('aegis.comments.'))"
        )
        == []
    )
    assert page.errors == []


BAR_TRAP = TRAP.replace(
    "kind: 'menu', x: e.clientX - 20, y: e.clientY - 12,",
    # The bar is drawn above its rect: a rect just below the click puts the
    # bar's first button, Ask, under it.
    "kind: 'select', rect: { left: e.clientX - 20, top: e.clientY + 30,"
    " right: e.clientX + 200, bottom: e.clientY + 50 },",
)


@pytest.mark.quarantine("#544")
def test_a_page_cannot_put_the_bars_ask_under_the_next_click(server, page):  # noqa: F811
    assert BAR_TRAP != TRAP
    (server.root / "trap.html").write_text(BAR_TRAP)
    page.goto(server.url)
    spawn(page)
    send(page, 1, ["trap.html"])
    page.fill("#input", "later")
    view(page, "iframe")
    frame = frame_of(page, "#viewer iframe")
    box = frame.locator("#b").bounding_box()
    at = (box["x"] + box["width"] / 2, box["y"] + box["height"] / 2)
    page.mouse.click(*at)
    page.wait_for_selector(".sbar")
    ask = page.locator(".sbar [data-id=ask]").bounding_box()
    assert ask["x"] <= at[0] <= ask["x"] + ask["width"]
    assert ask["y"] <= at[1] <= ask["y"] + ask["height"]
    page.mouse.click(*at)  # the second click of the two, on the bar's Ask
    page.wait_for_timeout(200)
    assert page.locator("#viewer").is_visible()
    assert page.evaluate("document.getElementById('input').value") == "later"
    assert page.errors == []


def test_a_second_right_click_in_a_page_keeps_its_menu_closable(server, page):  # noqa: F811
    (server.root / "page.html").write_text(PAGE)
    page.goto(server.url)
    spawn(page)
    send(page, 1, ["page.html"])
    view(page, "iframe")
    frame = frame_of(page, "#viewer iframe")
    outline = "(id) => (id ? document.getElementById(id) : document.querySelector('h2')).style.outline"
    frame.locator("#p1").click(button="right")
    page.wait_for_selector(".cmenu")
    frame.locator("h2").click(button="right")
    frame.wait_for_function(f"({outline})(null).includes('solid')")
    page.wait_for_timeout(200)  # the first menu's closed message lands too
    assert "solid" in frame.evaluate(outline, None)
    assert frame.evaluate(outline, "p1") == ""
    assert page.locator(".cmenu").count() == 1
    box = page.locator("#viewer iframe").bounding_box()
    page.mouse.click(box["x"] + box["width"] - 10, box["y"] + box["height"] - 10)
    page.wait_for_selector(".cmenu", state="detached")
    frame.wait_for_function(f"({outline})(null) === ''")
    assert page.errors == []


def test_ask_from_a_window_tab_goes_to_the_sessions_composer(server, page):  # noqa: F811
    """A window tab (#w=) follows the session but draws no composer: Ask
    takes the tab to the session, where the quote is seen."""
    from .test_canvas_browser import mcp

    page.goto(server.url)
    sid = spawn(page)
    aid, _ = _artifact(
        page, server, ("<h3>Pick</h3><p id=q>mango</p>", "aegis.state({});"), 0
    )
    mcp(
        page,
        3,
        "layout_set",
        {
            "windows": [
                {"n": 1, "columns": [{"panes": [{"id": "transcript"}]}]},
                {"n": 2, "columns": [{"panes": [{"id": aid}]}]},
            ]
        },
    )
    page.locator(".row.artifact .acard .held").filter(has_text="in window 2").wait_for()
    win = page.context.new_page()
    win.errors = []
    win.on("pageerror", lambda e: win.errors.append(str(e)))
    win.goto(f"{server.url.split('#')[0]}#w=2:{sid}")
    frame = frame_of(win, f'#canvas iframe[data-artifact="{aid}"]')
    frame.locator("#q").click(button="right")
    pick(win, "ask")
    win.wait_for_function(f"location.hash === '#s={sid}'")
    assert win.locator("#input").is_visible()
    assert win.input_value("#input").startswith(f"`{aid}` § Pick › #q\n> mango")
    assert win.errors == [] and page.errors == []


def test_a_press_in_another_page_closes_a_pages_menu_and_bar(server, page):  # noqa: F811
    (server.root / "a.html").write_text(PAGE)
    (server.root / "b.html").write_text(PAGE.replace("kiwi", "fig"))
    page.set_viewport_size({"width": 1280, "height": 1600})
    page.goto(server.url)
    spawn(page)
    send(page, 1, ["a.html"])
    send(page, 2, ["b.html"])
    one = frame_of(page, ".row.file >> nth=0 >> iframe")
    frame_of(page, ".row.file >> nth=1 >> iframe")  # loaded, with its script
    # Both on screen, so pressing the second scrolls nothing here: the
    # host's own scroll would close the menu for another reason.
    page.locator(".row.file iframe").nth(0).scroll_into_view_if_needed()
    there = page.locator(".row.file iframe").nth(1).bounding_box()
    assert there["y"] + 60 < 1600
    at = (there["x"] + 30, there["y"] + 30)
    page.evaluate(
        "window.__scrolls = 0;"
        " document.addEventListener('scroll', () => window.__scrolls++, true)"
    )
    one.locator("#p1").click(button="right")
    page.wait_for_selector(".cmenu")
    page.mouse.click(*at)
    page.wait_for_selector(".cmenu", state="detached")
    one.wait_for_function("document.getElementById('p1').style.outline === ''")
    one.locator("p:not([id])").dblclick(position={"x": 8, "y": 6})
    page.wait_for_selector(".sbar")
    page.mouse.click(*at)
    page.wait_for_selector(".sbar", state="detached")
    assert page.evaluate("window.__scrolls") == 0
    assert page.errors == []


def test_a_pages_menu_closes_when_its_frame_is_drawn_again(server, page):  # noqa: F811
    """The agent resends the artifact while its menu is open in the viewer:
    the frame it was about is gone, so the menu goes, and a new Comment lands
    in the tray now shown."""
    page.goto(server.url)
    sid = spawn(page)
    aid, _ = _artifact(
        page, server, ("<h3>Pick</h3><p id=q>mango</p>", "aegis.state({});"), 0
    )
    page.locator(".row.artifact .fbar .view").last.click()
    frame = frame_of(page, "#viewer iframe[data-artifact]")
    old = page.locator("#viewer iframe[data-artifact]").get_attribute("src")
    frame.locator("#q").click(button="right")
    page.wait_for_selector(".cmenu [data-id=comment]")
    # The agent, driven from a second tab of the same session.
    other = page.context.new_page()
    other.goto(f"{server.url.split('#')[0]}#s={sid}")
    other.fill(
        "#input", f"/mcp artifact_update {json.dumps({'id': aid, 'resend': True})}"
    )
    other.press("#input", "Enter")
    page.wait_for_function(
        f"document.querySelector('#viewer iframe[data-artifact]')?.getAttribute('src') !== {json.dumps(old)}"
    )
    page.wait_for_selector(".cmenu", state="detached")
    frame = frame_of(page, "#viewer iframe[data-artifact]")
    frame.locator("#q").click(button="right")
    pick(page, "comment")
    page.keyboard.type("ripe")
    tray = page.locator("#viewer .ctray")
    assert tray.locator(".cn").inner_text() == "1 comment"
    assert tray.locator(".note").input_value() == "ripe"
    other.close()
    assert page.errors == []


OWN_MENU = (
    "<html><body><h2>Board</h2><p id=c>cell</p>"
    "<script>c.addEventListener('contextmenu', (e) => {"
    " e.preventDefault(); c.textContent = 'flag'; })</script></body></html>"
)


def test_a_page_with_its_own_right_click_keeps_it(server, page):  # noqa: F811
    (server.root / "board.html").write_text(OWN_MENU)
    page.goto(server.url)
    spawn(page)
    send(page, 1, ["board.html"])
    view(page, "iframe")
    frame = frame_of(page, "#viewer iframe")
    frame.locator("#c").click(button="right")
    frame.wait_for_function("document.getElementById('c').textContent === 'flag'")
    page.wait_for_timeout(400)
    assert page.locator(".cmenu").count() == 0
    # Its heading, which the page leaves alone, has the host's menu.
    frame.locator("h2").click(button="right")
    page.wait_for_selector(".cmenu [data-id=ask]")
    assert page.errors == []


def test_a_note_being_typed_keeps_the_focus_through_a_resend(server, page):  # noqa: F811
    page.goto(server.url)
    sid = spawn(page)
    aid, _ = _artifact(
        page, server, ("<h3>Pick</h3><p id=q>mango</p>", "aegis.state({});"), 0
    )
    page.locator(".row.artifact .fbar .view").last.click()
    frame = frame_of(page, "#viewer iframe[data-artifact]")
    old = page.locator("#viewer iframe[data-artifact]").get_attribute("src")
    frame.locator("#q").click(button="right")
    pick(page, "comment")
    page.keyboard.type("ri")
    other = page.context.new_page()
    other.goto(f"{server.url.split('#')[0]}#s={sid}")
    other.fill(
        "#input", f"/mcp artifact_update {json.dumps({'id': aid, 'resend': True})}"
    )
    other.press("#input", "Enter")
    page.wait_for_function(
        "document.querySelector('#viewer iframe[data-artifact]')?.getAttribute('src')"
        f" !== {json.dumps(old)}"
    )
    page.keyboard.type("pe")
    note = page.locator("#viewer .ctray .note")
    assert note.count() == 1 and note.input_value() == "ripe"
    other.close()
    assert page.errors == []
