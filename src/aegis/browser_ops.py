"""The browsers' operations (#564, browsers.py).

A person reaches a browser through its pane: open, input, navigate, take over,
give back, give to a session, reopen, close. A member drives only their own
browsers, through ops.py's ``owned=`` check, as with terminals. An agent
reaches the browsers its session drives through the ``browser_*`` tools, and
every one of them but the reads answers ``person_has_control`` while the
person holds the page.
"""

from __future__ import annotations

import asyncio
import json
import time
from typing import TYPE_CHECKING, Literal

from pydantic import BaseModel, Field

from . import layout as canvas
from .browsers import SNAPSHOT_MAX, Browser, event_line
from .ops import Caller, OpError

if TYPE_CHECKING:
    from .app import App


class _Strict(BaseModel):
    model_config = {"extra": "forbid"}


class BrowserOpen(_Strict):
    url: str = Field(description="The address to load, such as http://localhost:8000.")
    profile: str | None = Field(
        None,
        description="The profile whose sign-ins it keeps; defaults to your handle.",
    )
    dock: str | None = Field(
        "right",
        description=canvas.DOCK_DOC + " Defaults to right; null leaves it a tab only.",
    )


class BrowserRef(_Strict):
    browser: str | None = Field(
        None, description="The browser's id; defaults to your only one."
    )


class BrowserGoto(BrowserRef):
    url: str | None = Field(None, description="An address to load.")
    action: Literal["back", "forward", "reload"] | None = None


class BrowserTarget(BrowserRef):
    target: str = Field(
        description='A Playwright selector: role=button[name="Send"], '
        "text=Sign in, label=Email, or CSS."
    )


class BrowserType(BrowserTarget):
    text: str = Field(max_length=100_000)
    submit: bool = Field(False, description="Press Enter after typing.")


class BrowserPress(BrowserRef):
    key: str = Field(description="A key or chord, such as Enter or Control+A.")
    target: str | None = None


class BrowserShot(BrowserRef):
    target: str | None = Field(None, description="Only this element.")
    full_page: bool = False


class BrowserEvents(BrowserRef):
    since: int = Field(0, ge=0, description="The revision you already read.")
    who: Literal["person", "agent", "all"] = "all"


class BrowserEval(BrowserRef):
    expression: str = Field(max_length=100_000)


class BrowserResize(BrowserRef):
    width: int = Field(ge=240, le=3840)
    height: int = Field(ge=240, le=2400)


class BrowserHandover(BrowserRef):
    reason: str = Field(
        min_length=1,
        max_length=300,
        description="What the person should do, shown on the page's banner.",
    )


class PaneRef(_Strict):
    id: str


class PaneOpen(_Strict):
    url: str = ""
    profile: str | None = None


class PaneInput(PaneRef):
    events: list[dict] = Field(max_length=200)


class PaneNav(PaneRef):
    url: str | None = None
    action: Literal["back", "forward", "reload"] | None = None


class PaneResize(PaneRef):
    width: int = Field(ge=240, le=3840)
    height: int = Field(ge=240, le=2400)


class PaneGive(PaneRef):
    session: str = Field(description="The handle or log id of the session.")


