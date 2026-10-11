"""The Help tab as a person uses it: the pages slice 1 ships, served by a real
`aegis serve`, listed, searched and read in headless Chromium (#339)."""

import pytest

from aegis.helppages import Index, load

from .test_browser import (  # noqa: F401
    CONFIG,
    WIDER,
    Server,
    browser,
    page,
    phone,
    server,
    spawn,
)

pytestmark = [pytest.mark.browser, pytest.mark.slow]

PAGES = {p.id: p for p in load()}
LISTING = Index(load()).listing()
GROUPS = (("tutorial", "Tutorials"), ("howto", "How-to"), ("concept", "Concepts"))


def refs(pg) -> list[str]:
    return pg.eval_on_selector_all(".helpv .hrow", "bs => bs.map(b => b.dataset.ref)")


def open_help(pg, server) -> None:  # noqa: F811
    pg.goto(server.url)
    pg.wait_for_selector("#a2[data-view=fleet]")
    pg.click("#help-btn")
    pg.wait_for_selector(".helpv .hrow")


def shown(pg, page_id: str) -> None:
    pg.wait_for_function(
        "t => document.querySelector('.helpv .htitle')?.textContent === t",
        arg=PAGES[page_id].title,
    )


def test_the_question_mark_opens_the_pages_grouped_beside_the_first_tutorial(
    server,  # noqa: F811
    page,  # noqa: F811
):
    open_help(page, server)
    assert page.evaluate("location.hash") == "#help"
    assert "on" in page.get_attribute("#help-btn", "class").split()
    heads = page.eval_on_selector_all(
        ".helpv .hgrp", "gs => gs.map(g => g.textContent)"
    )
    assert heads == [
        name for kind, name in GROUPS if any(r["kind"] == kind for r in LISTING)
    ]
    # Tutorials, how-to, concepts; the index's order inside each group.
    assert refs(page) == [
        r["id"] for kind, _ in GROUPS for r in LISTING if r["kind"] == kind
    ]
    first = next(r["id"] for r in LISTING if r["kind"] == "tutorial")
    shown(page, first)
    assert page.text_content(".helpv .hwhen") == f"{first} · {PAGES[first].when}"
    assert (
        "on" in page.get_attribute(f'.helpv .hrow[data-ref="{first}"]', "class").split()
    )
    # Slice 3 wires /tutorial; until then a tutorial page carries the button hidden.
    assert page.locator(".helpv .hstart").count() == 1
    assert not page.is_visible(".helpv .hstart")
    assert page.errors == []


def test_every_page_reads_with_each_section_where_search_points(server, page):  # noqa: F811
    open_help(page, server)
    for p in PAGES.values():
        page.evaluate("h => { location.hash = h; }", f"#help={p.id}")
        shown(page, p.id)
        ids = page.eval_on_selector_all(".helpv .hbody h2", "hs => hs.map(h => h.id)")
        assert ids == ["h-" + s.slug for s in p.sections[1:]], p.id
        assert page.locator(".helpv .hstart").count() == (p.kind == "tutorial"), p.id
        assert page.locator(".helpv .hbody > .md.doc").count() == 1
        # The title is drawn once, over the page, not again as its body's h1.
        assert page.locator(".helpv .hbody h1").count() == 0, p.id
    # A ref with a section opens the page scrolled to it.
    page.set_viewport_size({"width": 1280, "height": 400})
    p = max(PAGES.values(), key=lambda p: len(p.sections))
    s = p.sections[-1]
    page.evaluate("h => { location.hash = h; }", "#help=getting-started")
    shown(page, "getting-started")
    page.evaluate("h => { location.hash = h; }", f"#help={p.id}%23{s.slug}")
    page.wait_for_function(
        """id => {
          const h = document.getElementById(id);
          const box = document.querySelector('.helpv .hpage').getBoundingClientRect();
          if (!h) return false;
          const top = h.getBoundingClientRect().top;
          return top >= box.top - 1 && top < box.bottom;
        }""",
        arg="h-" + s.slug,
    )
    assert page.evaluate("document.querySelector('.helpv .hpage').scrollTop") > 0
    assert page.errors == []


