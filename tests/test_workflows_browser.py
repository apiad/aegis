"""The workflow card against a real aegis serve: the plan before approval,
the prompt folded under each node's sentence, and the run to its end."""

import json
import re

import pytest

from .test_browser import (  # noqa: F401
    CONFIG,
    Server,
    beta_session,
    browser,
    linked,
    page,
    spawn,
)
from .test_workflows_e2e import RESULT, flow
from .test_workflows_parallel import leaf, report
from .test_workflows_steps import flow as steps

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


def test_a_linked_servers_run_is_approved_and_followed_from_this_server(  # noqa: F811
    linked,  # noqa: F811
    browser,  # noqa: F811
    page,  # noqa: F811
):
    """The run lives on beta and the person reads its session through alpha:
    the card shows the plan, Approve and run reaches beta, the run follows."""
    arm = {"definition": flow(RESULT, RESULT), "args": {}}
    lid = beta_session(linked, browser, "/mcp workflow_arm " + json.dumps(arm))
    page.goto(linked.alpha.url)
    page.click(f"#tablist .tab[data-id='beta/{lid}']")
    card = ".wfcard[data-status=armed]"
    page.wait_for_selector(f"{card} >> text=Do the first thing.")
    page.click(f"{card} button >> text=Approve and run")
    page.wait_for_selector(".wfcard[data-status=done]", timeout=20000)
    page.wait_for_selector(".wfcard .story >> text=Did it, n is 7.")
    assert not page.errors


def test_a_linked_servers_gate_is_answered_from_this_server(linked, browser, page):  # noqa: F811
    """A decision card of a far run: Continue reaches beta and the run goes on."""
    arm = {"definition": GATED, "args": {}}
    lid = beta_session(linked, browser, "/mcp workflow_arm " + json.dumps(arm))
    page.goto(linked.alpha.url)
    page.click(f"#tablist .tab[data-id='beta/{lid}']")
    page.click(".wfcard[data-status=armed] button >> text=Approve and run")
    gate = ".wfdcard[data-what=gate]"
    page.wait_for_selector(f"{gate} textarea")
    page.click(f"{gate} button >> text=Continue")
    page.wait_for_selector(".wfcard[data-status=done]", timeout=20000)
    assert not page.errors


STEPS_FLOW = """id: steps
title: Steps
say: Commands, a probe and a pause.
root:
  seq:
    - {sh: "printf hi", id: hi, say: Say hi.}
    - {check: {sh: "test -n {{hi.out}}"}, id: ok, deadline: 2h, retries: 1, say: Check it.}
    - {wait: 1s, id: pause, say: Pause.}
  say: All of it.
"""


def test_the_card_shows_every_command_before_approval(server, page):  # noqa: F811
    page.goto(server.url)
    page.wait_for_selector("#a2[data-view=fleet]")
    spawn(
        page,
        "/mcp workflow_arm " + json.dumps({"definition": STEPS_FLOW, "args": {}}),
    )
    card = ".wfcard[data-status=armed]"
    page.wait_for_selector(f"{card} .wfrow[data-node=hi] pre.cmd >> text=$ printf hi")
    page.wait_for_selector(
        f"{card} .wfrow[data-node=ok] pre.cmd >> text=$ test -n {{{{hi.out}}}}"
    )
    page.wait_for_selector(
        f"{card} .wfrow[data-node=ok] .tags .deadline:has(use[href='#g-clock']) >> text=2h"
    )
    page.wait_for_selector(
        f"{card} .apbar .runs >> text=Runs 1 shell command and 1 probe"
    )
    page.click(f"{card} button >> text=Approve and run")
    page.wait_for_selector(".wfcard[data-status=done]", timeout=20000)
    page.wait_for_selector(".wfcard .wfrow[data-node=ok] pre.cmd >> text=$ test -n hi")
    assert not page.errors


REPEATS_FLOW = """id: repeats
title: Repeats
say: Commands that run more than once.
args: {type: object, properties: {xs: {type: array}}}
root:
  seq:
    - {sh: "printf hi", id: hi, say: Say hi.}
    - {map: {over: [a, b, c], as: it, do: {sh: "echo {{it}}", id: e, say: Echo.}}, id: m, say: Each.}
    - {map: {over: "{{args.xs}}", as: x, do: {sh: "echo {{x}}", id: f, say: Echo.}}, id: n, say: Each.}
    - {loop: {max: 4, do: {sh: "true", id: t, say: Try.}}, id: l, say: Rounds.}
  say: All of it.
"""


def test_the_approval_line_counts_commands_per_item_and_round(server, page):  # noqa: F811
    page.goto(server.url)
    page.wait_for_selector("#a2[data-view=fleet]")
    arm = {"definition": REPEATS_FLOW, "args": {"xs": [1, 2]}}
    spawn(page, "/mcp workflow_arm " + json.dumps(arm))
    runs = page.wait_for_selector(".wfcard[data-status=armed] .apbar .runs")
    text = runs.inner_text()
    assert text.startswith(
        "Runs 1 shell command, 1 shell command for each of 3 items, "
        "1 shell command for each of 2 items, "
        "and 1 shell command each round, up to 4 rounds, in "
    ), text
    assert not page.errors


TWO_FLOW = """id: two
title: Two
say: Two groups.
root:
  seq:
    - {sh: "printf hi", say: Say hi.}
    - {loop: {max: 1, until: {sh: "true"}, do: {sh: "true", say: Try.}}, say: Once.}
  say: Both.
"""


