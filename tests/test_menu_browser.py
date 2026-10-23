"""The one context menu (menu.js, #479): how it opens, walks, runs and closes,
and the menus built on it. Kept out of test_browser.py, whose server fixture it
borrows; the page here may read the clipboard."""

import json
import os
import signal
import time

import pytest

from .test_browser import (  # noqa: F401
    EXPECTED_CONSOLE,
    PICK,
    _artifact,
    browser,
    server,
    spawn,
    turns_done,
)

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
        "Insert path",
        "New session here",
        "Terminal here",
        "New file",
        "New folder",
        "Rename",
        "Copy path",
        "Delete",
    ]
    assert page.locator(".cmenu .grp").count() == 2  # Delete stands apart
    assert focused_label(page) == "Insert path"
    page.keyboard.press("ArrowDown")
    page.keyboard.press("ArrowDown")
    assert focused_label(page) == "Terminal here"
    page.keyboard.press("End")
    assert focused_label(page) == "Delete"
    page.keyboard.press("ArrowDown")  # wraps
    assert focused_label(page) == "Insert path"
    page.keyboard.press("Escape")
    page.wait_for_selector(".cmenu", state="detached")
    assert page.evaluate("document.activeElement.dataset.path") == "a.py"

    page.evaluate("navigator.clipboard.writeText('')")
    page.click(row("a.py"), button="right")
    page.wait_for_selector(".cmenu")
    for _ in range(6):
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
    assert focused_label(page) == "Insert path"
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


def test_files_menu_inserts_paths_and_opens_sessions_and_terminals_here(
    server,  # noqa: F811
    page,
    tmp_path,
):
    (tmp_path / "a.py").write_text("a = 1\n")
    (tmp_path / "sub" / "deep").mkdir(parents=True)
    page.goto(server.url)
    page.wait_for_selector("#a2[data-view=fleet]")
    key = spawn(page, "hello")
    page.fill("#input", "see")
    files(page, server)
    page.click(".ftree .trow[data-path='sub']")  # opens the folder
    page.wait_for_selector(row("sub/deep"))

    page.click(row("sub/deep"), button="right")
    page.click(".cmenu [data-id=insert-path]")
    page.wait_for_function(f"location.hash === '#s={key}'")
    page.wait_for_function(
        "document.querySelector('#input').value === 'see `sub/deep`'"
    )
    assert page.evaluate("document.activeElement.id") == "input"

    files(page, server)
    page.click(row("sub"), button="right")
    page.click(".cmenu [data-id=session-here]")
    page.wait_for_selector("#a2[data-view=spawn]")
    page.wait_for_function(
        f"document.querySelector('#sp-cwd').value === {json.dumps(str(tmp_path / 'sub'))}"
    )

    files(page, server)
    page.click(row("sub/deep"), button="right")
    page.click(".cmenu [data-id=terminal-here]")
    page.wait_for_function("location.hash.startsWith('#t=')")
    metas = list((tmp_path / ".aegis" / "state" / "terminals").glob("*/meta.json"))
    assert [json.loads(m.read_text())["cwd"] for m in metas] == [
        str((tmp_path / "sub" / "deep").resolve())
    ]
    assert page.errors == []


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
        "sleep",
        "restart",
        "handoff",
        "rename",
        "autoclose",
        "archive",
    ]
    assert page.locator(".cmenu .grp").count() == 3
    page.click(".cmenu [data-id=interrupt]")
    page.wait_for_selector(card(busy) + ":not(.working)")
    assert page.locator(card(idle)).count() == 1

    ids = card_menu(page, idle)
    assert "interrupt" not in ids and "sleep" in ids
    page.click(".cmenu [data-id=sleep]")
    page.wait_for_selector(card(idle) + ".asleep")
    stopped = card_menu(page, idle)
    assert "sleep" not in stopped and "continue" in stopped
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


def meta(srv, key: str) -> dict:
    return json.loads(
        (srv.root / ".aegis" / "state" / "sessions" / f"{key}.json").read_text()
    )