def register_browser_ops(app: App) -> None:
    r, br = app.registry, app.browsers

    def dock(s, bid: str, where: str | None) -> dict:
        """Put a browser in its session's canvas; a placement that does not
        fit is reported, not raised, as artifact_send does."""
        if s is None or where is None:
            return {}
        try:
            s.set_layout(canvas.place(s.layout, bid, where, s.dockable), "agent")
        except canvas.LayoutError as e:
            return {"docked": False, "reason": str(e)}
        return {"docked": where}

    def agent_browser(p: BrowserRef, caller: Caller, held: bool = True) -> Browser:
        b = br.of_session(caller.log_id or "", p.browser)
        if not b.running:
            raise OpError(
                "browser_stopped",
                f"browser {b.meta['name']} is stopped; browser_open it again",
            )
        if held and b.meta["holder"] != "agent":
            raise OpError(
                "person_has_control",
                "the person holds this page; wait until they give it back",
            )
        return b

    async def settle(b: Browser, rev: int) -> dict:
        """What a click or a key changed: the page, and what the log saw."""
        try:
            await b.page.wait_for_load_state("domcontentloaded", timeout=5_000)
        except Exception:
            pass
        await asyncio.sleep(0.3)
        b.meta.update(url=b.page.url, title=await b.page.title())
        br._card(b)
        seen = [
            event_line(e) for e in b.events if e["rev"] > rev and e["kind"] != "agent"
        ]
        return {"url": b.meta["url"], "title": b.meta["title"], "events": seen[-30:]}

    async def locate(b: Browser, target: str):
        loc = b.page.locator(target)
        try:
            n = await loc.count()
        except Exception as e:
            raise OpError("bad_target", str(e).splitlines()[0]) from None
        if n == 0:
            raise OpError("not_found", f"nothing on the page matches {target}")
        return loc.first, n

    # -- shared by the agent and the person -----------------------------------

    @r.op("browser.open", BrowserOpen, agent=True)
    async def open_(p: BrowserOpen, caller):
        """Start a browser on the server and load a URL. It docks beside your
        transcript (dock=) and shows as a tab, where the person watches and
        clicks in it; you hold the page until you hand it over. Its profile
        keeps sign-ins between opens."""
        if not caller.is_agent:
            raise OpError("bad_caller", "a person opens a browser with browser.spawn")
        s = app.sessions.sessions.get(caller.log_id or "")
        profile = p.profile or (s.handle if s is not None else None)
        b = await br.open_(
            app.who(caller).name, p.url, profile=profile, session=caller.log_id
        )
        out = {"id": b.id, "name": b.meta["name"], "url": b.meta["url"],
               "title": b.meta.get("title", "")}  # fmt: skip
        return out | dock(s, b.id, p.dock)

    @r.op("browser.goto", BrowserGoto, agent=True)
    async def goto(p: BrowserGoto, caller):
        """Load a URL in your browser, or go back, forward or reload."""
        if not p.url and not p.action:
            raise OpError("bad_params", "give a url or an action")
        b = agent_browser(p, caller)
        br.act(b, f"goto {p.url or p.action}")
        return await br.goto(b.id, p.url, p.action, "agent")

    @r.op("browser.snapshot", BrowserRef, agent=True, read=True)
    async def snapshot(p: BrowserRef, caller):
        """The page as text: its accessibility tree, the roles and names you
        can target. Works while the person holds the page too."""
        b = agent_browser(p, caller, held=False)
        try:
            tree = await b.page.locator("body").aria_snapshot()
        except Exception as e:
            raise OpError("snapshot_failed", str(e).splitlines()[0]) from None
        if len(tree) > SNAPSHOT_MAX:
            tree = tree[:SNAPSHOT_MAX] + "\n… (cut; target a part with browser_eval)"
        title = await b.page.title()
        holder = b.meta["holder"]
        return f"{b.page.url}  {title}\nholder: {holder}\n\n{tree}"

    @r.op("browser.click", BrowserTarget, agent=True)
    async def click(p: BrowserTarget, caller):
        """Click an element, after Playwright's waits. Returns the page after
        it and what the log saw: navigations, dialogs, console errors."""
        b = agent_browser(p, caller)
        loc, n = await locate(b, p.target)
        rev = b.rev
        br.act(b, f"click {p.target}")
        try:
            await loc.click(timeout=10_000)
        except Exception as e:
            raise OpError("click_failed", str(e).splitlines()[0]) from None
        out = await settle(b, rev)
        if n > 1:
            out["note"] = f"{n} elements matched; clicked the first"
        return out

    @r.op("browser.type", BrowserType, agent=True)
    async def type_(p: BrowserType, caller):
        """Fill a field with text, replacing what it held; submit presses
        Enter after it."""
        b = agent_browser(p, caller)
        loc, _ = await locate(b, p.target)
        secret = (await loc.get_attribute("type")) == "password"
        shown = f"{len(p.text)} characters" if secret else json.dumps(p.text[:80])
        rev = b.rev
        br.act(b, f"type {shown} into {p.target}")
        try:
            await loc.fill(p.text, timeout=10_000)
            if p.submit:
                await loc.press("Enter")
        except Exception as e:
            raise OpError("type_failed", str(e).splitlines()[0]) from None
        return await settle(b, rev)

    @r.op("browser.press", BrowserPress, agent=True)
    async def press(p: BrowserPress, caller):
        """Press a key or chord on an element, or on whatever has focus."""
        b = agent_browser(p, caller)
        rev = b.rev
        br.act(b, f"press {p.key}")
        try:
            if p.target:
                loc, _ = await locate(b, p.target)
                await loc.press(p.key, timeout=10_000)
            else:
                await b.page.keyboard.press(p.key)
        except OpError:
            raise
        except Exception as e:
            raise OpError("press_failed", str(e).splitlines()[0]) from None
        return await settle(b, rev)

    @r.op("browser.screenshot", BrowserShot, agent=True, read=True)
    async def screenshot(p: BrowserShot, caller):
        """Save a PNG of the viewport, the whole page or one element, and
        return its path; read it with your file tool to see it."""
        b = agent_browser(p, caller, held=False)
        shots = b.dir / "shots"
        shots.mkdir(parents=True, exist_ok=True)
        path = shots / f"{time.strftime('%Y%m%d-%H%M%S')}-{b.rev}.png"
        try:
            if p.target:
                loc, _ = await locate(b, p.target)
                await loc.screenshot(path=str(path), timeout=10_000)
            else:
                await b.page.screenshot(path=str(path), full_page=p.full_page)
        except OpError:
            raise
        except Exception as e:
            raise OpError("screenshot_failed", str(e).splitlines()[0]) from None
        return {"path": str(path)}

    @r.op("browser.events", BrowserEvents, agent=True, read=True)
    async def events(p: BrowserEvents, caller):
        """The browser's log after a revision: navigations, clicks and typing
        with the element each hit, console errors, failed requests, dialogs
        and changes of control. Pass the returned rev next time."""
        b = br.of_session(caller.log_id or "", p.browser)
        evs = [e for e in b.events if e["rev"] > p.since]
        if p.who != "all":
            evs = [e for e in evs if e.get("who") == p.who]
        return {"rev": b.rev, "events": [event_line(e) for e in evs[-200:]]}

    @r.op("browser.eval", BrowserEval, agent=True)
    async def eval_(p: BrowserEval, caller):
        """Run a JavaScript expression in the page and return its JSON value."""
        b = agent_browser(p, caller)
        br.act(b, "eval")
        try:
            value = await b.page.evaluate(p.expression)
        except Exception as e:
            raise OpError("eval_failed", str(e).splitlines()[0]) from None
        text = json.dumps(value, ensure_ascii=False, default=str)
        return text if len(text) <= 20_000 else text[:20_000] + "… (cut)"

    @r.op("browser.resize", BrowserResize, agent=True)
    async def resize(p: BrowserResize, caller):
        """Set the page's viewport, such as 390x844 for a phone-width test."""
        b = agent_browser(p, caller)
        br.act(b, f"resize {p.width}x{p.height}")
        return await br.resize(b.id, p.width, p.height)

    @r.op("browser.handover", BrowserHandover, agent=True)
    async def handover(p: BrowserHandover, caller):
        """Give the page to the person, with what they should do (pass a
        captcha, sign in, try a step), then end your turn. You are woken with
        > from browser:<id> · back, and what they did, when they give it back."""
        b = agent_browser(p, caller)
        br.handover(b, p.reason)
        return (
            f"The person holds {b.meta['name']} now and sees: {p.reason}. "
            "End your turn; you are woken when they give the page back."
        )

    @r.op("browser.close", BrowserRef, agent=True)
    async def close(p: BrowserRef, caller):
        """Stop your browser and remove its tab. Its profile, with its
        sign-ins, stays for the next open."""
        b = br.of_session(caller.log_id or "", p.browser)
        await br.close(b.id)

    # -- the person's pane -----------------------------------------------------

    @r.op("browser.available")
    async def avail(_, caller):
        """Whether this server runs browsers, and why not when it does not."""
        why = br.available()
        return {"ok": why is None, "why": why}

    @r.op("browser.spawn", PaneOpen)
    async def spawn(p: PaneOpen, caller):
        """Open a browser the caller owns and holds, with no agent."""
        b = await br.open_(app.who(caller).name, p.url, profile=p.profile)
        return br.card(b)

    @r.op("browser.input", PaneInput, owned="id")
    async def input_(p: PaneInput, caller):
        await br.input(p.id, p.events)

    @r.op("browser.nav", PaneNav, owned="id")
    async def nav(p: PaneNav, caller):
        return await br.goto(p.id, p.url, p.action, "person")

    @r.op("browser.view", PaneResize, owned="id")
    async def view(p: PaneResize, caller):
        """The person's pane took the page: fit the viewport to it."""
        b = br.get(p.id)
        if b.meta["holder"] != "person":
            raise OpError("agent_has_control", "the agent holds this page")
        return await br.resize(p.id, p.width, p.height)

    @r.op("browser.take", PaneRef, owned="id")
    async def take(p: PaneRef, caller):
        br.take(p.id)

    @r.op("browser.handback", PaneRef, owned="id")
    async def give_back(p: PaneRef, caller):
        await br.give_back(p.id)

    @r.op("browser.give", PaneGive, owned="id")
    async def give(p: PaneGive, caller):
        s = app.sessions.sessions.get(p.session) or next(
            (x for x in app.sessions.open_sessions() if x.handle == p.session), None
        )
        if s is None:
            raise OpError("unknown_session", f"no open session {p.session}")
        if app.owner_of(s.log_id) != app.owner_of(p.id):
            raise OpError("not_owner", "give a browser to a session of its owner")
        await br.give(p.id, s.log_id, s.handle)
        dock(s, p.id, "right")

    @r.op("browser.reopen", PaneRef, owned="id")
    async def reopen(p: PaneRef, caller):
        await br.reopen(p.id)

    @r.op("browser.shut", PaneRef, owned="id")
    async def shut(p: PaneRef, caller):
        """Close a browser from its tab: Chromium stops, the profile stays."""
        await br.close(p.id)