def test_the_approval_line_joins_two_groups_with_and(server, page):  # noqa: F811
    page.goto(server.url)
    page.wait_for_selector("#a2[data-view=fleet]")
    spawn(page, "/mcp workflow_arm " + json.dumps({"definition": TWO_FLOW}))
    runs = page.wait_for_selector(".wfcard[data-status=armed] .apbar .runs")
    text = runs.inner_text()
    assert text.startswith(
        "Runs 1 shell command and 1 shell command and 1 probe each round, "
        "up to 1 round, in "
    ), text
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
    # A long command wraps rather than widening the page.
    long = STEPS_FLOW.replace("printf hi", "printf hi" + " && echo" + " word" * 40)
    spawn(pg, "/mcp workflow_arm " + json.dumps({"definition": long, "args": {}}))
    pg.wait_for_selector(".wfcard .wfrow[data-node=hi] pre.cmd")
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
    arm = {"definition": flow("/busy 6", RESULT), "args": {}}
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


def test_a_stopped_run_shows_its_running_node_and_seq_stopped(server, page):  # noqa: F811
    page.goto(server.url)
    page.wait_for_selector("#a2[data-view=fleet]")
    arm = {"definition": flow("/busy 10", RESULT), "args": {}}
    spawn(page, "/mcp workflow_arm " + json.dumps(arm))
    page.click(".wfcard[data-status=armed] button >> text=Approve and run")
    page.wait_for_selector(
        ".wfcard[data-status=running] .wfrow[data-node=one][data-state=running]"
    )
    page.click(".wfcard[data-status=running] button >> text=Stop")
    card = ".wfcard[data-status=stopped]"
    page.wait_for_selector(f"{card} .wfrow[data-node=one][data-state=stopped]")
    states = page.eval_on_selector_all(
        f"{card} .wfrow", "rows => rows.map(r => r.dataset.state)"
    )
    assert states == ["stopped", "stopped", "pending"]
    assert not page.errors


TEAMS = f"""
id: teams
title: Teams
say: The mock team starts once the base image is built.
root:
  par:
    - map: {{over: [{{id: base}}, {{id: svc}}], as: s, cap: 1, do: {leaf("img", "Build {{s.id}}.", report(wait=0.5))}}}
      id: images
      say: Images, one at a time.
    - map: {{over: [{{id: m1}}], as: m, do: {leaf("mk", "Mock {{m.id}}.", report(wait=0.5))}}}
      id: mock
      after: [images.base]
      say: Mocks, after the base.
  say: Both teams.
"""


def test_a_person_sees_each_item_of_a_map_run(server, page):  # noqa: F811
    page.goto(server.url)
    page.wait_for_selector("#a2[data-view=fleet]")
    spawn(page, "/mcp workflow_arm " + json.dumps({"definition": TEAMS, "args": {}}))
    card = ".wfcard[data-status=armed]"
    page.wait_for_selector(f"{card} .wfrow.each[data-node=img]")
    page.wait_for_selector(
        f"{card} .wfrow[data-node=images] >> text=for each s · 1 at a time"
    )
    page.wait_for_selector(
        f"{card} .wfrow[data-node=mock] .after >> text=after images.base"
    )
    page.click(f"{card} button >> text=Approve and run")
    done = ".wfcard[data-status=done]"
    page.wait_for_selector(done, timeout=30000)
    page.wait_for_selector(
        f'{done} .wfrow[data-node="images.svc.img"][data-state=done] >> text=Build svc.'
    )
    page.wait_for_selector(f"{done} .wfrow[data-node=images] >> text=2 done")
    page.wait_for_selector(f"{done} .wfrow[data-node=mock] .after.met")
    assert not page.errors


def test_the_map_card_fits_a_phone(server, browser):  # noqa: F811
    from .test_browser import new_page

    errors: list = []
    pg = new_page(browser, errors)
    pg.set_viewport_size({"width": 390, "height": 800})
    pg.goto(server.url)
    pg.wait_for_selector("#a2[data-view=fleet]")
    spawn(pg, "/mcp workflow_arm " + json.dumps({"definition": TEAMS, "args": {}}))
    pg.wait_for_selector(".wfcard .wfrow.each")
    assert pg.evaluate("document.documentElement.scrollWidth") <= 390 and not errors


GATED = """id: gated
title: Gated
say: Ask before shipping.
root:
  seq:
    - {sh: "printf 41", id: a, say: Count.}
    - {gate: {by: person, ask: "Ship it with {{a.out}}?", show: [a.out]}, id: g, say: Ask the person.}
    - {sh: "true", id: ship, say: Ship.}
  say: Three.
"""


def send(pg, text: str) -> None:
    """Type a prompt into the open session and send it."""
    pg.fill("#input", text)
    pg.press("#input", "Enter")


def test_a_person_gate_card_asks_and_continue_finishes_the_run(server, page):  # noqa: F811
    page.goto(server.url)
    page.wait_for_selector("#a2[data-view=fleet]")
    spawn(page, "/mcp workflow_arm " + json.dumps({"definition": GATED, "args": {}}))
    page.click(".wfcard[data-status=armed] button >> text=Approve and run")
    card = ".wfdcard[data-what=gate]"
    page.wait_for_selector(f"{card} >> text=Ship it with 41?")
    page.wait_for_selector(f'{card} pre >> text=a.out: "41"')
    page.wait_for_selector(".wfcard .wfopen >> text=waits on you: Ask the person.")
    page.fill(f"{card} textarea", "go")
    page.click(f"{card} button >> text=Continue")
    page.wait_for_selector(".wfcard[data-status=done]", timeout=20000)
    page.wait_for_selector(f"{card} >> text=Continued by you: go")
    assert not page.errors


