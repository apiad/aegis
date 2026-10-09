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
    def __init__(self, root: Path, claude: str, opencode: str | None = None):
        self.root, self.claude, self.port = root, claude, _free_port()
        self.opencode = opencode
        self.releases = root / "pypi.json"
        self.env: dict[str, str] = {}
        self.args: list[str] = []
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
                *(["--opencode", self.opencode] if self.opencode else []),
                "--log-level",
                "info",
                *self.args,
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
    "  deepseek: {provider: opencode, model: opencode-go/fake-pro, effort: high, permission: full}\n"
)


@pytest.fixture
def server(tmp_path: Path, fake_claude: str, fake_opencode: str):
    (tmp_path / ".aegis.yaml").write_text(CONFIG)
    s = Server(tmp_path, fake_claude, fake_opencode).start()
    yield s
    s.stop()


class Linked:
    """Two servers, alpha linked to beta through `aegis link add`."""

    def __init__(self, root: Path, claude: str, opencode: str):
        self.beta = Server(root / "beta", claude, opencode)
        self.alpha = Server(root / "alpha", claude, opencode)
        for s, name in ((self.beta, "beta"), (self.alpha, "alpha")):
            s.root.mkdir()
            (s.root / ".aegis.yaml").write_text(CONFIG)
            s.args = ["--name", name]

    def start(self) -> "Linked":
        self.beta.start()
        token = (self.beta.root / ".aegis" / "state" / "token").read_text().strip()
        base = f"http://127.0.0.1:{self.beta.port}"
        link = subprocess.run(
            [sys.executable, "-m", "aegis", "link", "add", "beta", base,
             "--root", str(self.alpha.root), "--as", "alpha"],
            input=token + "\n", capture_output=True, text=True, timeout=30,
        )  # fmt: skip
        assert link.returncode == 0, link.stdout + link.stderr
        self.alpha.start()
        return self

    def stop(self) -> None:
        self.alpha.stop()
        self.beta.stop()


@pytest.fixture
def linked(tmp_path: Path, fake_claude: str, fake_opencode: str):
    pair = Linked(tmp_path, fake_claude, fake_opencode).start()
    yield pair
    pair.stop()


@pytest.fixture
def recap_server(tmp_path: Path, fake_claude: str, fake_opencode: str):
    (tmp_path / ".aegis.yaml").write_text(CONFIG + "recap: {agent: opus}\n")
    s = Server(tmp_path, fake_claude, fake_opencode).start()
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


def pick(pg, sel: str, text: str) -> None:
    """Type into a pick-chip and take its first match."""
    pg.click(f"{sel} input")
    pg.fill(f"{sel} input", text)
    pg.press(f"{sel} input", "Enter")


def picked(pg, sel: str) -> str:
    return pg.evaluate(f"document.querySelector('{sel}').value")


def test_the_composer_overrides_a_chip_resets_it_and_spawns_with_the_first_message(
    server, page
):
    page.goto(server.url)
    page.click("#tab-add")
    page.wait_for_function("document.querySelector('#sp-agent').value === 'opus'")
    assert picked(page, "#sp-model") == "opus"
    assert page.is_hidden("#sp-reset")
    assert page.is_hidden("#sp-server")  # no linked server, no server chip
    tops = page.eval_on_selector_all(
        "#spawn .pick, #sp-go", "els => els.map(e => e.getBoundingClientRect().top)"
    )
    assert max(tops) - min(tops) < 4, tops

    pick(page, "#sp-model", "sonnet")
    assert page.input_value("#sp-agent input") == "opus*"
    assert "diff" in page.get_attribute("#sp-model", "class")
    page.click("#sp-reset")
    assert picked(page, "#sp-model") == "opus"
    assert page.input_value("#sp-agent input") == "opus"
    assert page.is_hidden("#sp-reset")

    page.fill("#sp-text", "/argv")
    page.press("#sp-text", "Shift+Enter")
    assert page.evaluate("document.querySelector('#a2').dataset.view") == "spawn"
    page.fill("#sp-text", "/argv")
    pick(page, "#sp-effort", "max")
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
    assert picked(page, "#sp-effort") == "high", "a spawn clears the overrides"
    assert page.input_value("#sp-text") == ""
    assert page.errors == []


def test_the_chips_filter_as_you_type_and_a_long_model_keeps_the_send_button_in_the_corner(
    server, page
):
    (server.root / ".aegis.yaml").write_text(
        CONFIG
        + "  fable: {harness: claude-code, model: claude-fable-5-1, effort: high, permission: full}\n"
    )
    page.set_viewport_size({"width": 560, "height": 720})
    page.goto(server.url)
    page.click("#tab-add")
    page.wait_for_function(
        "document.querySelector('#sp-agent').options.some(o => o.value === 'fable')"
    )
    assert page.evaluate("document.querySelectorAll('select, datalist').length") == 0

    pick(page, "#sp-agent", "fab")
    assert picked(page, "#sp-agent") == "fable"
    assert picked(page, "#sp-model") == "claude-fable-5-1"
    # The chips wrap onto two rows; the send button keeps the corner (#207).
    send = page.evaluate(
        "document.querySelector('#sp-go').getBoundingClientRect().toJSON()"
    )
    box = page.evaluate(
        "document.querySelector('#spawn .box').getBoundingClientRect().toJSON()"
    )
    chips = page.evaluate(
        "[...document.querySelectorAll('#spawn .pick')].map(e => e.getBoundingClientRect().toJSON())"
    )
    assert len({round(c["top"]) for c in chips}) >= 2, chips
    assert all(c["right"] <= send["left"] for c in chips), (send, chips)
    # On the last chip row: the buttons are centred on it, and the mic is a
    # few pixels taller than the send in some fonts.
    assert abs(max(c["bottom"] for c in chips) - send["bottom"]) < 6, (send, chips)
    assert box["right"] - send["right"] < 20, (box, send)

    # Esc keeps the value; the arrows walk the list; a click takes a row.
    page.click("#sp-effort input")
    page.fill("#sp-effort input", "max")
    page.press("#sp-effort input", "Escape")
    assert picked(page, "#sp-effort") == "high"
    assert page.input_value("#sp-effort input") == "effort high"
    page.press("#sp-permission input", "ArrowDown")
    page.press("#sp-permission input", "ArrowDown")
    page.press("#sp-permission input", "Enter")
    assert picked(page, "#sp-permission") == "read"
    page.click("#sp-effort input")
    page.click("#sp-effort .opt:has-text('effort low')")
    assert picked(page, "#sp-effort") == "low"
    # The model chip takes an id no option names.
    pick(page, "#sp-model", "claude-opus-5-5")
    assert picked(page, "#sp-model") == "claude-opus-5-5"
    assert page.input_value("#sp-agent input") == "fable*"
    assert page.errors == []


def test_enter_in_the_model_or_cwd_field_moves_to_the_message_and_spawns_nothing(
    server, page
):
    page.goto(server.url)
    page.click("#tab-add")
    page.wait_for_function("document.querySelector('#sp-agent').value === 'opus'")
    page.fill("#sp-text", "half written")
    for field in ("#sp-model input", "#sp-cwd"):
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


def close_session(pg) -> None:
    """Close the shown session through the aegis dialog."""
    pg.click("#close")
    pg.wait_for_selector("#dialog .ok", state="visible")
    pg.click("#dialog .ok")


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
    assert page.is_visible(".row.tool.err") and not page.is_visible(
        ".row.tool.err pre.out"
    )
    page.click(".row.tool.err summary")
    page.wait_for_selector(".row.tool.err pre.out", state="visible")

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
    pick(page, "#theme", "logbook")
    assert page.evaluate(bg) != before == "#11100e"

    close_session(page)
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
    close_session(page)
    other.wait_for_selector("#a2[data-view=fleet]", timeout=5000)
    assert tab_ids(other) == []
    assert errors == [] and page.errors == []


# A slow runner paints late: a frame 300 ms out makes any redraw left to the
# next frame visible to the test (#161's CI failure).
SLOW_FRAMES = "const raf = window.requestAnimationFrame; window.requestAnimationFrame = (f) => setTimeout(() => raf(f), 300);"


def test_the_archive_pages_past_fifty(tmp_path, fake_claude, fake_opencode, page):
    (tmp_path / ".aegis.yaml").write_text(CONFIG)
    store = tmp_path / ".aegis" / "state" / "sessions"
    store.mkdir(parents=True)
    for i in range(120):
        meta = {
            "log_id": f"20261001-000000-{i:06x}",
            "handle": f"old-{i}",
            "archived": True,
            "last_activity": 1_700_000_000.0 + i // 3,
            "title": f"old talk {i}",
            "cwd": str(tmp_path),
        }
        (store / f"{meta['log_id']}.json").write_text(json.dumps(meta))
    s = Server(tmp_path, fake_claude, fake_opencode).start()
    try:
        page.goto(s.url)
        page.wait_for_selector("#a2[data-view=fleet]")
        page.wait_for_selector("#arch-count >> text=Showing 50 of 120")
        assert page.locator("#arch-list tr[data-id]").count() == 50
        page.click("#arch-next")
        page.wait_for_selector("#arch-count >> text=Showing 100 of 120")
        page.click("#arch-next")
        page.wait_for_selector("#arch-count >> text=Showing 120 of 120")
        assert not page.is_visible("#arch-next")
        rows = page.eval_on_selector_all(
            "#arch-list tr[data-id]", "rs => rs.map(r => r.dataset.id)"
        )
        assert len(rows) == len(set(rows)) == 120
    finally:
        s.stop()


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
    close_session(page)
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


