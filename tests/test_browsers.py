"""Browsers (#564): a headless Chromium the agent and the person share.

Every test drives a real Chromium through Playwright against a small site this
file serves on localhost, never the internet.
"""

import asyncio
import functools
import http.server
import threading

import pytest

from aegis import browsers as bmod
from aegis.app import App
from aegis.browsers import Browsers
from aegis.ops import Caller, OpError
from aegis.roots import make_roots

pytestmark = [pytest.mark.browser, pytest.mark.slow]

PAGE = """<!doctype html><html><head><title>Sign in</title></head><body style="margin:0">
<button id="go" style="position:absolute;left:100px;top:100px;width:120px;height:40px"
  onclick="document.title='clicked'">Go</button>
<form style="position:absolute;left:0;top:200px" onsubmit="event.preventDefault()">
  <label>Email <input id="email" name="email" oninput="document.title='typed '+this.value"></label>
  <label>Password <input id="pw" type="password" name="pw"></label>
  <input type="checkbox" id="robot" aria-label="I am not a robot">
</form>
<script>console.error("boom from the page")</script>
</body></html>"""


@pytest.fixture
def site(tmp_path):
    root = tmp_path / "site"
    root.mkdir()
    (root / "index.html").write_text(PAGE)
    handler = functools.partial(
        http.server.SimpleHTTPRequestHandler, directory=str(root)
    )
    handler.log_message = lambda *a: None
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_address[1]}/index.html"
    srv.shutdown()


class World:
    def __init__(self, tmp_path, watched=1):
        self.published: list[tuple[str, list[dict]]] = []
        self.woken: list[tuple[str, str, str]] = []
        self.watched = watched

        async def deliver(log_id, header, body):
            self.woken.append((log_id, header, body))
            return True

        self.br = Browsers(
            tmp_path / "state",
            lambda ch, ops: self.published.append((ch, ops)),
            lambda ch: self.watched,
            deliver,
        )

    def frames(self, bid):
        return [o for ch, ops in self.published if ch == f"browser:{bid}" for o in ops]


async def until(cond, timeout=10.0):
    end = asyncio.get_running_loop().time() + timeout
    while not cond():
        if asyncio.get_running_loop().time() > end:
            raise AssertionError("condition not met in time")
        await asyncio.sleep(0.05)


async def click_at(br, bid, x, y):
    await br.input(bid, [
        {"t": "mouse", "type": "mousePressed", "x": x, "y": y, "buttons": 1},
        {"t": "mouse", "type": "mouseReleased", "x": x, "y": y},
    ])  # fmt: skip


async def test_a_watched_browser_streams_frames_and_a_click_lands_where_it_points(
    tmp_path, site
):
    w = World(tmp_path)
    b = await w.br.open_("ana", site)
    try:
        await until(lambda: w.frames(b.id))
        f = w.frames(b.id)[0]
        assert f["frame"] and f["w"] == 1280
        await click_at(w.br, b.id, 150, 120)
        await until(lambda: any(e["kind"] == "click" for e in b.events))
        click = next(e for e in b.events if e["kind"] == "click")
        assert (click["who"], click["role"], click["name"]) == (
            "person",
            "button",
            "Go",
        )
        assert await b.page.title() == "clicked"
        assert any(e["kind"] == "console" and "boom" in e["text"] for e in b.events)
    finally:
        await w.br.shutdown()


async def test_an_unwatched_browser_sends_no_frames(tmp_path, site):
    w = World(tmp_path, watched=0)
    b = await w.br.open_("ana", site)
    try:
        await asyncio.sleep(1.5)
        assert w.frames(b.id) == []
    finally:
        await w.br.shutdown()


async def test_the_person_cannot_type_while_the_agent_holds_the_page(tmp_path, site):
    w = World(tmp_path)
    b = await w.br.open_("ana", site, session="s-1")
    try:
        with pytest.raises(OpError) as e:
            await click_at(w.br, b.id, 150, 120)
        assert e.value.code == "agent_has_control"
        w.br.take(b.id)
        await click_at(w.br, b.id, 150, 120)
        await until(lambda: any(e["kind"] == "click" for e in b.events))
    finally:
        await w.br.shutdown()


