"""The canvas in a browser: docked pages and files beside the transcript (#347)."""

import json
import re
import time
from pathlib import Path

import pytest

from .test_browser import (  # noqa: F401
    PICK,
    SHOWN,
    _artifact,
    browser,
    full_browser,
    page,
    push_ready,
    server,
    spawn,
    turns_done,
)

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
    # A narrow pane's bar keeps the name: the size and type are its tooltip.
    assert pane.locator(".pbar .fs").count() == 0
    assert pane.locator(".pbar .fn").inner_text() == "r.md"
    assert " B" in pane.locator(".pbar").get_attribute("title")
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
    page.fill(
        "#input", f"/mcp artifact_update {json.dumps({'id': aid, 'state': {'n': 7}})}"
    )
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
    other.fill(
        "#input", f"/mcp artifact_update {json.dumps({'id': aid, 'resend': True})}"
    )
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
    box, pane = (
        note.bounding_box(),
        page.locator(f'#canvas .pane[data-pane="{aid}"]').bounding_box(),
    )
    assert box["y"] + box["height"] <= pane["y"] + pane["height"] + 1, (box, pane)
    assert page.errors == []


def test_a_drag_whose_pane_is_undocked_elsewhere_still_ends(server, page, browser):  # noqa: F811
    page.goto(server.url)
    page.wait_for_selector("#a2[data-view=fleet]")
    sid = spawn(page)
    aid, _ = _artifact(page, server, PICK, 0)
    page.click(".row.artifact .fbar .dock")
    pane = page.locator(f'#canvas .pane[data-pane="{aid}"]')
    pane.wait_for()
    other = browser.new_page()
    other.goto(f"{server.url}#s={sid}")
    other.locator(f'#canvas .pane[data-pane="{aid}"]').wait_for()
    bar = pane.locator(".pbar").bounding_box()
    page.mouse.move(bar["x"] + 30, bar["y"] + 8)
    page.mouse.down()
    page.mouse.move(bar["x"] + 60, bar["y"] + 200, steps=5)
    assert page.locator("#canvas.dragging").count() == 1
    other.click(f'#canvas .pane[data-pane="{aid}"] .undock')
    pane.wait_for(state="detached")
    page.mouse.move(bar["x"] - 300, bar["y"] + 200, steps=5)
    page.mouse.up()
    assert page.locator("#canvas.dragging").count() == 0
    # Frames take clicks again: the page, back on its card, answers.
    page.frame_locator(f'.row.artifact iframe[data-artifact="{aid}"]').locator(
        "#b"
    ).click()
    page.wait_for_selector(".row.artifact .acard[data-status=submitted]")
    assert page.errors == []


def test_a_card_that_lands_while_a_grip_moves_is_kept(server, page, browser):  # noqa: F811
    page.goto(server.url)
    page.wait_for_selector("#a2[data-view=fleet]")
    sid = spawn(page)
    one, _ = _artifact(page, server, PICK, 0)
    two, _ = _artifact(page, server, PICK, 2)
    page.click(f'.row[data-id="{one}"] .fbar .dock')
    page.locator(f'#canvas .pane[data-pane="{one}"]').wait_for()
    other = browser.new_page()
    other.goto(f"{server.url}#s={sid}")
    other.locator(f'#canvas .pane[data-pane="{one}"]').wait_for()
    grip = page.locator("#canvas .cgrip.x").bounding_box()
    x, y = grip["x"] + grip["width"] / 2, grip["y"] + 300
    page.mouse.move(x, y)
    page.mouse.down()
    page.mouse.move(x - 40, y, steps=4)
    # Another browser docks a second page while the pointer is down here.
    other.click(f'.row[data-id="{two}"] .fbar .dock')
    page.locator(f'#canvas .pane[data-pane="{two}"]').wait_for()
    page.mouse.move(x - 80, y, steps=4)
    assert page.locator(f'#canvas .pane[data-pane="{two}"]').count() == 1, (
        "the drag must not draw over the card that landed"
    )
    page.mouse.up()
    page.wait_for_function(
        "x => document.querySelector('#tr-pane').getBoundingClientRect().right < x - 60",
        arg=x,
    )
    other.wait_for_function(
        "x => document.querySelector('#tr-pane').getBoundingClientRect().right < x - 60",
        arg=x,
    )
    assert page.locator(f'#canvas .pane[data-pane="{two}"]').count() == 1
    assert page.locator(".toast.error").count() == 0
    assert page.errors == []


def tap_mcp(pg, n, tool, args):
    """mcp() on a touch screen, where Enter adds a line: the send button."""
    pg.fill("#input", f"/mcp {tool} {json.dumps(args)}")
    pg.click("#send")
    turns_done(pg, n)


