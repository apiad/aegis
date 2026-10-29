"""The Schedules view against a real aegis serve: the button and its count, a
schedule's layers and runs with the quiet ticks folded, and the editor."""

import json
import time
from datetime import datetime
from pathlib import Path

import pytest

from .test_browser import (  # noqa: F401
    CONFIG,
    Server,
    browser,
    new_page,
    page,
    spawn,
)

pytestmark = [pytest.mark.browser, pytest.mark.slow]

RUN = "20261014-000000-abcdef"  # a run whose session is gone: it reads as archived


def iso(t: float) -> str:
    return datetime.fromtimestamp(t).isoformat(timespec="seconds")


def seed(root: Path) -> None:
    """Two schedules and a run log, written before the server boots: pr-triage
    with one fire and three quiet ticks, main-red paused by errors. The ticks
    end a minute ago, so nothing fires while the test runs."""
    state = root / ".aegis" / "state"
    state.mkdir(parents=True, exist_ok=True)
    now = int(time.time())
    anchor = now - 960  # ticks at now-660, now-360, now-60; the next in 4 minutes
    owner = {"kind": "user", "principal": "ana"}
    fire = {
        "target": "new",
        "agent": "opus",
        "cwd": str(root),
        "close": "done",
        "spawn": False,
    }
    defs = {
        "pr-triage": {
            "owner": owner,
            "created_at": anchor,
            "edited_at": anchor,
            "paused": None,
            "clock": {"every": "5m"},
            "overlap": "wait",
            "check": {"bash": "echo hi", "timeout_s": 30},
            "fire": fire,
            "prompt": "Triage the new pull requests.",
        },
        "main-red": {
            "owner": owner,
            "created_at": anchor,
            "edited_at": anchor,
            "paused": {"by": "errors", "at": iso(now - 60)},
            "clock": {"every": "10m"},
            "overlap": "wait",
            "check": {"bash": "exit 4", "timeout_s": 30},
            "fire": fire,
            "prompt": "main went red.",
        },
    }
    (state / "schedules.json").write_text(json.dumps(defs))
    t1, t2, t3 = anchor + 300, anchor + 600, anchor + 900
    recs = [
        {"t": iso(anchor), "s": "pr-triage", "ev": "created", "tick": anchor},
        {
            "t": iso(t1),
            "s": "pr-triage",
            "ev": "tick",
            "tick": t1,
            "out": "fired",
            "sha": "a",
        },
        {
            "t": iso(t1),
            "s": "pr-triage",
            "ev": "fired",
            "run": 1,
            "log_id": RUN,
            "target": "new",
            "tick": t1,
            "stdout": "#241 Schedules view\n",
        },
        {
            "t": iso(t1 + 58),
            "s": "pr-triage",
            "ev": "finished",
            "run": 1,
            "attention": "done",
            "closed": True,
            "secs": 58,
            "cost_usd": 0.21,
        },
        {
            "t": iso(t2),
            "s": "pr-triage",
            "ev": "tick",
            "tick": t2,
            "out": "quiet",
            "rc": 0,
        },
        {
            "t": iso(t3),
            "s": "pr-triage",
            "ev": "tick",
            "tick": t3,
            "out": "quiet",
            "rc": 1,
        },
        {"t": iso(anchor), "s": "main-red", "ev": "created", "tick": anchor},
        {"t": iso(now - 60), "s": "main-red", "ev": "paused", "by": "errors"},
    ]
    (state / "schedule-runs.jsonl").write_text(
        "".join(json.dumps(r) + "\n" for r in recs)
    )


@pytest.fixture
def server(tmp_path: Path, fake_claude: str, fake_opencode: str):
    (tmp_path / ".aegis.yaml").write_text(CONFIG)
    seed(tmp_path)
    s = Server(tmp_path, fake_claude, fake_opencode).start()
    yield s
    s.stop()


