"""The client as a person uses it: headless Chromium against ``aegis serve``
started as a real process, with the fake claude behind it."""

import json
import os
import re
import socket
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote

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
        self.releases = root / "pypi.json"
        self.env: dict[str, str] = {}
        self.proc = None
        self.url = ""

    def environ(self) -> dict[str, str]:
        # Off the network: the latest release is whatever this file says. Off
        # the desktop: a click on Open natively must not launch real apps.
        hidden = ("DISPLAY", "WAYLAND_DISPLAY", "AEGIS_OPENER")
        env = {k: v for k, v in os.environ.items() if k not in hidden}
        return env | {"AEGIS_RELEASES_URL": self.releases.as_uri()} | self.env

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
            env=self.environ(),
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


CONFIG = (
    "default_agent: opus\nagents:\n  opus: {harness: claude-code, model: opus, effort: high, permission: full}\n"
    "  deepseek: {provider: opencode, model: opencode-go/deepseek-v4-pro, effort: high, permission: full}\n"
)


@pytest.fixture
def server(tmp_path: Path, fake_claude: str):
    (tmp_path / ".aegis.yaml").write_text(CONFIG)
    s = Server(tmp_path, fake_claude).start()
    yield s
    s.stop()


def seed_quota() -> None:
    """A Claude reading fresh enough that the server adopts it without asking,
    and an OpenCode Go reading 14 minutes old behind a live 429 backoff. The
    paths are conftest's temp ones, inherited by `aegis serve`."""
    now = time.time()

    def at(s):
        return datetime.fromtimestamp(now + s, timezone.utc).isoformat()

    cache = Path(os.environ["AEGIS_QUOTA_CACHE"])
    cache.mkdir(parents=True, exist_ok=True)
    Path(os.environ["CLAUDE_CREDS"]).write_text(
        json.dumps({"claudeAiOauth": {"accessToken": "test-token"}})
    )
    Path(os.environ["OPENCODE_AUTH"]).write_text(
        json.dumps({"opencode-go": {"key": "test-key"}})
    )
    window = lambda kind, pct, s: {  # noqa: E731
        "kind": kind,
        "percent": pct,
        "severity": "normal",
        "resets_at": at(s),
        "is_active": True,
    }
    (cache / "claude.json").write_text(
        json.dumps(
            {
                "fetched_wall": now,
                "backoff_until_wall": 0.0,
                "windows": [
                    window("session", 71.0, 3 * 3600 + 6 * 60),
                    window("weekly_all", 47.0, 2 * 86400),
                ],
            }
        )
    )
    (cache / "opencode-go.json").write_text(
        json.dumps(
            {
                "fetched_wall": now - 840,
                "backoff_until_wall": now + 240,
                "windows": [window("rolling", 64.0, 3480)],
            }
        )
    )


@pytest.fixture
def quota_server(tmp_path: Path, fake_claude: str):
    (tmp_path / ".aegis.yaml").write_text(
        "default_agent: opus\nagents:\n  opus: {harness: claude-code, model: opus, effort: high, permission: full}\n"
    )
    seed_quota()
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
    pg.wait_for_function("document.querySelector('#sp-agent').value !== ''")
    if prompt:
        pg.fill("#sp-text", prompt)
        pg.press("#sp-text", "Enter")
    else:
        pg.click("#sp-go")
    pg.wait_for_selector("#a2[data-view=session]")
    if prompt:
        turns_done(pg, 1)
    return pg.evaluate("location.hash.slice(3)")


def test_the_composer_overrides_a_chip_resets_it_and_spawns_with_the_first_message(
    server, page
):
    page.goto(server.url)
    page.click("#tab-add")
    page.wait_for_function("document.querySelector('#sp-agent').value === 'opus'")
    assert page.input_value("#sp-model") == "opus"
    assert page.is_hidden("#sp-reset")
    # A select sizes to its longest option; the chips must still sit on one row.
    tops = page.eval_on_selector_all(
        "#spawn .pick, #sp-go", "els => els.map(e => e.getBoundingClientRect().top)"
    )
    assert max(tops) - min(tops) < 4, tops

    page.fill("#sp-model", "sonnet")
    assert page.inner_text("#sp-agent option:checked") == "opus*"
    assert "diff" in page.get_attribute("#sp-model", "class")
    page.click("#sp-reset")
    assert page.input_value("#sp-model") == "opus"
    assert page.inner_text("#sp-agent option:checked") == "opus"
    assert page.is_hidden("#sp-reset")

    page.fill("#sp-text", "/argv")
    page.press("#sp-text", "Shift+Enter")
    assert page.evaluate("document.querySelector('#a2').dataset.view") == "spawn"
    page.fill("#sp-text", "/argv")
    page.select_option("#sp-effort", "max")
    page.press("#sp-text", "Enter")
    page.wait_for_selector("#a2[data-view=session]")
    turns_done(page, 1)
    text = page.inner_text("#entries")
    assert "/argv" in text and '"--effort", "max"' in text

    page.click("#tab-fleet")
    page.wait_for_selector("#a2[data-view=fleet]")
    assert page.inner_text(".card .ln b") == "opus*"
    page.click("#tab-add")
    page.wait_for_function("document.querySelector('#sp-agent').value === 'opus'")
    assert page.input_value("#sp-effort") == "high", "a spawn clears the overrides"
    assert page.input_value("#sp-text") == ""
    assert page.errors == []


def test_enter_in_the_model_or_cwd_field_moves_to_the_message_and_spawns_nothing(
    server, page
):
    page.goto(server.url)
    page.click("#tab-add")
    page.wait_for_function("document.querySelector('#sp-agent').value === 'opus'")
    page.fill("#sp-text", "half written")
    for field in ("#sp-model", "#sp-cwd"):
        page.focus(field)
        page.press(field, "Enter")
        assert page.evaluate("document.activeElement.id") == "sp-text"
    page.wait_for_timeout(300)  # a spawn would have switched the view by now
    assert page.evaluate("document.querySelector('#a2').dataset.view") == "spawn"
    assert page.evaluate("document.querySelectorAll('#tablist .tab').length") == 0


def test_a_failed_spawn_keeps_the_text_and_says_why(server, page):
    page.goto(server.url)
    page.click("#tab-add")
    page.wait_for_function("document.querySelector('#sp-agent').value === 'opus'")
    page.fill("#sp-cwd", "/")
    page.fill("#sp-text", "keep me")
    page.press("#sp-text", "Enter")
    page.wait_for_function("document.querySelector('#sp-error').textContent !== ''")
    assert "outside" in page.inner_text("#sp-error")
    assert page.input_value("#sp-text") == "keep me"
    assert page.evaluate("document.querySelector('#a2').dataset.view") == "spawn"


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


# A slow runner paints late: a frame 300 ms out makes any redraw left to the
# next frame visible to the test (#161's CI failure).
SLOW_FRAMES = "const raf = window.requestAnimationFrame; window.requestAnimationFrame = (f) => setTimeout(() => raf(f), 300);"