def test_auto_hands_the_gate_to_the_main_agent(server, page):  # noqa: F811
    page.goto(server.url)
    page.wait_for_selector("#a2[data-view=fleet]")
    spawn(page, "/mcp workflow_arm " + json.dumps({"definition": GATED, "args": {}}))
    page.click(".wfcard[data-status=armed] button >> text=Approve and run")
    card = ".wfdcard[data-what=gate]"
    page.wait_for_selector(f"{card} >> text=Ship it with 41?")
    page.click(f"{card} button >> text=Auto")
    page.wait_for_selector(f"{card} >> text=Handed to the main agent")
    assert page.locator(f"{card} button >> text=Continue").count() == 0
    page.wait_for_selector(".wfcard .wfopen >> text=waits on the main agent: 1 gate")
    run = page.eval_on_selector(".wfcard", "c => c.dataset.run")
    send(
        page,
        "/mcp workflow_answer "
        + json.dumps({"run": run, "to": "g", "choice": "continue"}),
    )
    page.wait_for_selector(".wfcard[data-status=done]", timeout=20000)
    page.wait_for_selector(f"{card} >> text=Continued by the main agent")
    assert not page.errors


def test_an_edit_card_shows_the_diff_and_apply_changes_the_plan(server, page):  # noqa: F811
    slow = GATED.replace(
        '{gate: {by: person, ask: "Ship it with {{a.out}}?", show: [a.out]}, id: g, say: Ask the person.}',
        '{sh: "sleep 3", id: g, say: Wait.}',
    )
    noted = slow.replace(
        '    - {sh: "true", id: ship',
        '    - {sh: "touch notes", id: notes, say: Write notes.}\n    - {sh: "true", id: ship',
    )
    page.goto(server.url)
    page.wait_for_selector("#a2[data-view=fleet]")
    spawn(page, "/mcp workflow_arm " + json.dumps({"definition": slow, "args": {}}))
    page.click(".wfcard[data-status=armed] button >> text=Approve and run")
    page.wait_for_selector(".wfcard[data-status=running]")
    run = page.eval_on_selector(".wfcard", "c => c.dataset.run")
    send(page, "/mcp workflow_edit " + json.dumps({"run": run, "definition": noted}))
    card = ".wfdcard[data-what=edit]"
    page.wait_for_selector(f"{card} .d.add >> text=Write notes.")
    page.wait_for_selector(f"{card} >> text=Adds 1 shell command")
    page.wait_for_selector(".wfcard .wfrow[data-node=ship] .held")
    page.click(f"{card} button >> text=Apply change")
    page.wait_for_selector(
        ".wfcard[data-status=done] .wfrow[data-node=notes][data-state=done]",
        timeout=20000,
    )
    page.wait_for_selector(f"{card} >> text=applied")
    assert not page.errors


def test_the_gate_card_fits_a_phone(server, browser):  # noqa: F811
    from .test_browser import new_page

    errors: list = []
    pg = new_page(browser, errors)
    pg.set_viewport_size({"width": 390, "height": 800})
    pg.goto(server.url)
    pg.wait_for_selector("#a2[data-view=fleet]")
    spawn(pg, "/mcp workflow_arm " + json.dumps({"definition": GATED, "args": {}}))
    pg.click(".wfcard[data-status=armed] button >> text=Approve and run")
    pg.wait_for_selector(".wfdcard[data-what=gate] textarea")
    assert pg.evaluate("document.documentElement.scrollWidth") <= 390 and not errors


def test_arming_again_with_a_new_definition_shows_its_diff(server, page):  # noqa: F811
    page.goto(server.url)
    page.wait_for_selector("#a2[data-view=fleet]")
    spawn(page, "/mcp workflow_arm " + json.dumps({"definition": GATED, "args": {}}))
    page.click(".wfcard[data-status=armed] button >> text=Approve and run")
    gate = ".wfdcard[data-what=gate]"
    page.click(f"{gate} button >> text=Stop")
    page.wait_for_selector(".wfcard[data-status=stopped]")
    run = page.eval_on_selector(".wfcard", "c => c.dataset.run")
    changed = GATED.replace("by: person", "by: main").replace(
        '{sh: "true", id: ship', '{sh: "curl -X POST deploy", id: ship'
    )
    send(page, "/mcp workflow_arm " + json.dumps({"run": run, "definition": changed}))
    card = ".wfdcard[data-what=again]"
    page.wait_for_selector(f"{card} >> text=Run again")
    page.wait_for_selector(f"{card} .d.change >> text=Ask the person.")
    page.wait_for_selector(f"{card} .d.change .f >> text=who decides: main")
    page.wait_for_selector(f"{card} pre.cmd >> text=$ curl -X POST deploy")
    page.wait_for_selector(f"{card} >> text=Adds 1 shell command")
    page.wait_for_selector(f"{card} button >> text=Approve and run")
    # The run's own card approves too, so it shows the same diff above its
    # button: approving there must not skip what changed.
    runcard = ".wfcard[data-status=armed]"
    page.wait_for_selector(f"{runcard} .edit .d.change .f >> text=who decides: main")
    page.wait_for_selector(f"{runcard} .edit pre.cmd >> text=$ curl -X POST deploy")
    assert not page.errors


