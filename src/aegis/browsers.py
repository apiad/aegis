"""The server's browsers (#564): a headless Chromium the agent and the person share.

A browser belongs to the server, like a terminal. It has an owner, the person
who opened it or the owner of the agent that did, and a session when an agent
drives it. One of the two holds the page at a time: ``holder`` is ``agent`` or
``person``. The person can take it at any moment; the agent gets it back only
when the person gives it, and is woken then with ``> from browser:<id> · back``.

Playwright drives each browser through a persistent context, so a profile under
``<state>/browsers/profiles/<name>/`` keeps its sign-ins across stops. The pane
sees the page through ``Page.startScreencast``, started only while the
``browser:<id>`` channel has a subscriber, and sends input back as CDP input
events. Everything else on disk is under ``<state>/browsers/<id>/``:
``meta.json``, ``events.jsonl`` and the agent's screenshots.

The card rides the ``sessions`` channel with ``kind: "browser"`` and ``log_id``
set to the id, as a terminal's does.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import secrets
import time
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

from .launch import exit_proxy, resolve_via
from .ops import OpError

log = logging.getLogger("aegis.browsers")

MAX_LIVE = 3
IDLE_S = 30 * 60
WATCH_S = 0.5
FRAME_EVERY_S = 0.1  # at most 10 frames a second to the panes
EVENTS_KEEP = 5000
SNAPSHOT_MAX = 24_000  # characters of accessibility tree an agent gets
WIDTH, HEIGHT = 1280, 800
PROFILE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,39}$")
META_KEYS = {"id", "name", "profile", "url", "owner", "holder", "state"}

# Reports what the person does in the page, with the element it hit, through
# the __aegisEvent binding. A password field gives its length, never its text.
INIT_SCRIPT = r"""
(() => {
  if (window.__aegisHooked) return;
  window.__aegisHooked = true;
  const ROLE = {A: "link", BUTTON: "button", SELECT: "combobox", TEXTAREA: "textbox",
                SUMMARY: "button", IMG: "img", OPTION: "option", LABEL: "label"};
  const INPUT = {checkbox: "checkbox", radio: "radio", submit: "button", button: "button",
                 reset: "button", range: "slider", search: "searchbox"};
  const role = (el) => el.getAttribute("role")
    || (el.tagName === "INPUT" ? INPUT[el.type] || "textbox" : ROLE[el.tagName])
    || el.tagName.toLowerCase();
  const name = (el) => {
    const label = el.labels && el.labels[0] ? el.labels[0].innerText : "";
    const t = el.getAttribute("aria-label") || label || el.getAttribute("alt")
      || el.getAttribute("title") || el.getAttribute("placeholder")
      || (el.tagName === "INPUT" ? "" : el.innerText) || el.getAttribute("name") || "";
    return t.replace(/\s+/g, " ").trim().slice(0, 80);
  };
  const target = (el) => el.closest("a,button,input,select,textarea,summary,label,[role],[onclick]") || el;
  const send = (ev) => { try { window.__aegisEvent(ev); } catch (e) {} };
  addEventListener("click", (e) => {
    const el = target(e.target);
    send({kind: "click", role: role(el), name: name(el)});
  }, true);
  addEventListener("change", (e) => {
    const el = e.target;
    if (!(el instanceof HTMLInputElement || el instanceof HTMLTextAreaElement || el instanceof HTMLSelectElement)) return;
    if (el.type === "checkbox" || el.type === "radio") return;  // the click said it
    const v = el.type === "password" ? {length: el.value.length} : {text: el.value.slice(0, 500)};
    send({kind: "input", role: role(el), name: name(el), ...v});
  }, true);
  addEventListener("submit", (e) => {
    const f = e.target;
    send({kind: "submit", name: f.getAttribute("name") || f.getAttribute("action") || ""});
  }, true);
})();
"""

Deliver = Callable[[str, str, str], Awaitable[bool]]


def mint_browser_id() -> str:
    return f"b-{time.strftime('%Y%m%d-%H%M%S')}-{secrets.token_hex(3)}"


def _iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def available() -> bool:
    """Whether the ``browser`` extra is installed."""
    try:
        import playwright.async_api  # noqa: F401
    except ImportError:
        return False
    return True


def event_line(e: dict) -> str:
    """One event as an agent reads it."""
    kind, who = e["kind"], e.get("who", "")
    head = f"#{e['rev']} {who} {kind}".replace("  ", " ")
    if kind in ("click", "input"):
        what = f'{e.get("role", "")} "{e.get("name", "")}"'
        if "length" in e:
            what += f" ({e['length']} characters, password)"
        elif "text" in e:
            what += f" = {json.dumps(e['text'], ensure_ascii=False)}"
        return f"{head} {what}"
    if kind == "navigate":
        return f"{head} {e.get('url', '')}"
    if kind == "console":
        return f"{head} {e.get('level', '')}: {e.get('text', '')}"
    if kind == "network":
        return f"{head} {e.get('method', '')} {e.get('url', '')} {e.get('status', '')}"
    if kind == "dialog":
        return f"{head} {e.get('type', '')}: {e.get('text', '')}"
    if kind == "control":
        reason = f": {e['reason']}" if e.get("reason") else ""
        return f"{head} {e.get('what', '')}{reason}"
    rest = {k: v for k, v in e.items() if k not in ("rev", "ts", "who", "kind")}
    return f"{head} {json.dumps(rest, ensure_ascii=False)}"


class Browser:
    def __init__(self, meta: dict, path: Path) -> None:
        self.meta = meta
        self.dir = path
        self.ctx: Any = None
        self.page: Any = None
        self.cdp: Any = None
        self.frame: dict | None = None
        self.casting = False
        self.last_used = time.time()
        self.last_frame = 0.0
        self.events: list[dict] = []
        self.rev = 0
        self.watcher: asyncio.Task | None = None
        self.starting: asyncio.Lock = asyncio.Lock()

    @property
    def id(self) -> str:
        return self.meta["id"]

    @property
    def running(self) -> bool:
        return self.ctx is not None

    def load_events(self) -> None:
        path = self.dir / "events.jsonl"
        if not path.exists():
            return
        for line in path.read_text().splitlines()[-EVENTS_KEEP:]:
            try:
                self.events.append(json.loads(line))
            except ValueError:
                continue
        if self.events:
            self.rev = self.events[-1]["rev"]


def launch_proxy() -> dict[str, Any]:
    """Chromium's argument for the exit in force (exit.py), or nothing. A proxy
    named at launch holds until the browser restarts, like a child's variables."""
    proxy = exit_proxy(resolve_via())
    return {} if proxy is None else {"proxy": proxy}


