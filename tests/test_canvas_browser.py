"""The canvas in a browser: docked pages and files beside the transcript (#347)."""

import json
import re
from pathlib import Path

import pytest

from .test_browser import PICK, _artifact, browser, page, server, spawn, turns_done  # noqa: F401

pytestmark = [pytest.mark.browser, pytest.mark.slow]


def mcp(pg, n, tool, args):
    pg.fill("#input", f"/mcp {tool} {json.dumps(args)}")
    pg.press("#input", "Enter")
    turns_done(pg, n)


def test_an_agents_layout_draws_the_page_beside_the_transcript(server, page):  # noqa: F811
    page.goto(server.url)
    page.wait_for_selector("#a2[data-view=fleet]")
    spawn(page)
    aid, _ = _artifact(page, server, PICK, 0)
    mcp(
        page,
        3,
        "layout_set",
        {
            "windows": [
                {
                    "n": 1,
                    "columns": [
                        {"w": 0.6, "panes": [{"id": "transcript"}]},
                        {"w": 0.4, "panes": [{"id": aid}]},
                    ],
                }
            ]
        },
    )
    pane = page.locator(f'#canvas .pane[data-pane="{aid}"]')
    pane.wait_for()
    page.frame_locator(f'#canvas iframe[data-artifact="{aid}"]').locator("#st").filter(
        has_text="{}"
    ).wait_for()
    assert page.locator("iframe[data-artifact]").count() == 1, (
        "one live copy of the page"
    )
    assert page.locator(".row.artifact .acard .held").inner_text() == "docked · show"
    tr = page.locator("#tr-pane").bounding_box()
    box = pane.bounding_box()
    assert box["x"] >= tr["x"] + tr["width"] - 1  # to its right
    assert abs(box["width"] / (tr["width"] + box["width"]) - 0.4) < 0.03
    # The composer belongs to the transcript's pane on a desktop.
    inp = page.locator("#input").bounding_box()
    assert tr["x"] <= inp["x"] < tr["x"] + tr["width"]
    # Agent state reaches the docked frame.
    mcp(page, 4, "artifact_update", {"id": aid, "state": {"n": 7}})
    page.frame_locator(f'#canvas iframe[data-artifact="{aid}"]').locator("#st").filter(
        has_text='{"n":7}'
    ).wait_for()
    assert page.errors == []


def test_a_layout_that_arrives_before_its_entries_waits_for_them(server, page):  # noqa: F811
    page.goto(server.url)
    page.wait_for_selector("#a2[data-view=fleet]")
    spawn(page)
    aid, _ = _artifact(page, server, PICK, 0)
    mcp(
        page,
        3,
        "layout_set",
        {
            "windows": [
                {
                    "n": 1,
                    "columns": [
                        {"panes": [{"id": "transcript"}]},
                        {"panes": [{"id": aid}]},
                    ],
                }
            ]
        },
    )
    page.reload()  # the card can arrive before the transcript's snapshot
    page.locator(f'#canvas iframe[data-artifact="{aid}"]').wait_for()
    assert page.errors == []


def test_the_person_docks_drags_and_it_survives_a_reload_and_shows_elsewhere(
    server,  # noqa: F811
    page,  # noqa: F811
    browser,  # noqa: F811
):
    page.goto(server.url)
    page.wait_for_selector("#a2[data-view=fleet]")
    sid = spawn(page)
    aid, _ = _artifact(page, server, PICK, 0)
    page.click(".row.artifact .fbar .dock")
    pane = page.locator(f'#canvas .pane[data-pane="{aid}"]')
    pane.wait_for()
    assert page.locator(".row.artifact .acard .held").inner_text() == "docked · show"
    # Drag it by its bar onto the transcript's left edge: a new column there.
    bar = pane.locator(".pbar").bounding_box()
    tr = page.locator("#tr-pane").bounding_box()
    page.mouse.move(bar["x"] + 30, bar["y"] + 8)
    page.mouse.down()
    page.mouse.move(tr["x"] + 8, tr["y"] + tr["height"] / 2, steps=10)
    page.mouse.up()
    page.wait_for_function(
        "id => document.querySelector(`#canvas .pane[data-pane='${id}']`).getBoundingClientRect().x"
        " < document.querySelector('#tr-pane').getBoundingClientRect().x",
        arg=aid,
    )
    page.reload()
    page.locator(f'#canvas .pane[data-pane="{aid}"]').wait_for()
    other = browser.new_page()
    other.goto(f"{server.url}#s={sid}")
    other.locator(f'#canvas .pane[data-pane="{aid}"]').wait_for()
    # Undock in one; the other follows.
    page.click(f'#canvas .pane[data-pane="{aid}"] .undock')
    other.locator(f'#canvas .pane[data-pane="{aid}"]').wait_for(state="detached")
    page.frame_locator(f'.row.artifact iframe[data-artifact="{aid}"]').locator(
        "#st"
    ).wait_for()
    assert page.errors == []