def test_a_wrong_token_says_so_and_offers_the_login(server, page):
    page.goto(re.sub(r"token=[^&]+", "token=wrong", server.url))
    page.wait_for_selector("#login", state="visible")
    assert "refused" in page.inner_text("#login-error")
    assert "token=" not in page.url


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
        args = {"paths": [path]} | ({"caption": caption} if caption else {})
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


def test_a_set_of_files_is_one_card_that_pages_between_them(server, page):
    (server.root / "dot.png").write_bytes(PNG)
    (server.root / "notes.md").write_text("# Notes\n")
    (server.root / "log.txt").write_text("line one\n")
    page.goto(server.url)
    page.wait_for_selector("#a2[data-view=fleet]")
    spawn(page)
    args = {"paths": ["dot.png", "notes.md", "log.txt"], "caption": "Three"}
    page.fill("#input", f"/mcp file_send {json.dumps(args)}")
    page.press("#input", "Enter")
    turns_done(page, 1)
    card = page.locator(".row.file")
    assert card.count() == 1 and card.locator(".cap").inner_text() == "Three"
    bar = card.locator(".fbar")
    assert bar.locator(".count").inner_text() == "1 / 3"
    assert bar.locator(".fn").inner_text() == "dot.png"
    assert bar.locator(".prev").is_disabled() and not bar.locator(".next").is_disabled()
    first = bar.locator("a.open").get_attribute("href")
    assert card.locator(".stage").count() == 1 and card.locator("img").count() == 1

    bar.locator(".next").click()
    assert bar.locator(".count").inner_text() == "2 / 3"
    assert bar.locator(".fn").inner_text() == "notes.md"
    assert card.locator(".stage").count() == 1 and card.locator("img").count() == 0
    assert card.locator(".stage .md h1").inner_text() == "Notes"
    second = bar.locator("a.open").get_attribute("href")
    assert second != first and second.endswith("/notes.md")
    assert bar.locator("a.dl").get_attribute("href") == second + "?download=1"
    assert bar.locator(".native").get_attribute("data-name") == "notes.md"

    bar.locator(".next").click()
    assert bar.locator(".fn").inner_text() == "log.txt"
    assert card.locator(".stage pre").inner_text() == "line one"
    assert bar.locator(".next").is_disabled()
    bar.locator(".prev").click()
    bar.locator(".prev").click()
    assert bar.locator(".count").inner_text() == "1 / 3"
    assert bar.locator("a.open").get_attribute("href") == first
    assert page.request.get(server.url.split("/?")[0] + first).body() == PNG

    # A single file keeps the card it always had: no pager.
    page.fill("#input", f"/mcp file_send {json.dumps({'paths': ['dot.png']})}")
    page.press("#input", "Enter")
    turns_done(page, 2)
    assert page.locator(".row.file").nth(1).locator(".pager").count() == 0
    assert page.errors == []


def test_a_read_rows_file_opens_inside_the_row_on_request(server, page):
    notes = server.root / "notes.md"
    notes.write_text("# Notes\n")
    page.goto(server.url)
    page.wait_for_selector("#a2[data-view=fleet]")
    spawn(page)
    page.fill("#input", f"/read {notes}")
    page.press("#input", "Enter")
    turns_done(page, 1)
    row = page.locator(".row.tool")
    row.locator("summary").click()
    row.locator(".peek").click()
    card = row.locator(".fcard")
    card.wait_for()
    assert card.locator(".fn").inner_text() == "notes.md"
    assert card.locator(".stage .md h1").inner_text() == "Notes"
    assert "as of" in row.locator(".peekcap").inner_text()
    assert row.locator("details").get_attribute("open") is not None

    notes.write_text("# Changed\n")
    row.locator(".peek").click()
    row.locator(".stage .md h1", has_text="Changed").wait_for()
    assert row.locator(".fcard").count() == 1
    assert page.locator(".row.file").count() == 0
    assert page.errors == []


def test_open_natively_shows_only_on_the_servers_desktop_and_opens_the_copy(
    server, browser, page
):
    (server.root / "dot.png").write_bytes(PNG)
    page.goto(server.url)
    page.wait_for_selector("#a2[data-view=fleet]")
    sid = spawn(page)
    page.fill("#input", f"/mcp file_send {json.dumps({'paths': ['dot.png']})}")
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
    close_session(page)  # b goes to the archive
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


def test_an_opencode_session_streams_and_calls_aegis(server, page):
    page.goto(server.url)
    page.click("#tab-add")
    page.wait_for_function("document.querySelector('#sp-agent').value !== ''")
    pick(page, "#sp-agent", "deepseek")
    page.click("#sp-go")
    page.wait_for_selector("#a2[data-view=session]")
    # Subscribed before the stream starts: the text can only arrive as patches.
    page.fill("#input", "/stream 6")
    page.press("#input", "Enter")
    page.wait_for_function(
        "[...document.querySelectorAll('.row.prose .body')]"
        ".some(b => b.textContent.includes('chunk1'))"
    )
    prose = page.inner_text(".row.prose .body")
    assert "chunk6" not in prose, "the text is drawn while it streams"
    turns_done(page, 1)
    assert page.inner_text("#s-model") == "OpenCode, opencode-go/fake-pro"
    page.wait_for_function(
        "document.querySelector('#s-cost').textContent === '$0.0020'"
    )

    page.fill("#input", "/mcp meta {}")
    page.press("#input", "Enter")
    turns_done(page, 2)
    assert "meta" in page.locator(".row.tool.ok").last.inner_text()
    assert "OpenCode 1.18.31" in page.inner_text("#entries")


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


def test_a_read_review_leaves_the_fleets_needs_you_group(server, page):
    page.goto(server.url)
    page.wait_for_selector("#a2[data-view=fleet]")
    sid = spawn(page, "hello")
    report(page, attention="review", line="Read the spec", replies=[])
    turns_done(page, 2)
    page.wait_for_selector(".tab.on .dot.ready", timeout=6000)  # read: idle dot
    page.click("#tab-fleet")
    page.wait_for_selector(f"#cards .card.at-review[data-id='{sid}']")
    assert page.locator(".grp-h").count() == 0  # not under "Needs you"
    assert "review" in page.inner_text("#band-counts")  # the band counts attention
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


SETTINGS_CONFIG = (
    "# kept across a save\n"
    "default_agent: opus\nagents:\n  opus: {harness: claude-code, model: opus, effort: high, permission: full}\n"
    "  bad.one: {harness: claude-code, model: nope, effort: high, permission: full}\n"
)


@pytest.fixture
def settings_server(tmp_path: Path, fake_claude: str, fake_opencode: str):
    (tmp_path / ".aegis.yaml").write_text(SETTINGS_CONFIG)
    s = Server(tmp_path, fake_claude, fake_opencode).start()
    yield s
    s.stop()


def open_settings(pg, url: str) -> None:
    pg.goto(url)
    pg.wait_for_selector("#a2[data-view=fleet]")
    pg.keyboard.press("Alt+KeyS")
    pg.wait_for_selector("#a2[data-view=settings]")
    pg.wait_for_selector('.set-agent[data-row="agents.opus"]')


def test_settings_saves_an_edit_to_the_file_and_the_composer_follows(
    settings_server, page
):
    open_settings(page, settings_server.url)
    pick(page, '.set-agent[data-row="agents.opus"] pick-chip[name=effort]', "max")
    page.click("#set-save")
    page.wait_for_function(
        "document.querySelector('#set-status').textContent === 'Saved'"
    )
    text = (settings_server.root / ".aegis.yaml").read_text()
    assert "# kept across a save" in text and "effort: max" in text
    page.click("#tab-add")
    page.wait_for_function("document.querySelector('#sp-effort').value === 'max'")
    assert page.errors == []


def test_an_edit_on_disk_reloads_the_open_settings_page(settings_server, page):
    open_settings(page, settings_server.url)
    (settings_server.root / ".aegis.yaml").write_text(
        SETTINGS_CONFIG
        + "  extra: {harness: claude-code, model: sonnet, effort: low, permission: read}\n"
    )
    page.wait_for_selector('.set-agent[data-row="agents.extra"]', timeout=5000)


def test_an_edit_on_disk_under_unsaved_edits_offers_reload(settings_server, page):
    open_settings(page, settings_server.url)
    model = '.set-agent[data-row="agents.opus"] pick-chip[name=model]'
    page.fill(model + " input", "sonnet")
    (settings_server.root / ".aegis.yaml").write_text(SETTINGS_CONFIG + "# changed\n")
    page.wait_for_selector("#set-stale", timeout=5000)
    assert page.input_value(model + " input") == "sonnet"
    page.click("#set-reload")
    page.wait_for_function(
        "document.querySelector('.set-agent[data-row=\"agents.opus\"] pick-chip[name=model]').value === 'opus'"
    )


def test_run_doctor_marks_the_row(settings_server, page):
    open_settings(page, settings_server.url)
    page.click("#set-doctor")
    card = '.set-agent[data-row="agents.bad.one"]'
    page.wait_for_selector(card + ".warn", timeout=15000)
    assert "does not list 'nope'" in page.text_content(card)
    assert "1 warning" in page.text_content("#set-summary")