class Browsers:
    def __init__(
        self,
        state_root: Path,
        publish: Callable[[str, list[dict]], None],
        subscribers: Callable[[str], int],
        deliver: Deliver,
        multi_user: bool = False,
    ) -> None:
        self.dir = state_root / "browsers"
        self._publish = publish
        self._subscribers = subscribers
        self._deliver = deliver
        self._multi_user = multi_user
        self.open: dict[str, Browser] = {}
        self._pw: Any = None
        self._background: set[asyncio.Task] = set()
        # The browsers' cgroup in the work tree (cgroups.py), set at boot when
        # the unit is delegated: Chromium joins it, not the server's.
        self.cgroup: Path | None = None

    # -- what the server offers ------------------------------------------------

    def available(self) -> str | None:
        """None when browsers work here, else why not."""
        if self._multi_user:
            return "browsers do not run in multi-user mode yet"
        if not available():
            return "browsers need the extra: pip install 'aegis-harness[browser]'"
        return None

    def get(self, bid: str) -> Browser:
        b = self.open.get(bid)
        if b is None:
            raise OpError("unknown_browser", f"no open browser {bid}")
        return b

    def owner_of(self, bid: str) -> str | None:
        b = self.open.get(bid)
        return b.meta["owner"] if b else None

    def of_session(self, log_id: str, bid: str | None) -> Browser:
        """The browser an agent names, or its only one. A browser another
        session drives answers as one that does not exist."""
        mine = [b for b in self.open.values() if b.meta.get("session") == log_id]
        if bid is not None:
            b = self.open.get(bid)
            if b is None or b not in mine:
                raise OpError("unknown_browser", f"no browser {bid} of yours")
            return b
        if not mine:
            raise OpError(
                "no_browser", "you have no browser; open one with browser_open"
            )
        if len(mine) > 1:
            ids = ", ".join(b.id for b in mine)
            raise OpError(
                "which_browser", f"you have several browsers, name one: {ids}"
            )
        return mine[0]

    # -- life -----------------------------------------------------------------

    async def open_(
        self,
        owner: str,
        url: str,
        profile: str | None = None,
        session: str | None = None,
    ) -> Browser:
        if why := self.available():
            raise OpError("browser_unavailable", why)
        live = [b for b in self.open.values() if b.running]
        if len(live) >= MAX_LIVE:
            raise OpError(
                "too_many_browsers",
                f"{MAX_LIVE} browsers are running; close one first: "
                + ", ".join(b.meta["name"] for b in live),
            )
        profile = profile or "default"
        if not PROFILE.match(profile):
            raise OpError(
                "bad_profile", "a profile is lowercase letters, digits, - and _"
            )
        self._profile_free(profile)
        bid = mint_browser_id()
        meta = {
            "id": bid,
            "name": self._name("browser"),
            "profile": profile,
            "url": _url(url),
            "title": "",
            "owner": owner,
            "session": session,
            "holder": "agent" if session else "person",
            "ask": None,
            "handed_rev": None,
            "last_action": None,
            "width": WIDTH,
            "height": HEIGHT,
            "state": "starting",
            "reason": None,
            "created": time.time(),
        }
        b = Browser(meta, self.dir / bid)
        self.open[bid] = b
        try:
            await self._start(b)
        except OpError:
            del self.open[bid]
            raise
        return b

    def _profile_free(self, profile: str, but: Browser | None = None) -> None:
        for o in self.open.values():
            if o is not but and o.running and o.meta["profile"] == profile:
                raise OpError(
                    "profile_in_use",
                    f"profile {profile} is in use by {o.meta['name']} ({o.id})",
                )

    async def reopen(self, bid: str) -> None:
        b = self.get(bid)
        if b.running:
            return
        if why := self.available():
            raise OpError("browser_unavailable", why)
        if sum(o.running for o in self.open.values()) >= MAX_LIVE:
            raise OpError("too_many_browsers", f"{MAX_LIVE} browsers are running")
        self._profile_free(b.meta["profile"], but=b)
        await self._start(b)

    def _launcher(self) -> str | None:
        """A script that moves Chromium into the browsers' cgroup, then execs
        it; None to let Playwright start its own Chromium in the server's
        cgroup. No OOM score: Chromium sets its own (0 for the browser, 200 to
        300 for its children), all above the server's."""
        if self.cgroup is None:
            return None
        import shlex

        cg = shlex.quote(str(self.cgroup))
        script = self.dir / "chromium-in-cgroup.sh"
        script.parent.mkdir(parents=True, exist_ok=True)
        script.write_text(
            "#!/bin/sh\n"
            f"echo $$ > {cg}/cgroup.procs || echo 'aegis: could not join {cg}' >&2\n"
            f'exec {shlex.quote(self._pw.chromium.executable_path)} "$@"\n'
        )
        script.chmod(0o700)
        return str(script)

    async def _start(self, b: Browser) -> None:
        async with b.starting:
            if b.running:
                return
            try:
                if self._pw is None:
                    from playwright.async_api import async_playwright

                    self._pw = await async_playwright().start()
                prof = self.dir / "profiles" / b.meta["profile"]
                prof.mkdir(parents=True, exist_ok=True, mode=0o700)
                ctx = await self._pw.chromium.launch_persistent_context(
                    str(prof),
                    executable_path=self._launcher(),
                    headless=True,
                    viewport={"width": b.meta["width"], "height": b.meta["height"]},
                    args=["--no-first-run", "--no-default-browser-check"],
                    **launch_proxy(),
                )
            except Exception as e:
                raise OpError("spawn_failed", f"browser {b.meta['name']}: {e}") from e
            b.ctx = ctx
            await ctx.expose_binding(
                "__aegisEvent", lambda _src, ev: self._page_event(b, ev)
            )
            await ctx.add_init_script(INIT_SCRIPT)
            page = ctx.pages[0] if ctx.pages else await ctx.new_page()
            for extra in ctx.pages[1:]:
                await extra.close()
            self._attach(b, page)
            b.cdp = await ctx.new_cdp_session(page)
            b.cdp.on("Page.screencastFrame", lambda f: self._frame(b, f))
            ctx.on("close", lambda _c: self._closed(b))
            if not b.events:
                b.load_events()
            b.meta.update(state="running", reason=None, started_at=time.time())
            b.last_used = time.time()
            self._save(b)
            self._card(b)
            b.watcher = asyncio.create_task(self._watch(b))
        try:
            await page.goto(
                b.meta["url"], wait_until="domcontentloaded", timeout=20_000
            )
        except Exception as e:
            self._event(
                b,
                {
                    "kind": "navigate",
                    "url": b.meta["url"],
                    "error": str(e).splitlines()[0],
                },
            )

    def _attach(self, b: Browser, page: Any) -> None:
        b.page = page

        def nav(frame: Any) -> None:
            if frame is page.main_frame:
                b.meta["url"] = frame.url
                self._event(b, {"kind": "navigate", "url": frame.url})
                self._card(b)

        async def loaded() -> None:
            try:
                b.meta["title"] = await page.title()
            except Exception:
                return
            self._save(b)
            self._card(b)

        def console(msg: Any) -> None:
            if msg.type in ("error", "warning"):
                loc = msg.location or {}
                self._event(b, {"kind": "console", "level": msg.type, "text": msg.text[:500],
                                "source": loc.get("url"), "line": loc.get("lineNumber")})  # fmt: skip

        def response(r: Any) -> None:
            if r.status >= 400:
                self._event(b, {"kind": "network", "method": r.request.method,
                                "url": r.url, "status": r.status})  # fmt: skip

        def failed(req: Any) -> None:
            self._event(b, {"kind": "network", "method": req.method, "url": req.url,
                            "status": req.failure or "failed"})  # fmt: skip

        async def dialog(d: Any) -> None:
            # A headless dialog blocks the page and the pane cannot draw it:
            # answer it, and say so in the log.
            self._event(b, {"kind": "dialog", "type": d.type, "text": d.message[:500],
                            "answer": "accepted"})  # fmt: skip
            await d.accept()

        async def popup(p: Any) -> None:
            # One page per browser: a popup's address loads in the page itself.
            url = p.url
            await p.close()
            if url and url != "about:blank":
                self._event(b, {"kind": "popup", "url": url})
                await page.goto(url)

        def download(d: Any) -> None:
            self._event(b, {"kind": "download", "url": d.url, "kept": False})

        page.on("framenavigated", nav)
        page.on("load", lambda _p: self._spawn(loaded()))
        page.on("console", console)
        page.on("response", response)
        page.on("requestfailed", failed)
        page.on("dialog", lambda d: self._spawn(dialog(d)))
        page.on("popup", lambda p: self._spawn(popup(p)))
        page.on("download", download)

    async def stop(self, bid: str, reason: str) -> None:
        b = self.get(bid)
        await self._stop(b, reason)

    async def _stop(self, b: Browser, reason: str) -> None:
        if b.watcher is not None and b.watcher is not asyncio.current_task():
            b.watcher.cancel()
        b.watcher = None
        ctx, b.ctx, b.page, b.cdp, b.casting = b.ctx, None, None, None, False
        b.meta.update(state="stopped", reason=reason)
        if ctx is not None:
            try:
                await ctx.close()
            except Exception:
                pass
        self._save(b)
        if b.id in self.open:
            self._card(b)

    def _closed(self, b: Browser) -> None:
        """Chromium went away on its own (a crash, a kill)."""
        if b.ctx is not None:
            b.ctx = None
            self._spawn(self._stop(b, "crashed"))

    async def close(self, bid: str) -> None:
        b = self.get(bid)
        del self.open[bid]
        b.meta["archived"] = True
        await self._stop(b, "closed")
        self._publish("sessions", [{"remove": bid}])

    async def _watch(self, b: Browser) -> None:
        """Screencast only while a pane watches; stop a browser nobody uses."""
        try:
            while b.running:
                watched = self._subscribers(f"browser:{b.id}") > 0
                if watched:
                    b.last_used = time.time()
                if watched and not b.casting:
                    await self._cast(b, True)
                elif not watched and b.casting:
                    await self._cast(b, False)
                if not watched and time.time() - b.last_used > IDLE_S:
                    await self._stop(b, "idle")
                    return
                # A script changes the title, and pushState the address,
                # without a load: the card follows both.
                title, url = await b.page.title(), b.page.url
                if (title, url) != (b.meta.get("title"), b.meta["url"]):
                    b.meta.update(title=title, url=url)
                    self._save(b)
                    self._card(b)
                await asyncio.sleep(WATCH_S)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("aegis: browser %s watcher", b.id)

    async def _cast(self, b: Browser, on: bool) -> None:
        if b.cdp is None:
            return
        b.casting = on
        try:
            if on:
                await b.cdp.send("Page.startScreencast", {
                    "format": "jpeg", "quality": 60,
                    "maxWidth": b.meta["width"], "maxHeight": b.meta["height"]})  # fmt: skip
            else:
                await b.cdp.send("Page.stopScreencast")
        except Exception as e:
            log.debug("aegis: browser %s screencast %s: %r", b.id, on, e)

    def _frame(self, b: Browser, f: dict) -> None:
        md = f.get("metadata", {})
        b.frame = {
            "frame": f["data"],
            "w": md.get("deviceWidth"),
            "h": md.get("deviceHeight"),
        }
        self._publish(f"browser:{b.id}", [b.frame])
        now = time.monotonic()
        wait = max(0.0, FRAME_EVERY_S - (now - b.last_frame))
        b.last_frame = now + wait
        cdp = b.cdp

        async def ack() -> None:
            await asyncio.sleep(wait)
            if cdp is not None and b.cdp is cdp:
                try:
                    await cdp.send(
                        "Page.screencastFrameAck", {"sessionId": f["sessionId"]}
                    )
                except Exception:
                    pass

        self._spawn(ack())

    # -- the person's hands ----------------------------------------------------

    async def input(self, bid: str, events: list[dict]) -> None:
        b = self._live(bid)
        if b.meta["holder"] != "person":
            raise OpError(
                "agent_has_control", "the agent holds this page; take over first"
            )
        b.last_used = time.time()
        for ev in events:
            await self._dispatch(b, ev)

    async def _dispatch(self, b: Browser, ev: dict) -> None:
        t, mods = ev.get("t"), int(ev.get("mods", 0))
        if t == "mouse":
            await b.cdp.send("Input.dispatchMouseEvent", {
                "type": ev["type"], "x": ev["x"], "y": ev["y"], "modifiers": mods,
                "button": ev.get("button", "left"), "buttons": ev.get("buttons", 0),
                "clickCount": ev.get("count", 1)})  # fmt: skip
        elif t == "wheel":
            await b.cdp.send("Input.dispatchMouseEvent", {
                "type": "mouseWheel", "x": ev["x"], "y": ev["y"], "modifiers": mods,
                "deltaX": ev.get("dx", 0), "deltaY": ev.get("dy", 0)})  # fmt: skip
        elif t == "key":
            p = {"type": ev["type"], "key": ev.get("key", ""), "code": ev.get("code", ""),
                 "modifiers": mods, "windowsVirtualKeyCode": ev.get("keyCode", 0)}  # fmt: skip
            if ev.get("text"):
                p["text"] = p["unmodifiedText"] = ev["text"]
            await b.cdp.send("Input.dispatchKeyEvent", p)
        elif t == "text":
            await b.cdp.send("Input.insertText", {"text": ev.get("text", "")})
        else:
            raise OpError("bad_input", f"unknown input event {t!r}")

    async def goto(
        self, bid: str, url: str | None, action: str | None, who: str
    ) -> dict:
        b = self._live(bid)
        if who == "person" and b.meta["holder"] != "person":
            raise OpError(
                "agent_has_control", "the agent holds this page; take over first"
            )
        b.last_used = time.time()
        p = b.page
        try:
            if url:
                r = await p.goto(
                    _url(url), wait_until="domcontentloaded", timeout=30_000
                )
            elif action == "back":
                r = await p.go_back(wait_until="domcontentloaded")
            elif action == "forward":
                r = await p.go_forward(wait_until="domcontentloaded")
            else:
                r = await p.reload(wait_until="domcontentloaded")
        except Exception as e:
            raise OpError("navigation_failed", str(e).splitlines()[0]) from None
        title = await p.title()
        b.meta.update(url=p.url, title=title)
        self._save(b)
        self._card(b)
        return {"url": p.url, "title": title, "status": r.status if r else None}

    async def resize(self, bid: str, width: int, height: int) -> dict:
        b = self._live(bid)
        width, height = width // 8 * 8, height // 8 * 8
        await b.page.set_viewport_size({"width": width, "height": height})
        b.meta.update(width=width, height=height)
        if b.casting:
            await self._cast(b, False)
            await self._cast(b, True)
        self._save(b)
        self._card(b)
        return {"width": width, "height": height}

    # -- control ---------------------------------------------------------------

    def take(self, bid: str) -> None:
        """The person takes the page, whoever held it."""
        b = self.get(bid)
        if b.meta["holder"] == "person":
            return
        b.meta.update(holder="person", ask=None, handed_rev=b.rev)
        self._event(b, {"kind": "control", "what": "take over"}, who="person")
        self._save(b)
        self._card(b)

    async def give_back(self, bid: str) -> None:
        b = self.get(bid)
        if b.meta.get("session") is None:
            raise OpError(
                "no_session", "no agent drives this browser; give it to a session"
            )
        if b.meta["holder"] == "agent":
            return
        since = b.meta.get("handed_rev") or 0
        b.meta.update(holder="agent", ask=None, handed_rev=None)
        self._event(b, {"kind": "control", "what": "give back"}, who="person")
        self._save(b)
        self._card(b)
        await self._wake(b, "back", since)

    async def give(self, bid: str, log_id: str, handle: str) -> None:
        b = self.get(bid)
        if any(
            o.meta.get("session") == log_id for o in self.open.values() if o is not b
        ):
            raise OpError("has_browser", f"{handle} already drives a browser")
        b.meta.update(session=log_id, holder="agent", ask=None, handed_rev=None)
        self._event(b, {"kind": "control", "what": f"given to {handle}"}, who="person")
        self._save(b)
        self._card(b)
        await self._wake(b, "given", 0)

    def handover(self, b: Browser, reason: str) -> None:
        b.meta.update(holder="person", ask=reason, handed_rev=b.rev)
        self._event(
            b, {"kind": "control", "what": "handover", "reason": reason}, who="agent"
        )
        self._save(b)
        self._card(b)

    async def _wake(self, b: Browser, what: str, since: int) -> None:
        log_id = b.meta["session"]
        lines = [f"Browser {b.meta['name']} ({b.id}) is yours again.",
                 f"Page: {b.meta['url']}  {b.meta.get('title') or ''}".rstrip()]  # fmt: skip
        evs = [e for e in b.events if e["rev"] > since and e.get("who") == "person"]
        if what == "given":
            lines[0] = f"The person gave you browser {b.meta['name']} ({b.id})."
        if evs:
            lines.append(
                "What the person did:" if what == "back" else "What happened so far:"
            )
            lines += [event_line(e) for e in evs[-60:]]
        else:
            lines.append("The person did nothing in the page.")
        lines.append(
            "Read it with browser_snapshot; browser_events gives the full log."
        )
        header = f"> from browser:{b.id} · {what} · {_iso()}"
        await self._deliver(log_id, header, "\n".join(lines))

    # -- events ----------------------------------------------------------------

    def _page_event(self, b: Browser, ev: Any) -> None:
        if not isinstance(ev, dict) or ev.get("kind") not in (
            "click",
            "input",
            "submit",
        ):
            return
        keep = ("kind", "role", "name", "text", "length")
        self._event(b, {k: ev[k] for k in keep if k in ev})

    def _event(self, b: Browser, ev: dict, who: str | None = None) -> None:
        b.rev += 1
        e = {
            "rev": b.rev,
            "ts": round(time.time(), 3),
            "who": who or b.meta["holder"],
            **ev,
        }
        b.events.append(e)
        del b.events[:-EVENTS_KEEP]
        try:
            b.dir.mkdir(parents=True, exist_ok=True)
            with open(b.dir / "events.jsonl", "a") as f:
                f.write(json.dumps(e, ensure_ascii=False) + "\n")
        except OSError as err:
            log.warning("aegis: browser %s events: %r", b.id, err)

    def act(self, b: Browser, line: str) -> None:
        """An agent's action, logged and shown in the pane's bar."""
        b.last_used = time.time()
        b.meta["last_action"] = line
        self._event(b, {"kind": "agent", "what": line}, who="agent")
        self._card(b)

    # -- the card and the channel ---------------------------------------------

    def card(self, b: Browser) -> dict:
        m = b.meta
        return {
            "log_id": m["id"],
            "kind": "browser",
            "id": m["id"],
            "name": m["name"],
            "handle": m["name"],
            "title": m.get("title") or m["url"],
            "model": None,
            "state": m["state"],
            "reason": m.get("reason"),
            "mark": m["holder"] if b.running else "ended",
            "owner": m["owner"],
            "session": m.get("session"),
            "url": m["url"],
            "holder": m["holder"],
            "ask": m.get("ask"),
            "profile": m["profile"],
            "last_action": m.get("last_action"),
            "width": m["width"],
            "height": m["height"],
        }

    def cards(self) -> list[dict]:
        return [self.card(b) for b in self.open.values()]

    def channel(self, bid: str) -> Callable[[], dict] | None:
        if bid not in self.open:
            return None

        def snapshot() -> dict:
            b = self.open.get(bid)
            frame = b.frame if b is not None and b.running else None
            return {"id": bid, **(frame or {"frame": None})}

        return snapshot

    # -- boot and shutdown -----------------------------------------------------

    async def boot(self) -> None:
        """Load every browser's meta. The last run's end stopped every
        Chromium, so each comes back stopped, with a Reopen."""
        for path in sorted(self.dir.glob("b-*/meta.json")):
            try:
                meta = json.loads(path.read_text())
                if missing := META_KEYS - set(meta):
                    raise ValueError(f"meta.json lacks {sorted(missing)}")
                if meta.get("archived"):
                    continue
                if meta["state"] != "stopped":
                    meta.update(state="stopped", reason="server_stopped")
                b = Browser(meta, path.parent)
                b.load_events()
                self.open[meta["id"]] = b
                self._save(b)
            except Exception as e:
                log.warning("aegis: skipping browser %s: %r", path.parent.name, e)

    async def shutdown(self) -> None:
        for task in list(self._background):
            task.cancel()
        for b in list(self.open.values()):
            if b.running:
                await self._stop(b, "server_stopped")
        if self._pw is not None:
            try:
                await self._pw.stop()
            except Exception:
                pass
            self._pw = None

    # -- helpers ---------------------------------------------------------------

    def _live(self, bid: str) -> Browser:
        b = self.get(bid)
        if not b.running:
            raise OpError(
                "browser_stopped", f"browser {b.meta['name']} is stopped; reopen it"
            )
        return b

    def _spawn(self, coro: Awaitable) -> None:
        task = asyncio.ensure_future(coro)
        self._background.add(task)
        task.add_done_callback(self._background.discard)

    def _card(self, b: Browser) -> None:
        self._publish("sessions", [{"upsert": self.card(b)}])

    def _name(self, base: str) -> str:
        taken = {b.meta["name"] for b in self.open.values()}
        name, n = base, 2
        while name in taken:
            name, n = f"{base}-{n}", n + 1
        return name

    def _save(self, b: Browser) -> None:
        b.dir.mkdir(parents=True, exist_ok=True)
        tmp = b.dir / "meta.json.tmp"
        tmp.write_text(json.dumps(b.meta))
        os.replace(tmp, b.dir / "meta.json")


def _url(url: str) -> str:
    """A bare host or path becomes a URL a person would mean."""
    url = url.strip()
    if not url:
        return "about:blank"
    if re.match(r"^[a-z][a-z0-9+.-]*:", url):
        return url
    if url.startswith(("localhost", "127.", "0.0.0.0")):
        return f"http://{url}"
    return f"https://{url}"