def test_open_on_a_docked_page_shows_its_pane_and_rows_stay_at_fold_two(server, page):  # noqa: F811
    page.goto(server.url)
    page.wait_for_selector("#a2[data-view=fleet]")
    spawn(page)
    aid, _ = _artifact(page, server, PICK, 0)
    page.click(".row.artifact .fbar .dock")
    page.locator(f'#canvas .pane[data-pane="{aid}"]').wait_for()
    page.click(".row.artifact .fbar .view")
    page.locator(f'#canvas .pane[data-pane="{aid}"].flash').wait_for()
    assert page.locator("#viewer").is_hidden()
    # The card's Show outlines the pane too, and the page still runs once.
    page.locator(f'#canvas .pane[data-pane="{aid}"].flash').wait_for(state="detached")
    page.click(".row.artifact .held .locate")
    page.locator(f'#canvas .pane[data-pane="{aid}"].flash').wait_for()
    assert page.locator("iframe[data-artifact]").count() == 1
    # Alt+Z steps the fold level (keys.js); twice from 0 is level 2.
    page.keyboard.press("Alt+Z")
    page.keyboard.press("Alt+Z")
    assert page.get_attribute("#nav-fold", "data-level") == "2"
    assert page.locator(".row.artifact").is_visible()
    assert page.errors == []


BOXES = "JSON.stringify([...document.querySelectorAll('#canvas .pane')].map(p => p.style.cssText))"


def test_a_docked_file_follows_its_new_version(server, page):  # noqa: F811
    (server.root / "r.md").write_text("# One\n")
    page.goto(server.url)
    page.wait_for_selector("#a2[data-view=fleet]")
    spawn(page)
    mcp(page, 1, "file_send", {"paths": ["r.md"]})
    page.click(".row.file .fbar .dock")
    pane = page.locator("#canvas .pane.docked")
    pane.locator("text=One").wait_for()
    assert page.locator(".row.file .held").inner_text() == "docked · show"
    assert page.locator(".row.file .fbar .dock").is_hidden()
    before = page.evaluate(BOXES)
    (server.root / "r.md").write_text("# Two\n")
    mcp(page, 2, "file_send", {"paths": ["r.md"]})
    pane.locator("text=Two").wait_for()
    assert pane.locator(".pbar").inner_text().count("v2") == 1
    old, new = page.locator(".row.file").first, page.locator(".row.file").last
    assert old.locator(".held").inner_text() == "replaced by v2"
    assert old.locator(".fbar .dock").is_hidden()
    assert new.locator(".held").inner_text() == "docked · show"
    assert page.evaluate(BOXES) == before
    # The older copy stays openable: Open shows it in the viewer, not the pane.
    old.locator(".fbar .view").click()
    page.locator("#viewer .vbody").get_by_text("One").wait_for()
    page.keyboard.press("Escape")
    # Show outlines the pane; undocking gives the card its preview back.
    new.locator(".held .locate").click()
    page.locator("#canvas .pane.docked.flash").wait_for()
    pane.locator(".undock").click()
    pane.wait_for(state="detached")
    new.locator(".stage.markdown").wait_for()
    assert new.locator(".fbar .dock").is_visible()
    assert old.locator(".fbar .dock").is_hidden()
    assert page.errors == []


def test_a_stacked_panes_bar_is_grabbed_not_the_grip_above_it(server, page):  # noqa: F811
    for name in ("a.md", "b.md"):
        (server.root / name).write_text(f"# {name}\n")
    page.goto(server.url)
    page.wait_for_selector("#a2[data-view=fleet]")
    spawn(page)
    mcp(page, 1, "file_send", {"paths": ["a.md", "b.md"]})
    dock = page.locator(".row.file .fbar .dock")
    a = dock.get_attribute("data-dock")
    page.click(".row.file .pager .next")
    b = dock.get_attribute("data-dock")
    column = {"panes": [{"id": a}, {"id": b}]}
    windows = [{"n": 1, "columns": [{"panes": [{"id": "transcript"}]}, column]}]
    mcp(page, 2, "layout_set", {"windows": windows})
    lower = page.locator(f'#canvas .pane[data-pane="{b}"]')
    lower.get_by_text("b.md").last.wait_for()
    page.locator("#canvas .cgrip.y").wait_for()
    box = lower.locator(".pbar").bounding_box()
    hit = page.evaluate(
        "([x, y]) => !!document.elementFromPoint(x, y).closest('.pane.docked > .pbar')",
        [box["x"] + box["width"] / 2, box["y"] + 1],
    )
    assert hit, "the top edge of a stacked pane's bar drags the pane"
    assert page.errors == []


