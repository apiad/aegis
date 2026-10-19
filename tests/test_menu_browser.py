"""The one context menu (menu.js, #479): how it opens, walks, runs and closes,
and the menus built on it. Kept out of test_browser.py, whose server fixture it
borrows; the page here may read the clipboard."""

import pytest

from .test_browser import EXPECTED_CONSOLE, browser, server, spawn  # noqa: F401

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


def row(path: str) -> str:
    return f".ftree .trow[data-path='{path}']"


def files(pg, srv) -> None:
    pg.goto(f"{srv.url.split('#')[0]}#files")
    pg.wait_for_selector(row("a.py"))


def focused_label(pg) -> str:
    return pg.evaluate("document.activeElement.textContent.trim()")


def test_a_menu_walks_with_arrows_runs_with_enter_and_esc_returns_focus(
    server,  # noqa: F811
    page,
    tmp_path,
):
    (tmp_path / "a.py").write_text("a = 1\n")
    files(page, server)
    page.click(row("a.py"), button="right")
    page.wait_for_selector(".cmenu[role=menu]")
    labels = page.locator(".cmenu [role=menuitem]").all_inner_texts()
    assert [x.strip() for x in labels] == [
        "New file",
        "New folder",
        "Rename",
        "Copy path",
        "Delete",
    ]
    assert page.locator(".cmenu .grp").count() == 1  # Delete stands apart
    assert focused_label(page) == "New file"
    page.keyboard.press("ArrowDown")
    page.keyboard.press("ArrowDown")
    assert focused_label(page) == "Rename"
    page.keyboard.press("End")
    assert focused_label(page) == "Delete"
    page.keyboard.press("ArrowDown")  # wraps
    assert focused_label(page) == "New file"
    page.keyboard.press("Escape")
    page.wait_for_selector(".cmenu", state="detached")
    assert page.evaluate("document.activeElement.dataset.path") == "a.py"

    page.evaluate("navigator.clipboard.writeText('')")
    page.click(row("a.py"), button="right")
    page.wait_for_selector(".cmenu")
    for _ in range(3):
        page.keyboard.press("ArrowDown")
    assert focused_label(page) == "Copy path"
    page.keyboard.press("Enter")
    page.wait_for_selector(".cmenu", state="detached")
    page.wait_for_function("navigator.clipboard.readText().then(t => t === 'a.py')")

    # A press outside closes it; so does a change of view.
    page.click(row("a.py"), button="right")
    page.wait_for_selector(".cmenu")
    page.mouse.click(5, 790)
    page.wait_for_selector(".cmenu", state="detached")
    page.click(row("a.py"), button="right")
    page.wait_for_selector(".cmenu")
    page.evaluate("location.hash = '#fleet'")
    page.wait_for_selector(".cmenu", state="detached")
    assert page.errors == []


def test_shift_right_click_and_inputs_keep_the_browsers_menu(server, page, tmp_path):  # noqa: F811
    (tmp_path / "a.py").write_text("a = 1\n")
    files(page, server)
    fire = """([sel, shift]) => document.querySelector(sel).dispatchEvent(
        new MouseEvent('contextmenu', {bubbles: true, cancelable: true, shiftKey: shift, clientX: 50, clientY: 50}))"""
    assert page.evaluate(fire, [row("a.py"), True]) is True  # not prevented
    assert page.locator(".cmenu").count() == 0

    page.click(row("a.py"), button="right")
    page.click(".cmenu button:has-text('Rename')")
    page.wait_for_selector(".ftree .tname")
    assert page.evaluate(fire, [".ftree .tname", False]) is True
    assert page.locator(".cmenu").count() == 0
    assert page.errors == []


def test_the_menu_key_opens_the_focused_rows_menu(server, page, tmp_path):  # noqa: F811
    (tmp_path / "a.py").write_text("a = 1\n")
    files(page, server)
    page.focus(row("a.py"))
    page.keyboard.press("Shift+F10")
    page.wait_for_selector(".cmenu")
    assert focused_label(page) == "New file"
    page.keyboard.press("Escape")
    page.wait_for_selector(".cmenu", state="detached")
    page.keyboard.press("ContextMenu")
    page.wait_for_selector(".cmenu")
    assert page.errors == []


