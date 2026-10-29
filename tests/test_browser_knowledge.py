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


# The Knowledge view (js/knowledge.js, knowledge_ops.py's view ops).


def store_dir(root):
    return root / ".aegis" / "state" / "knowledge" / getpass.getuser()


def seed_memory(root, name: str, description: str, body: str = "", **meta) -> None:
    d = store_dir(root) / "memories"
    d.mkdir(parents=True, exist_ok=True)
    extra = "".join(f"{k}: {v}\n" for k, v in meta.items())
    (d / f"{name}.md").write_text(
        f"---\nname: {name}\ndescription: {description}\n{extra}---\n"
        f"{body or 'About ' + name}\n"
    )


def seed_view(root) -> None:
    seed_skill(root, "release", "Cut a release of the package")
    seed_skill(root, "smoke-test", "Run the guided smoke test of a build")
    seed_memory(root, "deploy-host", "Where the bot is deployed")
    seed_memory(root, "merge-after-green", "Merge after every check", status="proposed")
    seed_memory(
        root,
        "release-freeze",
        "No releases until the demo",
        lifetime="temporary",
        expires="2020-01-01",
    )


def names(page) -> list[str]:
    return page.locator(".klist .krow .nm").all_inner_texts()


def listed(page, want: list[str]) -> None:
    page.wait_for_function(
        "w => JSON.stringify([...document.querySelectorAll('.klist .krow .nm')]"
        ".map(e => e.textContent)) === JSON.stringify(w)",
        arg=want,
    )


def open_view(page, server) -> None:
    page.goto(server.url)
    page.wait_for_selector("#knowledge-btn")
    page.click("#knowledge-btn")
    page.wait_for_selector("#a2[data-view=knowledge]")


def edit(page, text: str) -> None:
    page.wait_for_selector(".kdetail .ked .cm-content")
    page.locator(".kdetail .ked .cm-content").fill(text)


def test_the_view_lists_and_filters(server, page):
    seed_view(server.root)
    seed_memory(server.root, "markup", "<img src=x onerror=alert(1)> as text")
    open_view(page, server)
    # The proposal first, then skills and memories by name.
    listed(
        page,
        [
            "merge-after-green",
            "release",
            "smoke-test",
            "deploy-host",
            "markup",
            "release-freeze",
        ],
    )
    rows = page.locator(".klist .krow")
    assert rows.nth(0).locator(".fl").inner_text() == "proposed"
    assert rows.nth(5).locator(".fl").inner_text() == "expired"
    assert rows.nth(1).locator(".use").inner_text() == "never used"
    # Item text is drawn as text, never as markup.
    assert "<img" in page.inner_text(".krow[data-name=markup] .ds")
    assert page.locator(".klist img").count() == 0
    page.click(".kkinds [data-kind=skill]")
    listed(page, ["release", "smoke-test"])
    page.click(".kkinds [data-kind=flagged]")
    listed(page, ["merge-after-green", "release-freeze"])
    page.click(".kkinds [data-kind=all]")
    page.fill(".kq", "deployed bot")
    listed(page, ["deploy-host"])
    page.fill(".kq", "nothing like this")
    page.wait_for_selector(".klist .empty")
    assert page.errors == []


def test_editing_saves_and_shows_in_history(server, page):
    seed_view(server.root)
    open_view(page, server)
    page.click(".krow[data-name=merge-after-green]")
    page.wait_for_function("location.hash === '#knowledge=merge-after-green'")
    page.wait_for_selector(".kdetail .kname")
    assert page.inner_text(".kdetail .kname") == "merge-after-green"
    assert page.inner_text(".kdetail .khead .fl") == "proposed"
    assert page.is_disabled(".ksave")
    edit(
        page,
        "---\nname: merge-after-green\ndescription: Merge only after every check passed\n"
        "status: proposed\n---\nThe author merges.\n",
    )
    page.click(".ksave")
    page.wait_for_function(
        "document.querySelector('.knote').textContent.includes('Saved')"
    )
    text = (store_dir(server.root) / "memories" / "merge-after-green.md").read_text()
    assert "status: approved" in text and "every check passed" in text
    page.wait_for_selector(".khist .kver")
    assert page.inner_text(".khist .kver .kvm") == "edit memory merge-after-green"
    assert page.inner_text(".khist .kver .kvnow") == "now"
    # The list shows it approved, out of the proposals.
    page.wait_for_function(
        "document.querySelector('.krow[data-name=merge-after-green] .fl').textContent === 'ok'"
    )
    # A change made elsewhere since the editor opened is refused, not overwritten.
    path = store_dir(server.root) / "memories" / "merge-after-green.md"
    path.write_text(path.read_text() + "edited by hand\n")
    edit(page, path.read_text().replace("edited by hand\n", "mine\n"))
    page.click(".ksave")
    page.wait_for_function(
        "document.querySelector('.knote').textContent.includes('changed since')"
    )
    assert path.read_text().endswith("edited by hand\n")
    assert page.errors == []


