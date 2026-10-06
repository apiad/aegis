"""The client as a person uses it: headless Chromium against ``aegis serve``
started as a real process, with the fake claude behind it."""

import json
import re
import socket
import subprocess
import sys
import time
from pathlib import Path

import pytest

pytestmark = [pytest.mark.browser, pytest.mark.slow]

playwright = pytest.importorskip("playwright.sync_api")

# The browser logs the refused reconnects while a test restarts the server.
EXPECTED_CONSOLE = ("ERR_CONNECTION_REFUSED",)


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class Server:
    def __init__(self, root: Path, claude: str):
        self.root, self.claude, self.port = root, claude, _free_port()
        self.proc = None
        self.url = ""

    def start(self) -> "Server":
        self.proc = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "aegis",
                "serve",
                "--root",
                str(self.root),
                "--port",
                str(self.port),
                "--claude",
                self.claude,
                "--log-level",
                "info",
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )
        deadline = time.monotonic() + 15
        self.url = ""
        while not self.url and time.monotonic() < deadline:
            line = self.proc.stdout.readline()
            if m := re.search(r"open (http://\S+)", line):
                self.url = m.group(1)
        assert self.url, "aegis serve printed no URL"
        for _ in range(100):
            try:
                socket.create_connection(("127.0.0.1", self.port), timeout=0.1).close()
                return self
            except OSError:
                time.sleep(0.05)
        raise AssertionError("aegis serve is not listening")

    output = ""

    def stop(self) -> None:
        if self.proc and self.proc.poll() is None:
            self.proc.terminate()
            out, _ = self.proc.communicate(timeout=10)
            self.output += out or ""


@pytest.fixture
def server(tmp_path: Path, fake_claude: str):
    (tmp_path / ".aegis.yaml").write_text(
        "default_agent: opus\nagents:\n  opus: {model: opus, effort: high, permission: full}\n"
    )
    s = Server(tmp_path, fake_claude).start()
    yield s
    s.stop()


@pytest.fixture
def browser():
    with playwright.sync_playwright() as p:
        b = p.chromium.launch()
        yield b
        b.close()


def new_page(browser, errors: list):
    pg = browser.new_page(viewport={"width": 1280, "height": 800})
    pg.on("pageerror", lambda e: errors.append(str(e)))
    pg.on(
        "console",
        lambda m: (
            m.type == "error"
            and not any(x in m.text for x in EXPECTED_CONSOLE)
            and errors.append(m.text)
        ),
    )
    pg.on("dialog", lambda d: d.accept())
    return pg


@pytest.fixture
def page(browser):
    errors: list = []
    pg = new_page(browser, errors)
    pg.errors = errors
    yield pg


def turns_done(pg, n: int) -> None:
    pg.wait_for_function(
        "n => [...document.querySelectorAll('.row.sys .body, .row.error .body')]"
        ".filter(b => /^(done in|interrupted)/.test(b.textContent)).length >= n",
        arg=n,
        timeout=8000,
    )


def spawn(pg, prompt: str | None = None) -> str:
    pg.click("#tab-add")
    pg.wait_for_selector("#a2[data-view=spawn]")
    pg.click("#sp-go")
    pg.wait_for_selector("#a2[data-view=session]")
    if prompt:
        pg.fill("#input", prompt)
        pg.press("#input", "Enter")
        turns_done(pg, 1)
    return pg.evaluate("location.hash.slice(3)")


def tab_ids(pg) -> list[str]:
    return pg.evaluate(
        "[...document.querySelectorAll('#tablist .tab')].map(t => t.dataset.id)"
    )


def test_a_session_from_spawn_to_close(server, page):
    page.goto(server.url)
    assert "token=" not in page.url, "the token stays out of the address bar"
    page.wait_for_selector("#a2[data-view=fleet]")
    spawn(page)

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
    page.wait_for_selector("#a2[data-view=fleet]")
    page.wait_for_selector("#arch-list tr[data-id]")
    assert tab_ids(page) == []
    assert page.errors == []


def test_a_prompt_sent_mid_turn_shows_pending_until_read(server, page):
    page.goto(server.url)
    page.wait_for_selector("#a2[data-view=fleet]")
    spawn(page)
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


def test_tabs_reorder_per_browser_and_survive_a_reload(server, browser, page):
    page.goto(server.url)
    page.wait_for_selector("#a2[data-view=fleet]")
    a, b, c = spawn(page, "alpha"), spawn(page, "beta"), spawn(page, "gamma")
    assert tab_ids(page) == [a, b, c]
    page.locator(f"#tablist .tab[data-id='{c}']").drag_to(
        page.locator(f"#tablist .tab[data-id='{a}']")
    )
    assert tab_ids(page) == [c, a, b]
    page.reload()
    page.wait_for_selector("#tablist .tab")
    assert tab_ids(page) == [c, a, b]
    page.keyboard.press("Alt+1")
    page.wait_for_function("id => location.hash === '#s=' + id", arg=c)

    # A second browser has its own order: the server's.
    errors: list = []
    other = new_page(browser, errors)
    other.goto(server.url)
    other.wait_for_selector("#tablist .tab")
    assert tab_ids(other) == [a, b, c]

    # A stale order naming gone sessions, missing a new one.
    page.evaluate(
        "ids => localStorage.setItem('aegis.tabs', JSON.stringify(ids))",
        ["gone-1", b, "gone-2"],
    )
    page.reload()
    page.wait_for_selector("#tablist .tab")
    assert tab_ids(page) == [b, a, c]
    assert json.loads(page.evaluate("localStorage.getItem('aegis.tabs')")) == [b, a, c]
    assert page.errors == [] and errors == []