def test_the_card_menu_speaks_the_lifecycle(server, page):  # noqa: F811
    page.goto(server.url)
    page.wait_for_selector("#a2[data-view=fleet]")
    busy = spawn(page, "hello")
    page.fill("#input", "/busy 30")
    page.press("#input", "Enter")
    page.wait_for_selector(".row.tool.running")
    waiting = spawn(page, "hello")
    page.goto(f"{server.url.split('#')[0]}#fleet")
    page.wait_for_selector(card(busy) + ".working")

    ids = card_menu(page, busy)
    assert {"interrupt", "sleep", "restart", "handoff", "archive"} <= set(ids)
    assert not {"wake", "continue", "close", "stop"} & set(ids)
    page.click(".cmenu [data-id=sleep]")
    page.wait_for_selector(card(busy) + ".asleep")
    assert page.text_content(card(busy) + " .hd .zz") == "zz slept"
    ids = card_menu(page, busy)
    assert {"wake", "continue", "restart"} <= set(ids)
    assert not {"interrupt", "sleep"} & set(ids)

    page.click(".cmenu [data-id=wake]")
    page.wait_for_selector(card(busy) + ":not(.asleep)")
    assert meta(server, busy)["pid"]
    page.goto(f"{server.url.split('#')[0]}#s={busy}")
    page.wait_for_selector(".row.user")
    assert page.locator(".row.user").count() == 2  # hello and /busy; Wake sent nothing

    page.goto(f"{server.url.split('#')[0]}#fleet")
    page.wait_for_selector(card(waiting))
    pid = meta(server, waiting)["pid"]
    card_menu(page, waiting)
    page.click(".cmenu [data-id=restart]")
    for _ in range(50):
        if meta(server, waiting)["pid"] not in (None, pid):
            break
        time.sleep(0.1)
    assert meta(server, waiting)["pid"] not in (None, pid)
    page.goto(f"{server.url.split('#')[0]}#s={waiting}")
    page.wait_for_selector(".row.user")
    assert page.locator(".row.user").count() == 1
    assert page.errors == []


def test_quiet_has_its_own_mark_and_survives_sleep(server, page):  # noqa: F811
    page.goto(server.url)
    page.wait_for_selector("#a2[data-view=fleet]")
    a = spawn(page)
    page.fill("#input", "/busy 30")
    page.press("#input", "Enter")
    page.wait_for_selector(".row.tool.running")
    page.goto(f"{server.url.split('#')[0]}#fleet")
    page.wait_for_selector(card(a) + ".working")
    card_menu(page, a)
    page.click(".cmenu [data-id=quiet]")
    page.wait_for_selector(card(a) + " .hd .quiet")
    assert page.locator(card(a) + " .hd .zz").count() == 0
    ids = card_menu(page, a)
    assert "unquiet" in ids and "interrupt" in ids
    page.click(".cmenu [data-id=sleep]")
    page.wait_for_selector(card(a) + " .hd .zz")
    assert page.locator(card(a) + " .hd .quiet").count() == 1
    assert "unquiet" in card_menu(page, a)
    assert page.errors == []


def test_a_linked_servers_old_states_read_as_asleep(server, page):  # noqa: F811
    # A server older than #479 sends stopped or parked and no asleep_why.
    page.goto(server.url)
    page.wait_for_selector("#a2[data-view=fleet]")
    got = page.evaluate(
        """async () => {
        const { lifecycle } = await import("/static/js/lifecycle.js");
        return [
            lifecycle({ state: "stopped" }),
            lifecycle({ state: "parked" }),
            lifecycle({ state: "idle" }),
            lifecycle({ state: "asleep", asleep_why: "exited" }),
        ];
    }"""
    )
    assert got == [
        {"state": "asleep", "asleep_why": "slept", "old": True},
        {"state": "asleep", "asleep_why": "idle", "old": True},
        {"state": "idle", "asleep_why": None, "old": True},
        {"state": "asleep", "asleep_why": "exited"},
    ]
    assert page.errors == []


def as_old_server(m):
    """A frame as a server older than #479 sends it: stopped or parked, and
    no asleep_why."""
    if isinstance(m, list):
        return [as_old_server(x) for x in m]
    if not isinstance(m, dict):
        return m
    out = {k: as_old_server(v) for k, v in m.items() if k != "asleep_why"}
    if m.get("state") == "asleep":
        out["state"] = "parked" if m.get("asleep_why") == "idle" else "stopped"
    return out