def test_a_phone_shows_the_canvas_as_tabs_with_the_composer_on_each(
    server, page, browser
):  # noqa: F811
    # The page is made on a desktop (the helper sends with Enter) and docked
    # on the phone.
    page.goto(server.url)
    page.wait_for_selector("#a2[data-view=fleet]")
    key = spawn(page)
    aid, _ = _artifact(page, server, PICK, 0)
    page.close()
    ctx = browser.new_context(viewport={"width": 390, "height": 844}, has_touch=True)
    phone = ctx.new_page()
    phone.errors = []
    phone.on("pageerror", lambda e: phone.errors.append(str(e)))
    phone.goto(f"{server.url}#s={key}")
    phone.wait_for_selector("#a2[data-view=session]")
    phone.click(".row.artifact .fbar .dock")
    strip = phone.locator("#canvas .ptabs")
    strip.wait_for()
    names = strip.locator("button").all_inner_texts()
    assert names[0] == "transcript" and len(names) == 2
    assert phone.locator("#canvas").get_attribute("data-mode") == "tabs"
    # The person's own dock leaves the transcript open, with a dot on the new tab.
    assert phone.locator("#tr").is_visible()
    tab = strip.locator(f'button[data-pane="{aid}"]')
    assert "dot" in tab.get_attribute("class")
    tab.click()
    assert "dot" not in tab.get_attribute("class")
    assert phone.locator("#tr-pane").is_hidden()
    assert phone.locator("#input").is_visible()  # the composer stays at the foot
    pane = phone.locator(f'#canvas .pane[data-pane="{aid}"]').bounding_box()
    composer = phone.locator("#composer").bounding_box()
    assert pane["width"] == 390 and pane["y"] + pane["height"] <= composer["y"] + 1, (
        pane,
        composer,
    )
    phone.frame_locator(f'#canvas iframe[data-artifact="{aid}"]').locator("#st").filter(
        has_text="{}"
    ).wait_for()
    # Show on the docked card opens the pane's tab.
    strip.locator('button[data-pane="transcript"]').click()
    assert phone.locator("#tr").is_visible()
    phone.locator(".row.artifact .held .locate").click()
    assert phone.locator(f'#canvas .pane[data-pane="{aid}"]').is_visible()
    strip.locator('button[data-pane="transcript"]').click()
    # The agent's state on the page dots its tab and leaves the transcript open.
    tap_mcp(phone, 3, "artifact_update", {"id": aid, "state": {"n": 7}})
    strip.locator(f'button[data-pane="{aid}"].dot').wait_for()
    assert phone.locator("#tr").is_visible()
    # The frame kept running while hidden: the state is there when the tab opens.
    tab.click()
    phone.frame_locator(f'#canvas iframe[data-artifact="{aid}"]').locator("#st").filter(
        has_text='{"n":7}'
    ).wait_for()
    # A long press offers Undock.
    box = tab.bounding_box()
    phone.mouse.move(box["x"] + box["width"] / 2, box["y"] + box["height"] / 2)
    phone.mouse.down()
    phone.wait_for_timeout(700)
    phone.mouse.up()
    phone.locator("#canvas .pmenu button").click()
    phone.locator(f'#canvas .pane[data-pane="{aid}"]').wait_for(state="detached")
    strip.wait_for(state="detached")
    assert phone.locator("#tr").is_visible()
    assert phone.evaluate("document.documentElement.scrollWidth") <= 390
    assert phone.errors == []
    ctx.close()


COLS = "JSON.stringify([...document.querySelectorAll('#canvas > .pane')].map(p => [p.dataset.pane, Math.round(p.getBoundingClientRect().width)]))"