def test_a_url_naming_no_page_says_which_pages_are_close(server, page):  # noqa: F811
    page.goto(server.url + "#help=monitor")
    page.wait_for_selector(".helpv .hbody .hnone")
    assert "monitors" in page.text_content(".helpv .hbody .hnone")
    # A malformed escape is a page name nobody has, not a thrown URIError.
    page.evaluate("h => { location.hash = h; }", "#help=50%")
    page.wait_for_function(
        "() => document.querySelector('.helpv .htitle').textContent === '50%'"
    )
    assert page.errors == []


def test_a_link_to_another_page_opens_it_in_the_tab(server, page):  # noqa: F811
    page.goto(server.url)
    page.wait_for_selector("#a2[data-view=fleet]")
    got = page.evaluate(
        """() => import('/static/js/help.js').then(({ pageRef }) => {
          const ids = ['monitors', 'queues'];
          return ['monitors', './queues.md', 'monitors#start-one', 'queues.md#workers',
                  '#workers', 'https://example.com/monitors', 'ghost', '']
            .map((h) => pageRef(h, ids, 'queues'));
        })"""
    )
    assert got == [
        "monitors",
        "queues",
        "monitors#start-one",
        "queues#workers",
        "queues#workers",
        None,
        None,
        None,
    ]


def test_every_key_is_one_click_from_the_help_tab(server, page):  # noqa: F811
    open_help(page, server)
    page.click(".helpv .hkeys")
    page.wait_for_selector("#keymap", state="visible")
    page.keyboard.press("Escape")
    page.wait_for_selector("#keymap", state="hidden")
    assert page.errors == []


QUESTION = "wait for CI without polling"


def test_typing_asks_help_search_and_lists_what_an_agent_would_get(server, page):  # noqa: F811
    open_help(page, server)
    want = [h.ref for h in Index(load()).search(QUESTION, limit=10)]
    assert want, "the spec's search table expects monitors for this question"
    page.fill(".helpv .jq", QUESTION)
    page.wait_for_function("() => !document.querySelector('.helpv .hgrp')")
    assert refs(page) == want
    assert page.locator(".helpv .hrow mark").count() > 0
    # A word is marked where a word starts, never inside another ("ci" in "decide").
    marks = page.eval_on_selector_all(
        ".helpv .hrow mark",
        "ms => ms.filter(m => /[a-z0-9]$/i.test(m.previousSibling?.textContent || '')).length",
    )
    assert marks == 0
    # Enter reads the best hit, at its section.
    page.press(".helpv .jq", "Enter")
    page.wait_for_function(
        "r => decodeURIComponent(location.hash) === '#help=' + r", arg=want[0]
    )
    shown(page, want[0].split("#")[0])
    page.fill(".helpv .jq", "")
    page.wait_for_selector(".helpv .hgrp")
    assert len(refs(page)) == len(PAGES)
    assert page.errors == []


def test_a_search_with_no_hit_says_so_over_the_whole_list(server, page):  # noqa: F811
    open_help(page, server)
    assert Index(load()).search("zzqx ?!", limit=10) == []
    page.fill(".helpv .jq", "zzqx ?!")
    page.wait_for_selector(".helpv .hrows .hnone")
    assert "No page matches “zzqx ?!”" in page.text_content(".helpv .hrows")
    assert len(refs(page)) == len(PAGES)
    assert page.errors == []


def test_an_older_answer_never_replaces_a_newer_one(server, page):  # noqa: F811
    page.goto(server.url)
    page.wait_for_selector("#a2[data-view=fleet]")
    got = page.evaluate(
        """async () => {
          const { Help } = await import('/static/js/help.js');
          const calls = [];
          const conn = { call: (op, p) => new Promise((resolve) => calls.push({ p, resolve })) };
          const box = document.createElement('section');
          const h = new Help(conn, box, { go() {}, onKeys() {} });
          h.pages = [{ id: 'a', title: 'A', kind: 'howto', when: 'a' },
                     { id: 'b', title: 'B', kind: 'howto', when: 'b' }];
          h.draw();
          h.q.value = 'mon';
          const older = h.search();
          h.q.value = 'monitors';
          const newer = h.search();
          calls[1].resolve({ hits: [{ ref: 'b', heading: '', when: 'b', snippet: 'newer' }] });
          await newer;
          calls[0].resolve({ hits: [{ ref: 'a', heading: '', when: 'a', snippet: 'older' }] });
          await older;
          return [calls.map((c) => c.p.query), [...box.querySelectorAll('.hrow')].map((r) => r.dataset.ref)];
        }"""
    )
    assert got == [["mon", "monitors"], ["b"]]


