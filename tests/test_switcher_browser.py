"""The session switcher (#781): Alt+P lists every open session, grouped by
server, quiet ones last, and opens the chosen one."""

import pytest

from .test_browser import (  # noqa: F401
    browser,
    server,
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


def meta(handle, mark="idle", server=None, quiet=False, last=100, **kw):
    key = f"{server}/{handle}" if server else handle
    return {
        "key": key,
        "handle": handle,
        "mark": mark,
        "attention": mark,
        "server": server,
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
        meta("gus", "review", server="lab"),
        meta("kai", "working", quiet=True, last=50),
        meta("lea", "waiting", server="lab", quiet=True),
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
        meta("hal-eval", server="lab", title="report"),
    ]
    links = [{"name": "lab", "state": "linked"}]
    assert page.evaluate(ROWS, [metas, "home", links, "hal"]) == [["lab", ["hal-eval"]]]
    # a title matches too, and the server's name
    assert page.evaluate(ROWS, [metas, "home", links, "pagin"]) == [["home", ["dan-api"]]]
    assert page.evaluate(ROWS, [metas, "home", links, "lab"]) == [["lab", ["hal-eval"]]]
    assert page.errors == []