def test_typing_in_settings_survives_session_patches(settings_server, page):
    open_settings(page, settings_server.url)
    box = '.set-agent[data-row="agents.opus"] pick-chip[name=model] input'
    page.click(box)
    page.keyboard.press("End")
    page.keyboard.type("-x")
    page.evaluate(
        "window.dispatchEvent(new HashChangeEvent('hashchange'))"
    )  # a render() with no view change
    page.keyboard.type("y")
    assert page.input_value(box) == "opus-xy"
    assert page.evaluate("document.activeElement.closest('pick-chip').name") == "model"


@pytest.fixture
def empty_server(tmp_path: Path, fake_claude: str, fake_opencode: str):
    s = Server(tmp_path, fake_claude, fake_opencode).start()
    yield s
    s.stop()


def test_an_empty_root_offers_set_up_and_saving_creates_the_file(empty_server, page):
    page.goto(empty_server.url + "#settings")
    page.wait_for_selector("#set-setup")
    page.click("#set-setup")
    page.wait_for_selector('.set-agent[data-row="agents.opus"]', timeout=15000)
    page.click("#set-save")
    page.wait_for_function(
        "document.querySelector('#set-status').textContent === 'Saved'"
    )
    assert "default_agent: opus" in (empty_server.root / ".aegis.yaml").read_text()


def test_renaming_the_default_agent_carries_to_the_default(settings_server, page):
    open_settings(page, settings_server.url)
    page.fill('.set-agent[data-row="agents.opus"] input[name=name]', "big")
    page.click("#set-save")
    page.wait_for_function(
        "document.querySelector('#set-status').textContent === 'Saved'"
    )
    text = (settings_server.root / ".aegis.yaml").read_text()
    assert "default_agent: big" in text and "  big:" in text


def test_renaming_an_agent_carries_to_the_queues_that_run_it(settings_server, page):
    (settings_server.root / ".aegis.yaml").write_text(
        SETTINGS_CONFIG + "queues:\n  q: {agent: opus, max_parallel: 2}\n"
    )
    open_settings(page, settings_server.url)
    page.wait_for_selector('.set-queue[data-row="queues.q"]')
    page.fill('.set-agent[data-row="agents.opus"] input[name=name]', "big")
    assert (
        picked(page, '.set-queue[data-row="queues.q"] pick-chip[name=agent]') == "big"
    )
    page.click("#set-save")
    page.wait_for_function(
        "document.querySelector('#set-status').textContent === 'Saved'"
    )
    assert "agent: big" in (settings_server.root / ".aegis.yaml").read_text()


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


def test_landing_after_two_replies_shows_a_recap_last_and_the_sparkle_makes_one(
    recap_server, page
):
    frames = frames_on(page)
    page.goto(recap_server.url)
    page.wait_for_selector("#a2[data-view=fleet]")
    spawn(page, "first")
    sid = page.evaluate("location.hash.slice(3)")
    page.wait_for_function(
        "() => !document.querySelector('.row.prose .rm .ic.unread')", timeout=6000
    )
    page.fill("#input", "/sleep 1")
    page.press("#input", "Enter")
    page.click("#tab-fleet")
    page.wait_for_selector(f".card[data-id='{sid}'] .ft >> text=1 unread", timeout=8000)
    # one unread is under the threshold: make a second one land while away
    page.click(f".tab[data-id='{sid}']")
    page.fill("#input", "/sleep 1")
    page.press("#input", "Enter")
    page.click("#tab-fleet")
    page.wait_for_selector(f".card[data-id='{sid}'] .ft >> text=2 unread", timeout=8000)
    frames.clear()
    page.click(f".tab[data-id='{sid}']")
    page.wait_for_selector(".row.recap .ctx >> text=recap of", timeout=8000)
    # The tab came back from the client's cache: its first frame was a delta,
    # and that delta is what asked for the recap.
    snaps = [f for f in frames if '"t": "snapshot"' in f and f"transcript:{sid}" in f]
    assert snaps and '"since"' in snaps[0], snaps
    assert page.eval_on_selector(
        "#entries", "n => n.lastElementChild.classList.contains('recap')"
    )
    page.fill("#input", "thanks")
    page.press("#input", "Enter")
    page.wait_for_selector(".row.recap.folded")
    turns_done(page, 4)  # mid-turn, recap.request answers busy even when forced
    page.click("#nav-recap")
    page.wait_for_function(
        "() => document.querySelectorAll('.row.recap').length === 2", timeout=8000
    )
    assert page.locator(".row.recap:not(.folded)").count() == 1
    # a refresh with no send between folds the earlier recap: one full box
    page.click("#nav-recap")
    page.wait_for_function(
        "() => document.querySelectorAll('.row.recap').length === 3", timeout=8000
    )
    assert page.locator(".row.recap:not(.folded)").count() == 1
    assert page.errors == []


def test_the_landing_recap_request_shows_no_hint_and_the_sparkle_does(server, page):
    page.goto(server.url)
    page.wait_for_selector("#a2[data-view=fleet]")
    spawn(page, "one")
    sid = page.evaluate("location.hash.slice(3)")
    turns_done(page, 1)
    page.click("#tab-fleet")
    page.wait_for_selector(f".card[data-id='{sid}']")
    page.click(f".tab[data-id='{sid}']")
    page.wait_for_selector("#a2[data-view=session]")
    page.wait_for_timeout(1000)  # the landing request's answer has come back
    assert page.text_content("#send-error") == ""
    page.click("#nav-recap")
    page.wait_for_selector("#send-error >> text=recap: {agent:", timeout=4000)
    assert page.errors == []


def test_coming_back_to_the_page_asks_for_a_recap_and_hiding_it_does_not(
    recap_server, page
):
    page.goto(recap_server.url)
    page.wait_for_selector("#a2[data-view=fleet]")
    spawn(page, "first")
    page.wait_for_function(
        "() => !document.querySelector('.row.prose .rm .ic.unread')", timeout=6000
    )
    # The person leaves the page: nothing on it is read while it is hidden.
    page.evaluate(
        """() => {
            window.__vis = 'hidden';
            Object.defineProperty(document, 'visibilityState',
                { configurable: true, get: () => window.__vis });
            document.dispatchEvent(new Event('visibilitychange'));
        }"""
    )
    for n in (2, 3):
        page.fill("#input", "/sleep 1")
        page.press("#input", "Enter")
        turns_done(page, n)
    page.evaluate("() => document.dispatchEvent(new Event('visibilitychange'))")
    page.wait_for_timeout(1500)  # a request fired while hidden would have landed
    assert page.locator(".row.recap").count() == 0
    page.evaluate(
        """() => {
            window.__vis = 'visible';
            document.dispatchEvent(new Event('visibilitychange'));
        }"""
    )
    page.wait_for_selector(".row.recap .ctx >> text=recap of", timeout=8000)
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
    # The navigator is drawn on the next frame.
    page.keyboard.press("Alt+ArrowUp")
    pos = "document.getElementById('nav-pos').textContent"
    page.evaluate("() => new Promise((r) => requestAnimationFrame(() => r()))")
    before = page.evaluate(pos)
    page.focus(".row.tool summary")
    assert page.eval_on_selector(".row.sel", "n => n.classList.contains('tool')")
    page.wait_for_function(f"b => {pos} !== b", arg=before, timeout=2000)
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


def test_the_navigator_counts_the_unread_and_jumps_to_the_first(server, page):
    page.goto(server.url)
    page.wait_for_selector("#a2[data-view=fleet]")
    spawn(page, "one")
    sid = page.evaluate("location.hash.slice(3)")
    page.wait_for_function(
        "() => !document.querySelector('.row.prose .rm .ic.unread')", timeout=6000
    )
    # A reply lands while away, and the page is not focused when we return,
    # so it stays unread.
    page.fill("#input", "/sleep 1")
    page.press("#input", "Enter")
    page.click("#tab-fleet")
    page.wait_for_selector(f".card[data-id='{sid}'] .ft >> text=1 unread", timeout=8000)
    page.evaluate("document.hasFocus = () => false")
    page.click(f".tab[data-id='{sid}']")
    page.wait_for_selector(".row.prose .rm .ic.unread", state="attached")
    page.wait_for_function(
        "() => document.getElementById('nav-pos').textContent"
        ".startsWith('1 unread · message ')"
    )
    unread = page.eval_on_selector(
        ".row.prose:has(.rm .ic.unread)", "n => n.dataset.id"
    )
    sel = "document.querySelector('.row.sel')?.dataset.id"
    page.keyboard.press("Alt+KeyU")
    assert page.evaluate(sel) == unread
    page.keyboard.press("Alt+ArrowUp")
    assert page.evaluate(sel) != unread
    page.click("#nav-pos")
    assert page.evaluate(sel) == unread
    page.wait_for_timeout(1500)  # still unfocused: nothing was read
    assert page.inner_text("#nav-pos").startswith("1 unread · ")
    assert page.errors == []


