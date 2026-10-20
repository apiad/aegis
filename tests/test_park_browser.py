"""Parking in the browser: a watched session stays up, a hidden page lets it
park, a parked one shows its chip and hint, and the first keystroke wakes it."""

from pathlib import Path

import pytest

from .test_browser import CONFIG, Server, browser, page, spawn, turns_done  # noqa: F401

pytestmark = [pytest.mark.browser, pytest.mark.slow]


@pytest.fixture
def park_server(tmp_path: Path, fake_claude: str, fake_opencode: str):
    (tmp_path / ".aegis.yaml").write_text(CONFIG + "park: {claude-code: 1}\n")
    s = Server(tmp_path, fake_claude, fake_opencode)
    s.env["AEGIS_PARK_EVERY_S"] = "0.2"
    s.start()
    yield s
    s.stop()


HIDE = """() => {
    Object.defineProperty(document, "hidden", {value: true, configurable: true});
    document.dispatchEvent(new Event("visibilitychange"));
}"""
SHOW = HIDE.replace("value: true", "value: false")
PARKED = "() => document.querySelector('#input').placeholder.startsWith('Asleep after')"


def test_a_watched_session_stays_up_and_a_hidden_page_lets_it_park(park_server, page):  # noqa: F811
    page.goto(park_server.url)
    page.wait_for_selector("#a2[data-view=fleet]")
    spawn(page, '/mcp turn_end {"attention": "done", "line": "finished"}')
    turns_done(page, 1)
    page.wait_for_timeout(2500)
    # Watched: past its park: seconds, the session shown still has its harness.
    assert not page.evaluate(PARKED)
    page.evaluate(HIDE)
    page.wait_for_function(PARKED, timeout=8000)
    assert (
        page.locator("#input").get_attribute("placeholder")
        == "Asleep after 1 min idle. Typing wakes it."
    )
    # The Fleet card carries the chip, and the band counts what parking freed.
    page.click("#tab-fleet")
    page.wait_for_selector("#a2[data-view=fleet]")
    page.wait_for_selector("#a2 .card .hd .zz.pk")
    assert page.locator("#a2 .card .hd .zz.pk").inner_text() == "zz idle 1 min"
    assert page.locator("#a2 .parked-count").inner_text().startswith("1 asleep · ~")


def test_typing_to_a_parked_session_wakes_it_before_send(park_server, page):  # noqa: F811
    page.goto(park_server.url)
    page.wait_for_selector("#a2[data-view=fleet]")
    spawn(page, '/mcp turn_end {"attention": "done", "line": "finished"}')
    turns_done(page, 1)
    # Hide, let it park, show again: showing does not wake it.
    page.evaluate(HIDE)
    page.wait_for_function(PARKED, timeout=8000)
    page.evaluate(SHOW)
    page.wait_for_timeout(500)
    assert (
        page.locator("#input").get_attribute("placeholder").startswith("Asleep after")
    )
    # The waking row may come and go faster than a poll: record whether it shows.
    page.evaluate("""() => {
        const w = document.querySelector('#waking');
        window.wakingSeen = false;
        new MutationObserver(() => { if (!w.hidden) window.wakingSeen = true; })
            .observe(w, {attributes: true, attributeFilter: ['hidden']});
    }""")
    page.locator("#input").type("h")
    # Woken: the session left parked, so the placeholder is the normal one.
    page.wait_for_function(
        "() => document.querySelector('#input').placeholder.startsWith('Message the agent')",
        timeout=8000,
    )
    assert page.evaluate("window.wakingSeen")
    assert page.locator("#waking").is_hidden()
    assert page.errors == []