def test_a_restart_brings_tabs_back_stopped_and_a_prompt_resumes(server, page):
    frames: list[str] = []

    def watch(ws):
        ws.on("framesent", lambda f: frames.append(f"> {f}"[:300]))
        ws.on("framereceived", lambda f: frames.append(f"< {f}"[:300]))
        ws.on("close", lambda _: frames.append("closed"))

    page.on("websocket", watch)
    page.goto(server.url)
    page.wait_for_selector("#a2[data-view=fleet]")
    a = spawn(page, "remember PELICAN")
    b = spawn(page)
    page.fill("#input", "/sleep 30")
    page.press("#input", "Enter")
    page.wait_for_selector(".row.tool.running")

    server.stop()
    server.start()
    page.wait_for_function(
        "document.getElementById('conn-dot').className.includes('ready')", timeout=15000
    )
    page.wait_for_function(
        "() => [...document.querySelectorAll('#tablist .tab')].every(t => t.classList.contains('stopped'))",
        timeout=5000,
    )
    assert set(tab_ids(page)) == {a, b}
    page.wait_for_selector(".row.sys .body >> text=the server stopped during a turn")

    page.click(f"#tablist .tab[data-id='{a}']")
    page.wait_for_function("id => location.hash === '#s=' + id", arg=a)
    assert "resumes" in page.get_attribute("#input", "placeholder")
    page.fill("#input", "/recall")
    page.press("#input", "Enter")
    try:
        page.wait_for_selector(
            ".row.prose .body >> text=earlier: remember PELICAN", timeout=8000
        )
    except Exception:
        server.stop()
        metas = {
            p.name: p.read_text()
            for p in (server.root / ".aegis" / "state" / "sessions").glob("*.json")
        }
        raise AssertionError(
            "no recall after the restart.\n"
            f"transcript tail: {page.inner_text('#entries')[-1500:]}\n"
            f"send error: {page.inner_text('#send-error')!r} side error: {page.inner_text('#side-error')!r}\n"
            f"page errors: {page.errors}\nmetas: {metas}\nserver output: {server.output[-4000:]}\n"
            + "last frames:\n"
            + "\n".join(frames[-40:])
        ) from None
    assert page.errors == []


def test_close_in_one_browser_removes_the_tab_in_another(server, browser, page):
    page.goto(server.url)
    page.wait_for_selector("#a2[data-view=fleet]")
    a = spawn(page, "shared one")
    errors: list = []
    other = new_page(browser, errors)
    other.goto(server.url + f"#s={a}")
    other.wait_for_selector("#a2[data-view=session]")
    page.click("#close")
    other.wait_for_selector("#a2[data-view=fleet]", timeout=5000)
    assert tab_ids(other) == []
    assert errors == [] and page.errors == []


def test_reopen_from_the_archive_and_rename(server, page):
    page.goto(server.url)
    page.wait_for_selector("#a2[data-view=fleet]")
    a = spawn(page, "an old conversation")
    page.click("#s-title")
    page.fill("#s-title input", "Old talk")
    page.press("#s-title input", "Enter")
    page.wait_for_selector("#tablist .tab .tname >> text=Old talk")
    page.click("#s-handle")
    page.fill("#s-handle input", "old-talk")
    page.press("#s-handle input", "Enter")
    page.wait_for_selector("#tablist .tab .srv >> text=old-talk")
    page.click("#close")
    page.wait_for_selector(f"#arch-list tr[data-id='{a}']")
    page.fill("#arch-q", "nothing like it")
    page.wait_for_selector("#arch-list .empty")
    page.fill("#arch-q", "old")
    page.locator(f"#arch-list tr[data-id='{a}'] button", has_text="Read").click()
    page.wait_for_selector("#a2[data-mode=read]")
    assert not page.is_visible(".composer") and not page.is_visible("#close")
    page.click("#reopen")
    page.wait_for_selector("#a2[data-view=session][data-mode=live]")
    assert tab_ids(page) == [a]
    assert page.errors == []


def test_a_wrong_token_says_so(server, page):
    page.goto(re.sub(r"token=[^&]+", "token=wrong", server.url))
    page.wait_for_function(
        "document.getElementById('boot-text').textContent.includes('refused')"
    )


def test_text_typed_right_after_switching_tabs_is_kept(server, page):
    page.goto(server.url)
    page.wait_for_selector("#a2[data-view=fleet]")
    a = spawn(page, "first")
    spawn(page, "second")
    kept = page.evaluate(
        """async (a) => {
            document.querySelector(`#tablist .tab[data-id='${a}']`).click();
            document.getElementById('input').value = 'typed at once';
            await new Promise((r) => setTimeout(r, 100));
            return document.getElementById('input').value;
        }""",
        a,
    )
    assert kept == "typed at once"
    assert page.errors == []


def test_a_monitor_shows_in_the_sidebar_and_its_wake_arrives_as_an_inbox_row(
    server, page, tmp_path
):
    page.goto(server.url)
    page.wait_for_selector("#a2[data-view=fleet]")
    spawn(page)
    flag = tmp_path / "browser-flag"
    args = json.dumps(
        {"description": "wait for the flag", "done": f"test -f {flag}", "interval_s": 1}
    )
    page.fill("#input", f"/mcp monitor_start {args}")
    page.press("#input", "Enter")
    page.wait_for_selector("#s-mon-sec:not([hidden]) .mon >> text=wait for the flag")
    assert page.inner_text(".row.tool .tn") == "monitor_start"
    page.click("#tab-fleet")
    page.wait_for_selector(".card .mons >> text=1 monitor")
    page.locator(".card").first.click()
    flag.touch()
    page.wait_for_selector(".row.inbox .from >> text=monitor:", timeout=10000)
    page.wait_for_selector("#s-mon-sec[hidden]", state="attached")
    assert page.errors == []