def open_view(page, server) -> None:  # noqa: F811
    page.goto(server.url)
    page.wait_for_selector("#a2[data-view=fleet]")
    page.click("#schedules-btn")
    page.wait_for_selector("#a2[data-view=schedules] .sch-row")


def test_the_button_counts_schedules_and_is_red_while_one_is_paused_by_errors(
    server,
    page,  # noqa: F811
):
    page.goto(server.url)
    page.wait_for_selector("#schedules-n.err >> text=2")
    open_view(page, server)
    page.click(".sch-row[data-name=main-red]")
    page.wait_for_selector(".sch-dhead h3 >> text=main-red")
    page.click(".sch-dhead button >> text=Resume")
    page.wait_for_selector("#schedules-n:not(.err)")
    assert page.text_content("#schedules-n") == "2" and not page.errors


def test_the_count_keeps_the_desktop_header_buttons_one_size_on_one_line(
    server,
    page,  # noqa: F811
):
    # #801: the count widened Schedules to 42 px beside 30 px buttons, and the
    # server's name and version sat 3 px below the icons.
    page.set_viewport_size({"width": 1440, "height": 900})
    page.goto(server.url)
    page.wait_for_selector("#schedules-n >> text=2")
    row = page.evaluate(
        """ids => Object.fromEntries(ids.map(id => {
          const b = document.getElementById(id), r = document.createRange();
          r.selectNodeContents(b);
          const q = (b.querySelector('svg, .ic') ?? r).getBoundingClientRect();
          const o = b.getBoundingClientRect();
          return [id, [q.y + q.height / 2, q.x + q.width / 2 - o.x - o.width / 2, o.width, o.height]];
        }))""",
        [
            "schedules-btn",
            "settings-btn",
            "files-btn",
            "journal-btn",
            "bell",
            "help-btn",
            "conn-text",
        ],
    )
    mids = [m for m, _, _, _ in row.values()]
    assert max(mids) - min(mids) <= 1, row
    buttons = {k: v for k, v in row.items() if k != "conn-text"}
    assert {(w, h) for _, _, w, h in buttons.values()} == {(30, 28)}, row
    assert all(abs(off) <= 1 for _, off, _, _ in buttons.values()), row
    assert not page.errors


def test_a_schedule_shows_its_layers_and_its_runs_with_quiet_ticks_folded(server, page):  # noqa: F811
    open_view(page, server)
    page.click(".sch-row[data-name=pr-triage]")
    page.wait_for_selector(".sch-dhead h3 >> text=pr-triage")
    layers = page.text_content(".sch-layers")
    assert "every 5m" in layers and "echo hi" in layers and "Triage the new" in layers
    page.wait_for_selector("table.runs tr.k-fired")
    kinds = page.eval_on_selector_all("table.runs tr", "rs => rs.map(r => r.className)")
    assert kinds == ["k-fired", "k-created"]
    fired = page.text_content("table.runs tr.k-fired")
    assert RUN in fired and "archived" in fired and "$0.21" in fired and "58s" in fired
    page.check(".sch-quiet input")
    page.wait_for_selector("table.runs tr.k-quiet >> text=no change ×2")
    assert not page.errors


def test_the_editor_checks_the_clock_tests_a_check_and_saves(server, page):  # noqa: F811
    open_view(page, server)
    page.click(".sch-new")
    page.wait_for_selector("#sch-save:disabled")
    page.fill("#sch-name", "nightly-docs")
    page.fill("#sch-clock", "5x")
    page.wait_for_selector("#sch-clock-hint.bad >> text=write a number")
    page.fill("#sch-clock", "1h")
    page.wait_for_selector("#sch-clock-hint >> text=next ticks")
    page.fill("#sch-prompt", "Check the docs build.")
    page.wait_for_selector("#sch-save:enabled")
    page.check("#sch-check")
    page.fill("#sch-bash", "echo hello")
    page.click(".sch-test")
    page.wait_for_selector("#sch-tested >> text=would fire")
    assert "hello" in page.text_content("#sch-tested")
    page.wait_for_selector("#sch-save:enabled")
    page.click("#sch-save")
    page.wait_for_selector(".sch-row[data-name=nightly-docs]")
    page.wait_for_selector(".sch-dhead h3 >> text=nightly-docs")
    assert page.text_content("#schedules-n") == "3" and not page.errors


