"""A terminal's tab in a real browser against a real aegis serve (#362)."""

import json
import time
from pathlib import Path

import pytest

from .test_browser import CONFIG, Server, browser  # noqa: F401

pytest.importorskip("playwright.sync_api")
from websockets.sync.client import connect  # noqa: E402

from aegis.web import PROTO  # noqa: E402

pytestmark = [pytest.mark.browser, pytest.mark.slow]

TERMINALS = """\
terminals:
  sh: {cmd: sh, cwd: .}
  tui: {cmd: sh tui.sh, cwd: .}
  key: {cmd: sh key.sh, cwd: .}
  osc: {cmd: sh osc.sh, cwd: .}
"""
# The fixture writes each into the server's config root, so no escape sequence
# has to survive YAML inside Python.
SCRIPTS = {
    "tui.sh": r"printf '\033[?1049h\033[2J\033[HTUI-SCREEN-UP'; sleep 100",
    "key.sh": r"stty raw -echo; head -c 1 | od -An -c",
    "osc.sh": r"printf '\033]52;c;cHduZWQ=\a\033]8;;javascript:alert(1)\033\\clickme\033]8;;\033\\\n'; sleep 100",
}


class TermServer(Server):
    def call(self, op: str, params: dict | None = None) -> dict:
        """One operation over a socket of its own, signed in with the token."""
        token = (self.root / ".aegis" / "state" / "token").read_text().strip()
        origin = f"http://127.0.0.1:{self.port}"
        with connect(f"ws://127.0.0.1:{self.port}/ws", origin=origin) as ws:
            ws.send(json.dumps({"t": "hello", "token": token, "proto": PROTO}))
            assert json.loads(ws.recv())["t"] == "welcome"
            ws.send(
                json.dumps({"t": "call", "id": 1, "op": op, "params": params or {}})
            )
            while (m := json.loads(ws.recv()))["t"] != "reply":
                pass
        assert "error" not in m, m["error"]
        return m["result"]


@pytest.fixture
def server(tmp_path: Path, fake_claude: str, fake_opencode: str):
    (tmp_path / ".aegis.yaml").write_text(CONFIG + TERMINALS)
    for name, body in SCRIPTS.items():
        (tmp_path / name).write_text(body + "\n")
    s = TermServer(tmp_path, fake_claude, fake_opencode).start()
    yield s
    s.stop()


def rows(page) -> str:
    return page.locator("#term-host .xterm-rows").inner_text()


def open_term(page, server, profile) -> str:
    card = server.call("term.spawn", {"profile": profile})
    page.goto(f"{server.url}#t={card['id']}")
    page.wait_for_selector("#term-host .xterm-rows")
    return card["id"]


def test_typing_in_direct_mode_reaches_the_shell_and_a_second_browser(server, browser):  # noqa: F811
    a, b = browser.new_page(), browser.new_page()
    tid = open_term(a, server, "sh")
    b.goto(f"{server.url}#t={tid}")
    a.click("#term-host")
    a.keyboard.type("echo hi-$((1+1))")
    a.keyboard.press("Enter")
    for page in (a, b):
        page.wait_for_function(
            "document.querySelector('#term-host .xterm-rows').innerText.includes('hi-2')"
        )


def test_the_box_sends_a_line_and_up_recalls_it(server, browser):  # noqa: F811
    page = browser.new_page()
    open_term(page, server, "sh")
    page.fill("#term-input", "echo boxed-$((3+4))")
    page.press("#term-input", "Enter")
    page.wait_for_function(
        "document.querySelector('#term-host .xterm-rows').innerText.includes('boxed-7')"
    )
    page.press("#term-input", "ArrowUp")
    assert page.input_value("#term-input") == "echo boxed-$((3+4))"


def test_a_fresh_browser_sees_a_tui_from_the_snapshot(server, browser):  # noqa: F811
    tid = server.call("term.spawn", {"profile": "tui"})["id"]
    time.sleep(0.5)  # the program prints at once; give the reader a tick or two
    page = browser.new_page()
    page.goto(f"{server.url}#t={tid}")
    page.wait_for_function(
        "document.querySelector('#term-host .xterm-rows')?.innerText.includes('TUI-SCREEN-UP')"
    )


