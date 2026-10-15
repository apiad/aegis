"""The workflow card against a real aegis serve: the plan before approval,
the prompt folded under each node's sentence, and the run to its end."""

import json

import pytest

from .test_browser import CONFIG, Server, browser, page, spawn  # noqa: F401
from .test_workflows_e2e import RESULT, flow
from .test_workflows_parallel import leaf, report

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
    page.wait_for_selector(f"{card} .wfrow[data-node=ok] .tags >> text=⏱ 2h")
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


def test_a_stopped_run_shows_its_running_node_and_seq_stopped(server, page):  # noqa: F811
    page.goto(server.url)
    page.wait_for_selector("#a2[data-view=fleet]")
    arm = {"definition": flow("/sleep 10", RESULT), "args": {}}
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