def test_an_old_servers_stopped_session_reads_as_asleep(server, browser):  # noqa: F811
    ctx = browser.new_context(viewport={"width": 1280, "height": 800})
    page = ctx.new_page()
    page.errors = []
    page.on("pageerror", lambda e: page.errors.append(str(e)))
    page.goto(server.url)
    page.wait_for_selector("#a2[data-view=fleet]")
    a = spawn(page, "hello")
    page.fill("#input", "/sleep")
    page.press("#input", "Enter")
    page.goto(f"{server.url.split('#')[0]}#fleet")
    page.wait_for_selector(card(a) + ".asleep")

    # The same server, its frames rewritten as a server older than #479 sends them.
    def route(ws):
        srv = ws.connect_to_server()

        def down(msg):
            if isinstance(msg, str) and msg.startswith("{"):
                msg = json.dumps(as_old_server(json.loads(msg)))
            ws.send(msg)

        srv.on_message(down)

    old = ctx.new_page()
    old.errors = []
    old.on("pageerror", lambda e: old.errors.append(str(e)))
    old.route_web_socket("**/*", route)
    old.goto(server.url)
    old.wait_for_selector(card(a) + ".asleep")
    assert old.text_content(card(a) + " .hd .zz") == "zz slept"
    old.click(card(a), button="right")
    ids = item_ids(old)
    assert "continue" in ids and not {"sleep", "restart", "wake"} & set(ids)
    old.keyboard.press("Escape")
    old.goto(f"{server.url.split('#')[0]}#s={a}")
    old.wait_for_function(
        "document.querySelector('#input').placeholder.startsWith('Asleep;')"
    )
    old.wait_for_function(
        "['stop-session', 'restart'].every(id => document.getElementById(id).disabled)"
    )
    assert old.errors == [] and page.errors == []
    ctx.close()


def dialog_says(pg, text: str) -> None:
    pg.wait_for_selector("#dialog .ok", state="visible")
    assert text in pg.inner_text("#dialog .q")
    pg.keyboard.press("Escape")
    pg.wait_for_selector("#dialog", state="hidden")


def test_sleep_asks_first_when_a_process_it_left_would_die(server, page, tmp_path):  # noqa: F811
    page.goto(server.url)
    page.wait_for_selector("#a2[data-view=fleet]")
    a = spawn(page, "hello")
    pidfile = tmp_path / "left.pid"
    page.fill(
        "#input", f"/sh nohup sleep 60.5813 >/dev/null 2>&1 & echo $! > {pidfile}"
    )
    page.press("#input", "Enter")
    for _ in range(80):
        if pidfile.exists() and pidfile.read_text().strip():
            break
        time.sleep(0.1)
    pid = int(pidfile.read_text())
    try:
        page.goto(f"{server.url.split('#')[0]}#fleet")
        page.wait_for_selector(card(a) + ":not(.working)")
        card_menu(page, a)
        page.click(".cmenu [data-id=sleep]")
        dialog_says(page, "1 other process(es) would die")
        assert page.locator(card(a) + ".asleep").count() == 0  # Esc kept it up
    finally:
        os.kill(pid, signal.SIGKILL)
    assert page.errors == []


def test_restart_asks_first_on_a_working_session(server, page):  # noqa: F811
    page.goto(server.url)
    page.wait_for_selector("#a2[data-view=fleet]")
    a = spawn(page)
    page.fill("#input", "/busy 30")
    page.press("#input", "Enter")
    page.wait_for_selector(".row.tool.running")
    # Typed: the line waits for the answer, and Esc keeps the turn.
    page.fill("#input", "/restart")
    page.press("#input", "Enter")
    dialog_says(page, "cuts the turn")
    assert page.is_visible(".row.tool.running")
    page.goto(f"{server.url.split('#')[0]}#fleet")
    page.wait_for_selector(card(a) + ".working")
    card_menu(page, a)
    page.click(".cmenu [data-id=restart]")
    dialog_says(page, "cuts the turn")
    assert page.locator(card(a) + ".working").count() == 1
    assert page.errors == []


def test_an_old_clients_cached_fleet_reads_as_asleep(server, browser):  # noqa: F811
    ctx = browser.new_context(viewport={"width": 1280, "height": 800})
    page = ctx.new_page()
    page.goto(server.url)
    page.wait_for_selector("#a2[data-view=fleet]")
    a = spawn(page, "hello")
    page.fill("#input", "/sleep")
    page.press("#input", "Enter")
    page.goto(f"{server.url.split('#')[0]}#fleet")
    page.wait_for_selector(card(a) + ".asleep")
    page.wait_for_timeout(500)  # the Fleet's save on this device
    page.close()

    # No server: the websocket opens and never speaks, so only the cache draws.
    cached = ctx.new_page()
    cached.errors = []
    cached.on("pageerror", lambda e: cached.errors.append(str(e)))
    cached.route_web_socket("**/*", lambda ws: None)
    cached.goto(server.url)
    cached.wait_for_selector(card(a))
    saved = cached.evaluate(
        "async () => (await import('/static/js/devicestore.js')).loadFleet()"
    )
    old = as_old_server(saved["metas"])
    assert [m["state"] for m in old if m["key"] == a] == ["stopped"]
    cached.evaluate(
        "async (metas) => (await import('/static/js/devicestore.js')).saveFleet(metas)",
        old,
    )
    cached.reload()
    cached.wait_for_selector(card(a) + ".asleep")
    assert cached.text_content(card(a) + " .hd .zz") == "zz slept"
    assert cached.errors == []
    ctx.close()