def test_help_with_words_opens_the_tab_searched_and_sends_nothing(server, page):  # noqa: F811
    page.goto(server.url)
    page.wait_for_selector("#a2[data-view=fleet]")
    spawn(page, "hello")
    page.fill("#input", "/help " + QUESTION)
    page.press("#input", "Enter")
    page.wait_for_selector("#a2[data-view=help]")
    assert page.input_value(".helpv .jq") == QUESTION
    want = [h.ref for h in Index(load()).search(QUESTION, limit=10)]
    page.wait_for_function(
        "n => !document.querySelector('.helpv .hgrp')"
        " && document.querySelectorAll('.helpv .hrow').length === n",
        arg=len(want),
    )
    assert refs(page) == want
    # Back in the session: the line is gone from the box and never became a turn.
    page.go_back()
    page.wait_for_selector("#a2[data-view=session] .row.user")
    assert page.input_value("#input") == ""
    assert page.evaluate("document.querySelectorAll('.row.user').length") == 1
    assert page.errors == []


@pytest.fixture
def long_named(tmp_path, fake_claude, fake_opencode):
    """A server whose name is wider than the 52 px the phone header has spare
    in a session view, measured with a three-letter name."""
    (tmp_path / ".aegis.yaml").write_text(CONFIG)
    s = Server(tmp_path, fake_claude, fake_opencode)
    s.args = ["--name", "a-long-server-name"]
    s.start()
    yield s
    s.stop()


def test_the_help_tab_is_two_screens_on_a_phone(long_named, browser):  # noqa: F811
    errors: list = []
    pg = phone(browser, errors)
    pg.goto(long_named.url)
    pg.wait_for_selector("#a2[data-view=fleet]")
    spawn(pg)  # a session view adds the panel button: the fullest header
    btn = pg.locator("#help-btn").bounding_box()
    gear = pg.locator("#settings-btn").bounding_box()
    assert btn and btn["x"] + btn["width"] <= 390 and btn["width"] >= 40, btn
    assert abs(btn["y"] - gear["y"]) < 2, "the ? sits on the header's first row"
    assert pg.evaluate(WIDER) == []
    pg.tap("#help-btn")
    pg.wait_for_selector(".helpv[data-screen=list] .hrow")
    assert pg.is_visible(".helpv .hlist") and not pg.is_visible(".helpv .hpage")
    pg.tap('.helpv .hrow[data-ref="getting-started"]')
    pg.wait_for_selector(".helpv[data-screen=page]")
    shown(pg, "getting-started")
    assert not pg.is_visible(".helpv .hlist") and pg.is_visible(".helpv .hback")
    assert pg.evaluate(WIDER) == []
    pg.tap(".helpv .hback")
    pg.wait_for_selector(".helpv[data-screen=list]")
    # The phone's own Back returns from a page to the list, not out of the tab.
    pg.tap('.helpv .hrow[data-ref="monitors"]')
    pg.wait_for_selector(".helpv[data-screen=page]")
    pg.go_back()
    pg.wait_for_selector(".helpv[data-screen=list]")
    assert pg.is_visible(".helpv .hlist")
    assert errors == []
    pg.close()


def test_a_page_arriving_late_leaves_the_focus_in_the_search_box(server, page):  # noqa: F811
    """The page takes the focus when it is asked for, not when its code
    blocks have painted: a person who went back to the box meanwhile keeps
    typing there."""
    page.goto(server.url)
    page.wait_for_selector("#a2[data-view=fleet]")
    got = page.evaluate(
        """async () => {
          const { Help } = await import('/static/js/help.js');
          let answer;
          const conn = { call: () => new Promise((resolve) => { answer = resolve; }) };
          const box = document.createElement('section');
          document.body.append(box);
          const h = new Help(conn, box, { go() {}, onKeys() {} });
          h.pages = [{ id: 'a', title: 'A', kind: 'howto', when: 'a' }];
          h.draw();
          box.dataset.screen = 'page';
          const read = h.read('a', '');
          h.q.focus();
          answer('## One\\n\\n```python\\nx = 1\\n```\\n');
          await read;
          const kept = document.activeElement === h.q;
          box.remove();
          return kept;
        }"""
    )
    assert got is True
