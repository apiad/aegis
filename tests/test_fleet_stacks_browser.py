"""The Fleet as stacks (#586): a group of sessions is one stack of cards that
unfolds across the grid on a click."""

import pytest

from .test_browser import (  # noqa: F401
    browser,
    report,
    server,
    spawn,
    turns_done,
)
from .test_groups_browser import three_and_one

pytestmark = [pytest.mark.browser, pytest.mark.slow]


@pytest.fixture
def page(browser):  # noqa: F811
    pg = browser.new_page(viewport={"width": 1280, "height": 900})
    pg.errors = []
    pg.on("pageerror", lambda e: pg.errors.append(str(e)))
    yield pg
    pg.close()


def fleet(pg):
    pg.goto(pg.url.split("#")[0] + "#fleet")
    pg.wait_for_selector("#a2[data-view=fleet]")


def test_a_group_is_one_stack_with_a_badge(server, page):  # noqa: F811
    a, b, c, d = three_and_one(page, server)
    fleet(page)
    page.wait_for_selector("#cards .stack")
    assert page.locator("#cards .stack").count() == 1
    assert page.inner_text("#cards .stack .badge") == "3"
    assert page.is_visible(f"#cards .stack .card[data-id='{a}']")
    assert (
        page.locator(
            f"#cards .card[data-id='{b}'], #cards .card[data-id='{c}']"
        ).count()
        == 0
    )
    assert (
        page.locator("#cards .stack .mrow .ic, #cards .stack .mrow .dot").count() == 2
    )
    assert page.is_visible(f"#cards > .card[data-id='{d}']")
    assert page.errors == []


def test_a_click_unfolds_a_stack_and_stack_folds_it_and_a_reload_keeps_it(server, page):  # noqa: F811
    a, b, c, d = three_and_one(page, server)
    fleet(page)
    page.click("#cards .stack")
    page.wait_for_selector("#cards .gopen")
    ids = page.eval_on_selector_all(
        "#cards .gopen .card", "cs => cs.map(c => c.dataset.id)"
    )
    assert ids == [a, b, c]
    assert "3 sessions" in page.inner_text("#cards .gopen .gh")
    page.reload()
    page.wait_for_selector("#cards .gopen")
    page.click("#cards .gopen .gh button")
    page.wait_for_selector("#cards .stack")
    assert page.errors == []


def test_a_member_that_needs_you_marks_the_stack_and_ranks_it_first(server, page):  # noqa: F811
    a, b, c, d = three_and_one(page, server)
    page.click(f"#tablist .tab[data-id='{c}']")
    page.evaluate("document.hasFocus = () => false")
    report(page, attention="needs_you", line="Which build?", replies=[])
    turns_done(page, 1)
    page.click(f"#tablist .tab[data-id='{d}']")
    page.evaluate("delete document.hasFocus")
    fleet(page)
    page.wait_for_selector("#cards .stack.need")
    first = page.eval_on_selector(
        "#cards", "b => [...b.children].find(n => n.matches('.card, .stack')).className"
    )
    assert "stack" in first
    handle = page.inner_text(f"#tablist .tab[data-id='{c}'] .hn")
    assert f"{handle} needs you" in page.inner_text("#cards .stack .mrow")
    assert page.errors == []


def test_the_fleets_unfolding_is_apart_from_the_bars_fold(server, page):  # noqa: F811
    a, b, c, d = three_and_one(page, server)
    fleet(page)
    page.click(f".tab.head[data-id='{a}'] .cnt")  # fold in the bar
    page.wait_for_function("document.querySelectorAll('.tblock.grp .tab').length === 1")
    assert page.locator("#cards .stack").count() == 1
    page.click("#cards .stack")
    page.wait_for_selector("#cards .gopen")
    assert page.locator(".tblock.grp .tab").count() == 1  # still folded there
    assert page.errors == []


def test_a_stacks_head_menu_archives_the_group(server, page):  # noqa: F811
    a, b, c, d = three_and_one(page, server)
    fleet(page)
    page.click(f"#cards .stack .card[data-id='{a}']", button="right")
    page.wait_for_selector(".cmenu [data-id=archivegroup]")
    assert page.errors == []


def test_a_hidden_members_question_reaches_a_stack_on_screen(server, page):  # noqa: F811
    import json

    a, b, c, d = three_and_one(page, server)
    page.click(f"#tablist .tab[data-id='{c}']")
    page.evaluate("document.hasFocus = () => false")
    steps = [["sleep", 1.5], ["turn_end", {"attention": "needs_you", "line": "Which?"}]]
    page.fill("#input", f"/script {json.dumps(steps)}")
    page.press("#input", "Enter")
    fleet(page)
    page.wait_for_selector("#cards .stack:not(.need)")
    page.wait_for_selector("#cards .stack.need", timeout=8000)
    assert page.errors == []
