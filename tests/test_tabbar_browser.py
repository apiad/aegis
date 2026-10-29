"""The tab bar (#586): bare icon buttons, the session panel's toggle in the
transcript's navigation pill, two-line tabs that keep the whole handle, a
strip that scrolls, and the menu at its end."""

import json

import pytest

from .test_browser import (  # noqa: F401
    browser,
    linked,
    server,
    spawn,
)

pytestmark = [pytest.mark.browser, pytest.mark.slow]

HEADER_ICONS = [
    "schedules-btn",
    "settings-btn",
    "files-btn",
    "journal-btn",
    "knowledge-btn",
    "usage-btn",
]


@pytest.fixture
def page(browser):  # noqa: F811
    pg = browser.new_page(viewport={"width": 1280, "height": 800})
    pg.errors = []
    pg.on("pageerror", lambda e: pg.errors.append(str(e)))
    yield pg
    pg.close()


def bare(pg, sel: str) -> dict:
    """What a header button draws around its icon."""
    return pg.eval_on_selector(
        sel,
        """b => { const c = getComputedStyle(b);
          const lbl = b.querySelector('.lbl');
          return {bg: c.backgroundColor, border: c.borderTopColor,
                  label: !!lbl && lbl.offsetWidth > 0,
                  icon: !!b.querySelector('svg') && b.querySelector('svg').getBoundingClientRect().width > 0}; }""",
    )


def test_the_header_buttons_are_bare_icons(server, page):  # noqa: F811
    page.goto(server.url)
    page.wait_for_selector("#a2[data-view=fleet]")
    for id in HEADER_ICONS:
        b = bare(page, f"#{id}")
        assert b == {
            "bg": "rgba(0, 0, 0, 0)",
            "border": "rgba(0, 0, 0, 0)",
            "label": False,
            "icon": True,
        }, id
        assert page.get_attribute(f"#{id}", "aria-label")
    assert page.errors == []


def test_the_usage_button_opens_the_usage_page(server, page):  # noqa: F811
    page.goto(server.url)
    page.wait_for_selector("#a2[data-view=fleet]")
    page.click("#usage-btn")
    page.wait_for_selector("#a2[data-view=usage]")
    assert "on" in page.get_attribute("#usage-btn", "class")
    page.click("#journal-btn")
    page.wait_for_selector("#a2[data-view=journal]")
    assert "on" not in page.get_attribute("#usage-btn", "class")
    assert page.errors == []


def test_files_stays_in_the_header_on_a_phone(server, browser):  # noqa: F811
    pg = browser.new_page(viewport={"width": 390, "height": 844})
    pg.goto(server.url)
    pg.wait_for_selector("#a2[data-view=fleet]")
    assert pg.is_visible("#files-btn")
    pg.close()


def test_the_panel_toggle_lives_in_the_nav_pill(server, page):  # noqa: F811
    page.goto(server.url)
    page.wait_for_selector("#a2[data-view=fleet]")
    spawn(page, "hello")
    assert page.is_visible("#nav #nav-side")
    assert not page.is_visible(".tabs #side-btn")
    before = page.get_attribute("#a2", "data-side")
    page.click("#nav-side")
    side = "(document.querySelector('#a2').dataset.side ?? null)"
    page.wait_for_function(f"{side} !== {json.dumps(before)}")
    page.keyboard.press("Alt+b")
    page.wait_for_function(f"{side} === {json.dumps(before)}")
    assert page.errors == []


def test_a_phone_header_is_one_row_above_fleet_and_the_tabs(server, browser):  # noqa: F811
    # #631: the header wrapped, leaving ? alone on a second row, and Fleet and
    # the panel toggle took width the tabs needed.
    pg = browser.new_page(viewport={"width": 390, "height": 844}, has_touch=True)
    pg.goto(server.url)
    pg.wait_for_selector("#a2[data-view=fleet]")
    for p in ("one", "two", "three"):
        spawn(pg, p)
    box = lambda sel: pg.locator(sel).bounding_box()  # noqa: E731
    # Each button's drawn icon (the ? is a glyph), and the server's name.
    row = pg.evaluate(
        """ids => Object.fromEntries(ids.map(id => {
          const b = document.getElementById(id), r = document.createRange();
          r.selectNodeContents(b);
          const q = (b.querySelector('svg, .ic') ?? r).getBoundingClientRect();
          const o = b.getBoundingClientRect();
          return [id, [q.y + q.height / 2, o.width, o.height]];
        }))""",
        [*HEADER_ICONS, "bell", "help-btn", "conn-text"],
    )
    mids = [m for m, _, _ in row.values()]
    assert max(mids) - min(mids) <= 1, row
    sizes = {(w, h) for k, (_, w, h) in row.items() if k != "conn-text"}
    assert len(sizes) == 1, row
    tops = {k: box(f"#{k}")["y"] for k in [*HEADER_ICONS, "bell", "help-btn"]}
    fleet, tabs = box("#tab-fleet"), box("#tablist")
    assert abs(fleet["y"] - tabs["y"]) < 8 and fleet["x"] < tabs["x"]
    assert fleet["y"] > max(tops.values())
    assert not pg.is_visible("#side-btn")
    pg.tap("#nav-side")
    pg.wait_for_function("document.getElementById('a2').dataset.side === 'open'")
    pg.close()


def spawn_on(pg, url: str, n: int) -> None:
    pg.goto(url)
    pg.wait_for_selector("#a2[data-view=fleet]")
    for _ in range(n):
        spawn(pg)


TABS = """() => [...document.querySelectorAll('#tablist .tab')].map(t => {
  const r = t.getBoundingClientRect(), hn = t.querySelector('.hn'), w = t.querySelector('.where');
  const wr = w && w.getBoundingClientRect();
  return {left: r.left, right: r.right,
          whole: !!hn && hn.scrollWidth <= hn.clientWidth,
          inside: !w || (wr.left >= r.left - 0.5 && wr.right <= r.right + 0.5)}; })"""