def test_the_view_fits_a_phone(server, browser):  # noqa: F811
    errors: list = []
    pg = browser.new_page(viewport={"width": 390, "height": 844})
    pg.on("pageerror", lambda e: errors.append(str(e)))
    open_view(pg, server)
    pg.click(".sch-row[data-name=pr-triage]")
    pg.wait_for_selector("table.runs tr.k-fired")
    assert pg.evaluate("document.documentElement.scrollWidth") <= 390
    pg.click(".sch-new")
    pg.wait_for_selector("#sch-name")
    assert pg.evaluate("document.documentElement.scrollWidth") <= 390
    assert not errors


def stored(server, name: str) -> dict:
    """A schedule's definition as the server saved it."""
    path = server.root / ".aegis" / "state" / "schedules.json"
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        defs = json.loads(path.read_text())
        if name in defs:
            return defs[name]
        time.sleep(0.1)
    raise AssertionError(f"{name} was never saved")


def test_the_editor_sets_quota_lines_per_pool_or_none(server, page):  # noqa: F811
    open_view(page, server)
    page.click(".sch-new")
    page.fill("#sch-name", "quota-check")
    page.fill("#sch-clock", "1h")
    page.fill("#sch-prompt", "Check the quota.")
    week = ".sch-q[data-pool=claude][data-win=weekly_all]"
    five = ".sch-q[data-pool=claude][data-win=session]"
    page.wait_for_selector(f"{week} .seg button.on[data-v=pace]")
    page.click(f"{five} input[type=checkbox]")
    page.click(f"{five} .seg button[data-v=percent]")
    page.fill(f"{five} .sch-pct", "8x")
    page.wait_for_selector("#sch-save:disabled")
    page.fill(f"{five} .sch-pct", "80")
    page.wait_for_selector("#sch-save:enabled")
    page.click("#sch-save")
    page.wait_for_selector(".sch-dhead h3 >> text=quota-check")
    layers = page.text_content(".sch-layers")
    assert "claude week on pace" in layers and "claude 5 hours below 80%" in layers
    assert stored(server, "quota-check")["quota"] == {
        "claude": {"weekly_all": "pace", "session": 80},
        "opencode-go": {"weekly": "pace"},
    }
    page.click(".sch-dhead button >> text=Edit")
    page.click("#sch-quota button[data-v='no limit']")
    page.wait_for_selector(f"{week}", state="detached")
    page.click("#sch-save")
    page.wait_for_selector(".sch-layers >> text=no limit")
    deadline = time.monotonic() + 5
    while stored(server, "quota-check")["quota"] and time.monotonic() < deadline:
        time.sleep(0.1)
    assert stored(server, "quota-check")["quota"] == {} and not page.errors


def test_an_agents_schedule_shows_a_card_a_person_unpauses(server, page):  # noqa: F811
    page.goto(server.url)
    page.wait_for_selector("#a2[data-view=fleet]")
    spec = {
        "name": "nightly-tests",
        "clock": {"every": "1h"},
        "fire": {"agent": "opus"},
        "prompt": "run the tests",
    }
    spawn(page, "/mcp schedule_create " + json.dumps(spec))
    page.wait_for_selector(".apcard[data-state=waiting] >> text=run the tests")
    page.click(".apcard[data-state=waiting] button >> text=Unpause")
    page.wait_for_selector(".apcard[data-name=nightly-tests][data-state=running]")
    assert not page.errors