def test_alt_j_walks_the_sessions_that_need_you_longest_waiting_first(server, page):
    """#199: Alt+J goes to the session that has waited longest for you, lands on
    its first unread message, and cycles; the Fleet's needs-you group agrees."""
    page.goto(server.url)
    page.wait_for_selector("#a2[data-view=fleet]")
    page.evaluate("document.hasFocus = () => false")  # nothing gets read
    a, b = spawn(page, "alpha"), spawn(page, "beta")
    report(page, attention="needs_you", line="Rebase or merge?", replies=[])
    turns_done(page, 2)
    page.click(f".tab[data-id='{a}']")
    page.wait_for_selector("#a2[data-view=session]")
    report(page, attention="needs_you", line="Which branch?", replies=[])
    turns_done(page, 2)
    # b asked first, so b has waited longest, against the tab order a, b.
    page.click("#tab-fleet")
    page.wait_for_selector(".card .ask >> text=Which branch?")
    assert page.eval_on_selector_all(".card", "cs => cs.map(c => c.dataset.id)") == [
        b,
        a,
    ]
    hash_ = "location.hash.slice(3)"
    page.keyboard.press("Alt+KeyJ")
    page.wait_for_function(f"{hash_} === '{b}'")
    first = "document.querySelector('.row.prose:has(.rm .ic.unread)')?.dataset.id"
    page.wait_for_function(
        f"document.querySelector('.row.sel')?.dataset.id === {first}"
    )
    page.keyboard.press("Alt+KeyJ")
    page.wait_for_function(f"{hash_} === '{a}'")
    page.keyboard.press("Alt+KeyJ")
    page.wait_for_function(f"{hash_} === '{b}'")
    assert page.errors == []


def test_alt_j_with_nobody_waiting_says_so_and_stays(server, page):
    page.goto(server.url)
    page.wait_for_selector("#a2[data-view=fleet]")
    sid = spawn(page, "hello")
    page.wait_for_selector(".tab.on .dot.ready", timeout=6000)  # read: idle dot
    page.keyboard.press("Alt+KeyJ")
    page.wait_for_selector("#note >> text=Nobody needs you")
    assert page.evaluate("location.hash.slice(3)") == sid
    assert page.errors == []


def test_a_needs_you_tab_blinks_until_its_last_message_is_read(server, page):
    page.goto(server.url)
    page.wait_for_selector("#a2[data-view=fleet]")
    a = spawn(page, "hello")
    page.wait_for_function(
        "() => !document.querySelector('.row.prose .rm .ic.unread')", timeout=6000
    )
    b = spawn(page, "hello")
    page.click(f".tab[data-id='{a}']")
    page.wait_for_selector(f".tab.on[data-id='{a}']")
    page.evaluate("document.hasFocus = () => false")  # the question lands unseen
    report(page, attention="needs_you", line="Rebase or merge?", replies=[])
    turns_done(page, 2)
    page.click(f".tab[data-id='{b}']")
    page.evaluate("delete document.hasFocus")
    page.wait_for_selector(
        f".tab.blink[data-id='{a}'] .ic.need", state="attached", timeout=6000
    )
    page.click(f".tab[data-id='{a}']")
    page.wait_for_selector(
        f".tab:not(.blink)[data-id='{a}'] .ic.need", state="attached", timeout=6000
    )
    assert page.errors == []


def test_the_latest_button_shows_before_any_agent_message(server, page):
    page.goto(server.url)
    page.wait_for_selector("#a2[data-view=fleet]")
    spawn(page, "/bash list => a.txt")  # a turn with no agent message
    assert page.locator(".row.prose").count() == 0
    page.wait_for_selector("#nav:not([hidden])")
    assert page.is_visible("#jump")
    assert page.inner_text("#nav-pos") == ""
    assert page.is_disabled("#nav-up") and page.is_disabled("#nav-down")
    page.fill("#input", "hello")
    page.press("#input", "Enter")
    turns_done(page, 2)
    page.wait_for_function(
        "() => document.getElementById('nav-pos').textContent.includes('message ')"
    )
    assert page.is_enabled("#nav-up") and page.is_enabled("#nav-down")
    assert page.errors == []


def test_the_divider_stays_where_it_was_across_a_reconnect(server, page):
    page.add_init_script("""
      window.__sockets = [];
      const WS = window.WebSocket;
      window.WebSocket = class extends WS {
        constructor(...a) { super(...a); window.__sockets.push(this); }
      };
    """)
    page.goto(server.url)
    page.wait_for_selector("#a2[data-view=fleet]")
    spawn(page, "one")
    sid = page.evaluate("location.hash.slice(3)")
    page.wait_for_function(
        "() => !document.querySelector('.row.prose .rm .ic.unread')", timeout=6000
    )
    page.fill("#input", "/sleep 1")
    page.press("#input", "Enter")
    page.click("#tab-fleet")
    page.wait_for_selector(f".card[data-id='{sid}'] .ft >> text=1 unread", timeout=8000)
    page.click(f".tab[data-id='{sid}']")
    page.wait_for_selector(".row.since")
    at = page.eval_on_selector(".row.since", "n => n.dataset.id")
    # Read on screen: a fresh placement now would find nothing unread.
    page.wait_for_function(
        "() => !document.querySelector('.row.prose .rm .ic.unread')", timeout=6000
    )
    # A reconnect resubscribes and takes a fresh snapshot of the same session.
    page.evaluate("delete window.__a2snapshot")
    page.evaluate("window.__sockets.at(-1).close()")
    page.wait_for_function(
        "window.__a2snapshot && window.__a2snapshot.painted", timeout=8000
    )
    assert page.eval_on_selector_all(
        ".row.since", "ns => ns.map(n => n.dataset.id)"
    ) == [at]
    assert page.errors == []


def test_more_than_500_messages_read_at_once_go_out_in_batches(
    tmp_path, fake_claude, browser
):
    # 600 one-line replies in one turn, read in one tick: more than one
    # session.read may carry (its ids are capped at 500).
    text = [
        {
            "type": "assistant",
            "message": {"content": [{"type": "text", "text": f"m{i}"}]},
        }
        for i in range(600)
    ]
    end = {"type": "result", "subtype": "success", "is_error": False, "num_turns": 1}
    store = tmp_path / "many.jsonl"
    store.write_text(
        "".join(
            json.dumps({"src": "claude", "line": json.dumps(x)}) + "\n"
            for x in [*text, end]
        )
    )
    (tmp_path / ".aegis.yaml").write_text(CONFIG)
    s = Server(tmp_path, fake_claude)
    s.env = {"FAKE_CLAUDE_REPLAY": str(store)}
    s.start()
    try:
        pg = new_page(browser, errors := [])
        pg.add_init_script("""
          window.__reads = [];
          const send = WebSocket.prototype.send;
          WebSocket.prototype.send = function (data) {
            const m = JSON.parse(data);
            if (m.op === "session.read") window.__reads.push(m.params.ids.length);
            return send.call(this, data);
          };
        """)
        # Tall enough that every row is on screen at once.
        pg.set_viewport_size({"width": 1280, "height": 40000})
        pg.goto(s.url)
        pg.wait_for_selector("#a2[data-view=fleet]")
        spawn(pg)
        pg.evaluate("document.hasFocus = () => false")  # nothing is read yet
        pg.fill("#input", "replay")
        pg.press("#input", "Enter")
        pg.wait_for_function(
            "() => document.getElementById('nav-pos').textContent"
            ".startsWith('600 unread')",
            timeout=30_000,
        )
        turns_done(pg, 1)  # no patch comes later to trim the mounted rows
        pg.click("#nav-pos")  # the first unread: mounts every row down from it
        assert pg.locator(".row.prose .rm .ic.unread").count() == 600
        # Every row on screen for over a second before the page is focused, so
        # the first tick takes all 600: a read's patch trims the mounted rows.
        pg.wait_for_timeout(2000)
        pg.evaluate("delete document.hasFocus")
        pg.wait_for_function(
            "() => !document.querySelector('.row.prose .rm .ic.unread')",
            timeout=10_000,
        )
        reads = pg.evaluate("window.__reads")
        assert reads == [500, 100]
        assert errors == []
    finally:
        s.stop()


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
    pick(pg, "#theme", "logbook")
    assert quote("#2f5ba8") in pg.get_attribute("#favicon", "href")
    pg.click("#tab-fleet")
    assert pg.title().startswith("(1) Fleet")
    assert errors == []
    ctx.close()


@pytest.mark.parametrize("width", [1000, 1100, 1366])
def test_each_quota_bar_shares_a_row_with_its_label_and_value(
    quota_server, browser, width
):
    errors: list = []
    page = new_page(browser, errors)
    page.set_viewport_size({"width": width, "height": 800})
    page.goto(quota_server.url)
    page.wait_for_selector("#band-quota .gauge")
    rows = page.evaluate(
        """[...document.querySelectorAll('#band-quota .gauge')]
            .filter(g => g.children.length === 3)
            .map(g => [...g.children].map(c => Math.round(c.getBoundingClientRect().top)))"""
    )
    assert rows, "no quota gauges drawn"
    for tops in rows:
        assert max(tops) - min(tops) < 12, tops
    assert errors == []


def test_close_asks_in_an_aegis_dialog_and_esc_cancels_without_interrupting(
    server, page
):
    native: list = []
    page.on("dialog", lambda d: native.append(d.message))
    page.goto(server.url)
    page.wait_for_selector("#a2[data-view=fleet]")
    a = spawn(page)
    page.fill("#input", "/sleep 3")
    page.press("#input", "Enter")
    page.wait_for_selector(".row.tool.running")
    page.click("#close")
    page.wait_for_selector("#dialog .ok", state="visible")
    assert "Close" in page.inner_text("#dialog .q")
    page.keyboard.press("Escape")
    page.wait_for_selector("#dialog", state="hidden")
    assert page.is_visible(".row.tool.running"), "Esc on the dialog interrupted"
    assert tab_ids(page) == [a]
    page.fill("#input", "/close")
    page.press("#input", "Enter")
    page.click("#dialog .cancel")
    assert tab_ids(page) == [a]
    close_session(page)
    page.wait_for_selector("#a2[data-view=fleet]")
    assert tab_ids(page) == [] and native == [] and page.errors == []