def test_a_narrow_desktop_window_draws_tabs_and_widening_restores_the_columns(
    server, page
):  # noqa: F811
    (server.root / "r.md").write_text("# One\n")
    page.goto(server.url)
    page.wait_for_selector("#a2[data-view=fleet]")
    spawn(page)
    aid, _ = _artifact(page, server, PICK, 0)
    mcp(page, 3, "file_send", {"paths": ["r.md"]})
    fkey = page.locator(".row.file .fbar .dock").get_attribute("data-dock")
    layout = {
        "windows": [
            {
                "n": 1,
                "columns": [
                    {"w": 0.4, "panes": [{"id": "transcript"}]},
                    {"w": 0.3, "panes": [{"id": aid}]},
                    {"w": 0.3, "panes": [{"id": fkey}]},
                ],
            }
        ]
    }
    mcp(page, 4, "layout_set", layout)
    page.locator(f'#canvas .pane[data-pane="{fkey}"]').wait_for()
    assert page.locator("#canvas").get_attribute("data-mode") is None
    wide = page.evaluate(COLS)
    # 1280 less the side panel leaves about 1020 px for the canvas, over the
    # minimums' 360 + 280 + 280. At 1100 it is about 840, under them: tabs.
    page.set_viewport_size({"width": 1100, "height": 800})
    page.locator("#canvas[data-mode=tabs] .ptabs").wait_for()
    names = page.locator("#canvas .ptabs button").all_inner_texts()
    assert names == ["transcript", "Pick", "r.md"], names
    assert page.locator("#tr").is_visible()
    assert page.locator(f'#canvas .pane[data-pane="{aid}"]').is_hidden()
    assert page.locator("#canvas .cgrip").count() == 0
    # The agent's state dots the page's tab; leaving tab mode takes the dot
    # with it, so narrowing again shows none.
    tab = page.locator(f'#canvas .ptabs button[data-pane="{aid}"]')
    mcp(page, 5, "artifact_update", {"id": aid, "state": {"n": 7}})
    page.locator(f'#canvas .ptabs button[data-pane="{aid}"].dot').wait_for()
    page.set_viewport_size({"width": 1280, "height": 800})
    page.wait_for_function("!document.querySelector('#canvas').dataset.mode")
    assert page.locator("#canvas .ptabs").count() == 0
    assert page.evaluate(COLS) == wide
    assert page.locator("#canvas .cgrip").count() == 2
    page.set_viewport_size({"width": 1100, "height": 800})
    page.locator("#canvas[data-mode=tabs] .ptabs").wait_for()
    assert "dot" not in tab.get_attribute("class")
    page.set_viewport_size({"width": 1280, "height": 800})
    page.wait_for_function("!document.querySelector('#canvas').dataset.mode")
    # A transcript's share under its 360 px, where the minimums fit, is drawn
    # at 360 from the other columns: 0.3 of about 1020 is 306.
    layout["windows"][0]["columns"][0]["w"] = 0.3
    layout["windows"][0]["columns"][1]["w"] = 0.35
    layout["windows"][0]["columns"][2]["w"] = 0.35
    mcp(page, 6, "layout_set", layout)
    page.wait_for_function(
        "Math.round(document.querySelector('#tr-pane').getBoundingClientRect().width) === 360"
    )
    assert page.locator("#canvas").get_attribute("data-mode") is None
    assert page.locator("#canvas .cgrip").count() == 2
    # The resizes woke the agent with no error from the page.
    assert page.locator(".row.inbox").count() == 0, page.locator(
        ".row.inbox"
    ).all_inner_texts()
    assert page.errors == []


def test_a_narrow_panes_bar_keeps_the_name_and_undock(server, page):  # noqa: F811
    (server.root / "report-final.md").write_text("# One\n")
    page.goto(server.url)
    page.wait_for_selector("#a2[data-view=fleet]")
    spawn(page)
    aid, _ = _artifact(page, server, PICK, 0)
    mcp(page, 3, "file_send", {"paths": ["report-final.md"]})
    fkey = page.locator(".row.file .fbar .dock").get_attribute("data-dock")
    # About 1020 px of canvas: the file's 0.23 is about 235 px, drawn at its
    # 280 px minimum, which is still under the 300 px a full bar needs.
    layout = {
        "windows": [
            {
                "n": 1,
                "columns": [
                    {"w": 0.42, "panes": [{"id": "transcript"}]},
                    {"w": 0.35, "panes": [{"id": aid}]},
                    {"w": 0.23, "panes": [{"id": fkey}]},
                ],
            }
        ]
    }
    mcp(page, 4, "layout_set", layout)
    pane = page.locator(f'#canvas .pane[data-pane="{fkey}"]')
    pane.locator("text=One").wait_for()
    assert page.locator("#canvas").get_attribute("data-mode") is None
    assert pane.bounding_box()["width"] < 300
    fn = pane.locator(".pbar .fn")
    assert fn.evaluate("n => n.scrollWidth <= n.clientWidth"), "the name is cut"
    assert pane.locator(".pbar .undock").is_visible()
    assert pane.locator(".pbar .open").is_hidden()
    assert pane.locator(".pbar .dl").is_hidden()
    # A wider pane keeps its buttons.
    wide = page.locator(f'#canvas .pane[data-pane="{aid}"]')
    assert wide.locator(".pbar .popout").is_visible()
    assert page.errors == []


def _watch(pg):
    """``pg`` keeping its page errors in ``pg.errors``."""
    pg.errors = []
    pg.on("pageerror", lambda e: pg.errors.append(str(e)))
    return pg


def _window_page(browser, url, **ctx):  # noqa: F811
    """A second browser tab at ``url``, keeping its page errors."""
    pg = _watch(browser.new_page(viewport={"width": 1280, "height": 800}, **ctx))
    pg.goto(url)
    return pg