def test_the_card_waits_when_the_session_was_open_before_the_call(server, page):  # noqa: F811
    """The browser is on the session before its agent calls schedule_create,
    as a person watching it is: the card shows the schedule waiting, not
    deleted, with no reload."""
    page.goto(server.url)
    page.wait_for_selector("#a2[data-view=fleet]")
    spawn(page, "hello")
    spec = {
        "name": "watched-tests",
        "clock": {"every": "1h"},
        "fire": {"agent": "opus"},
        "prompt": "run the watched tests",
    }
    page.fill("#input", "/mcp schedule_create " + json.dumps(spec))
    page.press("#input", "Enter")
    page.wait_for_selector(".apcard[data-name=watched-tests]")
    page.wait_for_selector(
        ".apcard[data-name=watched-tests][data-state=waiting]", timeout=3000
    )
    assert not page.errors


def test_an_agents_workflow_schedule_card_lists_the_steps_and_pins_on_unpause(
    server,
    page,  # noqa: F811
):
    from aegis.workflow_store import sha_of

    from .test_workflow_store import NAP
    from .test_workflows_browser import send

    page.goto(server.url)
    page.wait_for_selector("#a2[data-view=fleet]")
    spawn(page, "/mcp workflow_save " + json.dumps({"definition": NAP}))
    spec = {
        "name": "agent-nap",
        "clock": {"every": "1h"},
        "fire": {"agent": "opus"},
        "prompt": "/workflow nap a second",
    }
    send(page, "/mcp schedule_create " + json.dumps(spec))
    card = ".apcard[data-name=agent-nap]"
    page.wait_for_selector(f"{card}[data-state=waiting] >> text=Nap a while.")
    page.wait_for_selector(f"{card} .apsteps >> text=Wait {{{{args.secs}}}}.")
    page.wait_for_selector(f"{card} .aprun >> text=saved by")
    page.click(f"{card} button >> text=Unpause")
    page.wait_for_selector(f"{card}[data-state=running]")
    saved = server.root / ".aegis" / "workflows" / "nap.yaml"
    assert stored(server, "agent-nap")["pin"]["sha"] == sha_of(saved.read_text())
    # The agent changes the workflow: the card asks again, with the diff.
    longer = NAP.replace("Nap a while.", "Nap longer.")
    send(page, "/mcp workflow_save " + json.dumps({"definition": longer}))
    page.wait_for_selector(f"{card}[data-state=waiting] .apchanged")
    page.wait_for_selector(f"{card} .edit .d.change")
    page.set_viewport_size({"width": 390, "height": 800})
    assert page.evaluate("document.documentElement.scrollWidth") <= 390
    page.click(f"{card} button >> text=Unpause")
    page.wait_for_selector(f"{card}[data-state=running]")
    deadline = time.monotonic() + 5
    while stored(server, "agent-nap")["pin"]["sha"] != sha_of(saved.read_text()):
        assert time.monotonic() < deadline, "the pin never moved to the new version"
        time.sleep(0.1)
    assert not page.errors


def test_the_schedules_view_shows_a_changed_workflow_before_resume_pins_it(
    server,
    page,  # noqa: F811
):
    # Resume in the view pins the version it sends, so the view draws what
    # changed first, as the approval card does.
    from aegis.workflow_store import sha_of

    from .test_workflow_store import NAP
    from .test_workflows_browser import send

    page.goto(server.url)
    page.wait_for_selector("#a2[data-view=fleet]")
    spawn(page, "/mcp workflow_save " + json.dumps({"definition": NAP}))
    spec = {
        "name": "agent-nap",
        "clock": {"every": "1h"},
        "fire": {"agent": "opus"},
        "prompt": "/workflow nap a second",
    }
    send(page, "/mcp schedule_create " + json.dumps(spec))
    card = ".apcard[data-name=agent-nap]"
    page.click(f"{card} button >> text=Unpause")
    page.wait_for_selector(f"{card}[data-state=running]")
    longer = NAP.replace("Nap a while.", "Nap longer.")
    send(page, "/mcp workflow_save " + json.dumps({"definition": longer}))
    page.wait_for_selector(f"{card}[data-state=waiting] .apchanged")
    page.click("#schedules-btn")
    page.click("#a2[data-view=schedules] .sch-row[data-name=agent-nap]")
    detail = "#a2[data-view=schedules] .sch-detail"
    page.wait_for_selector(f"{detail} .sch-wf .apchanged")
    page.wait_for_selector(f"{detail} .sch-wf .edit .d.change")
    page.click(f"{detail} button >> text=Resume")
    saved = server.root / ".aegis" / "workflows" / "nap.yaml"
    deadline = time.monotonic() + 5
    while stored(server, "agent-nap")["pin"]["sha"] != sha_of(saved.read_text()):
        assert time.monotonic() < deadline, "the pin never moved to the new version"
        time.sleep(0.1)
    assert not page.errors


