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
        from aegis.telegram.commands import COMMANDS, CmdContext

        head, _, rest = text.partition(" ")
        verb = head.lstrip("/")
        tokens = rest.split()

        # Pull out @<peer>; only @<name> where name is non-empty counts.
        # All @<name> tokens are consumed; the first becomes target, the
        # rest are silently dropped. Bare "@" (no name) stays in args.
        target: str | None = None
        args: list[str] = []
        for t in tokens:
            if t.startswith("@") and len(t) > 1:
                if target is None:
                    target = t[1:]
                # additional @<name> tokens are dropped (first wins)
            else:
                args.append(t)

        # Longest-prefix match: try "<verb> <args[0]>" before "<verb>".
        key2 = f"{verb} {args[0]}" if args else None
        cmd = COMMANDS.get(key2) if key2 else None
        if cmd is not None:
            args = args[1:]
        else:
            cmd = COMMANDS.get(verb)

        if cmd is not None:
            ctx = CmdContext(
                bridge=self._bridge, cfg=self._cfg, manager=self._m,
                target=target, reply=self._reply, frontend=self)
            await cmd.handler(ctx, args)
            return

        await self._legacy_handle_alias(head, rest.strip())

    async def _legacy_handle_alias(self, head: str, rest: str) -> None:
        """The /<handle> alias-routing pattern: send `rest` to the named
        session, or set it active if no rest is given. Underscore→hyphen
        normalization because Telegram only auto-links [A-Za-z0-9_]+ but
        aegis handles are hyphenated.
        """
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

    async def run(self, bot) -> None:
        offset = 0
        while True:
            for up in await bot.get_updates(offset):
                offset = up["update_id"] + 1
                try:
                    await self.handle_update(up)
                except Exception:  # noqa: BLE001 - never die on one update
                    log.exception("update handling failed")