def test_a_long_press_on_touch_opens_it_and_swallows_the_click(
    browser,  # noqa: F811
    server,  # noqa: F811
    tmp_path,
):
    (tmp_path / "a.py").write_text("a = 1\n")
    ctx = browser.new_context(
        viewport={"width": 1280, "height": 800}, has_touch=True
    )  # a phone hides the tree in a drawer
    pg = ctx.new_page()
    errors = []
    pg.on("pageerror", lambda e: errors.append(str(e)))
    files(pg, server)
    box = pg.locator(row("a.py")).bounding_box()
    x, y = box["x"] + 20, box["y"] + box["height"] / 2
    cdp = ctx.new_cdp_session(pg)
    cdp.send(
        "Input.dispatchTouchEvent",
        {"type": "touchStart", "touchPoints": [{"x": x, "y": y}]},
    )
    pg.wait_for_selector(".cmenu", timeout=3000)
    cdp.send("Input.dispatchTouchEvent", {"type": "touchEnd", "touchPoints": []})
    pg.wait_for_timeout(300)
    assert pg.locator(".cmenu").count() == 1
    assert (
        pg.evaluate("location.hash") == "#files"
    )  # the click that followed opened nothing
    ctx.close()
    assert errors == []


# -- the fleet card and session tab menus ------------------------------------------


def item_ids(pg) -> list[str]:
    pg.wait_for_selector(".cmenu")
    return pg.eval_on_selector_all(
        ".cmenu [role=menuitem]", "bs => bs.map(b => b.dataset.id)"
    )


def card(key: str) -> str:
    return f"#cards .card[data-id='{key}']"


def card_menu(pg, key: str) -> list[str]:
    pg.keyboard.press("Escape")
    pg.wait_for_selector(".cmenu", state="detached")
    pg.click(card(key), button="right")
    return item_ids(pg)


def test_a_cards_menu_follows_its_state_and_acts_on_that_card(server, page, browser):  # noqa: F811
    page.goto(server.url)
    page.wait_for_selector("#a2[data-view=fleet]")
    busy = spawn(page)
    page.fill("#input", "/busy 30")
    page.press("#input", "Enter")
    page.wait_for_selector(".row.tool.running")
    idle = spawn(page, "hello")
    page.goto(f"{server.url.split('#')[0]}#fleet")
    page.wait_for_selector(card(busy) + ".working")

    page.click(card(busy), button="right")
    assert item_ids(page) == [
        "open",
        "newtab",
        "quiet",
        "interrupt",
        "stop",
        "handoff",
        "rename",
        "close",
    ]
    assert page.locator(".cmenu .grp").count() == 3
    page.click(".cmenu [data-id=interrupt]")
    page.wait_for_selector(card(busy) + ":not(.working)")
    assert page.locator(card(idle)).count() == 1

    ids = card_menu(page, idle)
    assert "interrupt" not in ids and "continue" in ids and "stop" in ids
    page.click(".cmenu [data-id=stop]")
    page.wait_for_selector(card(idle) + ".stopped")
    stopped = card_menu(page, idle)
    assert "stop" not in stopped and "continue" in stopped
    page.keyboard.press("Escape")

    # A long press on a touch screen opens the same items.
    ctx = browser.new_context(
        viewport={"width": 390, "height": 844}, has_touch=True, is_mobile=True
    )
    phone = ctx.new_page()
    phone.goto(server.url)
    phone.wait_for_selector(card(idle))
    box = phone.locator(card(idle)).bounding_box()
    point = {"x": box["x"] + 20, "y": box["y"] + box["height"] / 2}
    cdp = ctx.new_cdp_session(phone)
    cdp.send("Input.dispatchTouchEvent", {"type": "touchStart", "touchPoints": [point]})
    phone.wait_for_selector(".cmenu", timeout=3000)
    cdp.send("Input.dispatchTouchEvent", {"type": "touchEnd", "touchPoints": []})
    phone.wait_for_timeout(300)
    assert item_ids(phone) == stopped
    assert phone.evaluate("location.hash") in (
        "",
        "#fleet",
    )  # the press opened no session
    ctx.close()
    assert page.errors == []


def test_a_session_tab_has_the_cards_menu(server, page):  # noqa: F811
    page.goto(server.url)
    page.wait_for_selector("#a2[data-view=fleet]")
    a = spawn(page, "hello")
    page.goto(f"{server.url.split('#')[0]}#fleet")
    ids = card_menu(page, a)
    page.keyboard.press("Escape")
    page.click(f"#tablist .tab[data-id='{a}']", button="right")
    assert item_ids(page) == ids
    # Rename… opens the session with its handle in an input.
    page.click(".cmenu [data-id=rename]")
    page.wait_for_selector("#s-handle input.rename")
    assert page.evaluate("location.hash") == f"#s={a}"
    assert page.errors == []


def test_another_members_card_offers_only_opening(server, page, browser):  # noqa: F811
    from .test_browser import member_page

    page.goto(server.url)
    page.wait_for_selector("#a2[data-view=fleet]")
    a = spawn(page, "hello")
    pg, base = member_page(browser, server)
    pg.wait_for_selector(card(a))
    pg.click(card(a), button="right")
    assert item_ids(pg) == ["open", "newtab"]