def _runs(pg, src) -> int:
    """How many frames in ``pg`` have loaded the page at ``src``."""
    return sum(1 for f in pg.frames if f.url.endswith(src))


def test_a_second_window_runs_its_page_live(server, page, browser):  # noqa: F811
    page.goto(server.url)
    page.wait_for_selector("#a2[data-view=fleet]")
    sid = spawn(page)
    aid, _ = _artifact(page, server, PICK, 0)
    mcp(
        page,
        3,
        "layout_set",
        {
            "windows": [
                {"n": 1, "columns": [{"panes": [{"id": "transcript"}]}]},
                {"n": 2, "columns": [{"panes": [{"id": aid}]}]},
            ]
        },
    )
    held = page.locator(".row.artifact .acard .held")
    held.filter(has_text="in window 2").wait_for()
    assert held.inner_text() == "in window 2 · open"
    assert page.locator("iframe[data-artifact]").count() == 0
    assert page.locator(f'#canvas .pane[data-pane="{aid}"]').count() == 0
    two = _window_page(browser, f"{server.url}#w=2:{sid}")
    inner = two.frame_locator(f'#canvas iframe[data-artifact="{aid}"]')
    inner.locator("#st").filter(has_text="{}").wait_for()
    assert two.locator("#a2").get_attribute("data-view") == "window"
    assert two.locator("#tr-pane").is_hidden() and two.locator("#side").is_hidden()
    assert two.locator("header.tabs").is_hidden()
    assert two.title().endswith(" · window 2"), two.title()
    # The page runs once in the window's tab, and not at all in window 1's.
    src = two.locator(f'#canvas iframe[data-artifact="{aid}"]').get_attribute("src")
    assert _runs(two, src) == 1 and _runs(page, src) == 0
    # The agent's state reaches it there.
    mcp(page, 4, "artifact_update", {"id": aid, "state": {"n": 7}})
    inner.locator("#st").filter(has_text='{"n":7}').wait_for()
    inner.locator(
        "#b"
    ).click()  # submits: the agent hears it, the page leaves the canvas
    page.wait_for_selector(
        f".row.inbox .from >> text=artifact:{aid} · submit",
        state="attached",
        timeout=10000,
    )
    two.locator("text=This window is empty").wait_for()
    assert two.errors == [] and page.errors == []


def test_pop_out_opens_a_live_window_and_bring_back_returns_it(server, page):  # noqa: F811
    page.goto(server.url)
    page.wait_for_selector("#a2[data-view=fleet]")
    sid = spawn(page)
    aid, _ = _artifact(page, server, PICK, 0)
    # Pop out takes the place of the raw page in a new tab.
    assert page.locator(".row.artifact .fbar .open").count() == 0
    with page.context.expect_page() as opened:
        page.click(".row.artifact .fbar .popout")
    two = _watch(opened.value)
    two.frame_locator(f'#canvas iframe[data-artifact="{aid}"]').locator(
        "#st"
    ).wait_for()
    assert two.url.endswith(f"#w=2:{sid}")
    assert (
        page.locator(".row.artifact .acard .held").inner_text() == "in window 2 · open"
    )
    # Open on the card focuses that tab by its name rather than opening another.
    pages = len(page.context.pages)
    page.click(".row.artifact .acard .held .locate")
    page.wait_for_timeout(300)
    assert len(page.context.pages) == pages
    two.close()
    chip = page.locator("#canvas .wchip")
    chip.filter(has_text="window 2 · 1 pane").wait_for()
    chip.locator(".bring").click()
    page.locator(f'#canvas .pane[data-pane="{aid}"]').wait_for()
    assert page.locator("#canvas .wchip").count() == 0
    page.frame_locator(f'#canvas iframe[data-artifact="{aid}"]').locator(
        "#st"
    ).wait_for()
    assert page.errors == []


