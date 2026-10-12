"""The whats-new hint under the new tab's composer, against a real
`aegis serve` and the fake claude (#339). Fixtures from test_browser.py, the
meta reader from test_tutorial_browser.py."""

import getpass
import json
import re
from pathlib import Path

import pytest

from .test_browser import CONFIG, Server, browser, new_page, page, pick  # noqa: F401
from .test_tutorial_browser import eventually, meta

pytestmark = [pytest.mark.browser, pytest.mark.slow]

REPO = Path(__file__).resolve().parents[1]
RELEASES = re.findall(
    r"^## \[(\d+(?:\.\d+)+)\]",
    (REPO / "CHANGELOG.md").read_text(encoding="utf-8"),
    re.M,
)
ENTRIES_HAVE = "t => document.querySelector('#entries').innerText.includes(t)"


def record_path(root: Path) -> Path:
    return root / ".aegis" / "state" / "help" / f"{getpass.getuser()}.json"


@pytest.fixture
def toured(tmp_path: Path, fake_claude: str, fake_opencode: str):
    """A person who has not taken the tutorial and toured two releases back."""
    (tmp_path / ".aegis.yaml").write_text(CONFIG)
    record_path(tmp_path).parent.mkdir(parents=True)
    record_path(tmp_path).write_text(
        json.dumps({"tutorial": False, "toured": RELEASES[2]})
    )
    s = Server(tmp_path, fake_claude, fake_opencode).start()
    yield s
    s.stop()


def open_new_tab(pg) -> None:
    pg.click("#tab-add")
    pg.wait_for_selector("#sp-hints[data-ready]")
    pg.wait_for_function("document.querySelector('#sp-agent').value === 'opus'")


def test_the_hint_tours_the_releases_since_the_last_tour_with_the_chips(
    toured,
    page,  # noqa: F811
):
    installed = RELEASES[0]
    page.goto(toured.url)
    open_new_tab(page)
    assert page.is_visible("#hint-tutorial") and page.is_visible("#hint-whats-new")
    assert page.inner_text("#hint-whats-new-go") == (
        f"Run /whats-new to learn what's new in {installed}."
    )
    pick(page, "#sp-effort", "max")
    page.click("#hint-whats-new-go")
    page.wait_for_selector("#a2[data-view=session]")
    log_id = page.evaluate("location.hash.slice(3)")
    page.wait_for_function(ENTRIES_HAVE, arg=f"[{installed}] - ", timeout=8000)
    text = page.inner_text("#entries")
    assert f"[{RELEASES[1]}] - " in text
    assert f"[{RELEASES[2]}] - " not in text
    m = meta(toured, log_id)
    assert (m["agent"], m["effort"]) == ("opus", "max"), "the hint used the chips"
    assert m["title"] == f"What's new in {installed}"
    assert json.loads(record_path(toured.root).read_text())["toured"] == installed
    open_new_tab(page)
    assert page.is_visible("#hint-tutorial"), "the tutorial hint is untouched"
    assert page.is_hidden("#hint-whats-new")
    assert page.errors == []


def test_the_cross_records_the_tour_and_a_typed_whats_new_opens_its_guide(
    toured,
    browser,  # noqa: F811
):
    errors: list = []
    pg = new_page(browser, errors)
    pg.set_viewport_size({"width": 390, "height": 844})
    pg.goto(toured.url + "#new")  # the phone layout may fold the tab bar away
    pg.wait_for_selector("#sp-hints[data-ready]")
    assert pg.is_visible("#hint-whats-new")
    width = pg.evaluate("document.documentElement.scrollWidth")
    assert width <= 390, f"the hints are {width} px wide on a 390 px phone"
    pg.set_viewport_size({"width": 1280, "height": 800})
    pg.click("#hint-whats-new-x")
    assert pg.is_hidden("#hint-whats-new")
    record = record_path(toured.root)
    eventually(lambda: json.loads(record.read_text())["toured"] == RELEASES[0])
    assert pg.locator("#tablist .tab").count() == 0, "dismissing starts nothing"

    pg.fill("#sp-text", "hello")
    pg.click("#sp-go")
    pg.wait_for_selector("#a2[data-view=session]")
    first = pg.evaluate("location.hash")
    pg.fill("#input", "/whats-new")
    pg.press("#input", "Enter")
    pg.wait_for_function("h => location.hash !== h", arg=first)
    pg.wait_for_function(ENTRIES_HAVE, arg=f"[{RELEASES[0]}] - ", timeout=8000)
    assert f"[{RELEASES[1]}] - " not in pg.inner_text("#entries")
    assert pg.locator("#tablist .tab").count() == 2, (
        "the session it was typed in stays open"
    )
    assert errors == []