def test_agent_state_reaches_a_docked_page_whose_card_row_is_unmounted(server, page):  # noqa: F811
    page.goto(server.url)
    page.wait_for_selector("#a2[data-view=fleet]")
    spawn(page)
    aid, _ = _artifact(page, server, PICK, 0)
    page.click(".row.artifact .fbar .dock")
    inner = page.frame_locator(f'#canvas iframe[data-artifact="{aid}"]')
    inner.locator("#st").filter(has_text="{}").wait_for()
    # Rows above the mounted window go while the reader follows the bottom
    # (transcript.js), the docked page's card with them.
    # turns_done counts "done in" rows, and the early ones go too.
    page.fill("#input", "/many 320")
    page.press("#input", "Enter")
    page.locator(f'.row[data-id="{aid}"]').wait_for(state="detached", timeout=30_000)
    page.get_by_text("out 319").wait_for(timeout=30_000)
    page.fill("#input", f"/mcp artifact_update {json.dumps({'id': aid, 'state': {'n': 7}})}")
    page.press("#input", "Enter")
    inner.locator("#st").filter(has_text='{"n":7}').wait_for(timeout=10_000)
    assert page.errors == []


def test_a_docked_page_resent_while_its_tab_was_away_shows_the_new_page(
    server,  # noqa: F811
    page,  # noqa: F811
    browser,  # noqa: F811
):
    page.goto(server.url)
    page.wait_for_selector("#a2[data-view=fleet]")
    sid = spawn(page)
    aid, _ = _artifact(page, server, PICK, 0)
    made = page.locator(".row.prose .md", has_text='"path"').last.inner_text()
    path = Path(re.search(r'"path": "([^"]+)"', made).group(1))
    page.click(".row.artifact .fbar .dock")
    frame = f'#canvas iframe[data-artifact="{aid}"]'
    page.frame_locator(frame).locator("#b").wait_for()
    # The card's row leaves the mounted window, so only the canvas hears of the resend.
    page.fill("#input", "/many 320")
    page.press("#input", "Enter")
    page.locator(f'.row[data-id="{aid}"]').wait_for(state="detached", timeout=30_000)
    page.get_by_text("out 319").wait_for(timeout=30_000)
    before = page.get_attribute(frame, "src")
    spawn(page)  # the person moves to another tab
    other = browser.new_page()
    other.goto(f"{server.url}#s={sid}")
    other.locator(frame).wait_for()
    path.write_text(path.read_text().replace("B</button>", "C</button>"))
    other.fill("#input", f"/mcp artifact_update {json.dumps({'id': aid, 'resend': True})}")
    other.press("#input", "Enter")
    other.wait_for_function(
        "([sel, old]) => document.querySelector(sel)?.getAttribute('src') !== old",
        arg=[frame, before],
    )
    page.click(f"#tablist .tab[data-id='{sid}']")
    page.wait_for_function(
        "([sel, old]) => { const f = document.querySelector(sel); return f && f.getAttribute('src') !== old; }",
        arg=[frame, before],
        timeout=5000,
    )
    assert page.frame_locator(frame).locator("#b").inner_text() == "C"
    assert page.errors == []


FILL = "([sel]) => { const f = document.querySelector(sel); return [f.getBoundingClientRect().height, f.closest('.pbody').getBoundingClientRect().height]; }"


def test_a_docked_page_fills_its_pane_and_a_refused_submit_says_why(server, page):  # noqa: F811
    page.goto(server.url)
    page.wait_for_selector("#a2[data-view=fleet]")
    spawn(page)
    # A label over 140 characters is refused by the server, with a short
    # reason; the page posts it raw, as one that skips the script's checks would.
    bad = (
        '<button id="bad">bad</button><button id="ok">ok</button><span id="st"></span>',
        "st.textContent = JSON.stringify(state);"
        '  ok.onclick = () => aegis.submit({pick: "b"}, "Picked B");'
        '  bad.onclick = () => parent.postMessage({jsonrpc: "2.0", id: 99, method: "aegis/submit",'
        '    params: {data: {pick: "b"}, label: "x".repeat(200)}}, "*");',
    )
    aid, _ = _artifact(page, server, bad, 0)
    page.click(".row.artifact .fbar .dock")
    frame = f'#canvas iframe[data-artifact="{aid}"]'
    inner = page.frame_locator(frame)
    inner.locator("#st").filter(has_text="{}").wait_for()
    page.wait_for_timeout(500)  # the page has said its size by now
    got, body = page.evaluate(FILL, [frame])
    assert abs(got - body) < 2, (got, body)
    inner.locator("#bad").click()
    note = page.locator(f'#canvas .pane[data-pane="{aid}"] .pbody .refused')
    note.wait_for()
    assert note.inner_text().startswith("Not sent: ")
    # In view, not under the frame that fills the pane.
    box, pane = note.bounding_box(), page.locator(f'#canvas .pane[data-pane="{aid}"]').bounding_box()
    assert box["y"] + box["height"] <= pane["y"] + pane["height"] + 1, (box, pane)
    assert page.errors == []