# Slice 5: the card is a canvas item like a page or a file. These drive it
# through the selectors the artifact tests use (test_canvas_browser.py,
# test_viewer_browser.py).


def arm_one(pg, extra: dict | None = None) -> str:
    """Spawn a session whose first turn arms the two-step flow; its run id."""
    arm = {"definition": flow(RESULT, RESULT), "args": {}, **(extra or {})}
    spawn(pg, "/mcp workflow_arm " + json.dumps(arm))
    pg.wait_for_selector("#entries .wfcard")
    return pg.eval_on_selector("#entries .wfcard", "c => c.dataset.run")


def test_open_shows_the_card_full_screen_until_closed(server, page):  # noqa: F811
    page.goto(server.url)
    page.wait_for_selector("#a2[data-view=fleet]")
    run = arm_one(page)
    page.click("#entries .wfcard .fbar .view")
    page.wait_for_selector("#viewer .vbar >> text=Two steps")
    page.wait_for_selector(
        f'#viewer .wfcard[data-run="{run}"] >> text=Do the first thing.'
    )
    # Approving from the viewer runs it; the viewer's card follows.
    page.click("#viewer .wfcard button >> text=Approve and run")
    page.wait_for_selector("#viewer .wfcard[data-status=done]", timeout=20000)
    page.keyboard.press("Escape")
    assert page.locator("#viewer").is_hidden()
    assert not page.errors


def test_dock_from_the_viewer_then_open_outlines_the_pane(server, page):  # noqa: F811
    page.set_viewport_size({"width": 1300, "height": 900})
    page.goto(server.url)
    page.wait_for_selector("#a2[data-view=fleet]")
    run = arm_one(page)
    page.click("#entries .wfcard .fbar .view")
    page.click("#viewer .vbar .dock")
    page.wait_for_selector(f'#canvas .pane[data-pane="wf-{run}"] .wfcard')
    assert page.locator("#viewer").is_hidden()
    # Open on a docked card outlines its pane, as for a page, and so does Show.
    page.click("#entries .wfcard .fbar .view")
    page.wait_for_selector(f'#canvas .pane[data-pane="wf-{run}"].flash')
    assert page.locator("#viewer").is_hidden()
    page.wait_for_selector(f'#canvas .pane[data-pane="wf-{run}"]:not(.flash)')
    page.click("#entries .wfcard .held .locate")
    page.wait_for_selector(f'#canvas .pane[data-pane="wf-{run}"].flash')
    assert not page.errors


def test_an_agents_dock_draws_the_card_beside_the_transcript(server, page):  # noqa: F811
    page.set_viewport_size({"width": 1300, "height": 900})
    page.goto(server.url)
    page.wait_for_selector("#a2[data-view=fleet]")
    run = arm_one(page, {"dock": "right"})
    pane = page.locator(f'#canvas .pane[data-pane="wf-{run}"]')
    pane.locator(".wfcard >> text=Do the first thing.").wait_for()
    tr, box = page.locator("#tr-pane").bounding_box(), pane.bounding_box()
    assert box["x"] >= tr["x"] + tr["width"] - 1  # to its right
    assert page.locator("#entries .wfcard .held").inner_text() == "docked · show"
    assert not page.errors


def test_a_workflow_moves_to_a_second_window_and_runs_live_there(server, page):  # noqa: F811
    from .test_canvas_browser import _watch

    page.set_viewport_size({"width": 1300, "height": 900})
    page.goto(server.url)
    page.wait_for_selector("#a2[data-view=fleet]")
    run = arm_one(page, {"dock": "right"})
    pane = f'#canvas .pane[data-pane="wf-{run}"]'
    page.click(f"{pane} .pbar .move")
    with page.context.expect_page() as opened:
        page.locator(".cmenu [role=menuitem]", has_text="New window").click()
    two = _watch(opened.value)
    two.locator(f"{pane} .wfcard >> text=Do the first thing.").wait_for()
    page.locator("#entries .wfcard .held", has_text="in window 2").wait_for()
    assert page.locator("#entries .wfcard .held").inner_text() == "in window 2 · open"
    # The window's card approves and follows the run like window 1's.
    two.click(f"{pane} .wfcard button >> text=Approve and run")
    two.wait_for_selector(f"{pane} .wfcard[data-status=done]", timeout=20000)
    assert page.errors == [] and two.errors == []


def test_the_phone_canvas_shows_the_workflow_as_a_tab(server, browser):  # noqa: F811
    from .test_browser import new_page

    errors: list = []
    pg = new_page(browser, errors)
    pg.set_viewport_size({"width": 390, "height": 800})
    pg.goto(server.url)
    pg.wait_for_selector("#a2[data-view=fleet]")
    run = arm_one(pg, {"dock": "right"})
    tab = pg.locator(f'#canvas [role=tab][data-pane="wf-{run}"]')
    tab.wait_for()
    assert tab.inner_text() == "Two steps"
    tab.click()
    pg.wait_for_selector(f'#canvas .pane[data-pane="wf-{run}"] .wfcard')
    pg.click(f'#canvas .pane[data-pane="wf-{run}"] .wfcard .wfrow[data-node=one] .tog')
    assert pg.evaluate("document.documentElement.scrollWidth") <= 390 and not errors


