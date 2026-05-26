from __future__ import annotations

import asyncio
import logging
import time

from aegis.core.manager import SessionManager
from aegis.telegram.format import chunk, status_line
from aegis.tui.state import AgentState

log = logging.getLogger("aegis.telegram")


class TelegramFrontend:
    def __init__(self, bot, manager: SessionManager,
                 bridge, cfg, *, chat_id: int,
                 auto_prompt: str,
                 refresh_interval: float = 2.0) -> None:
        self._bot = bot
        self._m = manager
        self._bridge = bridge
        self._cfg = cfg
        self._chat = chat_id
        self._auto = auto_prompt
        self._refresh = refresh_interval
        self._active: str | None = None

    async def _reply(self, text: str) -> None:
        await self._bot.send_message(self._chat, text)

    def _augment(self, text: str) -> str:
        return f"{text}\n\n[{self._auto}]" if self._auto else text

    async def _send_to(self, core, text: str) -> None:
        if core.state is AgentState.working:
            await self._reply(f"{core.handle} is working — /interrupt first")
            return
        self._attach_observers(core)
        await core.send(self._augment(text))

    def _attach_observers(self, core) -> None:
        if getattr(core, "_tg_wired", False):
            return
        core._tg_wired = True
        state = {"mid": None, "buf": [], "refresher": None}

        def on_event(_c, ev):
            from aegis.events import AssistantText
            if isinstance(ev, AssistantText):
                state["buf"].append(ev.text)

        def on_state(_c, st, finished):
            asyncio.create_task(self._on_state(_c, st, finished, state))

        core.on_event = on_event
        core.on_state = on_state

    def _render_status(self, core) -> str:
        return status_line(core.handle, core.state.value,
                           getattr(core.agent, "model", "?"),
                           core.metrics.render(time.monotonic()))

    async def _refresh_loop(self, core, state) -> None:
        try:
            while True:
                await asyncio.sleep(self._refresh)
                mid = state.get("mid")
                if mid is None:
                    continue
                await self._bot.edit_message(
                    self._chat, mid, self._render_status(core))
        except asyncio.CancelledError:
            raise

    async def _on_state(self, core, st, finished, state) -> None:
        line = self._render_status(core)
        if st is AgentState.working and state["mid"] is None:
            state["mid"] = await self._bot.send_message(self._chat, line)
            if self._refresh > 0 and state.get("refresher") is None:
                state["refresher"] = asyncio.create_task(
                    self._refresh_loop(core, state))
        elif state["mid"] is not None:
            await self._bot.edit_message(self._chat, state["mid"], line)
        if finished:
            refresher = state.get("refresher")
            if refresher is not None:
                refresher.cancel()
                try:
                    await refresher
                except asyncio.CancelledError:
                    pass
                state["refresher"] = None
            reply = "".join(state["buf"]).strip()
            for part in chunk(reply, label=core.handle):
                await self._bot.send_message(self._chat, part, markdown=True)
            state["buf"].clear()
            state["mid"] = None

    async def handle_update(self, update: dict) -> None:
        msg = update.get("message") or {}
        if msg.get("chat", {}).get("id") != self._chat:
            log.debug("drop update from chat %s", msg.get("chat"))
            return
        text = (msg.get("text") or "").strip()
        if not text:
            return
        if text.startswith("/"):
            await self._command(text)
        else:
            await self._route_text(text)

    async def _route_text(self, text: str) -> None:
        if self._active is None:
            await self._reply("no active agent — /new to spawn")
            return
        core = self._m.get(self._active)
        if core is None:
            self._active = None
            await self._reply("no active agent — /new to spawn")
            return
        await self._send_to(core, text)

    async def _command(self, text: str) -> None:
        head, _, rest = text.partition(" ")
        rest = rest.strip()
        if head == "/new":
            try:
                core = self._m._sync_spawn(rest or None)
            except KeyError:
                await self._reply("unknown agent. " + self._agents_line())
                return
            self._active = core.handle
            await self._reply(
                f"▸ spawned {core.handle} ({core.agent_slug})")
        elif head == "/close":
            if self._active is None:
                await self._reply("no active agent")
                return
            closed = self._active
            await self._m.close(closed)
            rest_sessions = self._m.list_sessions()
            self._active = rest_sessions[0].handle if rest_sessions else None
            tail = (f"active: {self._active}" if self._active
                    else "no active agent")
            await self._reply(f"▸ closed {closed} · {tail}")
        elif head == "/interrupt":
            if self._active is not None:
                await self._m.interrupt(self._active)
                await self._reply(f"▸ interrupted {self._active}")
        elif head == "/agents":
            await self._reply(self._agents_line())
        elif head == "/sessions":
            await self._reply(self._sessions_line())
        elif head == "/help":
            await self._reply(
                "/new [slug] /close /interrupt /agents /sessions "
                "/<handle> [text] /help")
        else:
            # Telegram only auto-links /[A-Za-z0-9_]+, but handles are
            # hyphenated. Accept the tappable underscore alias too.
            raw = head[1:]
            core = self._m.get(raw) or self._m.get(raw.replace("_", "-"))
            if core is None:
                await self._reply(f"no session {raw!r} — /sessions")
                return
            if rest:
                await self._send_to(core, rest)
            else:
                self._active = core.handle
                await self._reply(f"▸ talking to {core.handle}")

    def _agents_line(self) -> str:
        return "agents: " + ", ".join(self._m.list_agents())

    def _sessions_line(self) -> str:
        si = self._m.list_sessions()
        if not si:
            return "no sessions"
        # One per line; /underscore_alias is tappable in Telegram and routes
        # back via the _ -> - normalisation in _command.
        return "\n".join(
            f"{'●' if s.state == 'working' else '○'} "
            f"/{s.handle.replace('-', '_')} {s.state}"
            for s in si)

    async def run(self, bot) -> None:
        offset = 0
        while True:
            for up in await bot.get_updates(offset):
                offset = up["update_id"] + 1
                try:
                    await self.handle_update(up)
                except Exception:  # noqa: BLE001 - never die on one update
                    log.exception("update handling failed")