def test_continue_is_on_every_card_that_is_not_working(server, page):  # noqa: F811
    page.goto(server.url)
    page.wait_for_selector("#a2[data-view=fleet]")
    waiting = spawn(page, "hello")
    cut = spawn(page)
    page.fill("#input", "/busy 30")
    page.press("#input", "Enter")
    page.wait_for_selector(".row.tool.running")
    page.goto(f"{server.url.split('#')[0]}#fleet")
    page.wait_for_selector(card(cut) + ".working")
    assert "continue" not in card_menu(page, cut)
    page.click(".cmenu [data-id=interrupt]")
    page.wait_for_selector(card(cut) + ":not(.working)")
    for key in (waiting, cut):
        ids = card_menu(page, key)
        assert "continue" in ids and "sleep" in ids and "wake" not in ids, key
    page.click(".cmenu [data-id=continue]")
    page.goto(f"{server.url.split('#')[0]}#s={cut}")
    page.wait_for_selector(".row.user >> text=Continue")
    assert page.errors == []


def test_sleep_asks_when_it_cannot_check_what_would_die(server, browser):  # noqa: F811
    ctx = browser.new_context(viewport={"width": 1280, "height": 800})
    page = ctx.new_page()
    page.errors = []
    page.on("pageerror", lambda e: page.errors.append(str(e)))

    # The server answers everything but the kills check, which fails.
    def route(ws):
        srv = ws.connect_to_server()

        def up(msg):
            m = json.loads(msg) if isinstance(msg, str) else {}
            if m.get("t") == "call" and m.get("op") == "session.kills":
                err = {"code": "timeout", "message": "no answer"}
                ws.send(json.dumps({"t": "reply", "id": m["id"], "error": err}))
            else:
                srv.send(msg)

        ws.on_message(up)

    page.route_web_socket("**/*", route)
    page.goto(server.url)
    page.wait_for_selector("#a2[data-view=fleet]")
    a = spawn(page, "hello")
    page.goto(f"{server.url.split('#')[0]}#fleet")
    page.wait_for_selector(card(a))
    card_menu(page, a)
    page.click(".cmenu [data-id=sleep]")
    dialog_says(page, "could not check what would end with it")
    assert page.locator(card(a) + ".asleep").count() == 0
    assert page.errors == []
    ctx.close()


# -- journal entries, sent cards, canvas tabs and archive rows --------------------


def mcp(pg, n, tool, args):
    pg.fill("#input", f"/mcp {tool} {json.dumps(args)}")
    pg.press("#input", "Enter")
    turns_done(pg, n)


def test_a_journal_entry_opens_its_commit_and_goes_to_its_session(
    server,  # noqa: F811
    page,
    tmp_path,
):
    import subprocess

    repo = tmp_path / "repo"
    repo.mkdir()
    git = ["git", "-C", str(repo)]
    subprocess.run([*git, "init", "-q"], check=True)
    subprocess.run(
        [*git, "remote", "add", "origin", "git@github.com:own/thing.git"], check=True
    )
    page.goto(server.url)
    page.wait_for_selector("#a2[data-view=fleet]")
    key = spawn(
        page,
        f"/sh cd {repo} && echo y > c.py && git add c.py && git -c user.name=n"
        " -c user.email=n@x commit -m 'add c'",
    )
    turns_done(page, 1)
    sha = subprocess.run(
        [*git, "rev-parse", "--short", "HEAD"], capture_output=True, text=True
    ).stdout.strip()
    page.evaluate("window.open = (u) => { window.opened = u; }")
    page.keyboard.press("Alt+KeyL")
    page.wait_for_selector("#a2[data-view=journal]")
    commit = page.locator(".journal .jrow.k-commit").first
    commit.wait_for()
    commit.click(button="right")
    assert item_ids(page) == ["url", "session"]
    assert page.locator(".cmenu [data-id=url]").inner_text().strip() == "Open commit"
    page.click(".cmenu [data-id=url]")
    assert page.evaluate("window.opened") == (
        f"https://github.com/own/thing/commit/{sha}"
    )

    turn = page.locator(".journal .jrow.k-turn").first
    turn.click(button="right")
    assert item_ids(page) == ["session"]
    page.click(".cmenu [data-id=session]")
    page.wait_for_function(f"location.hash.startsWith('#s={key}')")
    assert page.errors == []