def test_the_interrupt_sits_beside_send_and_restart_sends_continue(server, page):
    page.goto(server.url)
    page.wait_for_selector("#a2[data-view=fleet]")
    spawn(page)
    assert page.locator("#stop").count() == 0, "the text Stop under the box is gone"
    assert page.is_hidden("#interrupt")
    page.fill("#input", "/sleep 5")
    page.press("#input", "Enter")
    page.wait_for_selector(".row.tool.running")
    # The card's state is drawn on the next frame (#158), so the row can land
    # a frame before the button shows: wait for what a person sees.
    page.wait_for_selector("#interrupt", state="visible")
    box = page.locator(".composer .box").bounding_box()
    btn = page.locator("#interrupt").bounding_box()
    assert box["y"] <= btn["y"] and btn["y"] + btn["height"] <= box["y"] + box["height"]
    assert page.is_disabled("#restart")
    page.click("#interrupt")
    turns_done(page, 1)
    assert page.is_hidden("#interrupt")
    page.click("#restart")
    page.wait_for_selector(".row.user >> text=Continue")
    turns_done(page, 2)
    assert page.errors == []


def phone(browser, errors: list, landscape: bool = False):
    size = {"width": 844, "height": 390} if landscape else {"width": 390, "height": 844}
    pg = browser.new_context(viewport=size, has_touch=True, is_mobile=True).new_page()
    pg.on("pageerror", lambda e: errors.append(str(e)))
    return pg


WIDER = """[...document.querySelectorAll('#a2 *')].filter(e => {
  const r = e.getBoundingClientRect();
  return r.width > 0 && (r.left < -1 || r.right > innerWidth + 1) && !e.closest('.tablist, .side, pre, .diff');
}).map(e => e.className || e.tagName).slice(0, 5)"""


def test_a_phone_reaches_tabs_the_drawer_and_the_chips(server, browser):
    errors: list = []
    page = phone(browser, errors)
    page.goto(server.url)
    page.wait_for_selector("#a2[data-view=fleet]")
    a = spawn(page, "first")
    b = spawn(page, "second")
    assert page.evaluate(WIDER) == []
    page.tap(f"#tablist .tab[data-id='{a}']")
    page.wait_for_function("id => location.hash === '#s=' + id", arg=a)
    assert page.locator(".side").bounding_box()["x"] >= 389, "the drawer starts closed"
    page.tap("#side-btn")
    page.wait_for_function("document.getElementById('a2').dataset.side === 'open'")
    page.wait_for_timeout(250)  # the slide
    assert page.locator(".side").bounding_box()["x"] < 390 - 300
    assert page.is_visible("#restart") and page.is_visible("#close")
    page.mouse.click(20, 400)  # the dimmed transcript
    page.wait_for_function("document.getElementById('a2').dataset.side !== 'open'")
    # The header stays above the dimmed page: ☰ closes the drawer, and a tab
    # switches session with it open.
    page.tap("#side-btn")
    page.wait_for_function("document.getElementById('a2').dataset.side === 'open'")
    page.tap("#side-btn")
    page.wait_for_function("document.getElementById('a2').dataset.side !== 'open'")
    page.tap("#side-btn")
    page.wait_for_function("document.getElementById('a2').dataset.side === 'open'")
    page.tap(f"#tablist .tab[data-id='{b}']")
    page.wait_for_function("id => location.hash === '#s=' + id", arg=b)
    page.wait_for_function("document.getElementById('a2').dataset.side !== 'open'")
    # Enter adds a line on a touch screen; the button sends.
    page.tap("#input")
    page.keyboard.type("one")
    page.keyboard.press("Enter")
    page.keyboard.type("two")
    assert page.input_value("#input") == "one\ntwo"
    page.tap("#send")
    turns_done(page, 2)
    # Reply chips: one per row, 44 px or taller, and a long one wraps.
    page.evaluate(
        """(() => { const r = document.getElementById('replies'); r.hidden = false;
        r.innerHTML = '<span class=lbl>reply</span>' + ['Yes', 'No', 'x '.repeat(80)]
          .map(t => '<button class=rp>' + t + '</button>').join(''); })()"""
    )
    chips = page.eval_on_selector_all(
        ".rp", "bs => bs.map(b => b.getBoundingClientRect().toJSON())"
    )
    assert all(c["height"] >= 44 for c in chips)
    assert len({round(c["x"]) for c in chips}) == 1 and chips[0]["width"] > 300
    assert page.evaluate(WIDER) == []
    assert errors == []


def test_a_phone_in_landscape_gets_the_desktop_layout_with_touch_targets(
    server, browser
):
    errors: list = []
    page = phone(browser, errors, landscape=True)
    page.goto(server.url)
    page.wait_for_selector("#a2[data-view=fleet]")
    spawn(page)
    assert page.is_visible(".side") and page.is_hidden("#side-btn")
    assert page.locator("#send").bounding_box()["height"] >= 44
    assert errors == []


def frames_on(pg) -> list[str]:
    got: list[str] = []
    # A lambda: Playwright marks its handlers, and a builtin takes no attribute.
    pg.on("websocket", lambda ws: ws.on("framereceived", lambda f: got.append(f)))
    return got


def test_a_tool_row_loads_its_output_when_opened(server, page):
    frames = frames_on(page)
    page.goto(server.url)
    page.wait_for_selector("#a2[data-view=fleet]")
    spawn(page, "/bash list it => SECRET-OUT")
    assert page.locator(".row.tool pre.out").count() == 0, "the tail came unasked"
    assert not any('"tail"' in f for f in frames if '"kind": "tool"' in f)
    page.click(".row.tool summary")
    page.wait_for_selector(".row.tool pre.out >> text=SECRET-OUT")
    page.reload()
    page.wait_for_selector(".row.tool")
    page.click(".row.tool summary")
    page.wait_for_selector(".row.tool pre.out >> text=SECRET-OUT")
    assert page.errors == []


def test_an_open_row_refetches_when_its_result_lands(server, page):
    page.goto(server.url)
    page.wait_for_selector("#a2[data-view=fleet]")
    spawn(page)
    page.fill("#input", "/sleep 2")
    page.press("#input", "Enter")
    page.wait_for_selector(".row.tool.running")
    page.click(".row.tool.running summary")
    turns_done(page, 1)
    page.wait_for_selector(".row.tool.ok pre.out", state="visible")
    assert page.errors == []


def test_returning_to_a_tab_receives_only_what_changed(server, page):
    frames = frames_on(page)
    page.goto(server.url)
    page.wait_for_selector("#a2[data-view=fleet]")
    a = spawn(page, "alpha " + "x" * 3000)
    spawn(page, "beta")
    rows = page.evaluate("document.querySelectorAll('#entries .row').length")
    frames.clear()
    t0 = page.evaluate("performance.now()")
    page.click(f"#tablist .tab[data-id='{a}']")
    page.wait_for_function("id => location.hash === '#s=' + id", arg=a)
    # The cached rows show before the delta arrives: wait for the delta itself.
    page.wait_for_function("t => (window.__a2snapshot?.at ?? 0) > t", arg=t0)
    for _ in range(40):  # Playwright reports the frame on its own schedule
        snaps = [f for f in frames if '"t": "snapshot"' in f and f"transcript:{a}" in f]
        if snaps:
            break
        page.wait_for_timeout(50)
    page.wait_for_selector(".row.user >> text=alpha")
    assert snaps and all('"since"' in f for f in snaps), snaps
    assert sum(map(len, snaps)) < 1500, [len(f) for f in snaps]
    assert page.evaluate("document.querySelectorAll('#entries .row').length") >= 3
    assert rows >= 3 and page.errors == []


# -- dictation ----------------------------------------------------------------
# The stub engine in tests/fixtures/dictation answers each chunk with its length
# and keyword count, so the client's cutting, ordering and insertion can be
# checked without the 17.8 MB model.


@pytest.fixture
def dict_server(tmp_path: Path, fake_claude: str, fake_opencode: str):
    from aegis.dictation import PINS, pin_id

    d = tmp_path / "dictation" / pin_id()
    d.mkdir(parents=True)
    stub = (Path(__file__).parent / "fixtures" / "dictation" / "needle.js").read_bytes()
    for p in PINS:
        (d / p.name).write_bytes(stub if p.name == "needle.js" else b"stub")
    (tmp_path / ".aegis.yaml").write_text(CONFIG)
    s = Server(tmp_path, fake_claude, fake_opencode)
    s.env["AEGIS_DICTATION_DIR"] = str(d.parent)
    s.start()
    yield s
    s.stop()


def run_dictation(pg, body: str):
    """Run ``body`` with the dictation module as ``m``, sine ``tone(s)`` and
    silent ``gap(s)`` arrays, a ``source`` whose ``feed`` pushes samples in
    100 ms blocks, and ``prepare`` answering the server's pinned base."""
    from aegis.dictation import pin_id

    return pg.evaluate(
        """async ([base, body]) => {
            const m = await import('/static/js/dictation.js');
            const tone = (s) => Float32Array.from({ length: Math.round(s * 16000) }, (_, i) => 0.3 * Math.sin(i / 5));
            const gap = (s) => new Float32Array(Math.round(s * 16000));
            const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
            const until = async (f, ms = 15000) => { const t = Date.now(); while (!f()) { if (Date.now() - t > ms) throw new Error('timed out waiting for ' + f); await sleep(20); } };
            let push = null, ended = null;
            const source = async (on, end) => { push = on; ended = end; return async () => {}; };
            const endCapture = (why) => ended(why);
            const feed = (...parts) => { for (const p of parts) for (let i = 0; i < p.length; i += 1600) push(p.subarray(i, i + 1600)); };
            const prepare = async () => ({ base, keywords: ['aegis', 'pull request'] });
            return await new (Object.getPrototypeOf(async function () {}).constructor)(
                'm', 'tone', 'gap', 'sleep', 'until', 'source', 'feed', 'prepare', 'endCapture', body,
            )(m, tone, gap, sleep, until, source, feed, prepare, endCapture);
        }""",
        [f"/dictation/{pin_id()}/", body],
    )


