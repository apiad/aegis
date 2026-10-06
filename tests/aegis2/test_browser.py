"""The client as a person uses it: headless Chromium against ``aegis2 serve``
started as a real process, with the fake claude behind it."""

import re
import socket
import subprocess
import sys
import time
from pathlib import Path

import pytest

pytestmark = [pytest.mark.browser, pytest.mark.slow]

playwright = pytest.importorskip("playwright.sync_api")


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture
def server(tmp_path: Path, fake_claude: str):
    (tmp_path / ".aegis.yaml").write_text(
        "default_agent: opus\nagents:\n  opus: {model: opus, effort: high, permission: full}\n"
    )
    port = _free_port()
    proc = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "aegis2",
            "serve",
            "--root",
            str(tmp_path),
            "--port",
            str(port),
            "--claude",
            fake_claude,
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    url = None
    deadline = time.monotonic() + 15
    while url is None and time.monotonic() < deadline:
        line = proc.stdout.readline()
        if m := re.search(r"open (http://\S+)", line):
            url = m.group(1)
    assert url, "aegis2 serve printed no URL"
    for _ in range(100):  # wait for the port
        try:
            socket.create_connection(("127.0.0.1", port), timeout=0.1).close()
            break
        except OSError:
            time.sleep(0.05)
    yield url
    proc.terminate()
    proc.wait(10)


@pytest.fixture
def page():
    with playwright.sync_playwright() as p:
        browser = p.chromium.launch()
        pg = browser.new_page(viewport={"width": 1280, "height": 800})
        pg.errors = []
        pg.on("pageerror", lambda e: pg.errors.append(str(e)))
        pg.on("console", lambda m: m.type == "error" and pg.errors.append(m.text))
        pg.on("dialog", lambda d: d.accept())
        yield pg
        browser.close()


def turns_done(pg, n: int) -> None:
    pg.wait_for_function(
        "n => [...document.querySelectorAll('.row.sys .body, .row.error .body')]"
        ".filter(b => /^(done in|interrupted)/.test(b.textContent)).length >= n",
        arg=n,
        timeout=8000,
    )


def test_a_session_from_spawn_to_close(server, page):
    page.goto(server)
    assert "token=" not in page.url, "the token stays out of the address bar"
    page.wait_for_selector("#a2[data-view=spawn]")
    page.click("#sp-go")
    page.wait_for_selector("#a2[data-view=session]")

    page.fill("#input", "Hello **there**")
    page.press("#input", "Enter")
    turns_done(page, 1)
    assert "<strong>there</strong>" in page.inner_html(".row.prose .body")

    page.fill("#input", "/fail")
    page.press("#input", "Enter")
    turns_done(page, 2)
    assert page.is_visible(".row.tool.err pre.out")

    page.fill("#input", "/sleep 5")
    page.press("#input", "Enter")
    page.wait_for_selector(".row.tool.running")
    page.keyboard.press("Escape")
    turns_done(page, 3)
    assert page.locator(".row.tool.err .tr2").last.inner_text() == "interrupted"

    ids = page.evaluate(
        "[...document.querySelectorAll('#entries .row')].map(r => r.dataset.id)"
    )
    page.reload()
    page.wait_for_selector("#a2[data-view=session]")
    page.wait_for_function(
        "n => document.querySelectorAll('#entries .row').length === n", arg=len(ids)
    )
    assert (
        page.evaluate(
            "[...document.querySelectorAll('#entries .row')].map(r => r.dataset.id)"
        )
        == ids
    )

    bg = "getComputedStyle(document.getElementById('a2')).getPropertyValue('--bg').trim()"
    before = page.evaluate(bg)
    page.select_option("#theme", "logbook")
    assert page.evaluate(bg) != before == "#11100e"

    page.click("#close")
    page.wait_for_selector("#a2[data-view=spawn]")
    assert page.errors == []


def test_a_prompt_sent_mid_turn_shows_pending_until_read(server, page):
    page.goto(server)
    page.wait_for_selector("#a2[data-view=spawn]")
    page.click("#sp-go")
    page.wait_for_selector("#a2[data-view=session]")
    page.fill("#input", "/sleep 1")
    page.press("#input", "Enter")
    page.wait_for_selector(".row.tool.running")
    page.fill("#input", "steer")
    page.press("#input", "Enter")
    page.wait_for_selector(".row.user.pending")
    turns_done(page, 1)
    users = page.evaluate(
        "[...document.querySelectorAll('.row.user')].map(r => [r.className, r.textContent])"
    )
    assert [u[0] for u in users] == ["row user ok", "row user ok"]
    assert users[1][1].endswith("steer")
    assert page.errors == []


def test_a_wrong_token_says_so(server, page):
    page.goto(re.sub(r"token=[^&]+", "token=wrong", server))
    page.wait_for_function(
        "document.getElementById('boot-text').textContent.includes('refused')"
    )
