"""The session switcher (#781): Alt+P lists every open session, grouped by
server, quiet ones last, and opens the chosen one."""

import pytest

from .test_browser import (  # noqa: F401
    HOLD,
    browser,
    focused_id,
    hash_is,
    server,
    spawn,
    tab_ids,
)

pytestmark = [pytest.mark.browser, pytest.mark.slow]

ROWS = """([metas, here, links, q]) => import('/static/js/switcher.js').then(m =>
  m.switcherRows(metas, here, links, q).map(g => [g.name, g.rows.map(r => r.m.handle)]))"""


@pytest.fixture
def page(browser):  # noqa: F811
    pg = browser.new_page(viewport={"width": 1280, "height": 800})
    pg.errors = []
    pg.on("pageerror", lambda e: pg.errors.append(str(e)))
    yield pg
    pg.close()


def meta(handle, mark="idle", far=None, quiet=False, last=100, **kw):
    key = f"{far}/{handle}" if far else handle
    return {
        "key": key,
        "handle": handle,
        "mark": mark,
        "attention": mark,
        "server": far,
        "quiet": quiet,
        "last_activity": last,
        "title": "",
        **kw,
    }


def test_rows_group_by_server_order_by_attention_and_put_quiet_last(server, page):  # noqa: F811
    page.goto(server.url)
    page.wait_for_selector("#a2[data-view=fleet]")
    metas = [
        meta("ana", "working", last=500),
        meta("ben", "needs_you", last=300),
        meta("cara", "needs_you", last=100),  # waiting longer than ben
        meta("dev", "error", last=400),
        meta("eli", "idle", last=900),
        meta("gus", "review", far="lab"),
        meta("kai", "working", quiet=True, last=50),
        meta("lea", "waiting", far="lab", quiet=True),
    ]
    links = [{"name": "lab", "state": "linked"}]
    assert page.evaluate(ROWS, [metas, "home", links, ""]) == [
        ["home", ["cara", "ben", "dev", "ana", "eli"]],
        ["lab", ["gus"]],
        ["Quiet", ["kai", "lea"]],
    ]
    assert page.errors == []


def test_a_query_drops_empty_groups_and_ranks_by_score(server, page):  # noqa: F811
    page.goto(server.url)
    page.wait_for_selector("#a2[data-view=fleet]")
    metas = [
        meta("ana-docs", title="install guide"),
        meta("dan-api", title="pagination"),
        meta("hal-eval", far="lab", title="report"),
    ]
    links = [{"name": "lab", "state": "linked"}]
    assert page.evaluate(ROWS, [metas, "home", links, "hal"]) == [["lab", ["hal-eval"]]]
    # a title matches too, and the server's name
    assert page.evaluate(ROWS, [metas, "home", links, "pagin"]) == [
        ["home", ["dan-api"]]
    ]
    assert page.evaluate(ROWS, [metas, "home", links, "lab"]) == [["lab", ["hal-eval"]]]
    assert page.errors == []


def rows(pg) -> list[str]:
    return pg.eval_on_selector_all(
        "#switcher .sw-row", "rs => rs.map(r => r.dataset.id)"
    )


def selected(pg) -> str:
    return pg.get_attribute("#switcher .sw-row.on", "data-id")


def test_alt_p_lists_filters_opens_and_closes(server, page):  # noqa: F811
    page.goto(server.url)
    page.wait_for_selector("#a2[data-view=fleet]")
    a = spawn(page, "first")
    b = spawn(page, "second")
    page.focus("#input")
    page.keyboard.press("Alt+p")
    page.wait_for_selector("#switcher", state="visible")
    assert focused_id(page) == "switcher-q"
    assert sorted(rows(page)) == sorted([a, b])
    assert selected(page) == a  # b is in view: the selection starts elsewhere
    assert page.locator(f"#switcher .sw-row[data-id='{b}'] .here").count() == 1
    page.keyboard.press("Alt+p")  # again: the next row, wrapping
    assert selected(page) != a
    page.keyboard.press("Escape")
    page.wait_for_selector("#switcher", state="hidden")
    assert focused_id(page) == "input"  # handed back
    page.keyboard.press("Alt+p")
    handle = page.inner_text(f"#switcher .sw-row[data-id='{a}'] .h")
    page.keyboard.type(handle)
    assert selected(page) == a
    assert page.locator("#switcher .sw-row.on .h b").count() == len(handle)
    page.keyboard.press("Enter")
    page.wait_for_selector("#switcher", state="hidden")
    hash_is(page, f"#s={a}")
    page.keyboard.press("Alt+p")
    page.keyboard.type("zzqxw")
    page.wait_for_selector("#switcher-rows .empty")
    page.keyboard.press("Escape")
    assert page.errors == []


def test_a_quiet_session_is_listed_last_and_enter_brings_its_tab_back(server, page):  # noqa: F811
    page.goto(server.url)
    page.wait_for_selector("#a2[data-view=fleet]")
    a = spawn(page, "stays")
    b = spawn(page, HOLD)
    page.wait_for_function("!document.querySelector('#quiet-session').disabled")
    page.click("#quiet-session")
    page.wait_for_function("id => location.hash === '#s=' + id", arg=a)
    assert tab_ids(page) == [a]
    page.keyboard.press("Alt+p")
    page.wait_for_selector("#switcher", state="visible")
    assert rows(page)[-1] == b
    heads = page.eval_on_selector_all(
        "#switcher .sw-gh .nm", "hs => hs.map(h => h.textContent)"
    )
    assert heads[-1] == "Quiet"
    assert page.locator(f"#switcher .sw-row.dim[data-id='{b}']").count() == 1
    assert selected(page) == b  # a is in view: the selection starts on the next row
    page.keyboard.press("PageDown")  # the first row of the next group, wrapping
    assert selected(page) == a
    page.keyboard.press("PageUp")
    assert selected(page) == b
    page.keyboard.press("Enter")
    page.wait_for_function("id => location.hash === '#s=' + id", arg=b)
    page.wait_for_function(
        f"[...document.querySelectorAll('#tablist .tab')].some(t => t.dataset.id === '{b}')"
    )
    assert tab_ids(page) == [a, b]
    assert page.errors == []


def test_the_palette_and_the_key_list_offer_the_switcher(server, page):  # noqa: F811
    page.goto(server.url)
    page.wait_for_selector("#a2[data-view=fleet]")
    page.keyboard.press("Control+k")
    page.keyboard.type("switch to a session")
    assert page.get_attribute("#palette .cmd-row.on", "data-action") == "sessions"
    assert page.inner_text("#palette .cmd-row.on kbd") == "Alt+P"
    page.keyboard.press("Enter")
    page.wait_for_selector("#switcher", state="visible")
    page.wait_for_selector("#palette", state="hidden")
    assert page.errors == []