def test_the_header_counts_steps_and_now_names_whats_working(server, page):  # noqa: F811
    page.goto(server.url)
    page.wait_for_selector("#a2[data-view=fleet]")
    arm = {"definition": flow(report(7, 6), RESULT), "args": {}}
    spawn(page, "/mcp workflow_arm " + json.dumps(arm))
    card = "#entries .wfcard"
    page.wait_for_selector(f"{card} .wfhead >> text=2 steps")
    assert page.locator(f"{card} .wfhead .bar").count() == 0
    page.click(f"{card} button >> text=Approve and run")
    page.wait_for_selector(f"{card} .wfhead >> text=0 of 2 done")
    now = f"{card} .wfnow"
    page.wait_for_selector(f"{now} .nowline[data-node=one] >> text=Do the first thing.")
    page.wait_for_selector(f"{now} .nowline.next >> text=Do the second thing.")
    # The Now line's time counts up with the row's, without a redraw.
    first = page.inner_text(f"{now} .nowline[data-node=one] .dur")
    page.wait_for_function(
        "f => document.querySelector('#entries .wfcard .wfnow .nowline[data-node=one] .dur').textContent !== f",
        arg=first,
    )
    page.wait_for_selector(
        f"{card}[data-status=done] .wfhead >> text=2 of 2 done", timeout=20000
    )
    assert page.locator(now).count() == 0
    assert (
        page.locator(f"{card} .wfhead .bar i").get_attribute("style").endswith("100%;")
    )
    assert not page.errors


FAILS = """id: fails
title: Fails
say: A branch not taken, then a command that fails.
root:
  seq:
    - {if: {when: {expr: "false"}, then: {sh: "true", id: never, say: Never.}}, id: maybe, say: Maybe.}
    - {sh: "true", id: fine, say: Fine.}
    - {sh: "false", id: bad, say: Fail.}
  say: All of it.
"""


def test_the_header_counts_a_failure_but_not_a_branch_not_taken(server, page):  # noqa: F811
    page.goto(server.url)
    page.wait_for_selector("#a2[data-view=fleet]")
    spawn(page, "/mcp workflow_arm " + json.dumps({"definition": FAILS, "args": {}}))
    card = "#entries .wfcard"
    page.click(f"{card} button >> text=Approve and run")
    page.wait_for_selector(
        f"{card} .wfrow[data-node=bad][data-state=failed]", timeout=20000
    )
    # The run waits on the main agent; never is skipped and not counted.
    page.wait_for_selector(
        f"{card}[data-status=running] .wfhead >> text=1 of 2 done · 1 failed"
    )
    assert (
        page.locator(f"{card} .wfhead .bar i").get_attribute("style").endswith("50%;")
    )
    assert not page.errors


EARLY = """id: early
title: Early
say: Two steps finish early, then a wait outlives their deadlines.
root:
  seq:
    - {sh: "true", id: r0, deadline: 2s, say: R0.}
    - {seq: [{sh: "true", id: a, say: A.}], id: plan, deadline: 2s, say: Plan.}
    - {wait: 4s, id: hold, say: Hold.}
  say: All of it.
"""


def test_finished_steps_stay_done_past_their_deadlines(server, page):  # noqa: F811
    page.goto(server.url)
    page.wait_for_selector("#a2[data-view=fleet]")
    spawn(page, "/mcp workflow_arm " + json.dumps({"definition": EARLY, "args": {}}))
    card = "#entries .wfcard"
    page.click(f"{card} button >> text=Approve and run")
    page.wait_for_selector(f"{card}[data-status=done]", timeout=20000)
    for node in ("r0", "plan", "a", "hold"):
        assert page.locator(f"{card} .wfrow[data-node={node}][data-state=done]").count()
    assert page.locator(f"{card} .wfrow[data-state=failed]").count() == 0
    assert not page.errors


def test_the_header_counts_an_open_question(server, page):  # noqa: F811
    from .test_workflows_parallel import script

    page.goto(server.url)
    page.wait_for_selector("#a2[data-view=fleet]")
    asks = script(["workflow_ask", {"question": "Which port?"}], ["sleep", 8])
    arm = {"definition": flow(asks, RESULT), "args": {}}
    spawn(page, "/mcp workflow_arm " + json.dumps(arm))
    card = "#entries .wfcard"
    page.click(f"{card} button >> text=Approve and run")
    page.wait_for_selector(f"{card} .wfhead >> text=0 of 2 done · 1 question")
    assert not page.errors


EIGHT = """id: eight
title: Eight
say: Eight commands, one of them failing.
root:
  map: {over: "{{args.xs}}", as: x, cap: 8, do: {sh: "test {{x}} != c", id: t, say: "Check {{x}}."}}
  id: m
  say: All eight.
args: {type: object, properties: {xs: {type: array}}, required: [xs]}
"""


def items_shown(pg, card: str, under: str) -> int:
    return pg.locator(f"{card} .wfrow[data-node^='{under}']").count()


