"""Reading replies aloud (#637): the chip on a reply, the navigator's toggle
that follows a session, and what gets said, against a real ``aegis serve``.

The browser's voice is replaced by a stub that records each utterance and
ends it a moment later (or holds it, for a stop), so what would be spoken can
be read back without a sound card or installed voices."""

import pytest

from .test_browser import EXPECTED_CONSOLE, browser, server, spawn, turns_done  # noqa: F401

pytestmark = [pytest.mark.browser, pytest.mark.slow]

STUB = """
window.__said = [];
window.__hold = false;
let pending = null;
window.SpeechSynthesisUtterance = class { constructor(text) { this.text = text; this.lang = ''; } };
Object.defineProperty(window, 'speechSynthesis', { value: {
  getVoices: () => [
    { lang: 'en-US', name: 'Test English', localService: true },
    { lang: 'es-ES', name: 'Test Spanish', localService: true },
  ],
  speak(u) {
    window.__said.push({ text: u.text, lang: u.lang });
    pending = u;
    if (!window.__hold) setTimeout(() => { if (pending === u) { pending = null; u.onend?.(); } }, 5);
  },
  cancel() {
    window.__said.push({ text: '<cancel>' });
    const u = pending; pending = null;
    u?.onerror?.({ error: 'interrupted' });
  },
} });
"""


@pytest.fixture
def page(browser):  # noqa: F811
    ctx = browser.new_context(viewport={"width": 1280, "height": 800})
    ctx.add_init_script(STUB)
    pg = ctx.new_page()
    pg.errors = []
    pg.on("pageerror", lambda e: pg.errors.append(str(e)))
    pg.on(
        "console",
        lambda m: (
            m.type == "error"
            and not any(x in m.text for x in EXPECTED_CONSOLE)
            and pg.errors.append(m.text)
        ),
    )
    yield pg
    assert not pg.errors, pg.errors
    ctx.close()


def said(pg) -> list[dict]:
    return pg.evaluate("window.__said")


def spoken(pg) -> str:
    return " ".join(u["text"] for u in said(pg) if u["text"] != "<cancel>")


def wait_said(pg, text: str) -> None:
    pg.wait_for_function(
        "t => window.__said.some((u) => u.text.includes(t))", arg=text, timeout=6000
    )


def chip(pg, n: int = 0):
    return pg.locator(".row.prose .rm .speak").nth(n)


SPLIT = (
    r"/split I fixed it in `src/aegis/monitors.py:142`, see [the docs](https://example.com/x)."
    r" || Run this:\n\n```py\nprint('secret code')\n```\n\nAll tests pass."
)


def test_a_chip_reads_its_reply_and_the_rest_of_the_turn(server, page):  # noqa: F811
    page.goto(server.url)
    spawn(page, SPLIT)
    assert page.locator(".row.prose").count() == 2
    chip(page, 0).hover()
    chip(page, 0).click()
    wait_said(page, "All tests pass")
    text = spoken(page)
    assert "monitors.py" in text and "src/aegis" not in text and ":142" not in text, (
        text
    )
    assert "the docs" in text, "a link is read as its text"
    assert "code block" in text and "secret code" not in text, text
    assert "tool output" not in text, "a tool row is skipped"
    assert all(u["lang"].startswith("en") for u in said(page))


def test_the_second_reply_s_chip_reads_from_there(server, page):  # noqa: F811
    page.goto(server.url)
    spawn(page, SPLIT)
    chip(page, 1).hover()
    chip(page, 1).click()
    wait_said(page, "All tests pass")
    assert "monitors.py" not in spoken(page)


def test_a_second_click_stops_and_the_chip_shows_it(server, page):  # noqa: F811
    page.goto(server.url)
    spawn(page, r"/md One long reply. It has two sentences.")
    page.evaluate("window.__hold = true")
    c = chip(page)
    c.hover()
    c.click()
    wait_said(page, "One long reply")
    assert "on" in c.get_attribute("class")
    assert page.locator(".row.prose.speaking").count() == 1
    c.click()
    page.wait_for_function("!document.querySelector('.row.prose .speak.on')")
    assert said(page)[-1]["text"] == "<cancel>"
    assert page.locator(".row.prose.speaking").count() == 0
    assert "two sentences" not in spoken(page), "stopping ends the reply"


def test_a_spanish_reply_gets_a_spanish_voice(server, page):  # noqa: F811
    page.goto(server.url)
    spawn(
        page, r"/md Terminé el informe de la provincia y lo envío así, con las tablas."
    )
    chip(page).hover()
    chip(page).click()
    wait_said(page, "Terminé")
    assert said(page)[0]["lang"] == "es-ES"


def test_the_toggle_reads_each_new_reply_and_not_the_old_ones(server, page):  # noqa: F811
    page.goto(server.url)
    spawn(page, r"/md An old reply.")
    page.click("#speak-follow")
    assert page.get_attribute("#speak-follow", "aria-pressed") == "true"
    page.fill("#input", r"/md A new reply arrived.")
    page.press("#input", "Enter")
    turns_done(page, 2)
    wait_said(page, "A new reply arrived")
    assert "An old reply" not in spoken(page)
    page.click("#speak-follow")
    assert page.get_attribute("#speak-follow", "aria-pressed") == "false"
    page.fill("#input", r"/md Not followed any more.")
    page.press("#input", "Enter")
    turns_done(page, 3)
    page.wait_for_timeout(300)
    assert "Not followed" not in spoken(page)


def test_settings_offers_phonon_beside_the_browser_voice(server, page):  # noqa: F811
    page.goto(server.url + "#settings")
    page.wait_for_selector("#set-speak-engine")
    assert (
        page.evaluate("document.querySelector('#set-speak-engine').value") == "builtin"
    )
    note = page.text_content("#set-speak .set-speak-note")
    assert "65 MB" in note and "English only" in note, note