def test_move_to_window_lists_the_windows_and_opens_the_one_chosen(server, page):  # noqa: F811
    (server.root / "r.md").write_text("# One\n")
    page.goto(server.url)
    page.wait_for_selector("#a2[data-view=fleet]")
    sid = spawn(page)
    aid, _ = _artifact(page, server, PICK, 0)
    mcp(page, 3, "file_send", {"paths": ["r.md"]})
    fkey = page.locator(".row.file .fbar .dock").get_attribute("data-dock")
    mcp(
        page,
        4,
        "layout_set",
        {
            "windows": [
                {
                    "n": 1,
                    "columns": [
                        {"panes": [{"id": "transcript"}]},
                        {"panes": [{"id": fkey}]},
                    ],
                },
                {"n": 2, "columns": [{"panes": [{"id": aid}]}]},
            ]
        },
    )
    pane = page.locator(f'#canvas .pane[data-pane="{fkey}"]')
    pane.locator("text=One").wait_for()
    # The file's card says where it is too, and its chip counts one pane.
    page.locator("#canvas .wchip").filter(has_text="window 2 · 1 pane").wait_for()
    pane.locator(".pbar .move").click()
    items = page.locator("#canvas .pmenu button").all_inner_texts()
    assert items == ["Window 2", "New window"], items
    with page.context.expect_page() as opened:
        page.locator("#canvas .pmenu button", has_text="Window 2").click()
    two = _watch(opened.value)
    assert two.url.endswith(f"#w=2:{sid}")
    two.locator(f'#canvas .pane[data-pane="{fkey}"]').locator("text=One").wait_for()
    two.locator(f'#canvas .pane[data-pane="{aid}"]').wait_for()
    page.locator("#canvas .wchip").filter(has_text="window 2 · 2 panes").wait_for()
    assert page.locator(".row.file .fcard .held").inner_text() == "in window 2 · open"
    # From window 2, the menu offers window 1 and a new one.
    two.locator(f'#canvas .pane[data-pane="{aid}"] .pbar .move').click()
    assert two.locator("#canvas .pmenu button").all_inner_texts() == [
        "Window 1",
        "New window",
    ]
    two.locator("#canvas .pmenu button", has_text="Window 1").click()
    page.locator(f'#canvas .pane[data-pane="{aid}"]').wait_for()
    page.locator("#canvas .wchip").filter(has_text="window 2 · 1 pane").wait_for()
    assert two.locator(f'#canvas .pane[data-pane="{aid}"]').count() == 0
    assert page.errors == [] and two.errors == []


def test_a_phone_strip_holds_every_windows_panes(server, page, browser):  # noqa: F811
    (server.root / "r.md").write_text("# One\n")
    page.goto(server.url)
    page.wait_for_selector("#a2[data-view=fleet]")
    key = spawn(page)
    aid, _ = _artifact(page, server, PICK, 0)
    mcp(page, 3, "file_send", {"paths": ["r.md"]})
    fkey = page.locator(".row.file .fbar .dock").get_attribute("data-dock")
    mcp(
        page,
        4,
        "layout_set",
        {
            "windows": [
                {
                    "n": 1,
                    "columns": [
                        {"panes": [{"id": "transcript"}]},
                        {"panes": [{"id": fkey}]},
                    ],
                },
                {"n": 2, "columns": [{"panes": [{"id": aid}]}]},
            ]
        },
    )
    page.close()
    ctx = browser.new_context(viewport={"width": 390, "height": 844}, has_touch=True)
    phone = ctx.new_page()
    phone.errors = []
    phone.on("pageerror", lambda e: phone.errors.append(str(e)))
    phone.goto(f"{server.url}#s={key}")
    strip = phone.locator("#canvas .ptabs")
    strip.wait_for()
    assert strip.locator("button").all_inner_texts() == ["transcript", "r.md", "Pick"]
    assert phone.locator("#canvas .wchip").count() == 0
    strip.locator(f'button[data-pane="{aid}"]').click()
    phone.frame_locator(f'#canvas iframe[data-artifact="{aid}"]').locator(
        "#st"
    ).wait_for()
    assert phone.locator(f'#canvas .pane[data-pane="{aid}"] .move').is_hidden()
    assert phone.locator(f'#canvas .pane[data-pane="{aid}"] .popout').is_hidden()
    # Its card holds the docked line, not another window's.
    strip.locator('button[data-pane="transcript"]').click()
    assert phone.locator(".row.artifact .acard .held").inner_text() == "docked · show"
    # A window tab on a phone draws its panes as tabs too, with no transcript tab.
    win = ctx.new_page()
    win.goto(f"{server.url}#w=2:{key}")
    win.locator(f'#canvas iframe[data-artifact="{aid}"]').wait_for()
    assert win.locator("#canvas").get_attribute("data-mode") == "tabs"
    assert win.locator("#canvas .ptabs button").all_inner_texts() == ["Pick"]
    assert win.locator(f'#canvas .pane[data-pane="{aid}"]').is_visible()
    assert phone.errors == []
    ctx.close()


def _two_windows(aid, *more):
    """window 1: the transcript and the panes ``more``, one column each;
    window 2: ``aid``."""
    return {
        "windows": [
            {
                "n": 1,
                "columns": [{"panes": [{"id": i}]} for i in ("transcript", *more)],
            },
            {"n": 2, "columns": [{"panes": [{"id": aid}]}]},
        ]
    }