def test_restore_brings_back_an_old_version(server, page):
    seed_memory(server.root, "deploy-host", "Where the bot is deployed")
    open_view(page, server)
    page.click(".krow[data-name=deploy-host]")
    front = "---\nname: deploy-host\ndescription: Where the bot is deployed\n---\n"
    for body in ("on the small host", "on the big host"):
        edit(page, front + body + "\n")
        page.click(".ksave")
        page.wait_for_function(
            "n => document.querySelectorAll('.khist .kver').length === n",
            arg=1 if body.startswith("on the small") else 2,
        )
    path = store_dir(server.root) / "memories" / "deploy-host.md"
    assert "on the big host" in path.read_text()
    page.click(".khist .kver:nth-of-type(2) .krestore")
    page.wait_for_function("document.querySelectorAll('.khist .kver').length === 3")
    assert "on the small host" in path.read_text()
    assert page.inner_text(".khist .kver .kvm").startswith("restore memory deploy-host")
    page.wait_for_function(
        "document.querySelector('.ked .cm-content').textContent.includes('small host')"
    )
    # Delete keeps the history, and Restore brings the memory back.
    page.click(".kdel")
    page.click("#dialog .ok")
    page.wait_for_function("!document.querySelector('.ked')")
    assert not path.exists()
    page.wait_for_selector(".klist .empty")
    page.click(".khist .kver:nth-of-type(2) .krestore")
    page.wait_for_selector(".ked .cm-content")
    assert "on the small host" in path.read_text()
    listed(page, ["deploy-host"])
    assert page.errors == []


def test_the_view_on_a_phone_opens_an_item_full_width(server, page):
    seed_view(server.root)
    page.set_viewport_size({"width": 390, "height": 844})
    page.goto(server.url + "#knowledge")
    page.wait_for_selector("#a2[data-view=knowledge] .klist .krow")
    assert page.evaluate("document.documentElement.scrollWidth") <= 390
    # No usage column on a phone.
    assert not page.locator(".krow .use").first.is_visible()
    page.click(".krow[data-name=release]")
    page.wait_for_selector(".kdetail .ked .cm-content")
    assert not page.locator(".klist").is_visible()
    box = page.locator(".kdetail").bounding_box()
    assert box["x"] >= 0 and box["x"] + box["width"] <= 390 and box["width"] > 340
    assert page.evaluate("document.documentElement.scrollWidth") <= 390
    page.click(".kback")
    page.wait_for_function("location.hash === '#knowledge'")
    page.wait_for_selector(".klist .krow")
    assert page.locator(".klist").is_visible()
    assert page.errors == []


def test_the_cards_edit_opens_the_view_on_the_item(server, page):
    page.goto(server.url)
    memory = {
        "name": "merge-after-green",
        "description": "Merge a PR only after every check passed",
        "body": "The author merges without being asked.",
        "type": "feedback",
    }
    spawn(page, "/mcp memory_save " + json.dumps(memory))
    page.wait_for_selector(".row.knowledge .kcard a.btn")
    page.click(".row.knowledge .kcard a.btn")
    page.wait_for_selector("#a2[data-view=knowledge] .kdetail .ked .cm-content")
    assert page.inner_text(".kdetail .kname") == "merge-after-green"
    assert page.errors == []
