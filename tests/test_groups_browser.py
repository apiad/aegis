"""Groups in the client (#586): groups.js's blocks, and the bar that draws them."""

import json

import pytest

from .test_browser import (  # noqa: F401
    browser,
    server,
    spawn,
)

pytestmark = [pytest.mark.browser, pytest.mark.slow]


@pytest.fixture
def page(browser):  # noqa: F811
    pg = browser.new_page(viewport={"width": 1280, "height": 800})
    pg.errors = []
    pg.on("pageerror", lambda e: pg.errors.append(str(e)))
    yield pg
    pg.close()


def m(key, created, group=None, server=None, **kw):
    lid = key.split("/")[-1]
    return {
        "key": key,
        "log_id": lid,
        "created_at": created,
        "group": group,
        "server": server,
        **kw,
    }


def blocks(pg, metas, order=None) -> list:
    """[[head key, [member keys]], ...] from groups.js's blocksOf."""
    return pg.evaluate(
        """async ([metas, order]) => {
          const g = await import('/static/js/groups.js');
          return g.blocksOf(metas, 'alpha', order).map(b => [b.head.key, b.members.map(x => x.key)]);
        }""",
        [metas, order or {"blocks": [], "members": {}}],
    )


def test_blocks_of_a_chain_a_root_gone_and_a_link(server, page):  # noqa: F811
    page.goto(server.url)
    page.wait_for_selector("#a2[data-view=fleet]")
    a, b, c = m("a", 1), m("b", 2, "a"), m("c", 3, "a")
    lone = m("d", 4)
    far = m("beta/e", 5, "a@alpha", server="beta")
    term = m("t", 6, "a", kind="term")
    assert blocks(page, [a, b, lone, c, far, term]) == [
        ["a", ["a", "b", "c", "beta/e"]],
        ["d", ["d"]],
        ["t", ["t"]],
    ]
    # The root archived: the next oldest heads the group, which keeps its place.
    assert blocks(page, [b, lone, c]) == [["b", ["b", "c"]], ["d", ["d"]]]
    # An offline head yields to an online member.
    assert blocks(page, [dict(a, off=True), b]) == [["b", ["b", "a"]]]
    assert page.errors == []


def test_blocks_follow_a_stored_order_and_an_old_flat_one(server, page):  # noqa: F811
    page.goto(server.url)
    page.wait_for_selector("#a2[data-view=fleet]")
    a, b, c, d = m("a", 1), m("b", 2, "a"), m("c", 3, "a"), m("d", 4)
    order = {"blocks": ["d@alpha", "a@alpha"], "members": {"a@alpha": ["c", "b"]}}
    assert blocks(page, [a, b, c, d], order) == [["d", ["d"]], ["a", ["a", "c", "b"]]]
    got = page.evaluate(
        "async (v) => (await import('/static/js/groups.js')).readOrder(v)",
        json.dumps(["d", "c", "a"]),
    )
    assert got == {"blocks": [], "members": {}, "keys": ["d", "c", "a"]}
    assert page.errors == []