def test_dictation_cuts_provisional_pieces_at_4_to_8s_and_a_final_past_20s(
    dict_server, page
):
    page.goto(dict_server.url)
    got = run_dictation(
        page,
        """const out = []; const port = new MessageChannel().port1;
        // Emitted parts are handed to a worker, which detaches their buffers.
        const c = new m.Chunker((p) => { out.push({ final: p.final, secs: p.parts.map((a) => +(a.length / 16000).toFixed(2)) }); port.postMessage(p.parts, p.parts.map((a) => a.buffer)); });
        for (const p of [tone(5), gap(0.5), tone(17), gap(0.5), tone(3)])
            for (let i = 0; i < p.length; i += 1600) c.push(p.subarray(i, i + 1600));
        return out;""",
    )
    prov = [p for p in got if not p["final"]]
    fin = [p for p in got if p["final"]]
    assert 5.0 <= prov[0]["secs"][0] <= 5.4, "the pause after 5 s cuts the first piece"
    # A pure tone has no quiet point, so the cut inside [4 s, 8 s] is wherever
    # rounding puts it: assert the range, not the place.
    assert all(4.0 <= p["secs"][0] <= 8.0 for p in prov[1:]), prov
    assert len(fin) == 1 and len(fin[0]["secs"]) == 1
    idx = got.index(fin[0])
    before = [p["secs"][0] for p in got[:idx]]
    assert 20 <= fin[0]["secs"][0] <= 28 and sum(before[:-1]) < 20, (
        "closed at the first boundary past 20 s"
    )
    assert abs(fin[0]["secs"][0] - sum(before)) < 0.05, (
        "the final is exactly the joined provisional pieces"
    )


def test_dictation_voice_gate_skips_quiet_pieces_but_the_final_spans_them(
    dict_server, page
):
    page.goto(dict_server.url)
    got = run_dictation(
        page,
        """const out = []; const c = new m.Chunker((p) => out.push({ final: p.final, secs: +(p.parts[0].length / 16000).toFixed(1) }));
        const quiet = [tone(0.5), gap(3.5)];
        for (let k = 0; k < 6; k++) for (const p of quiet) for (let i = 0; i < p.length; i += 1600) c.push(p.subarray(i, i + 1600));
        return { out, voiced: m.voiceSecs(tone(2)), quiet: m.voiceSecs(gap(2)), sliver: m.voiceSecs(tone(0.5)) };""",
    )
    assert got["voiced"] >= 1.8 and got["quiet"] == 0 and 0.3 <= got["sliver"] <= 0.6
    assert [p for p in got["out"] if not p["final"]] == [], (
        "no provisional piece had a second of voice"
    )
    fin = [p for p in got["out"] if p["final"]]
    assert len(fin) == 1 and fin[0]["secs"] >= 20, (
        "the final still spans the quiet pieces"
    )


def test_dictation_finish_returns_the_pending_piece_then_the_tail_in_halves(
    dict_server, page
):
    page.goto(dict_server.url)
    got = run_dictation(
        page,
        """const fin = (only, ...parts) => { const c = new m.Chunker(() => {}); for (const p of parts) c.push(p); return c.finish(only).map((p) => ({ final: p.final, secs: p.parts.map((a) => +(a.length / 16000).toFixed(1)) })); };
        return { long: fin(false, tone(6), gap(0.3), tone(8)), short: fin(false, tone(4)),
                 silent: fin(false, gap(5)), sliver: fin(false, tone(0.5)), only: fin(true, tone(0.5)) };""",
    )
    long = got["long"]
    assert all(not p["final"] for p in long[:-1]) and long[-1]["final"]
    assert len(long[-1]["secs"]) == 2 and 14.1 <= sum(long[-1]["secs"]) <= 14.4, (
        "the 14.3 s stretch splits in two"
    )
    assert got["short"] == [
        {"final": False, "secs": [4.0]},
        {"final": True, "secs": [4.0]},
    ]
    assert got["silent"] == [] and got["sliver"] == []
    assert got["only"] == [{"final": True, "secs": [0.5]}], (
        "a one-word recording is still transcribed"
    )


def test_dictation_shows_provisional_text_and_replaces_it_with_the_final(
    dict_server, page
):
    page.goto(dict_server.url)
    got = run_dictation(
        page,
        """const el = document.createElement('textarea'); document.body.append(el);
        el.value = 'Before. After.'; el.setSelectionRange(7, 7);
        const errors = [], seen = [];
        el.addEventListener('input', () => seen.push(el.value));
        const d = new m.Dictation({ prepare, onError: (e) => errors.push(e) });
        await d.start({ el, key: null, current: () => null }, source);
        // Four 5 s phrases with pauses: pieces of 5.15 and 5.5 s, a final at 21.65 s.
        for (let k = 0; k < 4; k++) feed(tone(5), gap(0.5));
        await until(() => (/\\[2\\d\\.\\ds/.test(el.value)));
        const afterFinal = el.value;
        el.value += ' typed';
        // Two phrases and 3 s pending at stop: an 11 s stretch plus the 3 s tail.
        for (let k = 0; k < 2; k++) feed(tone(5), gap(0.5));
        feed(tone(3));
        await d.stop();
        await until(() => !(d.state !== 'idle'));
        if (errors.length) throw new Error(errors.join('; '));
        return { seen, afterFinal, end: el.value };""",
    )
    prov = [
        v
        for v in got["seen"]
        if re.search(r"\[5\.\ds kw=2\]", v) and not re.search(r"\[2\d\.", v)
    ]
    assert len(prov) >= 4, got["seen"]
    assert re.fullmatch(r"Before\. \[2\d\.\ds kw=2\] After\.", got["afterFinal"]), got[
        "afterFinal"
    ]
    # At stop the 3 s pending piece showed first, then the 14 s stretch came back
    # as two halves that replaced the provisional pieces together.
    assert any(re.search(r"\[3\.[0-4]s kw=2\]", v) for v in got["seen"]), (
        "the pending piece showed first"
    )
    assert re.fullmatch(
        r"Before\. \[2\d\.\ds kw=2\] \[\d+\.\ds kw=2\] \[\d+\.\ds kw=2\] After\. typed",
        got["end"],
    ), got["end"]


def test_dictation_keeps_an_edit_inside_provisional_text_and_drops_the_final(
    dict_server, page
):
    page.goto(dict_server.url)
    got = run_dictation(
        page,
        """const el = document.createElement('textarea'); document.body.append(el);
        const d = new m.Dictation({ prepare });
        await d.start({ el, key: null, current: () => null }, source);
        feed(tone(4.5), gap(0.5));
        await until(() => (el.value.includes('kw=')));
        el.value = el.value.replace('kw=2', 'EDITED');
        for (let k = 0; k < 3; k++) feed(tone(5), gap(0.5));
        await until(() => !(d.queue.length || d.workers.some((w) => w.job)));
        await sleep(100);
        await d.stop();
        await until(() => !(d.state !== 'idle'));
        return el.value;""",
    )
    assert got.startswith("[4.7s EDITED] [5.5s kw=2]"), got
    assert not re.search(r"\[2\d\.", got), "the final over the edited span was dropped"


def test_dictation_replaces_the_span_after_typing_before_it(dict_server, page):
    page.goto(dict_server.url)
    got = run_dictation(
        page,
        """const el = document.createElement('textarea'); document.body.append(el);
        const d = new m.Dictation({ prepare });
        await d.start({ el, key: null, current: () => null }, source);
        feed(tone(4.5), gap(0.5));
        await until(() => (el.value.includes('kw=')));
        el.value = 'Typed first. ' + el.value;
        for (let k = 0; k < 3; k++) feed(tone(5), gap(0.5));
        await until(() => (/\\[2\\d\\.\\ds/.test(el.value)));
        await d.stop();
        await until(() => !(d.state !== 'idle'));
        return el.value;""",
    )
    assert re.fullmatch(r"Typed first\. \[2\d\.\ds kw=2\]", got), got


def test_dictation_picks_provisional_jobs_before_final_ones(dict_server, page):
    page.goto(dict_server.url)
    got = run_dictation(
        page,
        """return [m.pick([{ final: true }, { final: true }, { final: false }]),
                m.pick([{ final: true }]), m.pick([{ final: false }, { final: true }])];""",
    )
    assert got == [2, 0, 0]