def test_esc_closes_a_pane_menu_without_interrupting_and_a_card_leaves_it_open(
    server, page
):  # noqa: F811
    page.goto(server.url)
    page.wait_for_selector("#a2[data-view=fleet]")
    spawn(page)
    aid, _ = _artifact(page, server, PICK, 0)
    page.click(".row.artifact .fbar .dock")
    pane = page.locator(f'#canvas .pane[data-pane="{aid}"]')
    pane.wait_for()
    page.fill("#input", "/sleep 3")
    page.press("#input", "Enter")
    page.wait_for_selector(".row.tool.running")
    menu = page.locator("#canvas .pmenu")
    pane.locator(".pbar .move").click()
    menu.wait_for()
    page.keyboard.press("Escape")
    menu.wait_for(state="detached")
    assert page.is_visible(".row.tool.running"), "Esc on the menu does not interrupt"
    # The turn's end lands a new card for the session: the menu stays open.
    pane.locator(".pbar .move").click()
    menu.wait_for()
    turns_done(page, 3)
    page.wait_for_timeout(300)
    assert menu.is_visible()
    assert page.errors == []


def test_a_windows_only_pane_moved_to_a_new_window_gets_a_tab_of_its_own(
    server, page, browser
):  # noqa: F811
    page.goto(server.url)
    page.wait_for_selector("#a2[data-view=fleet]")
    sid = spawn(page)
    aid, _ = _artifact(page, server, PICK, 0)
    mcp(page, 3, "layout_set", _two_windows(aid))
    two = _window_page(browser, f"{server.url}#w=2:{sid}")
    pane = two.locator(f'#canvas .pane[data-pane="{aid}"]')
    pane.wait_for()
    pane.locator(".pbar .move").click()
    with two.context.expect_page() as opened:
        two.locator("#canvas .pmenu button", has_text="New window").click()
    three = _watch(opened.value)
    three.frame_locator(f'#canvas iframe[data-artifact="{aid}"]').locator(
        "#st"
    ).wait_for()
    assert three.url.endswith(f"#w=3:{sid}"), three.url
    two.locator("text=This window is empty").wait_for()
    page.locator("#canvas .wchip").filter(has_text="window 3 · 1 pane").wait_for()
    assert two.errors == [] and three.errors == [] and page.errors == []


def test_a_hidden_transcript_reads_nothing_dots_its_tab_and_find_opens_it(server, page):  # noqa: F811
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
    # 600 px is under the minimums' 360 + 280: tabs, on a desktop.
    page.set_viewport_size({"width": 600, "height": 800})
    strip = page.locator("#canvas[data-mode=tabs] .ptabs")
    strip.wait_for()
    strip.locator(f'button[data-pane="{aid}"]').click()
    page.fill("#input", "hello again")
    page.press("#input", "Enter")
    turns_done(page, 4)
    # The reply landed behind the page's tab: the transcript's tab is dotted,
    # and the reply stays unread while nobody can see it.
    strip.locator('button[data-pane="transcript"].dot').wait_for(timeout=5000)
    page.wait_for_timeout(1500)
    last = page.locator(".row.prose").last
    assert last.locator(".rm .ic.i-unread").count() == 1
    # j acts on nothing it cannot show; Ctrl+F opens the transcript's tab with its bar.
    strip.locator(f'button[data-pane="{aid}"]').focus()
    page.keyboard.press("j")
    assert "on" in strip.locator(f'button[data-pane="{aid}"]').get_attribute("class")
    page.keyboard.press("Control+f")
    page.locator("#find").wait_for()
    assert "on" in strip.locator('button[data-pane="transcript"]').get_attribute(
        "class"
    )
    assert page.locator("#find").is_visible()
    last.locator(".rm .ic.i-read").wait_for(timeout=6000)  # seen now, for a second
    assert page.errors == []


def test_a_narrow_desktop_window_draws_only_its_own_windows_panes(
    server, page, browser
):  # noqa: F811
    (server.root / "r.md").write_text("# One\n")
    page.goto(server.url)
    page.wait_for_selector("#a2[data-view=fleet]")
    sid = spawn(page)
    aid, _ = _artifact(page, server, PICK, 0)
    mcp(page, 3, "file_send", {"paths": ["r.md"]})
    fkey = page.locator(".row.file .fbar .dock").get_attribute("data-dock")
    mcp(page, 4, "layout_set", _two_windows(aid, fkey))
    page.set_viewport_size({"width": 700, "height": 800})
    page.locator(f'#canvas .pane[data-pane="{fkey}"]').locator("text=One").wait_for()
    page.locator("#canvas .wchip").filter(has_text="window 2 · 1 pane").wait_for()
    assert page.locator("#canvas").get_attribute("data-merged") is None
    assert page.locator(f'#canvas .pane[data-pane="{aid}"]').count() == 0
    assert page.locator(f'#canvas .pane[data-pane="{fkey}"] .move').is_visible()
    assert (
        page.locator(".row.artifact .acard .held").inner_text() == "in window 2 · open"
    )
    two = _watch(browser.new_page(viewport={"width": 700, "height": 800}))
    two.goto(f"{server.url}#w=2:{sid}")
    two.frame_locator(f'#canvas iframe[data-artifact="{aid}"]').locator(
        "#st"
    ).wait_for()
    src = two.locator(f'#canvas iframe[data-artifact="{aid}"]').get_attribute("src")
    assert _runs(two, src) == 1 and _runs(page, src) == 0
    assert two.locator(f'#canvas .pane[data-pane="{fkey}"]').count() == 0
    assert page.errors == [] and two.errors == []


