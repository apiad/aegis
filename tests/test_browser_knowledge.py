# ruff: noqa: F811  (fixtures imported from test_browser)
"""Knowledge in the client: the skills a session loaded, on its card and in
its sidebar."""

import getpass
import json

import pytest

from .test_browser import browser, page, server, spawn, turns_done  # noqa: F401

pytestmark = [pytest.mark.browser, pytest.mark.slow]


def seed_skill(root, name: str, description: str, script: str = "") -> None:
    d = root / ".aegis" / "state" / "knowledge" / getpass.getuser() / "skills" / name
    d.mkdir(parents=True)
    block = "aegis:\n  scripts: [go.py]\n" if script else ""
    (d / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: {description}\n{block}---\n1. go\n"
    )
    if script:
        (d / "go.py").write_text(script)


def test_the_card_lists_loaded_skills(server, page):
    seed_skill(server.root, "release", "Cut a release", "print('cut')\n")
    seed_skill(server.root, "deploy-bot", "Deploy the bot")
    page.goto(server.url)
    spawn(page, "/mcp skill_read " + json.dumps({"name": "deploy-bot"}))
    page.fill("#input", "/mcp skill_read " + json.dumps({"name": "release"}))
    page.press("#input", "Enter")
    turns_done(page, 2)
    run = {"skill": "release", "script": "go.py"}
    page.fill("#input", "/mcp skill_run " + json.dumps(run))
    page.press("#input", "Enter")
    turns_done(page, 3)
    page.wait_for_function("document.querySelectorAll('#s-skills > div').length === 2")
    assert page.inner_text("#s-skills-sec h4") == "Skills loaded"
    page.wait_for_function(
        "document.querySelector('#s-skills').textContent.includes('1 run')"
    )
    rows = page.locator("#s-skills > div").all_inner_texts()
    assert [r.split() for r in rows] == [["deploy-bot"], ["release", "1", "run"]]
    page.click("#tab-fleet")
    page.wait_for_selector("#a2[data-view=fleet]")
    page.wait_for_selector(".card .pl .skills")
    assert (
        page.inner_text(".card .pl .skills") == "skills\ndeploy-bot · release (1 run)"
    )
    page.set_viewport_size({"width": 390, "height": 844})
    assert page.evaluate("document.documentElement.scrollWidth") <= 390
    box = page.locator(".card .pl .skills").bounding_box()
    assert box["x"] >= 0 and box["x"] + box["width"] <= 390
    assert page.errors == []


def test_a_long_skill_name_wraps_in_the_sidebar_at_390_px(server, page):
    name = "deploy_the_bot_to_the_staging_host_and_then_check_every_endpoint"
    assert len(name) == 64
    seed_skill(server.root, name, "A long one")
    page.goto(server.url)
    spawn(page, "/mcp skill_read " + json.dumps({"name": name}))
    turns_done(page, 1)
    page.wait_for_function("document.querySelectorAll('#s-skills > div').length === 1")
    page.set_viewport_size({"width": 390, "height": 844})
    page.click("#nav-side")
    page.wait_for_function("document.querySelector('#a2').dataset.side === 'open'")
    page.wait_for_timeout(400)  # the drawer slides in
    assert page.evaluate("document.documentElement.scrollWidth") <= 390
    row = page.locator("#s-skills > div")
    box = row.bounding_box()
    assert box["x"] >= 0 and box["x"] + box["width"] <= 390
    # Every part of the row stays inside the drawer: the name wraps.
    widths = page.evaluate(
        """() => {
          const side = document.querySelector('#side').getBoundingClientRect();
          return [...document.querySelectorAll('#s-skills > div, #s-skills > div > *')]
            .map((e) => e.getBoundingClientRect())
            .map((r) => [r.left >= side.left - 0.5, r.right <= side.right + 0.5,
                         r.right <= 390, r.height]);
        }"""
    )
    assert all(a and b and c for a, b, c, _ in widths), widths
    assert widths[0][3] > 25  # wrapped: one line of 12.5 px text is under 20 px
    assert page.inner_text("#s-skills > div").replace("\n", "") == name
    assert page.errors == []