def test_a_sent_cards_menu_docks_opens_a_window_and_downloads(server, page):  # noqa: F811
    (server.root / "r.md").write_text("# One\n")
    page.goto(server.url)
    page.wait_for_selector("#a2[data-view=fleet]")
    sid = spawn(page)
    mcp(page, 1, "file_send", {"paths": ["r.md"]})
    fkey = page.locator(".row.file .fbar .dock").get_attribute("data-dock")
    page.click(".row.file .fbar .fn", button="right")
    assert item_ids(page) == ["dock-right", "dock-left", "window", "download"]
    with page.expect_download() as got:
        page.click(".cmenu [data-id=download]")
    assert got.value.suggested_filename == "r.md"

    page.click(".row.file .fbar .fn", button="right")
    page.click(".cmenu [data-id=dock-left]")
    pane = page.locator(f'#canvas .pane[data-pane="{fkey}"]')
    pane.wait_for()
    page.wait_for_function(
        "k => document.querySelector(`#canvas .pane[data-pane='${k}']`).getBoundingClientRect().x"
        " < document.querySelector('#tr-pane').getBoundingClientRect().x",
        arg=fkey,
    )
    # Docked here: no Dock; a new window still takes it.
    page.click(".row.file .fbar .fn", button="right")
    assert item_ids(page) == ["window", "download"]
    with page.context.expect_page() as opened:
        page.click(".cmenu [data-id=window]")
    two = opened.value
    two.wait_for_function(f"location.hash === '#w=2:{sid}'")
    two.locator(f'#canvas .pane[data-pane="{fkey}"]').wait_for()

    aid, _ = _artifact(page, server, PICK, 1)
    page.click(".row.artifact .fbar", button="right", position={"x": 60, "y": 10})
    assert item_ids(page) == ["dock-right", "dock-left", "window"]
    page.click(".cmenu [data-id=dock-right]")
    page.locator(f'#canvas .pane[data-pane="{aid}"]').wait_for()
    assert page.errors == []


def test_a_canvas_tab_offers_undock_and_move_to_window(server, page):  # noqa: F811
    (server.root / "r.md").write_text("# One\n")
    page.goto(server.url)
    page.wait_for_selector("#a2[data-view=fleet]")
    sid = spawn(page)
    mcp(page, 1, "file_send", {"paths": ["r.md"]})
    fkey = page.locator(".row.file .fbar .dock").get_attribute("data-dock")
    page.click(".row.file .fbar .dock")
    page.locator(f'#canvas .pane[data-pane="{fkey}"]').wait_for()
    page.set_viewport_size({"width": 820, "height": 800})
    tab = page.locator(f'#canvas .ptabs button[data-pane="{fkey}"]')
    tab.wait_for()
    tab.click(button="right")
    assert item_ids(page) == ["undock", "move"]
    page.click(".cmenu [data-id=move]")
    assert item_ids(page) == ["new-window"]
    with page.context.expect_page() as opened:
        page.click(".cmenu [data-id=new-window]")
    opened.value.wait_for_function(f"location.hash === '#w=2:{sid}'")
    tab.wait_for(state="detached")
    page.set_viewport_size({"width": 1280, "height": 800})
    assert page.errors == []


def test_an_archive_rows_menu_reads_and_reopens(server, page):  # noqa: F811
    page.goto(server.url)
    page.wait_for_selector("#a2[data-view=fleet]")
    key = spawn(page, "hello")
    turns_done(page, 1)
    page.fill("#input", "/archive")
    page.press("#input", "Enter")
    page.goto(f"{server.url.split('#')[0]}#fleet")
    row = page.locator(f"#arch-list tr[data-id='{key}']")
    row.wait_for()
    row.locator("td").first.click(button="right")
    assert item_ids(page) == ["read", "reopen"]
    page.click(".cmenu [data-id=read]")
    page.wait_for_function(f"location.hash === '#read={key}'")
    page.goto(f"{server.url.split('#')[0]}#fleet")
    row.locator("td").first.click(button="right")
    page.click(".cmenu [data-id=reopen]")
    page.wait_for_selector(f"#cards .card[data-id='{key}']", state="attached")
    assert page.errors == []