def test_dictation_puts_late_text_in_the_draft_the_textarea_no_longer_shows(
    dict_server, page
):
    page.goto(dict_server.url)
    got = run_dictation(
        page,
        """const el = document.createElement('textarea'); document.body.append(el);
        let showing = 'aegis.draft.a';
        localStorage.setItem('aegis.draft.a', 'draft of a');
        const d = new m.Dictation({ prepare });
        await d.start({ el, key: 'aegis.draft.a', current: () => showing }, source);
        feed(tone(3));
        showing = 'aegis.draft.b'; el.value = 'b is shown';
        await d.stop();
        while (d.state !== 'idle') await sleep(20);
        return [el.value, localStorage.getItem('aegis.draft.a')];""",
    )
    assert got[0] == "b is shown"
    assert got[1].startswith("draft of a [3.0s kw=2]")


def test_dictation_says_why_when_the_browser_ends_the_capture(dict_server, page):
    page.goto(dict_server.url)
    got = run_dictation(
        page,
        """const el = document.createElement('textarea'); document.body.append(el);
        const errors = [], states = [];
        const d = new m.Dictation({ prepare, onError: (e) => errors.push(e), onState: (s) => states.push(s) });
        await d.start({ el, key: null, current: () => null }, source);
        feed(tone(3));
        endCapture('track');
        await until(() => d.state === 'idle');
        const first = { errors: [...errors], text: el.value };
        await d.start({ el, key: null, current: () => null }, source);
        feed(tone(2));
        await d.stop('button');
        await until(() => d.state === 'idle');
        return { first, errors, states };""",
    )
    assert got["first"]["errors"] == [
        "Microphone stopped: the browser ended the microphone"
    ]
    assert "[3.0s" in got["first"]["text"], (
        "what was said before the track ended still lands"
    )
    assert len(got["errors"]) == 1, "a stop the person asked for says nothing"
    assert got["states"][-1] == "idle"


@pytest.fixture
def mic_page(tmp_path: Path):
    """A page whose microphone is Chromium's fake device playing 4 s of tone
    and 1 s of silence, on a loop, with the permission already granted."""
    import math
    import wave

    wav = tmp_path / "speech.wav"
    with wave.open(str(wav), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(16000)
        w.writeframes(
            b"".join(
                (int(9000 * math.sin(i / 5)) if i < 4 * 16000 else 0).to_bytes(
                    2, "little", signed=True
                )
                for i in range(5 * 16000)
            )
        )
    with playwright.sync_playwright() as p:
        b = p.chromium.launch(
            args=[
                "--use-fake-ui-for-media-stream",
                "--use-fake-device-for-media-stream",
                f"--use-file-for-fake-audio-capture={wav}",
            ]
        )
        ctx = b.new_context(
            viewport={"width": 1280, "height": 800}, permissions=["microphone"]
        )
        errors: list = []
        pg = ctx.new_page()
        pg.on("pageerror", lambda e: errors.append(str(e)))
        pg.errors = errors
        yield pg
        b.close()


def dictate_for(pg, mic: str, seconds: float, press=None) -> None:
    (press or (lambda: pg.click(mic)))()
    pg.wait_for_selector(f"{mic}[data-state=listening]", timeout=10000)
    pg.wait_for_timeout(int(seconds * 1000))


def test_the_mic_button_inserts_dictated_text_and_sends_nothing(dict_server, mic_page):
    pg = mic_page
    pg.goto(dict_server.url)
    pg.wait_for_selector("#a2[data-view=fleet]")
    spawn(pg)
    pg.fill("#input", "Look at")
    dictate_for(pg, "#mic", 2.5)
    pg.click("#mic")
    pg.wait_for_selector("#mic[data-state=idle]", timeout=10000)
    v = pg.input_value("#input")
    assert re.fullmatch(r"Look at \[\d\.\ds kw=\d+\]", v), v
    assert pg.locator(".row.sys .body", has_text="done in").count() == 0
    assert pg.errors == []


def test_enter_while_dictating_sends_what_was_said(dict_server, mic_page):
    pg = mic_page
    pg.goto(dict_server.url)
    pg.wait_for_selector("#a2[data-view=fleet]")
    spawn(pg)
    dictate_for(pg, "#mic", 2.5)
    pg.press("#input", "Enter")
    turns_done(pg, 1)
    assert pg.get_attribute("#mic", "data-state") == "idle"
    assert pg.input_value("#input") == ""
    assert pg.locator(".row", has_text="kw=").count() >= 1, "the dictated text was sent"


def test_alt_m_dictates_and_a_tab_switch_sends_late_text_to_its_own_draft(
    dict_server, mic_page
):
    pg = mic_page
    pg.goto(dict_server.url)
    pg.wait_for_selector("#a2[data-view=fleet]")
    a = spawn(pg)
    spawn(pg)
    pg.click(f"#tablist .tab[data-id='{a}']")
    pg.wait_for_function("(a) => location.hash === `#s=${a}`", arg=a)
    pg.focus("#input")
    dictate_for(pg, "#mic", 2.5, press=lambda: pg.keyboard.press("Alt+KeyM"))
    pg.click(f"#tablist .tab:not([data-id='{a}'])")
    pg.wait_for_selector("#mic[data-state=idle]", timeout=10000)
    assert "kw=" not in pg.input_value("#input")
    draft = pg.evaluate("(a) => localStorage.getItem(`aegis.draft.${a}`) || ''", a)
    assert "kw=" in draft
    pg.click(f"#tablist .tab[data-id='{a}']")
    pg.wait_for_function("() => document.getElementById('input').value.includes('kw=')")
    assert pg.errors == []


def test_the_new_tab_box_dictates_too(dict_server, mic_page):
    pg = mic_page
    pg.goto(dict_server.url)
    pg.wait_for_selector("#a2[data-view=fleet]")
    pg.click("#tab-add")
    pg.wait_for_selector("#a2[data-view=spawn]")
    dictate_for(pg, "#sp-mic", 2.5)
    pg.click("#sp-mic")
    pg.wait_for_selector("#sp-mic[data-state=idle]", timeout=10000)
    assert "kw=" in pg.input_value("#sp-text")
    assert pg.get_attribute("#mic", "data-state") == "idle"


def test_the_mic_is_disabled_without_a_microphone_api(dict_server, page):
    page.add_init_script(
        "Object.defineProperty(Navigator.prototype, 'mediaDevices', { get: () => undefined })"
    )
    page.goto(dict_server.url)
    page.wait_for_selector("#a2[data-view=fleet]")
    spawn(page)
    assert page.locator("#mic").is_disabled()
    assert "https" in page.get_attribute("#mic", "title")
    page.focus("#input")
    page.keyboard.press("Alt+KeyM")
    assert page.get_attribute("#mic", "data-state") == "idle"


def test_a_model_that_cannot_be_had_says_so_and_stops(
    tmp_path, fake_claude, fake_opencode, mic_page
):
    blocked = tmp_path / "not-a-dir"
    blocked.write_text("a file where the cache directory goes")
    (tmp_path / ".aegis.yaml").write_text(CONFIG)
    s = Server(tmp_path, fake_claude, fake_opencode)
    s.env["AEGIS_DICTATION_DIR"] = str(blocked)
    s.start()
    try:
        pg = mic_page
        pg.goto(s.url)
        pg.wait_for_selector("#a2[data-view=fleet]")
        spawn(pg)
        pg.click("#mic")
        pg.wait_for_function(
            "() => document.getElementById('send-error').textContent.includes('Dictation model unavailable')"
        )
        pg.wait_for_selector("#mic[data-state=idle]")
        assert pg.input_value("#input") == ""
    finally:
        s.stop()


# LibriSpeech test-clean (CC BY 4.0), row 1 of the openslr/librispeech_asr split.
LIBRISPEECH_REF = (
    "THE ENGLISH FORWARDED TO THE FRENCH BASKETS OF FLOWERS OF WHICH THEY HAD MADE"
    " A PLENTIFUL PROVISION TO GREET THE ARRIVAL OF THE YOUNG PRINCESS THE FRENCH IN"
    " RETURN INVITED THE ENGLISH TO A SUPPER WHICH WAS TO BE GIVEN THE NEXT DAY"
)


def word_error_rate(ref: str, hyp: str) -> float:
    def words(s: str) -> list[str]:
        return re.sub(r"[^\w\s]", " ", s.lower()).split()

    r, h = words(ref), words(hyp)
    prev = list(range(len(h) + 1))
    for i in range(1, len(r) + 1):
        cur = [i]
        for j in range(1, len(h) + 1):
            cur.append(
                min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (r[i - 1] != h[j - 1]))
            )
        prev = cur
    return prev[-1] / len(r)


