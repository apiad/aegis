"""The canvas in a browser: docked pages and files beside the transcript (#347)."""

import json

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
    assert page.locator(".row.artifact .acard .held").inner_text() == "docked"
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
