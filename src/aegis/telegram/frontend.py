from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from aegis.core.manager import SessionManager
from aegis.telegram.format import Spillover, chunk, render_html
from aegis.tui.state import AgentState

log = logging.getLogger("aegis.telegram")


def _new_state() -> dict:
    return {
        "mid": None,
        "envelope": None,
        "tool_counts": {},
        "buf": [],
    }


class TelegramFrontend:
    def __init__(self, bot, manager: SessionManager,
                 bridge, cfg, *, chat_id: int,
                 auto_prompt: str,
                 state_dir: Path) -> None:
        self._bot = bot
        self._m = manager
        self._bridge = bridge
        self._cfg = cfg
        self._chat = chat_id
        self._auto = auto_prompt
        self._state_dir = Path(state_dir)
        self._active: str | None = None
        # Per-handle turn state: {"mid": int|None, "envelope": str|None,
        #                        "tool_counts": dict[str, int], "buf": list[str]}
        self._states: dict[str, dict] = {}

    def _state_for(self, handle: str) -> dict:
        return self._states.setdefault(handle, _new_state())

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

        def on_event(c, ev):
            asyncio.create_task(self._on_event(c, ev))

        def on_state(c, st, finished):
            asyncio.create_task(self._on_state(c, st, finished))

        def on_inbox(c, msg):
            s = self._state_for(c.handle)
            sender = getattr(msg, "sender", None)
            if sender is not None:
                kind = getattr(sender, "kind", "?")
                handle = getattr(sender, "handle", "?")
                queue = getattr(sender, "queue", None)
                if queue:
                    s["envelope"] = f"from {kind}:{handle}:{queue}"
                else:
                    s["envelope"] = f"from {kind}:{handle}"

        def on_close(c, reason):
            self._states.pop(c.handle, None)
            if self._active == c.handle:
                self._active = None

        core.add_event_observer(on_event)
        core.add_state_observer(on_state)
        core.add_inbox_observer(on_inbox)
        core.add_close_observer(on_close)

    def _render_ticker(self, core, state: dict) -> str:
        icon = {"working": "🔧",
                "ready": "✅",
                "error": "⚠️"}.get(core.state.value, "⏳")
        if state["tool_counts"]:
            counts_str = ", ".join(
                f"{n} x{c}" for n, c in state["tool_counts"].items())
        else:
            counts_str = "thinking…"
        envelope = state.get("envelope")
        prefix = f"✉️ {envelope} · " if envelope else ""
        return f"{prefix}{icon} {counts_str}"

    async def _edit_ticker(self, core) -> None:
        state = self._state_for(core.handle)
        mid = state.get("mid")
        if mid is None:
            return
        text = self._render_ticker(core, state)
        try:
            await self._bot.edit_message(
                self._chat, mid, text, parse_mode="HTML")
        except Exception:
            log.exception("ticker edit failed; turn proceeds without further updates")

    async def _on_event(self, core, ev) -> None:
        from aegis.events import AssistantText, ToolUse
        state = self._state_for(core.handle)
        if isinstance(ev, AssistantText):
            state["buf"].append(ev.text)
        elif isinstance(ev, ToolUse):
            counts = state["tool_counts"]
            counts[ev.name] = counts.get(ev.name, 0) + 1
            await self._edit_ticker(core)

    async def _on_state(self, core, st, finished) -> None:
        state = self._state_for(core.handle)
        if st is AgentState.working and state["mid"] is None:
            text = self._render_ticker(core, state)
            try:
                mid = await self._bot.send_message(
                    self._chat, text, parse_mode="HTML")
            except Exception:
                log.exception("status send failed; turn proceeds without ticker")
                mid = None
            if mid is None:
                log.warning("send_message returned None; no ticker for this turn")
            else:
                state["mid"] = mid
        elif state["mid"] is not None:
            await self._edit_ticker(core)
        if finished:
            reply_md = "".join(state["buf"]).strip() or "(no output)"
            await self._send_reply(core, reply_md, state)
            state["mid"] = None
            state["envelope"] = None
            state["tool_counts"] = {}
            state["buf"] = []

    def _write_overflow(self, handle: str, raw_md: str) -> Path:
        import datetime as _dt
        folder = self._state_dir / "overflow"
        folder.mkdir(parents=True, exist_ok=True)
        ts = _dt.datetime.now().strftime("%Y-%m-%d-%H%M%S")
        path = folder / f"aegis-reply-{ts}-{handle}.md"
        path.write_text(raw_md)
        return path

    async def _send_reply(self, core, reply_md: str, state: dict) -> None:
        html = render_html(reply_md)
        parts_or_spill = chunk(html, raw_md=reply_md)
        if isinstance(parts_or_spill, Spillover):
            path = self._write_overflow(core.handle, reply_md)
            peek_md = reply_md[:500]
            peek_html = render_html(peek_md)
            caption = (f"<i>{core.handle}</i>\n\n{peek_html}\n\n…\n\n"
                       f"📎 Full response ({len(reply_md)} chars) attached.")
            if len(caption) > 1024:
                caption = caption[:1000] + "…\n\n📎 attached."
            await self._bot.send_document(
                self._chat, path, caption=caption, parse_mode="HTML")
            return
        parts = parts_or_spill
        for i, part in enumerate(parts):
            if len(parts) > 1:
                label = f"<i>{core.handle} ({i + 1}/{len(parts)})</i>\n"
                text = label + part
            else:
                text = part
            await self._bot.send_message(self._chat, text, parse_mode="HTML")

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

        target: str | None = None
        args: list[str] = []
        for t in tokens:
            if t.startswith("@") and len(t) > 1:
                if target is None:
                    target = t[1:]
            else:
                args.append(t)

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

    def _offset_path(self) -> Path:
        return self._state_dir / "telegram.offset"

    def _load_offset(self) -> int:
        try:
            return int(self._offset_path().read_text().strip())
        except FileNotFoundError:
            return 0
        except ValueError:
            log.warning("telegram.offset corrupt; starting at 0")
            return 0

    def _save_offset(self, offset: int) -> None:
        p = self._offset_path()
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(p.suffix + ".tmp")
        tmp.write_text(f"{offset}\n")
        tmp.replace(p)

    async def run(self, bot) -> None:
        offset = self._load_offset()
        while True:
            for up in await bot.get_updates(offset):
                offset = up["update_id"] + 1
                self._save_offset(offset)
                try:
                    await self.handle_update(up)
                except Exception:
                    log.exception("update handling failed")