@pytest.mark.parametrize("frames", ["normal", "slow"])
def test_reopen_from_the_archive_and_rename(server, page, frames):
    if frames == "slow":
        page.add_init_script(SLOW_FRAMES)
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
    # An archived meta carries no attention: the state alone, no glyph.
    assert page.inner_text("#s-status").strip() == "archived"
    assert page.locator("#s-status svg").count() == 0
    assert not any(
        c.startswith("at-") for c in page.get_attribute("#s-status", "class").split()
    )
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


def box_fits(pg) -> bool:
    """The message box shows its whole content: it was measured while visible."""
    return pg.evaluate(
        "(() => { const i = document.getElementById('input');"
        " return i.clientHeight > 0 && i.clientHeight >= i.scrollHeight; })()"
    )


def test_the_message_box_opens_at_full_height(server, page):
    page.goto(server.url)
    page.wait_for_selector("#a2[data-view=fleet]")
    a = spawn(page)
    assert box_fits(page), "opened from the new-tab form"
    page.click("#tab-fleet")
    page.click(f".card[data-id='{a}']")
    page.wait_for_selector("#a2[data-view=session]")
    assert box_fits(page), "opened from the Fleet"
    page.reload()
    page.wait_for_selector("#a2[data-view=session]")
    assert box_fits(page), "booted straight into the session"
    assert page.errors == []


def test_send_sits_inside_the_message_box_and_sends(server, page):
    page.goto(server.url)
    page.wait_for_selector("#a2[data-view=fleet]")
    spawn(page)
    box = page.locator(".composer .box").bounding_box()
    btn = page.locator("#send").bounding_box()
    assert box["x"] <= btn["x"] and btn["x"] + btn["width"] <= box["x"] + box["width"]
    assert box["y"] <= btn["y"] and btn["y"] + btn["height"] <= box["y"] + box["height"]
    page.fill("#input", "via the button")
    page.click("#send")
    turns_done(page, 1)
    assert page.input_value("#input") == ""
    assert page.errors == []