def test_a_workflow_schedules_card_shows_the_commands_it_runs(
    server,
    page,  # noqa: F811
):
    from .test_schedules_workflows import SHELL
    from .test_workflows_browser import send

    page.goto(server.url)
    page.wait_for_selector("#a2[data-view=fleet]")
    spawn(page, "/mcp workflow_save " + json.dumps({"definition": SHELL}))
    spec = {
        "name": "tidy-up",
        "clock": {"every": "1h"},
        "fire": {"agent": "opus"},
        "prompt": "/workflow tidy now",
    }
    send(page, "/mcp schedule_create " + json.dumps(spec))
    card = ".apcard[data-name=tidy-up][data-state=waiting]"
    page.wait_for_selector(f"{card} .runs >> text=without asking")
    page.wait_for_selector(f"{card} .apcmds >> text=$ git clean -n")
    page.click("#schedules-btn")
    page.click("#a2[data-view=schedules] .sch-row[data-name=tidy-up]")
    page.wait_for_selector(".sch-detail .sch-wf .apcmds >> text=$ test -f done")
    assert not page.errors


def test_a_waiting_card_redraws_on_every_save_and_after_a_refused_unpause(
    server,
    page,  # noqa: F811
):
    # Unpause pins the version the card shows, so the card must show each
    # save made while it waits, and a hand edit an Unpause was refused for.
    from aegis.workflow_store import sha_of

    from .test_workflow_store import NAP
    from .test_workflows_browser import send

    page.goto(server.url)
    page.wait_for_selector("#a2[data-view=fleet]")
    spawn(page, "/mcp workflow_save " + json.dumps({"definition": NAP}))
    spec = {
        "name": "agent-nap",
        "clock": {"every": "1h"},
        "fire": {"agent": "opus"},
        "prompt": "/workflow nap a second",
    }
    send(page, "/mcp schedule_create " + json.dumps(spec))
    card = ".apcard[data-name=agent-nap]"
    page.wait_for_selector(f"{card}[data-state=waiting] >> text=Nap a while.")
    for say in ("Nap two.", "Nap three."):
        text = NAP.replace("Nap a while.", say)
        send(page, "/mcp workflow_save " + json.dumps({"definition": text}))
        page.wait_for_selector(f"{card}[data-state=waiting] >> text={say}")
    # A hand edit no save sees: the Unpause is refused and the card redraws.
    saved = server.root / ".aegis" / "workflows" / "nap.yaml"
    saved.write_text(saved.read_text().replace("Nap three.", "Nap by hand."))
    page.click(f"{card} button >> text=Unpause")
    page.wait_for_selector(f"{card}[data-state=waiting] >> text=Nap by hand.")
    # The Schedules view draws the same version.
    page.click("#schedules-btn")
    page.click("#a2[data-view=schedules] .sch-row[data-name=agent-nap]")
    detail = "#a2[data-view=schedules] .sch-detail"
    page.wait_for_selector(f"{detail} .sch-wf >> text=Nap by hand.")
    page.click(f"{detail} button >> text=Resume")
    deadline = time.monotonic() + 5
    while stored(server, "agent-nap")["pin"]["sha"] != sha_of(saved.read_text()):
        assert time.monotonic() < deadline, "the pin never moved to the new version"
        time.sleep(0.1)
    assert not page.errors