def test_a_map_of_more_than_six_items_folds_but_shows_its_failure(server, page):  # noqa: F811
    page.goto(server.url)
    page.wait_for_selector("#a2[data-view=fleet]")
    arm = {"definition": EIGHT, "args": {"xs": list("abcdefgh")}}
    spawn(page, "/mcp workflow_arm " + json.dumps(arm))
    card = "#entries .wfcard"
    page.click(f"{card} button >> text=Approve and run")
    page.wait_for_selector(
        f'{card} .wfrow[data-node="m.2.t"][data-state=failed]', timeout=20000
    )
    page.wait_for_selector(f"{card} .wfrow[data-node=m] >> text=7 done")
    # Folded: the failed item shows, the seven done ones do not.
    assert items_shown(page, card, "m.") == 1
    page.click(f"{card} .wfrow[data-node=m] .fold >> text=show all 8")
    assert items_shown(page, card, "m.") == 8
    page.click(f"{card} .wfrow[data-node=m] .fold >> text=fold")
    assert items_shown(page, card, "m.") == 1
    assert not page.errors


def test_a_folded_maps_story_lines_fold_with_it(server, page):  # noqa: F811
    page.goto(server.url)
    page.wait_for_selector("#a2[data-view=fleet]")
    arm = {"definition": EIGHT, "args": {"xs": list("abcdefgh")}}
    spawn(page, "/mcp workflow_arm " + json.dumps(arm))
    card = "#entries .wfcard"
    page.click(f"{card} button >> text=Approve and run")
    page.wait_for_selector(f"{card} .wfrow[data-node=m] >> text=7 done", timeout=20000)
    folded = page.locator(f"{card} .story .line.folded")
    assert folded.count() == 1 and "lines folded" in folded.inner_text()
    # The failed item's line stays in the story.
    assert page.locator(f"{card} .story .line[data-mark=bad]").count() >= 1
    page.click(f"{card} .wfrow[data-node=m] .fold >> text=show all 8")
    assert folded.count() == 0
    page.set_viewport_size({"width": 390, "height": 800})
    assert page.evaluate("document.documentElement.scrollWidth") <= 390
    assert not page.errors


def test_a_map_of_six_items_stays_open(server, page):  # noqa: F811
    page.goto(server.url)
    page.wait_for_selector("#a2[data-view=fleet]")
    arm = {"definition": EIGHT, "args": {"xs": list("abdefg")}}
    spawn(page, "/mcp workflow_arm " + json.dumps(arm))
    card = "#entries .wfcard"
    page.click(f"{card} button >> text=Approve and run")
    page.wait_for_selector(f"{card}[data-status=done]", timeout=20000)
    assert items_shown(page, card, "m.") == 6
    assert page.locator(f"{card} .wfrow[data-node=m] .fold").count() == 0
    assert not page.errors


ROUNDS = """id: rounds
title: Rounds
say: Eight rounds of one command.
root:
  loop: {max: 8, do: {sh: "true", id: t, say: Try.}}
  id: l
  say: Rounds.
"""


def test_a_loop_past_six_rounds_shows_only_its_current_round(server, page):  # noqa: F811
    page.goto(server.url)
    page.wait_for_selector("#a2[data-view=fleet]")
    spawn(page, "/mcp workflow_arm " + json.dumps({"definition": ROUNDS, "args": {}}))
    card = "#entries .wfcard"
    page.click(f"{card} button >> text=Approve and run")
    page.wait_for_selector(
        f"{card} .wfrow[data-node=l] >> text=round 8 of 8", timeout=20000
    )
    page.wait_for_selector(f"{card}[data-status=done]", timeout=20000)
    assert items_shown(page, card, "l.") == 1
    assert page.locator(f"{card} .wfrow[data-node='l.8.t']").count() == 1
    page.click(f"{card} .wfrow[data-node=l] .fold >> text=7 earlier rounds")
    assert items_shown(page, card, "l.") == 8
    assert not page.errors


def test_a_working_steps_card_menu_shows_its_actions_disabled(server, page):  # noqa: F811
    page.goto(server.url)
    page.wait_for_selector("#a2[data-view=fleet]")
    arm = {"definition": flow(report(7, 8), RESULT), "args": {}}
    spawn(page, "/mcp workflow_arm " + json.dumps(arm))
    card = "#entries .wfcard"
    page.click(f"{card} button >> text=Approve and run")
    page.locator(f"{card} .wfrow[data-node=one] a.who").wait_for()
    page.goto(server.url + "#fleet")
    step = page.locator(".card.quiet", has=page.locator(".badge", has_text="· one"))
    step.wait_for()
    step.click(button="right")
    page.wait_for_selector(".cmenu")
    why = "is a step of a running workflow"
    for item in ("sleep", "restart", "archive"):
        b = page.locator(f".cmenu [data-id={item}]")
        assert b.is_disabled() and why in b.get_attribute("title"), item
    assert page.locator(".cmenu [data-id=open]").is_enabled()
    assert not page.errors


def test_a_working_step_opens_read_only_from_the_card_with_no_tab(server, page):  # noqa: F811
    page.goto(server.url)
    page.wait_for_selector("#a2[data-view=fleet]")
    arm = {"definition": flow(report(7, 8), RESULT), "args": {}}
    spawn(page, "/mcp workflow_arm " + json.dumps(arm))
    card = "#entries .wfcard"
    page.click(f"{card} button >> text=Approve and run")
    link = page.locator(f"{card} .wfrow[data-node=one] a.who")
    link.wait_for()
    page.wait_for_selector(f"{card} .wfnow a.who")  # Now links it too
    tabs = page.locator("#tablist .tab").count()
    link.click()
    page.wait_for_function(
        "() => document.querySelector('#input').placeholder.includes('Step one of the workflow Two steps')"
    )
    assert page.locator("#tablist .tab").count() == tabs  # no tab added
    assert page.is_disabled("#input") and page.is_disabled("#send")
    # It closes and stops with its run: the server refuses both, too.
    assert page.is_disabled("#close") and page.is_disabled("#stop-session")
    # Back in the Fleet, its card names the run, and a click keeps it quiet.
    page.goto(server.url + "#fleet")
    fleet = page.locator(".card.quiet .badge", has_text="workflow · Two steps · one")
    fleet.wait_for()
    fleet.click()
    page.wait_for_selector("#a2[data-view=session]")
    page.wait_for_timeout(300)  # an unquiet would add the tab by now
    assert page.locator("#tablist .tab").count() == tabs
    assert page.is_disabled("#input")
    assert not page.errors


