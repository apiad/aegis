"""/handoff in the client (#874): the new tab opens, the old tab's box is gone."""

import pytest

from .test_agents import mcp
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


ROW = (
    "name => [...document.querySelectorAll('#cmd-menu .cmd-row .nm')]"
    ".some(n => n.textContent === name)"
)


def test_handoff_line_opens_the_new_tab_and_hides_the_old_box(server, page):  # noqa: F811
    page.goto(server.url)
    page.wait_for_selector("#a2[data-view=fleet]")
    old = spawn(page, mcp("session_spawn", agent="opus", prompt="hello"))
    page.fill("#input", "/handoff [[slow]] go on")  # its first turn takes 1.5 s
    page.press("#input", "Enter")
    page.wait_for_function("k => location.hash.slice(3) !== k", arg=old)
    new = page.evaluate("location.hash.slice(3)")
    assert page.is_visible("#composer")
    page.evaluate("k => { location.hash = `#s=${k}`; }", old)
    page.wait_for_selector("#handoff-done a")
    assert not page.is_visible("#composer")
    assert "takes no messages" in page.inner_text("#handoff-done")
    page.click("#handoff-done a")
    page.wait_for_function("k => location.hash.slice(3) === k", arg=new)
    page.wait_for_function(
        "k => !document.querySelector(`#tablist .tab[data-id='${k}']`)",
        arg=old,
        timeout=8000,
    )
    assert page.errors == []


def test_the_menu_completes_handoffs_flags(server, page):  # noqa: F811
    page.goto(server.url)
    page.wait_for_selector("#a2[data-view=fleet]")
    spawn(page, mcp("session_spawn", agent="opus", prompt="hello"))
    page.click("#input")
    page.keyboard.type("/handoff --a")
    page.wait_for_function(ROW, arg="--agent")
    page.keyboard.press("Enter")  # accepts the flag, sends nothing
    assert page.input_value("#input") == "/handoff --agent "
    page.wait_for_function(ROW, arg="deepseek")
    assert page.errors == []
