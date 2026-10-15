"""The workflow card against a real aegis serve: the plan before approval,
the prompt folded under each node's sentence, and the run to its end."""

import json

import pytest

from .test_browser import CONFIG, Server, browser, page, spawn  # noqa: F401
from .test_workflows_e2e import RESULT, flow

pytestmark = [pytest.mark.browser, pytest.mark.slow]


@pytest.fixture
def server(tmp_path, fake_claude, fake_opencode):
    (tmp_path / ".aegis.yaml").write_text(CONFIG)
    s = Server(tmp_path, fake_claude, fake_opencode).start()
    yield s
    s.stop()


def test_a_person_reads_the_plan_approves_it_and_watches_it_finish(server, page):  # noqa: F811
    page.goto(server.url)
    page.wait_for_selector("#a2[data-view=fleet]")
    arm = {"definition": flow(RESULT, RESULT), "args": {}}
    spawn(page, "/mcp workflow_arm " + json.dumps(arm))
    card = ".wfcard[data-status=armed]"
    page.wait_for_selector(f"{card} >> text=Do the first thing.")
    page.click(f"{card} .wfrow[data-node=one] .tog")
    page.wait_for_selector(f"{card} .wfrow[data-node=one] pre")
    page.click(f"{card} button >> text=Approve and run")
    page.wait_for_selector(".wfcard[data-status=done]", timeout=20000)
    page.wait_for_selector(".wfcard .story >> text=Did it, n is 7.")
    assert not page.errors


def test_the_card_fits_a_phone(server, browser):  # noqa: F811
    from .test_browser import new_page

    errors: list = []
    pg = new_page(browser, errors)
    pg.set_viewport_size({"width": 390, "height": 800})
    pg.goto(server.url)
    pg.wait_for_selector("#a2[data-view=fleet]")
    spawn(
        pg,
        "/mcp workflow_arm "
        + json.dumps({"definition": flow(RESULT, RESULT), "args": {}}),
    )
    pg.wait_for_selector(".wfcard")
    assert pg.evaluate("document.documentElement.scrollWidth") <= 390 and not errors


def test_the_card_docks_beside_the_transcript_as_a_file_does(server, page):  # noqa: F811
    page.set_viewport_size({"width": 1300, "height": 900})
    page.goto(server.url)
    page.wait_for_selector("#a2[data-view=fleet]")
    spawn(
        page,
        "/mcp workflow_arm "
        + json.dumps({"definition": flow(RESULT, RESULT), "args": {}}),
    )
    page.click("#entries .wfcard .btn.dock")
    pane = ".pane[data-pane^=wf-] .wfcard[data-status=armed]"
    page.wait_for_selector(f"{pane} >> text=Do the first thing.")
    page.wait_for_selector(".pane[data-pane^=wf-] .pbar >> text=Two steps")
    page.wait_for_selector("#entries .wfcard .held >> text=docked")
    page.click(f"{pane} button >> text=Approve and run")
    page.wait_for_selector(
        ".pane[data-pane^=wf-] .wfcard[data-status=done]", timeout=20000
    )
    assert not page.errors


def test_the_running_card_counts_up_without_redrawing(server, page):  # noqa: F811
    page.goto(server.url)
    page.wait_for_selector("#a2[data-view=fleet]")
    arm = {"definition": flow("/sleep 6", RESULT), "args": {}}
    spawn(page, "/mcp workflow_arm " + json.dumps(arm))
    page.click(".wfcard[data-status=armed] button >> text=Approve and run")
    card = ".wfcard[data-status=running]"
    page.wait_for_selector(f"{card} .wfrow[data-node=one][data-state=running]")
    page.evaluate(
        """(card) => {
          const c = document.querySelector(card);
          window.__row = c.querySelector('.wfrow[data-node=one]');
          window.__stop = [...c.querySelectorAll('button')].find(b => b.textContent === 'Stop');
          window.__stop.disabled = true;  // as a click does while the call is out
          window.__dur = window.__row.querySelector('.dur').textContent;
        }""",
        card,
    )
    page.wait_for_function(
        """(card) => document.querySelector(card + ' .wfrow[data-node=one] .dur')
             .textContent !== window.__dur""",
        arg=card,
        timeout=5000,
    )
    same = page.evaluate(
        """(card) => {
          const c = document.querySelector(card);
          return c.querySelector('.wfrow[data-node=one]') === window.__row
            && window.__stop.isConnected && window.__stop.disabled;
        }""",
        card,
    )
    assert same, "the tick redrew the card"
    assert not page.errors