def test_terminal_bytes_cannot_write_the_clipboard_or_run_a_javascript_link(
    server,
    browser,  # noqa: F811
):
    context = browser.new_context(permissions=["clipboard-read", "clipboard-write"])
    page = context.new_page()
    page.on("dialog", lambda d: pytest.fail(f"a terminal opened a dialog: {d.message}"))
    page.goto(server.url)
    page.evaluate("navigator.clipboard.writeText('before')")
    open_term(page, server, "osc")
    page.wait_for_function(
        "document.querySelector('#term-host .xterm-rows').innerText.includes('clickme')"
    )
    # xterm's screen lies over its rows and takes the mouse; force clicks there.
    # Scoped to the rows: the terminal's Fleet card can show the same last line.
    link = page.locator("#term-host .xterm-rows").get_by_text("clickme")
    link.click(modifiers=["Control"], force=True)
    link.click(force=True)
    assert page.evaluate("navigator.clipboard.readText()") == "before"
    assert len(context.pages) == 1


def test_on_a_phone_the_key_row_sends_esc_and_the_page_fits(server, browser):  # noqa: F811
    context = browser.new_context(
        viewport={"width": 390, "height": 844}, has_touch=True, is_mobile=True
    )
    page = context.new_page()
    open_term(page, server, "key")
    page.tap("#term-host")
    page.wait_for_selector("#term-keys:not([hidden])")
    page.tap("#term-keys [data-key=esc]")
    page.wait_for_function(
        "document.querySelector('#term-host .xterm-rows').innerText.includes('033')"
    )
    assert page.evaluate("document.documentElement.scrollWidth") <= 390


def test_a_terminal_tab_is_marked_and_closes(server, browser):  # noqa: F811
    page = browser.new_page()
    tid = open_term(page, server, "sh")
    tab = page.locator(f".tab[data-id='{tid}']")
    assert "▸_" in tab.inner_text()
    assert tab.locator(".dot.ready").count() == 1
    page.click("#term-close")
    page.wait_for_selector(f".tab[data-id='{tid}']", state="detached")


def test_the_picker_offers_the_shell_and_the_profiles(server, browser):  # noqa: F811
    page = browser.new_page()
    page.goto(f"{server.url}#new")
    group = page.locator("#sp-terms")
    assert group.locator("[data-profile]").all_inner_texts()[0].startswith("shell")
    group.locator("[data-profile=sh]").click()
    page.wait_for_selector("#term-host .xterm-rows")
    assert page.url.split("#")[1].startswith("t=t-")


def test_the_filter_shows_only_terminals_and_is_remembered(server, browser):  # noqa: F811
    server.call("session.spawn", {"agent": "opus"})
    server.call("term.spawn", {"profile": "sh"})
    page = browser.new_page()
    page.goto(server.url)
    page.wait_for_selector("#cards .card.term")  # the Fleet has drawn
    page.click(".kinds [data-kind=terminals]")
    assert (
        page.locator("#cards .card").count()
        == page.locator("#cards .card.term").count()
        == 1
    )
    page.reload()
    page.wait_for_selector("#cards .card.term")
    assert page.locator("#cards .card").count() == 1
    assert page.locator(".kinds [data-kind=terminals][aria-checked=true]").count() == 1


def test_a_closed_terminal_is_in_the_archive_and_replays(server, browser):  # noqa: F811
    tid = server.call("term.spawn", {"profile": "tui"})["id"]
    server.call("term.close", {"id": tid})
    page = browser.new_page()
    page.goto(server.url)
    row = page.locator(f"#arch-terms [data-id='{tid}']")
    row.locator("button", has_text="Read").click()
    page.wait_for_function(
        "document.querySelector('#term-host .xterm-rows')?.innerText.includes('TUI-SCREEN-UP')"
    )
    assert page.locator("#term-close").is_hidden()