def test_a_long_workflow_title_stays_inside_its_steps_card(server, page):  # noqa: F811
    # #670: the badge ran past the card and over its neighbour.
    long = "A workflow title far too long to fit on one fleet card at any width"
    definition = flow(report(7, 8), RESULT).replace(
        "title: Two steps", f"title: {long}"
    )
    page.goto(server.url)
    page.wait_for_selector("#a2[data-view=fleet]")
    spawn(
        page, "/mcp workflow_arm " + json.dumps({"definition": definition, "args": {}})
    )
    page.click("#entries .wfcard button >> text=Approve and run")
    page.locator("#entries .wfcard .wfrow[data-node=one] a.who").wait_for()
    page.goto(server.url + "#fleet")
    card = page.locator(".card", has=page.locator(".badge", has_text="workflow ·"))
    badge = card.locator(".badge", has_text="workflow ·")
    badge.wait_for()
    b, c = badge.bounding_box(), card.bounding_box()
    assert b["x"] + b["width"] <= c["x"] + c["width"], (b, c)
    assert badge.get_attribute("title") == f"workflow · {long} · one"
    assert not page.errors


def test_a_steps_see_chip_opens_its_part_of_the_plan(server, page, tmp_path):  # noqa: F811
    from .test_workflows_canvas import SEEN

    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "plan.html").write_text("<h2 id=slice-1>One</h2>")
    page.goto(server.url)
    page.wait_for_selector("#a2[data-view=fleet]")
    spawn(page, "/mcp workflow_arm " + json.dumps({"definition": SEEN, "args": {}}))
    chip = ".wfcard .wfrow[data-node=one] .see"
    page.wait_for_selector(f"{chip} >> text=see docs/plan.html#slice-1")
    page.click(chip)
    page.wait_for_selector('#viewer iframe[src$="#slice-1"]')
    assert not page.errors


def test_a_linked_servers_see_chip_opens_its_file_through_via(  # noqa: F811
    linked,  # noqa: F811
    browser,  # noqa: F811
    page,  # noqa: F811
):
    from .test_workflows_canvas import SEEN

    (linked.beta.root / "docs").mkdir()
    (linked.beta.root / "docs" / "plan.html").write_text("<h2 id=slice-1>One</h2>")
    arm = {"definition": SEEN, "args": {}}
    lid = beta_session(linked, browser, "/mcp workflow_arm " + json.dumps(arm))
    page.goto(linked.alpha.url)
    page.click(f"#tablist .tab[data-id='beta/{lid}']")
    chip = ".wfcard .wfrow[data-node=one] .see"
    page.wait_for_selector(f"{chip} >> text=see docs/plan.html#slice-1")
    page.click(chip)
    page.wait_for_selector('#viewer iframe[src^="/via/beta/files/"][src$="#slice-1"]')
    assert not page.errors


def test_the_workflow_menu_lists_saved_ones_and_loop(server, page):  # noqa: F811
    from .test_workflow_store import NAP

    page.goto(server.url)
    page.wait_for_selector("#a2[data-view=fleet]")
    spawn(page, "/mcp workflow_save " + json.dumps({"definition": NAP}))
    page.click("#input")
    page.keyboard.type("/workflow ")
    page.wait_for_selector("#cmd-menu .cmd-row >> text=Nap")
    page.wait_for_selector(
        "#cmd-menu .cmd-row >> text=Repeat a prompt until it says done"
    )
    page.wait_for_selector("#cmd-menu .cmd-row .src >> text=built in")
    page.keyboard.press("Escape")
    # /workflow alone opens the same menu; nothing reaches the agent.
    send(page, "/workflow")
    page.wait_for_selector("#cmd-menu:not([hidden]) .cmd-row >> text=Nap")
    assert page.input_value("#input") == "/workflow "
    page.keyboard.press("Escape")
    page.fill("#input", "")
    # So does the palette's "Run a saved workflow".
    page.keyboard.press("Control+k")
    page.wait_for_selector("#palette", state="visible")
    page.keyboard.type("run a saved workflow")
    page.keyboard.press("Enter")
    page.wait_for_selector("#cmd-menu:not([hidden]) .cmd-row >> text=Nap")
    assert page.input_value("#input") == "/workflow "
    assert not page.errors


def launch_token(pg) -> str:
    """The token of the launch turn the session's agent was handed."""
    pg.wait_for_function(
        "document.querySelector('#entries').textContent.includes('token=\"')"
    )
    text = pg.evaluate("document.querySelector('#entries').textContent")
    return re.search(r'token="([^"]+)"', text).group(1)


