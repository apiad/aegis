# ruff: noqa: F811  (fixtures imported from test_browser)
"""Knowledge in the client: the skills a session loaded, on its card and in
its sidebar."""

import getpass
import json

import pytest

from .test_browser import browser, page, server, spawn, turns_done  # noqa: F401

pytestmark = [pytest.mark.browser, pytest.mark.slow]


def seed_skill(root, name: str, description: str) -> None:
    d = root / ".aegis" / "state" / "knowledge" / getpass.getuser() / "skills" / name
    d.mkdir(parents=True)
    (d / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: {description}\n---\n1. go\n"
    )


def test_the_card_lists_loaded_skills(server, page):
    seed_skill(server.root, "release", "Cut a release")
    seed_skill(server.root, "deploy-bot", "Deploy the bot")
    page.goto(server.url)
    spawn(page, "/mcp skill_read " + json.dumps({"name": "deploy-bot"}))
    page.fill("#input", "/mcp skill_read " + json.dumps({"name": "release"}))
    page.press("#input", "Enter")
    turns_done(page, 2)
    page.wait_for_function("document.querySelectorAll('#s-skills > div').length === 2")
    assert page.inner_text("#s-skills-sec h4") == "Skills loaded"
    assert page.inner_text("#s-skills").split() == ["deploy-bot", "release"]
    page.click("#tab-fleet")
    page.wait_for_selector("#a2[data-view=fleet]")
    page.wait_for_selector(".card .pl .skills")
    assert page.inner_text(".card .pl .skills") == "skills\ndeploy-bot · release"
    page.set_viewport_size({"width": 390, "height": 844})
    assert page.evaluate("document.documentElement.scrollWidth") <= 390
    box = page.locator(".card .pl .skills").bounding_box()
    assert box["x"] >= 0 and box["x"] + box["width"] <= 390
    assert page.errors == []