def test_columns_under_their_minimum_are_drawn_at_it_from_the_wider_ones(server, page):  # noqa: F811
    for name in ("a.md", "b.md"):
        (server.root / name).write_text(f"# {name}\n")
    page.set_viewport_size({"width": 1700, "height": 900})
    page.goto(server.url)
    page.wait_for_selector("#a2[data-view=fleet]")
    spawn(page)
    aid, _ = _artifact(page, server, PICK, 0)
    mcp(page, 3, "file_send", {"paths": ["a.md"]})
    mcp(page, 4, "file_send", {"paths": ["b.md"]})
    fa, fb = (
        page.locator(".row.file .fbar .dock").nth(i).get_attribute("data-dock")
        for i in (0, 1)
    )
    shares = {"transcript": 0.4, aid: 0.4, fa: 0.1, fb: 0.1}
    mcp(
        page,
        5,
        "layout_set",
        {
            "windows": [
                {
                    "n": 1,
                    "columns": [
                        {"w": w, "panes": [{"id": i}]} for i, w in shares.items()
                    ],
                }
            ]
        },
    )
    page.locator(f'#canvas .pane[data-pane="{fb}"]').wait_for()
    assert page.locator("#canvas").get_attribute("data-mode") is None
    width = page.evaluate("document.querySelector('#canvas').clientWidth")
    assert width >= 360 + 3 * 280, width
    drawn = dict(json.loads(page.evaluate(COLS)))
    drawn["transcript"] = page.evaluate(
        "document.querySelector('#tr-pane').getBoundingClientRect().width"
    )
    # The two narrow columns are raised to 280 px; the transcript and the page
    # give it up in proportion to how far each is above its own minimum.
    mins = {"transcript": 360, aid: 280, fa: 280, fb: 280}
    need = sum(max(0, mins[i] - w * width) for i, w in shares.items())
    spare = sum(max(0, w * width - mins[i]) for i, w in shares.items())
    for i, w in shares.items():
        px = w * width
        want = mins[i] if px < mins[i] else px - (px - mins[i]) * need / spare
        assert abs(drawn[i] - want) <= 2, (i, drawn[i], want)
    # The grips sit on the drawn boundaries.
    lefts = page.evaluate(
        "[...document.querySelectorAll('#canvas > .cgrip.x')].map(g => Math.round(g.getBoundingClientRect().x + 4))"
    )
    edges = page.evaluate(
        "ids => ids.map(i => Math.round((i === 'transcript' ? document.querySelector('#tr-pane') : document.querySelector(`#canvas .pane[data-pane='${i}']`)).getBoundingClientRect().x))",
        list(shares)[1:],
    )
    assert all(abs(a - b) <= 2 for a, b in zip(sorted(lefts), sorted(edges))), (
        lefts,
        edges,
    )
    assert page.errors == []


def test_a_window_tab_never_quiets_a_push_for_its_session(server, full_browser):  # noqa: F811
    ctx = full_browser.new_context()
    ctx.grant_permissions(["notifications"])
    pg = ctx.new_page()
    deliver = push_ready(ctx, pg, server.url)
    sid = spawn(pg, "hello")
    pg.goto(f"{server.url}#w=2:{sid}")  # the session's window 2, focused and visible
    pg.wait_for_selector("#a2[data-view=window]")
    pg.wait_for_timeout(300)  # the page tells the worker what it shows
    deliver(
        {
            "title": "x: needs you",
            "body": "",
            "key": sid,
            "kind": "needs_you",
            "tag": f"{sid}:needs_you",
        }
    )
    deadline = time.monotonic() + 5
    while not pg.evaluate(SHOWN) and time.monotonic() < deadline:
        time.sleep(0.1)
    assert pg.evaluate(SHOWN) == [["x: needs you", f"{sid}:needs_you"]]
    ctx.close()