def test_a_monitor_shows_in_the_sidebar_and_its_wake_arrives_as_an_inbox_row(
    server, page, tmp_path
):
    page.goto(server.url)
    page.wait_for_selector("#a2[data-view=fleet]")
    spawn(page)
    flag = tmp_path / "browser-flag"
    args = json.dumps(
        {
            "description": "wait for the flag",
            "done": f"test -f {flag}",
            "progress": None,
            "interval_s": 1,
        }
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


def test_a_monitor_with_no_reading_animates_and_one_with_a_reading_fills(
    server, page, tmp_path
):
    pct = tmp_path / "pct"
    page.goto(server.url)
    page.wait_for_selector("#a2[data-view=fleet]")
    spawn(page)
    for args in (
        {"description": "no command", "done": "false", "progress": None},
        {
            "description": "a pct file",
            "done": "false",
            "progress": f"cat {pct}",
            "interval_s": 1,
        },
    ):
        page.fill("#input", f"/mcp monitor_start {json.dumps(args)}")
        page.press("#input", "Enter")
        page.wait_for_selector(f"#s-monitors .mon >> text={args['description']}")
    mons = page.locator("#s-monitors .mon")
    # no command, and a command with no reading yet: both running, amount unknown
    assert mons.nth(0).locator(".bar.indet").count() == 1
    assert mons.nth(1).locator(".bar.indet").count() == 1
    pct.write_text("42\n")
    page.wait_for_selector("#s-monitors .mon:nth-child(2) .kv >> text=42%")
    measured = mons.nth(1)
    assert measured.locator(".bar.indet").count() == 0
    assert measured.locator(".bar i").get_attribute("style") == "width: 42%;"
    assert mons.nth(0).locator(".bar.indet").count() == 1
    assert page.errors == []


def arm(page, **args):
    page.fill("#input", f"/mcp monitor_start {json.dumps(args)}")
    page.press("#input", "Enter")
    page.wait_for_selector(f"#s-monitors .mon >> text={args['description']}")


def test_hovering_a_monitor_opens_its_card_and_it_stays_open_as_readings_arrive(
    server, page, tmp_path
):
    """The sidebar redraws on every session patch; the card must not close, or
    lose its place, each time one of its monitor's readings arrives (#174)."""
    pct = tmp_path / "pct"
    pct.write_text("0\n")
    page.goto(server.url)
    page.wait_for_selector("#a2[data-view=fleet]")
    spawn(page)
    arm(
        page,
        description="a pct file",
        done="false",
        progress=f"cat {pct}",
        interval_s=1,
    )
    page.hover("#s-monitors .mon")
    page.wait_for_selector("#mcard.show")
    card = page.inner_text("#mcard")
    assert "a pct file" in card and f"cat {pct}" in card
    assert "not yet" in card and "no progress" not in card
    # Count every close: a card that closes and reopens on each redraw ends in the
    # same state as one that stayed open, and flickers in between.
    page.evaluate(
        """() => { window.closes = 0; const c = document.getElementById("mcard");
        new MutationObserver(() => { if (!c.classList.contains("show")) window.closes++; })
          .observe(c, {attributes: true, attributeFilter: ["class"]}); }"""
    )
    page.wait_for_selector("#mcard.show .big .p >> text=0")
    pct.write_text("20\n")
    page.wait_for_selector("#mcard.show .big .p >> text=20")
    pct.write_text("40\n")
    page.wait_for_selector("#mcard.show .big .p >> text=40")
    assert page.locator("#mcard .chart svg .dot").count() == 2
    assert "ETA" in page.inner_text("#mcard .big .e")
    assert "since progress first moved" in page.inner_text("#mcard .chart .basis")
    assert "40% · ~" in page.inner_text("#s-monitors .mon .kv")
    assert page.evaluate("window.closes") == 0
    page.keyboard.press("Escape")
    page.wait_for_selector("#mcard:not(.show)", state="attached")
    assert page.errors == []


def test_a_monitor_whose_check_cannot_run_is_marked_and_its_card_says_why(server, page):
    page.goto(server.url)
    page.wait_for_selector("#a2[data-view=fleet]")
    spawn(page)
    arm(
        page,
        description="never passes",
        done="no-such-aegis-cmd --status",
        progress=None,
        interval_s=1,
    )
    page.wait_for_selector("#s-monitors .mon.bad >> text=check fails")
    page.hover("#s-monitors .mon")
    page.wait_for_selector("#mcard.show .ck.bad")
    bad = page.inner_text("#mcard .ck.bad")
    assert "exit 127" in bad and "command not found" in bad
    assert "no-such-aegis-cmd" in page.inner_text("#mcard .ck.bad .err")
    assert "no progress command" in page.inner_text("#mcard .big")
    assert page.errors == []


def test_the_running_build_and_the_latest_release_show_in_the_top_bar_and_sidebar(
    server, page
):
    from aegis.version import running

    run = running()
    server.releases.write_text('{"info": {"version": "99.0.0"}}')
    page.goto(server.url)
    page.wait_for_selector("#ver-top:not([hidden])")
    shown = run["commit"][:7] if run["dev"] and run["commit"] else run["version"]
    top = page.inner_text("#ver-top")
    assert top.startswith(shown)
    assert ("dev" in top) == run["dev"]
    assert "99.0.0" in page.get_attribute("#ver-top", "title")

    spawn(page)
    page.wait_for_selector("#ver-sec:not([hidden])")
    assert page.inner_text("#ver-head") == f"aegis on {socket.gethostname()}"
    assert page.inner_text("#ver-run").startswith(shown)
    assert page.inner_text("#ver-latest").startswith("99.0.0")
    if not run["dev"]:
        assert "update" in page.inner_text("#ver-latest")
    assert page.errors == []


def test_the_fleet_band_and_the_sidebar_show_quota_and_the_host(quota_server, page):
    page.goto(quota_server.url)
    five = page.locator("#band .gauge[data-kind=session]")
    five.wait_for()
    # 71% with 38% of the window gone: on pace for about 187%, so red.
    assert "critical" in five.get_attribute("class")
    assert "71%" in five.inner_text()
    assert re.search(r"→ 18\d%", five.inner_text())
    assert five.locator(".tick").count() == 1
    week = page.locator("#band .gauge[data-kind=weekly_all]")
    assert "normal" in week.get_attribute("class") and "→" not in week.inner_text()
    stale = page.locator("#band .gauge[data-kind=rolling]")
    assert "stale" in stale.get_attribute("class")
    assert stale.locator(".tick").count() == 0
    assert "retrying in" in page.inner_text("#band-quota-age")
    page.wait_for_selector("#band-host .gauge")
    assert "CPU" in page.inner_text("#band-host")

    spawn(page)
    page.wait_for_selector("#s-quota .qrow.critical")
    assert "Claude 5 hours" in page.inner_text("#s-quota")
    assert "OpenCode" not in page.inner_text("#s-quota")
    assert page.errors == []


def test_quota_rows_survive_session_updates_so_their_tooltip_stays(
    quota_server, browser, page
):
    """A rebuilt row loses its hover tooltip. Sessions patch up to four times a
    second per working agent and quota about once a minute, so a sessions
    patch must not rebuild the quota rows (final review of #146)."""
    page.goto(quota_server.url)
    row = "#band .gauge[data-kind=session]"
    page.wait_for_selector(row)
    page.evaluate(f"document.querySelector('{row}').__kept = true")
    other = new_page(browser, [])
    other.goto(quota_server.url)
    other.wait_for_selector("#a2[data-view=fleet]")
    spawn(other, "hello")
    page.wait_for_selector("#cards .card")
    assert page.evaluate(f"document.querySelector('{row}').__kept === true")

    page.click("#cards .card")
    page.wait_for_selector("#s-quota .qrow")
    page.evaluate("document.querySelector('#s-quota .qrow').__kept = true")
    page.fill("#input", "again")
    page.press("#input", "Enter")
    turns_done(page, 2)
    assert page.evaluate("document.querySelector('#s-quota .qrow').__kept === true")
    assert page.errors == []


# A 1x1 PNG, so the preview has a real image to decode.
PNG = bytes.fromhex(
    "89504e470d0a1a0a0000000d4948445200000001000000010806000000"
    "1f15c4890000000d49444154789c6360f8ffff3f0005fe02fea7d6a4"
    "f50000000049454e44ae426082"
)


def test_a_sent_file_previews_in_the_transcript_with_open_and_download(server, page):
    (server.root / "dot.png").write_bytes(PNG)
    # The report's script reports whether it can reach the client's storage.
    (server.root / "report.html").write_text(
        '<p id="s">no script</p><script>let r;'
        'try { sessionStorage.length; r = "storage open"; }'
        'catch (e) { r = "storage refused"; }'
        'document.getElementById("s").textContent = r;</script>'
    )
    (server.root / "notes.md").write_text("# Notes\n\n- one\n- two\n")
    page.goto(server.url)
    page.wait_for_selector("#a2[data-view=fleet]")
    spawn(page)
    sends = (("dot.png", "The **chart**"), ("report.html", None), ("notes.md", None))
    for n, (path, caption) in enumerate(sends, 1):
        args = {"path": path} | ({"caption": caption} if caption else {})
        page.fill("#input", f"/mcp file_send {json.dumps(args)}")
        page.press("#input", "Enter")
        turns_done(page, n)  # one at a time: prompts sent mid-turn share a turn

    img = page.locator(".row.file img")
    img.wait_for()
    page.wait_for_function("document.querySelector('.row.file img').naturalWidth > 0")
    first = page.locator(".row.file").first
    assert "chart" in first.locator(".cap strong").inner_text()
    href = first.locator("a.open").get_attribute("href")
    assert first.locator("a.open").get_attribute("target") == "_blank"
    assert first.locator("a.dl").get_attribute("href") == href + "?download=1"
    assert page.request.get(server.url.split("/?")[0] + href).body() == PNG

    frame = page.locator(".row.file iframe")
    assert frame.get_attribute("sandbox") == "allow-scripts"
    report = page.frame_locator(".row.file iframe").locator("#s")
    report.filter(has_text="storage").wait_for()
    assert report.inner_text() == "storage refused"
    assert page.locator(".row.file .md h1").inner_text() == "Notes"
    assert page.errors == []


def test_open_natively_shows_only_on_the_servers_desktop_and_opens_the_copy(
    server, browser, page
):
    (server.root / "dot.png").write_bytes(PNG)
    page.goto(server.url)
    page.wait_for_selector("#a2[data-view=fleet]")
    sid = spawn(page)
    page.fill("#input", f"/mcp file_send {json.dumps({'path': 'dot.png'})}")
    page.press("#input", "Enter")
    turns_done(page, 1)
    native = page.locator(".row.file .fbar .native")
    assert native.count() == 1 and not native.is_visible(), "a headless server"

    # The same server with an opener: the button shows and opens the copy.
    marker = server.root / "opened"
    opener = server.root / "opener.sh"
    opener.write_text(f'#!/bin/sh\nprintf "%s" "$1" > "{marker}"\n')
    opener.chmod(0o755)
    server.stop()
    server.env = {"AEGIS_OPENER": str(opener)}
    server.start()
    page.goto(f"{server.url}#s={sid}")
    page.wait_for_selector(".row.file .fbar .native", state="visible")
    page.click(".row.file .fbar .native")
    for _ in range(100):
        if marker.exists():
            break
        time.sleep(0.02)
    assert marker.read_text().endswith("/dot.png")
    assert (server.root / ".aegis") in Path(marker.read_text()).parents
    assert page.errors == []


VERDICT_BOX = """sel => {
  const r = [...document.querySelectorAll('.row.tool')].pop();
  const box = q => r.querySelector(q).getBoundingClientRect().width;
  const v = r.querySelector('.tr2');
  return {line: box('.line'), verdict: box('.tr2'), cut: v.scrollWidth > v.clientWidth};
}"""


def test_a_bash_verdict_takes_the_room_its_description_leaves(server, page):
    """A long verdict is cut only when the row is full: beside a short
    description it shows whole, and against a long one it keeps most of it."""
    page.goto(server.url)
    page.wait_for_selector("#a2[data-view=fleet]")
    spawn(page)
    verdict = "7 of 12 logs hold ERROR: " + ", ".join(f"node-{i}.log" for i in range(5))
    page.fill("#input", f"/bash Scan logs => {verdict}")
    page.press("#input", "Enter")
    turns_done(page, 1)
    short = page.evaluate(VERDICT_BOX)
    assert len(verdict) > 46 and not short["cut"], short

    long_desc = "Scan every log under the release tree for errors " * 6
    page.fill("#input", f"/bash {long_desc} => {verdict * 3}")
    page.press("#input", "Enter")
    turns_done(page, 2)
    full = page.evaluate(VERDICT_BOX)
    assert full["cut"] and full["verdict"] > 0.6 * full["line"], full
    assert page.errors == []


# -- cost that must not grow with the transcript or the tab count (#157, #158) --

FIXTURE = Path(__file__).parent / "fixtures" / "session.jsonl"
ROWS = "#entries > .row"
BOTTOM_GAP = (
    "(() => { const s = document.getElementById('tr');"
    " return s.scrollHeight - s.scrollTop - s.clientHeight; })()"
)


@pytest.fixture
def replay_server(tmp_path: Path, fake_claude: str):
    (tmp_path / ".aegis.yaml").write_text(CONFIG)
    s = Server(tmp_path, fake_claude)
    s.env = {"FAKE_CLAUDE_REPLAY": str(FIXTURE), "FAKE_CLAUDE_REPEAT": "16"}
    s.start()
    yield s
    s.stop()


def test_a_long_transcript_mounts_its_tail_and_the_rest_as_the_reader_scrolls_up(
    replay_server, page
):
    page.goto(replay_server.url)
    page.wait_for_selector("#a2[data-view=fleet]")
    spawn(page)
    page.fill("#input", "replay")
    page.press("#input", "Enter")
    page.wait_for_function(
        "document.getElementById('s-status').textContent.trim() === 'done'",
        timeout=60_000,
    )
    page.reload()
    page.wait_for_function("window.__a2snapshot && window.__a2snapshot.painted")
    total = page.evaluate("window.__a2snapshot.count")
    assert total > 300

    # Only the tail is in the DOM, and the view follows it to the bottom once
    # the rows have taken their real heights.
    assert page.locator(ROWS).count() <= 200
    page.wait_for_function(f"{BOTTOM_GAP} < 48", timeout=3000)
    page.wait_for_timeout(500)
    assert page.evaluate(BOTTOM_GAP) < 48

    # Scrolling to the top mounts the earlier rows above, and the row that was
    # first stays where the reader saw it.
    while (n := page.locator(ROWS).count()) < total:
        first = page.evaluate(f"document.querySelector('{ROWS}').dataset.id")
        page.evaluate("document.getElementById('tr').scrollTop = 0")
        page.wait_for_function(
            f"n => document.querySelectorAll('{ROWS}').length > n", arg=n, timeout=3000
        )
        drift = page.evaluate(
            """id => {
              const row = document.querySelector(`#entries > .row[data-id="${id}"]`);
              return row.getBoundingClientRect().top - document.getElementById('tr').getBoundingClientRect().top;
            }""",
            first,
        )
        assert abs(drift) < 60, drift
    ids = page.evaluate(
        f"[...document.querySelectorAll('{ROWS}')].map(r => r.dataset.id)"
    )
    assert len(ids) == len(set(ids)) == total
    assert page.errors == []


def test_a_sessions_patch_redraws_only_its_own_tab_and_card(server, browser, page):
    page.goto(server.url)
    page.wait_for_selector("#a2[data-view=fleet]")
    a, b = spawn(page, "alpha"), spawn(page, "beta")
    keep = (
        "id => document.querySelector(`#tablist .tab[data-id='${id}']`).__kept = true"
    )
    kept = (
        "id => document.querySelector(`#tablist .tab[data-id='${id}']`).__kept === true"
    )
    page.evaluate(keep, a)
    page.fill("#input", "again")
    page.press("#input", "Enter")
    turns_done(page, 2)
    assert page.evaluate(kept, a), "a patch for b rebuilt a's tab"

    page.click("#tab-fleet")
    page.wait_for_selector(f"#cards .card[data-id='{a}']")
    for id_ in (a, b):
        page.evaluate(
            "id => document.querySelector(`#cards .card[data-id='${id}']`).__kept = true",
            id_,
        )
    other = new_page(browser, [])
    other.goto(f"{server.url.split('#')[0]}#s={b}")
    other.wait_for_selector("#a2[data-view=session]")
    other.fill("#input", "once more")
    other.press("#input", "Enter")
    turns_done(other, 3)
    page.wait_for_function(
        "id => { const c = document.querySelector(`#cards .card[data-id='${id}']`);"
        " return c.__kept !== true && c.textContent.includes('done') }",
        arg=b,
    )
    assert page.evaluate(
        "id => document.querySelector(`#cards .card[data-id='${id}']`).__kept === true",
        a,
    ), "a patch for b rebuilt a's card"
    assert tab_ids(page) == [a, b]
    assert page.errors == []


# -- the keyboard (#159) ------------------------------------------------------


def focused_id(pg) -> str:
    return pg.evaluate("document.activeElement.id")


def hash_is(pg, h: str) -> None:
    pg.wait_for_function("h => location.hash === h", arg=h, timeout=3000)


def test_alt_period_and_alt_comma_move_focus_between_composer_and_transcript(
    server, page
):
    page.goto(server.url)
    page.wait_for_selector("#a2[data-view=fleet]")
    spawn(page, "hello")
    page.keyboard.press("Alt+,")
    assert focused_id(page) == "tr"
    page.keyboard.press("Alt+.")
    assert focused_id(page) == "input"
    page.keyboard.type("jk")  # in a text field, plain keys are text
    assert page.input_value("#input") == "jk"
    for back in ("i", "/"):
        page.keyboard.press("Alt+,")
        page.keyboard.press(back)
        assert focused_id(page) == "input"
    assert page.input_value("#input") == "jk"
    assert page.errors == []


def test_alt_brackets_cycle_fleet_and_tabs_and_digits_pick_a_tab(server, page):
    page.goto(server.url)
    page.wait_for_selector("#a2[data-view=fleet]")
    a, b = spawn(page, "alpha"), spawn(page, "beta")
    steps = (("Alt+]", "#fleet"), ("Alt+]", f"#s={a}"), ("Alt+[", "#fleet"))
    for key, want in (*steps, ("Alt+[", f"#s={b}")):
        page.keyboard.press(key)
        hash_is(page, want)
    page.keyboard.press("Alt+,")
    for key, want in (("1", f"#s={a}"), ("0", "#fleet"), ("2", f"#s={b}")):
        page.keyboard.press(key)
        hash_is(page, want)
    page.keyboard.press("Alt+KeyN")
    hash_is(page, "#new")
    page.keyboard.press("Alt+.")
    assert focused_id(page) == "sp-text"
    page.keyboard.press("Enter")  # spawns from the composer
    page.wait_for_selector("#a2[data-view=session]")
    assert page.errors == []


def selected(pg) -> str | None:
    return pg.evaluate(
        "document.querySelector('#entries .row.sel')?.dataset.id ?? null"
    )


def row_ids(pg, cls: str = "") -> list[str]:
    return pg.evaluate(
        f"[...document.querySelectorAll('#entries .row{cls}')].map(r => r.dataset.id)"
    )


def test_j_k_and_the_turn_keys_walk_the_transcript(server, page):
    page.goto(server.url)
    page.wait_for_selector("#a2[data-view=fleet]")
    spawn(page, "one")
    page.fill("#input", "two")
    page.press("#input", "Enter")
    turns_done(page, 2)
    rows, users = row_ids(page), row_ids(page, ".user")
    page.keyboard.press("Alt+,")  # nothing selected: the last row on screen
    assert selected(page) == rows[-1]
    walk = (
        ("k", rows[-2]),
        ("j", rows[-1]),
        ("j", rows[-1]),
        ("g", rows[0]),
        ("K", rows[0]),  # the first row is the spawn's, not a message
        ("J", users[0]),
        ("J", users[1]),
        ("K", users[0]),
        ("ArrowDown", rows[rows.index(users[0]) + 1]),
        ("ArrowUp", users[0]),
        ("G", rows[-1]),
    )
    for key, want in walk:
        page.keyboard.press(key)
        assert selected(page) == want, key
    assert page.errors == []


def test_a_selected_row_keeps_its_selection_when_it_updates_and_enter_opens_it(
    server, page
):
    page.goto(server.url)
    page.wait_for_selector("#a2[data-view=fleet]")
    spawn(page)
    page.fill("#input", "/sleep 1")
    page.press("#input", "Enter")
    page.wait_for_selector(".row.tool.running")
    tid = page.get_attribute(".row.tool", "data-id")
    page.keyboard.press("Alt+,")
    page.keyboard.press("G")
    for _ in range(len(row_ids(page))):
        if selected(page) == tid:
            break
        page.keyboard.press("k")
    assert selected(page) == tid
    turns_done(page, 1)  # the result replaced the tool row's node
    assert selected(page) == tid
    is_open = f"document.querySelector('.row[data-id=\"{tid}\"] details').open"
    page.keyboard.press("Enter")
    assert page.evaluate(is_open) is True
    page.keyboard.press(" ")
    assert page.evaluate(is_open) is False

    # Tab is native: the row holding focus becomes the selection, and Enter on
    # its summary toggles its details once, not twice.
    page.keyboard.press("Tab")
    assert page.evaluate("document.activeElement.tagName") == "SUMMARY"
    holder = page.evaluate("document.activeElement.closest('.row').dataset.id")
    assert selected(page) == holder
    was = page.evaluate("document.activeElement.parentElement.open")
    page.keyboard.press("Enter")
    assert page.evaluate("document.activeElement.parentElement.open") is not was
    assert page.errors == []


def test_fleet_cards_and_archive_rows_walk_with_j_and_open_with_enter(server, page):
    page.goto(server.url)
    page.wait_for_selector("#a2[data-view=fleet]")
    a, b = spawn(page, "alpha"), spawn(page, "beta")
    page.click("#close")  # b goes to the archive
    page.wait_for_selector("#a2[data-view=fleet]")
    page.reload()  # the archive misses a Close until a reload (#160)
    page.wait_for_selector(f"#arch-list tr[data-id='{b}']")
    sel = "document.querySelector('#cards .sel, #arch-list .sel')?.dataset.id ?? null"
    page.keyboard.press("Alt+,")
    assert page.evaluate(sel) == a
    page.keyboard.press("j")
    assert page.evaluate(sel) == b
    page.keyboard.press("Enter")
    hash_is(page, f"#read={b}")
    page.keyboard.press("Alt+0")
    page.wait_for_selector("#a2[data-view=fleet]")
    page.keyboard.press("k")
    assert page.evaluate(sel) == a
    page.keyboard.press("Enter")
    hash_is(page, f"#s={a}")
    page.keyboard.press("Alt+0")
    page.wait_for_selector("#a2[data-view=fleet]")
    page.keyboard.press("/")
    assert focused_id(page) == "arch-q"
    assert page.errors == []


def test_question_mark_lists_every_key_and_escape_closes_it(server, page):
    page.goto(server.url)
    page.wait_for_selector("#a2[data-view=fleet]")
    n = page.evaluate("import('/static/js/keys.js').then(m => m.KEYS.length)")
    page.keyboard.press("?")
    page.wait_for_selector("#keymap", state="visible")
    assert page.locator("#keymap tr.k").count() == n
    page.keyboard.press("Escape")
    page.wait_for_selector("#keymap", state="hidden")
    page.click("#keys-btn")
    page.wait_for_selector("#keymap", state="visible")
    page.keyboard.press("?")
    page.wait_for_selector("#keymap", state="hidden")
    assert page.errors == []


def test_g_in_a_long_transcript_mounts_and_selects_the_first_entry(replay_server, page):
    page.goto(replay_server.url)
    page.wait_for_selector("#a2[data-view=fleet]")
    spawn(page)
    page.fill("#input", "replay")
    page.press("#input", "Enter")
    page.wait_for_function(
        "document.getElementById('s-status').textContent.trim() === 'done'",
        timeout=60_000,
    )
    page.reload()
    page.wait_for_function("window.__a2snapshot && window.__a2snapshot.painted")
    total = page.evaluate("window.__a2snapshot.count")
    assert page.locator(ROWS).count() < total
    page.keyboard.press("Alt+,")
    page.keyboard.press("g")
    assert page.locator(ROWS).count() == total
    assert selected(page) == row_ids(page)[0]


def menu_rows(pg) -> list[str]:
    return pg.evaluate(
        "[...document.querySelectorAll('#cmd-menu .cmd-row .nm')].map(n => n.textContent)"
    )


def test_the_menu_completes_a_model_and_esc_closes_it_without_interrupting(
    server, page
):
    page.goto(server.url)
    page.wait_for_selector("#a2[data-view=fleet]")
    spawn(page, "hello")
    page.fill("#input", "/sleep 3")
    page.press("#input", "Enter")
    page.wait_for_selector(".row.tool.running")
    page.click("#input")
    page.keyboard.type("/mo")
    page.wait_for_selector("#cmd-menu:not([hidden])")
    assert menu_rows(page)[0] == "/model"
    page.keyboard.press("Tab")
    assert page.input_value("#input") == "/model "
    page.keyboard.type("son")
    page.keyboard.press("Enter")  # accepts the highlighted model
    assert page.input_value("#input") == "/model sonnet "
    page.keyboard.press("Escape")
    assert page.is_hidden("#cmd-menu")
    assert page.is_visible(".row.tool.running"), "Esc on the menu does not interrupt"
    page.press("#input", "Enter")
    page.wait_for_function(
        "() => document.getElementById('chip-model').textContent === 'sonnet'"
    )
    assert page.input_value("#input") == ""
    assert page.errors == []


def test_alt_slash_with_a_draft_runs_a_command_and_keeps_the_draft(server, page):
    page.goto(server.url)
    page.wait_for_selector("#a2[data-view=fleet]")
    spawn(page, "hello")
    page.fill("#input", "half a thought")
    page.keyboard.press("Alt+/")
    page.wait_for_selector("#cmd-filter")  # prefilled with "/"
    page.keyboard.type("effort lo")
    page.keyboard.press("Enter")
    page.keyboard.press("Enter")
    page.wait_for_function(
        "() => document.getElementById('chip-effort').textContent === 'low effort'"
    )
    assert page.input_value("#input") == "half a thought"
    assert page.errors == []


def test_an_unknown_command_is_flagged_and_claudes_commands_render(server, page):
    page.goto(server.url)
    page.wait_for_selector("#a2[data-view=fleet]")
    spawn(page, "hello")
    page.fill("#input", "/bogus x")
    page.wait_for_selector(".composer.bad")
    page.press("#input", "Enter")
    page.wait_for_function(
        "() => document.getElementById('send-error').textContent.includes('// to send it as text')"
    )
    page.fill("#input", "/context")
    page.press("#input", "Enter")
    page.wait_for_selector(".row.command .cmd")
    # textContent: rows use content-visibility:auto, so innerText of a row not
    # yet painted reads empty (#161).
    assert page.text_content(".row.command .cmd") == "/context"
    assert page.errors == []


def test_clicking_the_model_chip_opens_the_menu_on_models(server, page):
    page.goto(server.url)
    page.wait_for_selector("#a2[data-view=fleet]")
    spawn(page, "hello")
    page.click("#chip-model")
    page.wait_for_selector("#cmd-menu:not([hidden])")
    assert page.input_value("#input") == "/model "
    assert menu_rows(page)[:3] == ["opus", "sonnet", "haiku"]
    assert page.errors == []


def test_a_chip_click_over_a_draft_keeps_the_draft(server, page):
    page.goto(server.url)
    page.wait_for_selector("#a2[data-view=fleet]")
    spawn(page, "hello")
    page.fill("#input", "keep me")
    page.click("#chip-effort")
    page.wait_for_selector("#cmd-filter")
    assert page.input_value("#cmd-filter") == "/effort "
    page.keyboard.type("lo")
    page.keyboard.press("Enter")
    page.keyboard.press("Enter")
    page.wait_for_function(
        "() => document.getElementById('chip-effort').textContent === 'low effort'"
    )
    assert page.input_value("#input") == "keep me"
    assert page.errors == []


def test_help_opens_the_menu_and_sends_nothing(server, page):
    page.goto(server.url)
    page.wait_for_selector("#a2[data-view=fleet]")
    spawn(page, "hello")
    page.fill("#input", "/help")
    page.press("#input", "Enter")
    page.wait_for_selector("#cmd-menu:not([hidden])")
    assert page.input_value("#input") == "/"
    assert menu_rows(page)[0] == "/model"
    assert page.evaluate("document.querySelectorAll('.row.user').length") == 1
    assert page.errors == []


# -- attention (#171) -----------------------------------------------------------


def report(pg, **args) -> None:
    pg.fill("#input", f"/mcp turn_end {json.dumps(args)}")
    pg.press("#input", "Enter")


def test_a_question_marks_the_tab_card_and_band_and_the_fleet_groups_it(server, page):
    page.goto(server.url)
    page.wait_for_selector("#a2[data-view=fleet]")
    spawn(page, "hello")
    first = page.evaluate("location.hash.slice(3)")
    spawn(page, "hello")
    report(page, attention="needs_you", line="Rebase or merge?", replies=[])
    turns_done(page, 2)
    page.wait_for_selector(".tab.on svg.ic use[href='#g-need']", state="attached")
    page.click("#tab-fleet")
    page.wait_for_selector(".card .ask >> text=Rebase or merge?")
    assert page.inner_text(".grp-h >> nth=0") == "Needs you"
    cards = page.eval_on_selector_all(".card", "cs => cs.map(c => c.dataset.id)")
    assert cards[-1] == first  # the done session sits below the one that needs you
    assert "need you" in page.inner_text("#band-counts")
    page.click(".seg button[data-order=tabs]")
    page.reload()
    page.wait_for_selector("#a2[data-view=fleet]")
    assert (
        page.eval_on_selector_all(".card", "cs => cs.map(c => c.dataset.id)")[0]
        == first
    )
    assert page.locator(".grp-h").count() == 0
    assert page.errors == []


def test_a_patch_that_changes_a_cards_group_regroups_the_open_fleet(
    server, browser, page
):
    page.goto(server.url)
    page.wait_for_selector("#a2[data-view=fleet]")
    a, b = spawn(page, "alpha"), spawn(page, "beta")
    page.click("#tab-fleet")
    page.wait_for_selector(f"#cards .card[data-id='{b}']")
    assert page.locator(".grp-h").count() == 0  # the order bar's heading names the list
    other = new_page(browser, [])
    other.goto(f"{server.url.split('#')[0]}#s={b}")
    other.wait_for_selector("#a2[data-view=session]")
    report(other, attention="needs_you", line="Which branch?", replies=[])
    page.wait_for_selector(".card .ask >> text=Which branch?")
    assert page.locator(".grp-h").all_inner_texts() == ["Needs you", "Everything else"]
    assert page.eval_on_selector_all(".card", "cs => cs.map(c => c.dataset.id)") == [
        b,
        a,
    ]
    assert page.errors == []


def test_reply_pills_send_their_text_and_all_disappear(server, page):
    page.goto(server.url)
    page.wait_for_selector("#a2[data-view=fleet]")
    spawn(page, "hello")
    plan = [{"text": "read", "state": "done"}, {"text": "fix", "state": "doing"}]
    page.fill("#input", f"/mcp plan_update {json.dumps({'items': plan})}")
    page.press("#input", "Enter")
    turns_done(page, 2)
    page.wait_for_selector("#s-plan-sec:not([hidden]) >> text=fix")
    marks = page.eval_on_selector_all(
        "#s-plan > div > svg.ic",
        """ms => ms.map(m => {
          const s = getComputedStyle(m);
          return [m.getAttribute('class'), s.width, s.animationName, s.color];
        })""",
    )
    ok = page.evaluate(
        "getComputedStyle(document.getElementById('a2')).getPropertyValue('--ok').trim()"
    )
    probe = page.evaluate(
        f"(() => {{ const d = document.createElement('i'); d.style.color = '{ok}';"
        " document.body.append(d); const c = getComputedStyle(d).color; d.remove(); return c; })()"
    )
    assert [(c, w, a) for c, w, a, _ in marks] == [
        ("ic done", "12px", "none"),
        ("ic work", "12px", "none"),
    ]
    assert marks[0][3] == probe
    report(
        page,
        attention="needs_you",
        line="Rebase or merge?",
        replies=["rebase onto main", "merge main into it"],
    )
    turns_done(page, 3)
    page.wait_for_selector("#s-ask:not([hidden]) >> text=Rebase or merge?")
    page.wait_for_selector("#replies:not([hidden]) .rp >> text=merge main into it")
    page.click("#replies .rp >> text=rebase onto main")
    page.wait_for_selector("#replies", state="hidden")
    turns_done(page, 4)
    assert "rebase onto main" in page.inner_text(".row.user >> nth=-1")
    assert page.locator("#replies .rp").count() == 0 or page.is_hidden("#replies")
    assert "at-done" in page.get_attribute("#s-status", "class").split()
    assert page.errors == []


def test_a_reply_read_on_screen_turns_its_mark_and_clears_the_done_badge(server, page):
    page.goto(server.url)
    page.wait_for_selector("#a2[data-view=fleet]")
    spawn(page, "hello")
    report(page, attention="done", line="did it", replies=[])
    turns_done(page, 2)
    # On screen and focused for a second: both replies become read.
    page.wait_for_function(
        "() => document.querySelectorAll('.row.prose .rm .ic.read').length >= 2"
        " && !document.querySelector('.row.prose .rm .ic.unread')",
        timeout=6000,
    )
    page.wait_for_selector(".tab.on .dot.ready")  # done, read: the plain idle dot
    assert page.errors == []


def test_a_reply_taller_than_the_view_becomes_read(server, page):
    page.goto(server.url)
    page.wait_for_selector("#a2[data-view=fleet]")
    # The fake claude quotes the prompt, so a tall prompt makes a tall reply.
    spawn(page, "\n\n".join(f"line {i}" for i in range(120)))
    # Never half visible: the row is more than twice the transcript's height.
    assert page.evaluate(
        "() => [...document.querySelectorAll('.row.prose')].at(-1).offsetHeight"
        " > 2 * document.querySelector('#tr').clientHeight"
    )
    page.wait_for_function(
        "() => !document.querySelector('.row.prose .rm .ic.unread')", timeout=6000
    )
    assert page.errors == []


def test_a_tall_reply_that_lands_off_screen_becomes_read_when_you_scroll_into_it(
    server, page
):
    page.goto(server.url)
    page.wait_for_selector("#a2[data-view=fleet]")
    spawn(page, "hello")
    page.wait_for_function(
        "() => !document.querySelector('.row.prose .rm .ic.unread')", timeout=6000
    )
    # A prompt sent mid-turn is answered when the tool call ends, with text that
    # quotes it: the tall reply lands two seconds from now, with the reader at
    # the top.
    page.fill("#input", "/sleep 2")
    page.press("#input", "Enter")
    page.wait_for_selector(".row.tool.running")
    page.fill("#input", "\n\n".join(f"line {i}" for i in range(200)))
    page.press("#input", "Enter")
    # A send scrolls to the bottom once it is accepted, and so does its pending
    # row while the reader follows; the reader goes up after both.
    page.wait_for_function("() => document.querySelector('#input').value === ''")
    page.wait_for_selector(".row.user.pending")
    # Until the scroll event lands, the transcript still follows and an update
    # takes it back down, so scroll up until it stays.
    page.wait_for_function(
        """() => {
            const tr = document.querySelector('#tr');
            const up = tr.scrollTop === 0;
            tr.scrollTop = 0;
            return up;
        }""",
        polling=100,
    )
    tall = "[...document.querySelectorAll('.row.prose')].at(-1)"
    page.wait_for_function(
        f"() => {tall}.textContent.includes('line 199')", timeout=8000
    )
    page.wait_for_timeout(1500)
    assert page.evaluate("() => document.querySelector('#tr').scrollTop") == 0
    assert page.evaluate(f"() => !!{tall}.querySelector('.rm .ic.unread')")
    # The top edge comes in first, then the row covers the view in steps, as
    # a reader scrolling down meets it.
    page.evaluate(
        f"""() => {{
            const tr = document.querySelector('#tr');
            const top = {tall}.getBoundingClientRect().top - tr.getBoundingClientRect().top;
            tr.scrollTop += top - tr.clientHeight + 40;
        }}"""
    )
    for _ in range(3):
        page.wait_for_timeout(200)
        page.evaluate(
            "() => { const tr = document.querySelector('#tr'); tr.scrollTop += tr.clientHeight / 2; }"
        )
    assert page.evaluate(
        f"""() => {{
            const v = document.querySelector('#tr').getBoundingClientRect();
            const r = {tall}.getBoundingClientRect();
            return r.top <= v.top && r.bottom >= v.bottom;
        }}"""
    ), "the row covers the view"
    # More than five times the view: its ratio never reaches 0.25. Off screen
    # it has no real height (content-visibility), so this is measured here.
    assert page.evaluate(
        f"() => {tall}.offsetHeight > 5 * document.querySelector('#tr').clientHeight"
    )
    page.wait_for_function(
        f"() => !!{tall}.querySelector('.rm .ic.read')", timeout=6000
    )
    assert page.errors == []


def test_a_reply_that_lands_while_you_are_away_stays_unread_until_you_look(
    server, page
):
    page.goto(server.url)
    page.wait_for_selector("#a2[data-view=fleet]")
    spawn(page, "hello")
    sid = page.evaluate("location.hash.slice(3)")
    # The first reply is on screen: it becomes read before we leave.
    page.wait_for_function(
        "() => !document.querySelector('.row.prose .rm .ic.unread')", timeout=6000
    )
    page.fill("#input", "/sleep 1")
    page.press("#input", "Enter")
    page.click("#tab-fleet")  # away while the reply arrives
    page.wait_for_selector(f".card[data-id='{sid}'] .ft >> text=1 unread", timeout=8000)
    page.click(f".tab[data-id='{sid}']")
    page.wait_for_selector(".row.prose .rm .ic.unread", state="attached")
    page.wait_for_function(
        "() => !document.querySelector('.row.prose .rm .ic.unread')", timeout=6000
    )
    assert page.errors == []


def test_the_divider_and_navigator_walk_agent_messages(server, page):
    page.goto(server.url)
    page.wait_for_selector("#a2[data-view=fleet]")
    spawn(page, "one")
    sid = page.evaluate("location.hash.slice(3)")
    page.wait_for_function(
        "() => !document.querySelector('.row.prose .rm .ic.unread')", timeout=6000
    )
    # A reply lands while away: a one-second turn, and the Fleet before it ends.
    page.fill("#input", "/sleep 1")
    page.press("#input", "Enter")
    page.click("#tab-fleet")
    page.wait_for_selector(f".card[data-id='{sid}'] .ft >> text=1 unread", timeout=8000)
    page.click(f".tab[data-id='{sid}']")
    page.wait_for_selector(".row.since")
    since = page.eval_on_selector(
        ".row.since", "n => getComputedStyle(n, '::before').content"
    )
    assert "new since you left" in since
    # On screen, it is read within a second; the divider stays where it was.
    page.wait_for_function(
        "() => document.querySelector('#nav-pos').textContent.startsWith('message ')",
        timeout=6000,
    )
    assert page.locator(".row.since").count() == 1
    page.click(".nav .up")
    assert page.eval_on_selector(".row.sel", "n => n.classList.contains('prose')")
    page.keyboard.press("Alt+ArrowDown")
    assert page.eval_on_selector(".row.sel", "n => n.classList.contains('prose')")
    # The divider is a style, never a row of its own: from the row above it,
    # j lands on the row it decorates.
    page.click(".nav .up")
    page.keyboard.press("j")  # the turn's end, the row just above the divider
    assert page.eval_on_selector(
        ".row.sel", "n => n.nextElementSibling.classList.contains('since')"
    )
    page.keyboard.press("j")
    assert page.eval_on_selector(".row.sel", "n => n.classList.contains('since')")
    # A selection by focus moves the position too, not only the keys.
    page.keyboard.press("Alt+ArrowUp")
    pos = "document.getElementById('nav-pos').textContent"
    before = page.evaluate(pos)
    page.focus(".row.tool summary")
    assert page.eval_on_selector(".row.sel", "n => n.classList.contains('tool')")
    assert page.evaluate(pos) != before
    # Switching sessions drops the navigator with the old rows, before the new
    # session's snapshot arrives.
    other = spawn(page)
    page.goto(f"{server.url}#s={sid}")
    page.wait_for_selector("#nav:not([hidden])")
    hidden = page.evaluate(
        """id => new Promise((done) => {
          addEventListener("hashchange", () => done(document.getElementById("nav").hidden), { once: true });
          location.hash = `#s=${id}`;
        })""",
        other,
    )
    assert hidden
    assert page.errors == []


def test_the_divider_is_drawn_on_its_row_when_scrolling_up_mounts_it(
    replay_server, page
):
    page.goto(replay_server.url)
    page.wait_for_selector("#a2[data-view=fleet]")
    sid = spawn(page)
    # The whole replay lands while away, so the divider sits above its first
    # entry, far above the mounted tail.
    page.fill("#input", "replay")
    page.press("#input", "Enter")
    page.click("#tab-fleet")
    card = f".card[data-id='{sid}']"
    page.wait_for_selector(f"{card} .unr", timeout=60_000)
    page.wait_for_selector(f"{card}:not(.at-working)", timeout=60_000)
    page.evaluate("delete window.__a2snapshot")  # the empty session's, from spawn
    page.click(f".tab[data-id='{sid}']")
    page.wait_for_function("window.__a2snapshot && window.__a2snapshot.painted")
    total = page.evaluate("window.__a2snapshot.count")
    assert page.locator(ROWS).count() < total
    assert page.locator(".row.since").count() == 0
    # Each row records whether it carried the divider when it was mounted: a
    # later update would redraw it, so the moment of mounting is what counts.
    page.evaluate(
        """() => {
          window.__mountedSince = [];
          new MutationObserver((ms) => {
            for (const m of ms) for (const n of m.addedNodes)
              if (n.classList?.contains("since")) window.__mountedSince.push(n.dataset.id);
          }).observe(document.getElementById("entries"), { childList: true });
        }"""
    )
    while (n := page.locator(ROWS).count()) < total:
        page.evaluate("document.getElementById('tr').scrollTop = 0")
        page.wait_for_function(
            f"n => document.querySelectorAll('{ROWS}').length > n", arg=n, timeout=3000
        )
    assert len(page.evaluate("window.__mountedSince")) == 1
    assert page.locator(".row.since").count() == 1
    assert page.errors == []


def test_the_title_favicon_and_a_notification_ping_when_a_session_needs_you(
    server, browser
):
    errors: list = []
    ctx = browser.new_context(viewport={"width": 1280, "height": 800})
    ctx.grant_permissions(["notifications"])
    pg = ctx.new_page()
    pg.on("pageerror", lambda e: errors.append(str(e)))
    # A hidden tab, as browsers make it: document.hidden is true and animation
    # frames do not run. The flag survives a reload through sessionStorage.
    pg.add_init_script("""
      window.__notes = [];
      window.__hidden = sessionStorage.getItem('hidden') === '1';
      window.Notification = class { constructor(t, o) { window.__notes.push([t, o]); }
        static get permission() { return 'granted'; }
        static requestPermission() { return Promise.resolve('granted'); } };
      Object.defineProperty(document, 'hidden', { get: () => window.__hidden === true });
      const raf = window.requestAnimationFrame.bind(window);
      window.requestAnimationFrame = (cb) => (window.__hidden ? 0 : raf(cb));
    """)
    pg.goto(server.url)
    pg.wait_for_selector("#a2[data-view=fleet]")
    first = spawn(pg, "hello")
    spawn(pg, "hello")
    assert not pg.title().startswith("(")
    # While the page is visible, a session that needs you counts but sends no
    # notification.
    report(pg, attention="needs_you", line="Seen here?", replies=[])
    pg.wait_for_function(
        "() => document.title.startsWith('(1) ')", polling=100, timeout=8000
    )
    report(pg, attention="done", line="Done.", replies=[])
    pg.wait_for_function("() => !document.title.startsWith('(')", timeout=8000)
    assert pg.evaluate("window.__notes.length") == 0
    pg.evaluate("window.__hidden = true")
    report(pg, attention="needs_you", line="Merge or rebase?", replies=[])
    pg.wait_for_function(
        "() => document.title.startsWith('(1) ')", polling=100, timeout=8000
    )
    assert "dot" in pg.get_attribute("#favicon", "href")
    pg.wait_for_function("() => window.__notes.length === 1", polling=100, timeout=8000)
    title, opts = pg.evaluate("window.__notes[0]")
    assert title.endswith(": needs you")
    assert opts["body"] == "Merge or rebase?"
    # Another session's turn, still hidden: the one that needs you has not
    # changed, so it does not notify again.
    pg.evaluate("id => (location.hash = '#s=' + id)", first)
    report(pg, attention="done", line="Done.", replies=[])
    pg.wait_for_function(
        "() => [...document.querySelectorAll('.row.sys .body')]"
        ".filter(b => /^done in/.test(b.textContent)).length >= 2",
        polling=100,
        timeout=8000,
    )
    assert pg.title().startswith("(1) ")
    assert pg.evaluate("window.__notes.length") == 1
    # A page opened hidden while the session already needs you counts it but
    # does not notify: nothing changed while the page was there.
    pg.evaluate("sessionStorage.setItem('hidden', '1')")
    pg.reload()
    pg.wait_for_function(
        "() => document.title.startsWith('(1) ')", polling=100, timeout=8000
    )
    assert pg.evaluate("window.__notes.length") == 0
    pg.evaluate("sessionStorage.removeItem('hidden'); window.__hidden = false")
    # The dot takes the theme's accent.
    assert quote("#e0a872") in pg.get_attribute("#favicon", "href")
    pg.select_option("#theme", "logbook")
    assert quote("#2f5ba8") in pg.get_attribute("#favicon", "href")
    pg.click("#tab-fleet")
    assert pg.title().startswith("(1) Fleet")
    assert errors == []
    ctx.close()