def test_fourteen_tabs_do_not_overlap_keep_whole_handles_and_scroll(
    linked,  # noqa: F811
    browser,  # noqa: F811
    page,
):
    other = browser.new_page()
    spawn_on(other, linked.beta.url, 6)
    other.close()
    spawn_on(page, linked.alpha.url, 8)
    page.wait_for_function("document.querySelectorAll('#tablist .tab').length === 14")
    tabs = page.evaluate(TABS)
    for a, b in zip(tabs, tabs[1:]):
        assert a["right"] <= b["left"] + 0.5, (a, b)
    assert all(t["whole"] for t in tabs), tabs
    assert all(t["inside"] for t in tabs), tabs
    width = "document.getElementById('tablist')"
    assert page.evaluate(f"{width}.scrollWidth > {width}.clientWidth")
    page.hover("#tablist")
    page.mouse.wheel(0, 400)
    page.wait_for_function(f"{width}.scrollLeft > 0")
    assert page.errors == []


def test_a_tab_reads_its_handle_over_its_title(server, page):  # noqa: F811
    page.goto(server.url)
    page.wait_for_selector("#a2[data-view=fleet]")
    key = spawn(page, "hello there")
    tab = page.locator(f"#tablist .tab[data-id='{key}']")
    page.wait_for_function(
        f"document.querySelector(\"#tablist .tab[data-id='{key}'] .tt\").textContent !== ''"
    )
    handle = tab.locator(".hn").inner_text()
    assert handle and " " not in handle
    assert tab.locator(".tt").inner_text()
    assert tab.get_attribute("title") is None
    assert tab.get_attribute("aria-label").startswith(f"{handle}: ")


def test_a_long_handle_on_a_phone_keeps_the_page_its_width(server, browser):  # noqa: F811
    pg = browser.new_page(viewport={"width": 390, "height": 844}, has_touch=True)
    pg.goto(server.url)
    pg.wait_for_selector("#a2[data-view=fleet]")
    key = spawn(pg)
    pg.fill("#input", "/rename extraordinarily-lengthy-handle")
    pg.tap("#send")  # on a touch screen Enter adds a line
    tab = f"#tablist .tab[data-id='{key}']"
    pg.wait_for_selector(f"{tab} .hn >> text=extraordinarily-lengthy-handle")
    assert pg.evaluate("document.documentElement.scrollWidth") <= 390
    pg.locator(tab).scroll_into_view_if_needed()
    assert pg.locator(tab).is_visible()
    pg.close()


def test_the_more_button_shows_only_when_the_strip_overflows(server, browser):  # noqa: F811
    pg = browser.new_page(viewport={"width": 900, "height": 700})
    pg.goto(server.url)
    pg.wait_for_selector("#a2[data-view=fleet]")
    spawn(pg)
    assert pg.is_hidden("#tab-more")
    for _ in range(5):
        spawn(pg)
    pg.wait_for_selector("#tab-more:visible")
    assert pg.inner_text("#tab-more").strip() == "6"
    assert pg.locator('#tab-more use[href="#g-caret-d"]').count() == 1
    pg.close()


def test_the_more_button_opens_the_switcher_and_a_row_scrolls_its_tab_into_view(
    server,  # noqa: F811
    browser,  # noqa: F811
):
    # #781: the button opens the session switcher in place of its menu.
    pg = browser.new_page(viewport={"width": 900, "height": 700})
    pg.goto(server.url)
    pg.wait_for_selector("#a2[data-view=fleet]")
    keys = [spawn(pg) for _ in range(6)]
    pg.goto(server.url.split("#")[0] + "#fleet")
    pg.click("#tab-more")
    pg.wait_for_selector("#switcher", state="visible")
    rows = pg.eval_on_selector_all(
        "#switcher .sw-row", "els => els.map(e => e.dataset.id)"
    )
    assert sorted(rows) == sorted(keys)
    pg.click(f"#switcher .sw-row[data-id='{keys[-1]}']")
    pg.wait_for_function(f"location.hash === '#s={keys[-1]}'")
    inside = pg.evaluate(
        f"""() => {{ const t = document.querySelector("#tablist .tab[data-id='{keys[-1]}']").getBoundingClientRect();
              const l = document.getElementById('tablist').getBoundingClientRect();
              return t.left >= l.left - 1 && t.right <= l.right + 1; }}"""
    )
    assert inside
    pg.close()


def test_on_a_phone_the_more_button_always_shows_and_opens_the_switcher(
    server,  # noqa: F811
    browser,  # noqa: F811
):
    # #781: a phone has no Alt key; the button is its way in, even when the
    # one tab fits, and a tap leaves the field unfocused: no keyboard.
    pg = browser.new_page(
        viewport={"width": 390, "height": 844}, has_touch=True, is_mobile=True
    )
    pg.goto(server.url)
    pg.wait_for_selector("#a2[data-view=fleet]")
    a = spawn(pg)
    pg.goto(server.url.split("#")[0] + "#fleet")
    pg.wait_for_selector("#tab-more:visible")
    assert pg.inner_text("#tab-more").strip() == "1"
    pg.tap("#tab-more")
    pg.wait_for_selector("#switcher", state="visible")
    assert pg.evaluate("document.activeElement.id") != "switcher-q"
    box = pg.locator("#switcher .panel").bounding_box()
    assert box["width"] >= 389
    assert pg.evaluate("document.documentElement.scrollWidth") <= 390
    pg.tap(f"#switcher .sw-row[data-id='{a}']")
    pg.wait_for_function(f"location.hash === '#s={a}'")
    pg.close()