def test_the_viewer_offers_pop_out_for_a_live_page(server, page):  # noqa: F811
    page.goto(server.url)
    page.wait_for_selector("#a2[data-view=fleet]")
    sid = spawn(page)
    aid, _ = _artifact(page, server, PICK, 0)
    page.click(".row.artifact .fbar .view")
    page.locator("#viewer iframe[data-artifact]").wait_for()
    assert page.locator("#viewer .open").count() == 0, (
        "no raw page with no bridge behind it"
    )
    with page.context.expect_page() as opened:
        page.click("#viewer .popout")
    two = _watch(opened.value)
    two.frame_locator(f'#canvas iframe[data-artifact="{aid}"]').locator(
        "#st"
    ).wait_for()
    assert two.url.endswith(f"#w=2:{sid}")
    assert page.locator("#viewer").is_hidden()
    assert page.errors == [] and two.errors == []


def test_a_window_tab_runs_no_cards_page_and_its_keys_stay_in_the_window(
    server, page, browser
):  # noqa: F811
    page.goto(server.url)
    page.wait_for_selector("#a2[data-view=fleet]")
    sid = spawn(page)
    aid, _ = _artifact(page, server, PICK, 0)
    other, _ = _artifact(page, server, PICK, 2, title="Other")
    mcp(page, 5, "layout_set", _two_windows(aid))
    two = _window_page(browser, f"{server.url}#w=2:{sid}")
    two.frame_locator(f'#canvas iframe[data-artifact="{aid}"]').locator(
        "#st"
    ).wait_for()
    two.locator(f'.row.artifact[data-id="{other}"]').wait_for(state="attached")
    assert two.locator("#entries iframe[data-artifact]").count() == 0
    # The keys that go to another tab, the Fleet or a new session do nothing here.
    for k in (
        "Alt+BracketRight",
        "Alt+BracketLeft",
        "Alt+KeyN",
        "Alt+KeyJ",
        "Alt+Digit0",
        "Control+k",
    ):
        two.keyboard.press(k)
    two.wait_for_timeout(300)
    assert two.url.endswith(f"#w=2:{sid}"), two.url
    assert two.locator("#palette").is_hidden()
    assert two.errors == [] and page.errors == []


def test_two_quick_pop_outs_open_two_windows(server, page):  # noqa: F811
    page.goto(server.url)
    page.wait_for_selector("#a2[data-view=fleet]")
    sid = spawn(page)
    a, _ = _artifact(page, server, PICK, 0)
    b, _ = _artifact(page, server, PICK, 2, title="Other")
    before = len(page.context.pages)
    # Both clicks before the first card lands.
    page.evaluate(
        "document.querySelectorAll('.row.artifact .fbar .popout').forEach(b => b.click())"
    )
    page.locator("#canvas .wchip").filter(has_text="window 3").wait_for()
    deadline = time.monotonic() + 5
    while len(page.context.pages) < before + 2 and time.monotonic() < deadline:
        page.wait_for_timeout(100)
    tabs = page.context.pages[before:]
    drawn = {}
    for t in tabs:
        t.locator("#canvas iframe[data-artifact]").wait_for()
        drawn[t.url.split("#")[1]] = t.locator(
            "#canvas iframe[data-artifact]"
        ).get_attribute("data-artifact")
    assert drawn == {f"w=2:{sid}": a, f"w=3:{sid}": b}, drawn
    assert page.errors == []


def test_bring_back_into_a_full_row_of_columns_stacks_the_rest(server, page):  # noqa: F811
    for name in ("r1.md", "r2.md", "r3.md"):
        (server.root / name).write_text(f"# {name}\n")
    page.set_viewport_size({"width": 1700, "height": 900})
    page.goto(server.url)
    page.wait_for_selector("#a2[data-view=fleet]")
    spawn(page)
    aid, _ = _artifact(page, server, PICK, 0)
    for n, name in enumerate(("r1.md", "r2.md", "r3.md"), start=3):
        mcp(page, n, "file_send", {"paths": [name]})
    f1, f2, f3 = (
        page.locator(".row.file .fbar .dock").nth(i).get_attribute("data-dock")
        for i in range(3)
    )
    lay = _two_windows(aid, f1, f2)
    lay["windows"][1]["columns"].append({"panes": [{"id": f3}]})
    mcp(page, 6, "layout_set", lay)
    chip = page.locator("#canvas .wchip")
    chip.filter(has_text="window 2 · 2 panes").wait_for()
    chip.locator(".bring").click()
    page.locator(f'#canvas .pane[data-pane="{f3}"]').wait_for()
    page.locator(f'#canvas .pane[data-pane="{aid}"]').wait_for()
    assert page.locator("#canvas .wchip").count() == 0
    # Window 1 had room for one more column: the page takes it, the file goes under it.
    top = page.locator(f'#canvas .pane[data-pane="{aid}"]').bounding_box()
    low = page.locator(f'#canvas .pane[data-pane="{f3}"]').bounding_box()
    assert abs(top["x"] - low["x"]) < 2 and low["y"] > top["y"], (top, low)
    assert page.errors == []