@pytest.mark.live
def test_live_dictation_transcribes_a_librispeech_clip(
    tmp_path, fake_claude, fake_opencode
):
    """The real model, downloaded through the server from Hugging Face into the
    real cache (so a second run downloads nothing), and the clip played into
    Chromium's fake microphone in real time. Its 14.2 s go out as a split tail."""
    clip = (
        Path(__file__).parent
        / "fixtures"
        / "dictation"
        / "librispeech-test-clean-row1.wav"
    )
    cache = Path(os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache")
    (tmp_path / ".aegis.yaml").write_text(CONFIG)
    s = Server(tmp_path, fake_claude, fake_opencode)
    s.env["AEGIS_DICTATION_DIR"] = str(cache / "aegis" / "dictation")
    s.start()
    try:
        with playwright.sync_playwright() as p:
            b = p.chromium.launch(
                args=[
                    "--use-fake-ui-for-media-stream",
                    "--use-fake-device-for-media-stream",
                    f"--use-file-for-fake-audio-capture={clip}%noloop",
                ]
            )
            pg = b.new_context(permissions=["microphone"]).new_page()
            pg.goto(s.url)
            pg.wait_for_selector("#a2[data-view=fleet]")
            spawn(pg)
            pg.click("#mic")
            pg.wait_for_timeout(15500)
            pg.click("#mic")
            pg.wait_for_selector("#mic[data-state=idle]", timeout=300_000)
            text = pg.input_value("#input")
            b.close()
    finally:
        s.stop()
    wer = word_error_rate(LIBRISPEECH_REF, text)
    print(f"live dictation WER {wer:.1%}: {text}")
    assert wer < 0.15, text


def test_pasting_the_token_signs_this_browser_in_and_it_stays(server, browser):
    errors: list = []
    page = new_page(browser, errors)
    bare = server.url.split("?")[0]
    page.goto(bare)
    page.wait_for_selector("#login", state="visible")
    page.fill("#login-token", "wrong")
    page.press("#login-token", "Enter")
    page.wait_for_function("document.getElementById('login-error').textContent !== ''")
    errors.clear()  # Chrome logs the refused login's 401; it is the one expected
    token = server.url.split("token=")[1]
    page.fill("#login-token", token)
    page.press("#login-token", "Enter")
    page.wait_for_selector("#a2[data-view=fleet]")
    page.reload()
    page.wait_for_selector("#a2[data-view=fleet]")
    assert page.evaluate("document.cookie") == "", "HttpOnly: no script sees it"
    # Opened from another site, as a link in a chat app would.
    page.goto(f"data:text/html,<a id=go href='{bare}'>aegis</a>")
    page.click("#go")
    page.wait_for_selector("#a2[data-view=fleet]")
    assert errors == []


def test_two_servers_on_one_host_keep_their_own_sign_in(
    server, browser, tmp_path, fake_claude
):
    (tmp_path / "other").mkdir()
    (tmp_path / "other" / ".aegis.yaml").write_text(CONFIG)
    other = Server(tmp_path / "other", fake_claude).start()
    try:
        errors: list = []
        page = new_page(browser, errors)
        page.goto(server.url)
        page.wait_for_selector("#a2[data-view=fleet]")
        page.goto(other.url)
        page.wait_for_selector("#a2[data-view=fleet]")
        page.goto(server.url.split("?")[0])
        page.wait_for_selector("#a2[data-view=fleet]")
        assert errors == []
    finally:
        other.stop()


def test_a_rotated_token_shows_the_login_instead_of_retrying(server, page):
    page.goto(server.url)
    page.wait_for_selector("#a2[data-view=fleet]")
    server.stop()
    (server.root / ".aegis" / "state" / "token").unlink()
    server.start()
    page.wait_for_selector("#login", state="visible", timeout=15000)


# -- links: alpha shows and drives beta's sessions (links.py) ---------------------
def beta_session(linked, browser, prompt: str = "from beta") -> str:
    """A session started on beta from beta's own page; its log id."""
    errors: list = []
    pg = new_page(browser, errors)
    pg.goto(linked.beta.url)
    pg.wait_for_selector("#a2[data-view=fleet]")
    lid = spawn(pg, prompt)
    pg.close()
    return lid


def test_a_linked_servers_sessions_show_under_its_band(linked, browser, page):
    lid = beta_session(linked, browser)
    page.goto(linked.alpha.url)
    page.wait_for_selector("#a2[data-view=fleet]")
    box = "#remotes .remote[data-server=beta]"
    page.wait_for_selector(f"{box} .card[data-id='beta/{lid}']")
    assert page.inner_text(f"{box} .band-server") == "beta"
    assert "linked" in page.inner_text(f"{box} .band-link")
    assert page.locator("#cards .card").count() == 0  # alpha holds none itself
    tab = page.locator(f"#tablist .tab[data-id='beta/{lid}']")
    assert tab.locator(".where").inner_text() == "beta"
    assert not page.errors


def test_a_remote_tab_takes_prompts_and_the_far_store_has_them(linked, browser, page):
    lid = beta_session(linked, browser)
    page.goto(linked.alpha.url)
    page.click(f"#tablist .tab[data-id='beta/{lid}']")
    page.wait_for_selector("#a2[data-view=session]")
    assert page.evaluate("location.hash") == f"#s=beta/{lid}"
    turns_done(page, 1)  # the first turn, folded on beta, drawn on alpha
    page.fill("#input", "hello across")
    page.press("#input", "Enter")
    turns_done(page, 2)
    store = linked.beta.root / ".aegis" / "state" / "transcripts" / f"{lid}.jsonl"
    assert "hello across" in store.read_text()
    assert not (
        linked.alpha.root / ".aegis" / "state" / "transcripts" / f"{lid}.jsonl"
    ).exists()
    page.reload()  # the hash names a far session: it must come back, not bounce to the Fleet
    page.wait_for_selector("#a2[data-view=session]")
    assert page.evaluate("location.hash") == f"#s=beta/{lid}"
    assert not page.errors


def test_a_dropped_link_greys_the_far_server_and_comes_back(linked, browser, page):
    lid = beta_session(linked, browser)
    page.goto(linked.alpha.url)
    box = "#remotes .remote[data-server=beta]"
    page.wait_for_selector(f"{box} .card[data-id='beta/{lid}']")
    linked.beta.stop()
    page.wait_for_selector(f"{box} .band.off")
    page.wait_for_selector(f"{box} .card.off")
    assert "offline since" in page.inner_text(f"{box} .band-link")
    page.click(f"{box} .card")
    page.wait_for_selector("#a2[data-view=session]")
    assert page.is_disabled("#input")
    linked.beta.start()
    page.wait_for_selector("#input:not([disabled])", timeout=20000)
    page.fill("#input", "after the drop")
    page.press("#input", "Enter")
    turns_done(page, 2)
    page.click("#tab-fleet")
    page.wait_for_selector(f"{box} .card:not(.off)")
    assert not [e for e in page.errors if "server_offline" not in e]


def test_spawn_on_a_linked_server_from_the_new_tab(linked, page):
    page.goto(linked.alpha.url)
    page.wait_for_selector("#a2[data-view=fleet]")
    page.click("#tab-add")
    page.wait_for_selector("#sp-server:not([hidden])")
    pick(page, "#sp-server", "beta")
    page.wait_for_function(
        "document.querySelector('#sp-agent').value === 'opus'"
        " && document.querySelector('#sp-cwd').value.includes('beta')"
    )
    page.fill("#sp-text", "born on beta")
    page.press("#sp-text", "Enter")
    page.wait_for_selector("#a2[data-view=session]")
    key = page.evaluate("location.hash.slice(3)")
    assert key.startswith("beta/")
    turns_done(page, 1)
    store = linked.beta.root / ".aegis" / "state" / "transcripts" / f"{key[5:]}.jsonl"
    assert "born on beta" in store.read_text()
    page.wait_for_selector(f"#tablist .tab[data-id='{key}'] .where")
    assert not page.errors


def test_settings_lists_the_link_and_edits_the_far_config(linked, page):
    page.goto(linked.alpha.url)
    page.wait_for_selector("#a2[data-view=fleet]")
    page.click("#settings-btn")
    page.wait_for_selector("#set-servers .link-beta")
    assert "linked" in page.inner_text("#set-servers .link-beta")
    assert "alpha" in page.inner_text("#set-path")
    page.click("#set-srvpick button[data-server=beta]")
    page.wait_for_function(
        "document.querySelector('#set-path')?.textContent.includes('beta')"
    )
    page.click("#set-srvpick button[data-server='']")
    page.wait_for_function(
        "document.querySelector('#set-path')?.textContent.includes('alpha')"
    )
    assert not page.errors


def test_a_far_file_card_links_only_through_via(linked, browser, page):
    f = linked.beta.root / "report.html"
    f.write_text("<h1>from beta</h1>")
    lid = beta_session(
        linked, browser, f"/mcp file_send {json.dumps({'paths': [str(f)]})}"
    )
    page.goto(linked.alpha.url)
    page.click(f"#tablist .tab[data-id='beta/{lid}']")
    page.wait_for_selector(".fcard .fbar .dl")
    dl = page.get_attribute(".fcard .dl", "href")
    assert dl.startswith("/via/beta/files/") and dl.endswith(
        "/report.html?download=1"
    ), dl
    assert page.get_attribute(".fcard .open", "href") == dl.removesuffix("?download=1")
    assert page.get_attribute(".fcard iframe", "sandbox") == "allow-scripts"
    assert page.locator(".fcard .native").count() == 0
    got = page.request.get(f"http://127.0.0.1:{linked.alpha.port}{dl}")
    assert got.status == 200 and "attachment" in got.headers["content-disposition"]
    assert not page.errors


def test_the_archive_filters_by_server_and_names_one_that_is_down(
    linked, browser, page
):
    errors: list = []
    pg = new_page(browser, errors)
    pg.goto(linked.beta.url)
    pg.wait_for_selector("#a2[data-view=fleet]")
    lid = spawn(pg, "to be archived on beta")
    close_session(pg)
    pg.close()
    page.goto(linked.alpha.url)
    page.wait_for_selector(f"#arch-list tr[data-id='beta/{lid}'] .where >> text=beta")
    page.wait_for_selector("#arch-servers button[data-server=beta] >> text=1")
    page.click("#arch-servers button[data-server=beta]")
    page.wait_for_selector("#arch-servers button.on[data-server=beta]")
    assert page.locator("#arch-list tr[data-id]").count() == 1
    linked.beta.stop()
    page.wait_for_selector("#arch-servers .off >> text=beta offline", timeout=20000)