def test_a_launched_card_says_who_started_it_and_has_no_approve(server, page):  # noqa: F811
    page.goto(server.url)
    page.wait_for_selector("#a2[data-view=fleet]")
    spawn(page, "hello")
    send(page, "/loop check the deploy every 1h")
    tok = launch_token(page)
    args = {"prompt": "check the deploy", "every": "1h"}
    send(
        page,
        "/mcp workflow_arm " + json.dumps({"id": "loop", "token": tok, "args": args}),
    )
    card = ".wfcard[data-status=running]"
    page.wait_for_selector(
        f"{card} .wfwho >> text=Started by your /loop line: check the deploy every 1h"
    )
    kv = page.inner_text(f"{card} .wfargs")
    assert "every" in kv and "1h" in kv and "max" in kv and "20" in kv
    page.wait_for_selector(f"{card} .wfrow >> text=round 1 of 20")
    assert page.locator(f"{card} button >> text=Approve and run").count() == 0
    page.set_viewport_size({"width": 390, "height": 800})
    page.wait_for_selector(f"{card} .wfargs")
    assert page.evaluate("document.documentElement.scrollWidth") <= 390
    assert not page.errors


def test_approving_tells_the_main_agent_and_its_card_waits(server, page):  # noqa: F811
    """#474: the click wakes the main agent with where the run next stops,
    and after that turn its card waits on the run."""
    page.goto(server.url)
    page.wait_for_selector("#a2[data-view=fleet]")
    gate = '{gate: {by: person, ask: "Publish it?"}, id: pub, say: Ask first.}'
    arm = {"definition": steps("{wait: 30s, id: nap, say: Pause.}", gate), "args": {}}
    main = spawn(page, "/mcp workflow_arm " + json.dumps(arm))
    page.click(".wfcard[data-status=armed] button >> text=Approve and run")
    page.wait_for_selector(
        'text=It next stops at the gate "Publish it?"', timeout=10000
    )
    page.click("#tab-fleet")
    page.wait_for_selector(
        f"#cards .card[data-id='{main}'] .s >> text=waiting · workflow", timeout=10000
    )
    assert not page.errors


PROGRESS_FLOW = """id: progress
title: Progress
say: Wait for a flag, measuring how far along it is.
root:
  seq:
    - {check: {sh: "test -f flag", every: 5s}, progress: "echo 40", id: ci, say: Wait for the flag.}
  say: All of it.
"""


def test_the_card_draws_a_steps_progress_command_and_its_bar(server, page, tmp_path):  # noqa: F811
    page.goto(server.url)
    page.wait_for_selector("#a2[data-view=fleet]")
    spawn(
        page,
        "/mcp workflow_arm " + json.dumps({"definition": PROGRESS_FLOW, "args": {}}),
    )
    card = ".wfcard[data-status=armed]"
    page.wait_for_selector(
        f"{card} .wfrow[data-node=ci] pre.progress >> text=progress $ echo 40"
    )
    page.click(f"{card} button >> text=Approve and run")
    now = ".wfcard[data-status=running] .wfnow .nowline[data-node=ci]"
    page.wait_for_selector(f"{now} .pct >> text=40%", timeout=15000)
    assert page.eval_on_selector(f"{now} .bar i", "i => i.style.width") == "40%"
    page.wait_for_selector(".wfcard .wfrow[data-node=ci] .out >> text=40%")
    (tmp_path / "flag").write_text("")
    page.wait_for_selector(".wfcard[data-status=done]", timeout=20000)
    page.wait_for_selector(".wfcard .wfrow[data-node=ci] .out >> text=100%")
    assert not page.errors


def test_inputs_fold_and_the_header_shows_time_and_cost(server, page):  # noqa: F811
    # #712: a long input folds to its name and a preview, a list opens as
    # coloured JSON, and the header says how long the run took and what its
    # steps cost.
    page.goto(server.url)
    page.wait_for_selector("#a2[data-view=fleet]")
    definition = flow(report(7, 0), RESULT) + (
        "args: {type: object, properties: {xs: {type: array}, rules: {type: string},"
        " n: {type: integer}}}\n"
    )
    args = {
        "xs": [{"issue": 111, "bench": "frame"}, {"issue": 112, "bench": "ecs"}],
        "rules": "How to work: " + "read the issue, then fix it. " * 10,
        "n": 3,
    }
    spawn(
        page,
        "/mcp workflow_arm " + json.dumps({"definition": definition, "args": args}),
    )
    card = "#entries .wfcard"
    page.click(f"{card} button >> text=Approve and run")
    page.wait_for_selector(
        f"{card}[data-status=done] .wfhead >> text=2 of 2 done", timeout=20000
    )
    eta = page.inner_text(f"{card} .wfhead .eta")
    assert re.search(r"\d+m\d\d elapsed · \$\d+\.\d+ spent", eta), eta
    # Folded by default: one line each, the short one inline.
    xs = f"{card} details.wfarg:has(.k:text-is('xs'))"
    assert page.inner_text(f"{xs} > summary .v") == "2 items"
    assert not page.locator(xs).evaluate("d => d.open")
    assert page.locator(f"{card} details.wfarg:has(.k:text-is('rules'))").count() == 1
    assert page.inner_text(f"{card} .wfarg.inline:has(.k:text-is('n')) .v") == "3"
    page.click(f"{xs} > summary")
    page.wait_for_selector(f"{xs} .fence[data-lang=json] code.hljs")
    assert '"issue": 111' in page.inner_text(f"{xs} .fence pre")
    page.set_viewport_size({"width": 390, "height": 800})
    assert page.evaluate("document.documentElement.scrollWidth") <= 390
    assert not page.errors