async def test_a_handover_and_give_back_wake_the_agent_with_what_the_person_did(
    tmp_path, site
):
    w = World(tmp_path)
    b = await w.br.open_("ana", site, session="s-1")
    try:
        w.br.handover(b, "Pass the captcha")
        assert w.br.card(b)["ask"] == "Pass the captcha"
        await click_at(w.br, b.id, 150, 120)
        await until(lambda: any(e["kind"] == "click" for e in b.events))
        await w.br.give_back(b.id)
        log_id, header, body = w.woken[-1]
        assert log_id == "s-1" and header.startswith(f"> from browser:{b.id} · back")
        assert 'person click button "Go"' in body
        assert w.br.card(b)["holder"] == "agent"
    finally:
        await w.br.shutdown()


async def test_typing_into_a_password_field_logs_its_length_not_its_text(
    tmp_path, site
):
    w = World(tmp_path)
    b = await w.br.open_("ana", site)
    try:
        box = await b.page.locator("#pw").bounding_box()
        x, y = box["x"] + 5, box["y"] + 5
        await click_at(w.br, b.id, x, y)
        await w.br.input(b.id, [{"t": "text", "text": "hunter22"}])
        await b.page.locator("#pw").blur()  # change fires on blur
        await until(lambda: any(e["kind"] == "input" for e in b.events))
        ev = next(e for e in b.events if e["kind"] == "input")
        assert ev["length"] == 8 and "text" not in ev
        assert "hunter22" not in (b.dir / "events.jsonl").read_text()
    finally:
        await w.br.shutdown()


async def test_a_profile_serves_one_browser_at_a_time(tmp_path, site):
    w = World(tmp_path, watched=0)
    await w.br.open_("ana", site, profile="work")
    try:
        with pytest.raises(OpError) as e:
            await w.br.open_("ana", site, profile="work")
        assert e.value.code == "profile_in_use"
    finally:
        await w.br.shutdown()


async def test_an_idle_browser_stops_and_reopens_signed_in(tmp_path, site, monkeypatch):
    monkeypatch.setattr(bmod, "IDLE_S", 0.5)
    w = World(tmp_path, watched=0)
    b = await w.br.open_("ana", site)
    try:
        await b.page.evaluate("document.cookie = 'sid=42; max-age=3600'")
        await until(lambda: b.meta["state"] == "stopped", timeout=5)
        assert b.meta["reason"] == "idle" and not b.running
        await w.br.reopen(b.id)
        assert "sid=42" in await b.page.evaluate("document.cookie")
    finally:
        await w.br.shutdown()


async def test_multi_user_mode_has_no_browsers(tmp_path, site):
    async def deliver(*a):
        return True

    br = Browsers(tmp_path, lambda *a: None, lambda ch: 0, deliver, multi_user=True)
    with pytest.raises(OpError) as e:
        await br.open_("ana", site)
    assert e.value.code == "browser_unavailable"


async def test_an_agent_drives_its_browser_through_the_tools(
    tmp_path, site, fake_claude
):
    app = App(make_roots(tmp_path, None), claude_bin=fake_claude, interrupt_timeout=0.5)
    me, other = Caller("agent", "s-me"), Caller("agent", "s-other")
    call = app.registry.call
    try:
        opened = await call("browser.open", {"url": site}, me)
        bid = opened["id"]
        snap = await call("browser.snapshot", {}, me)
        assert 'button "Go"' in snap and "holder: agent" in snap
        out = await call("browser.click", {"target": 'role=button[name="Go"]'}, me)
        assert out["title"] == "clicked"
        await call("browser.type", {"target": "#pw", "text": "s3cret"}, me)
        assert app.browsers.get(bid).meta["last_action"] == "type 6 characters into #pw"
        shot = await call("browser.screenshot", {}, me)
        assert shot["path"].endswith(".png")
        with pytest.raises(OpError) as e:
            await call("browser.snapshot", {"browser": bid}, other)
        assert e.value.code == "unknown_browser"
        await call("browser.handover", {"reason": "Tick the box"}, me)
        with pytest.raises(OpError) as e:
            await call("browser.click", {"target": "#robot"}, me)
        assert e.value.code == "person_has_control"
        evs = await call("browser.events", {"who": "agent"}, me)
        assert any("handover: Tick the box" in line for line in evs["events"])
        await call("browser.close", {}, me)
        assert bid not in app.browsers.open
    finally:
        await app.browsers.shutdown()
