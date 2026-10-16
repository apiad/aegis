"""The server's state and its operations, independent of any transport.

Operations: ``agents.list``, ``session.spawn``, ``session.send`` (which
resolves a ``/`` line first, ``commands.py``), ``session.read``,
``recap.request``, ``btw.cancel``, ``dictation.prepare``, ``session.configure``,
``commands.list``, ``session.interrupt``, ``session.stop``, ``session.reopen``,
``session.rename``, ``archive.list``, ``server.version``, ``file.open``, ``file.peek``,
``attachment.begin``, ``attachment.put``, ``attachment.drop``,
``quota.read``, ``transcript.detail``, ``transcript.search``, ``transcript.output``, and ``config.read``, ``config.write``,
``config.detect``, ``config.doctor`` and ``config.propose`` (``config_ops.py``),
and ``artifact.create``, ``artifact.send``, ``artifact.read``,
``artifact.update``, ``artifact.close``, ``artifact.state``, ``artifact.emit``,
``artifact.submit``, ``artifact.error`` and ``artifact.probed``
(``artifact_ops.py``),
and ``journal.search``, ``journal.note`` and ``journal.rows`` (``journal/ops.py``),
and ``help.search``, ``help.read`` and ``help.pages`` (``help_ops.py``),
and ``schedule.list``, ``schedule.runs``, ``schedule.create``,
``schedule.update``, ``schedule.delete``, ``schedule.pause``,
``schedule.resume``, ``schedule.fire`` and ``schedule.test``
(``schedule_ops.py``).
Channels: ``sessions`` (every open session's meta; patches ``upsert`` and
``remove``), ``transcript:<log_id>`` (any session, archived included; a
subscribe ``since`` a revision gets what changed after it), ``quota`` (each
provider's windows; patches ``set``), ``host`` (CPU, RAM and disk while someone
watches; patches ``set``), ``journal`` (when its rows last changed; patches
``set``), ``schedules`` (every schedule's state; patches ``upsert`` and
``remove``, ``schedules.py``) and ``config`` (``.aegis.yaml`` as aegis holds
it; patches ``set``).
"""

from __future__ import annotations

import asyncio
import base64
import binascii
import contextlib
import dataclasses
import getpass
import logging
import re
import time
from collections import OrderedDict
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field, model_validator
from pydantic.json_schema import SkipJsonSchema

from . import archive, attachments as att, commands, dictation, files, restarts
from . import workflow_store
from .agent_ops import (
    far_error,
    handoff_opening,
    register_agent_ops,
    spawn_opening,
    spawn_tail,
)
from .artifact_ops import register_artifact_ops
from .agents import (
    EFFORTS,
    HARNESSES,
    PERMISSION_ORDER,
    SUPPORTED_HARNESSES,
    model_suggestions,
    resolve,
)
from .cgroups import SessionCgroups
from .channels import Channels, Throttle
from .claude.process import PERMISSION_MODE, ControlError
from .config import Config, Snapshot
from .config_ops import register_config_ops
from .confirm import Confirmations
from .help_ops import register_help_ops
from .host import HostSampler
from .net.service import NetService
from .links import LinkError, Links, probe
from .mcp import PATH as MCP_PATH, Tokens, build_mcp
from . import refs
from .monitors import Monitors, iso_now
from .names import valid_handle
from .nudges import Nudges
from .park import Parker, Watchers
from .push import register_push_ops
from .queues import Queues
from .journal.ops import register_journal_ops
from .journal.service import Journal
from .layout_ops import register_layout_ops
from .schedule_ops import register_schedule_ops
from .schedules import Schedules
from .workflow_ops import register_workflow_ops
from .workflows import Workflows
from .quota import Quota
from .quota_gate import (
    POOLS,
    Gate,
    continued,
    decide,
    pool_for,
    pools_wire,
    refusal,
)
from .btws import SideNotes
from .recaps import Recaps
from .ops import Caller, NoParams, OpError, Principal, Registry as Ops
from .tokens import TokenStore
from .registry import Registry
from .roots import Roots
from .transcript.entries import _sent_file
from .session import PUBLISH_EVERY_S
from .term_ops import register_term_ops
from .terminals import Terminals
from .version import Versions

log = logging.getLogger("aegis.app")
# A log id as registry.new_log_id mints it, checked on what a link answers.
LOG_ID = re.compile(r"^\d{8}-\d{6}-[0-9a-f]{6}$")
# How many nonced sends the server remembers, across every session.
SEND_NONCES = 512

Effort = Literal["low", "medium", "high", "xhigh", "max"]
Permission = Literal["read", "write", "full", "auto"]


class _Strict(BaseModel):
    model_config = {"extra": "forbid"}


class SpawnedFrom(_Strict):
    handle: str
    server: str


class SpawnParams(_Strict):
    agent: str | None = Field(
        None,
        description="The agent to start from (agents_list shows them); "
        "omitted means default_agent.",
    )
    harness: str | None = Field(None, description="Overrides the agent's harness.")
    model: str | None = Field(None, description="Overrides the agent's model.")
    effort: Effort | None = Field(None, description="Overrides the agent's effort.")
    permission: Permission | None = Field(
        None, description="Overrides the agent's permission."
    )
    cwd: str | None = Field(
        None,
        description="Working directory inside the server's root; a relative one "
        "resolves against yours.",
    )
    prompt: str | None = Field(
        None, min_length=1, description="The new session's first message."
    )
    # Set by a server that links this one, for an agent of its own that
    # spawns here (app.spawn); refused from anything but a link.
    quiet: bool = Field(
        False,
        description="Start it without a tab, for background work: its tab "
        "appears when its turn ends. Needs a prompt.",
    )
    spawned_from: SkipJsonSchema[SpawnedFrom | None] = Field(None, exclude=True)


class SendParams(_Strict):
    log_id: str
    text: str = ""
    # Upload ids from attachment.begin, every one complete (attachments.py).
    attachments: list[str] = Field(default_factory=list, max_length=att.MAX_FILES)
    # The transcript revision a message written offline answered (outbox.js).
    expect_rev: int | None = None
    # The page's id for this message: a retry with the same nonce is answered
    # without being delivered again (#350).
    nonce: str | None = Field(default=None, min_length=8, max_length=64)

    @model_validator(mode="after")
    def _says_something(self) -> "SendParams":
        if not self.text and not self.attachments:
            raise ValueError("a message needs text or attachments")
        return self


class AttachBegin(_Strict):
    log_id: str
    name: str = Field(min_length=1, max_length=1000)
    size: int = Field(ge=0, le=files.MAX_BYTES)


class AttachPut(_Strict):
    log_id: str
    upload_id: str
    offset: int = Field(ge=0)
    # Base64 of at most CHUNK_BYTES.
    data: str = Field(max_length=4 * ((att.CHUNK_BYTES + 2) // 3))


class AttachDrop(_Strict):
    log_id: str
    upload_id: str


class ReadParams(_Strict):
    log_id: str
    ids: list[str] = Field(max_length=500)


class RecapParams(_Strict):
    log_id: str
    force: bool = False


class BtwCancelParams(_Strict):
    log_id: str
    id: str


class ConfigureParams(_Strict):
    log_id: str
    model: str | None = None
    effort: Effort | None = None
    permission: Permission | None = None


class LogParams(_Strict):
    log_id: str


class DetailParams(_Strict):
    log_id: str
    ids: list[str] = Field(min_length=1, max_length=100)


class OutputParams(_Strict):
    log_id: str
    id: str


class SearchParams(_Strict):
    log_id: str
    q: str = Field(min_length=1, max_length=200)


class RenameParams(_Strict):
    log_id: str | None = None
    handle: str | None = None
    title: str | None = None


class FileRef(_Strict):
    file_id: str
    name: str


class StatParams(_Strict):
    log_id: str
    entry_id: str


class PeekParams(_Strict):
    log_id: str
    entry_id: str
    # a path link: the index into a reply's detail.paths
    index: int | None = None
    # the person's choice when a path names two files
    pick: Literal["root", "cwd"] | None = None


class LinkAdd(_Strict):
    url: str = Field(description="The far server's page URL, e.g. https://dev.example")
    token: str = Field(min_length=1)
    name: str | None = Field(
        None, description="What it must call itself; omitted: whatever it does."
    )


class LinkName(_Strict):
    name: str


class TokenAdd(_Strict):
    name: str = Field(description="Who the token is: lowercase, e.g. ben or laptop")
    role: Literal["admin", "member"] = "member"


class TokenName(_Strict):
    name: str


class TokenPolicy(_Strict):
    name: str = Field(description="Whose policy; * sets the default for members.")
    policy: dict | None = Field(
        description="Pool -> {mode, lines} (quota_gate.py); null: use the default."
    )


class ArchiveParams(_Strict):
    query: str | None = None
    server: str | None = Field(None, description="One server only; omitted: all.")
    workers: bool = Field(
        False, description="Include queue workers; omitted: leave them out."
    )
    limit: int = Field(default=50, ge=1, le=200)
    cursor: str | None = Field(
        None, description="The cursor the last page returned; omitted: the first."
    )


def _dead(e: Exception) -> OpError:
    return OpError(
        "session_dead",
        f"the agent stopped while being written to: {e}; send again to resume",
    )


class App:
    def __init__(
        self,
        roots: Roots,
        claude_bin: str = "claude",
        interrupt_timeout: float = 10.0,
        base_url: str | None = None,
        server_name: str = "aegis",
        opencode_bin: str = "opencode",
        codex_bin: str = "codex",
        dictation_dir: Path | None = None,
        user: str | None = None,
    ) -> None:
        self.roots = roots
        self.server_name = server_name
        self.claude_bin = claude_bin
        self.opencode_bin = opencode_bin
        self.codex_bin = codex_bin
        self.dictation = dictation.Store(dictation_dir or dictation.default_dir())
        self.channels = Channels(self._resolve)
        # Every session's card changes go out together, a few times a second at
        # most, however many sessions are working (#158).
        self._sessions_out = Throttle(
            lambda ops: self.channels.publish("sessions", ops),
            self._sessions_key,
            PUBLISH_EVERY_S,
        )
        self._on_wire: dict[str, tuple] = {}  # log_id -> its fields a person acts on
        self.sessions = Registry(
            roots,
            self.publish,
            claude_bin,
            interrupt_timeout,
            opencode_bin=opencode_bin,
            codex_bin=codex_bin,
        )
        self.terminal_cgroups = None
        self.terminals = Terminals(
            roots.state_root,
            roots.config_root,
            lambda: self.config.current().terminals,
            self.publish,
            lambda: self.terminal_cgroups,
        )
        self.tokens = Tokens()
        self.monitors = Monitors(self.sessions, roots.state_root / "monitors.json")
        self.config = Config(roots.config_root, self._on_config)
        self._config_task: asyncio.Task | None = None
        self._background: set[asyncio.Task] = set()
        self.queues = Queues(
            self.sessions, self.monitors, roots.state_root / "tasks.jsonl", self.config
        )
        self.confirmations = Confirmations()
        self.quota = Quota(self.publish)
        self.host = HostSampler(
            self.publish,
            self.channels.subscribers,
            roots.config_root,
            net=lambda: (
                self.net.wire() if self.config.current().network.enabled else None
            ),
        )
        self.net = NetService(
            lambda: self.config.current().network,
            watched=lambda: self.channels.subscribers("host") > 0,
            on_change=self.host.check,
        )
        reg = self.sessions
        reg.tokens, reg.monitors, reg.queues, reg.server_name = (
            self.tokens,
            self.monitors,
            self.queues,
            server_name,
        )
        reg.context = lambda: self.config.current().context
        reg.quota = self.quota
        self.journal = Journal(roots.state_root, self.publish)
        reg.journal = self.journal
        self.schedules = Schedules(self)
        reg.schedules = self.schedules
        self.workflows = Workflows(self)
        self.saved = workflow_store.Store(roots.config_root, roots.state_root)
        reg.workflows = self.workflows
        self.catalogs = commands.Catalogs(
            roots.state_root / "stderr" / "catalog-probe.log"
        )
        # The last sends that carried a nonce, oldest first: (log_id, nonce)
        # -> the delivery a retry answers with instead of delivering again.
        self._sends: OrderedDict[tuple[str, str], asyncio.Future] = OrderedDict()
        reg.catalogs = self.catalogs
        reg.mcp_url = f"{base_url.rstrip('/')}{MCP_PATH}" if base_url else None
        self.versions = Versions()
        self.recaps = Recaps(self)
        self.side_notes = SideNotes(self)
        self.sessions.side_notes = self.side_notes
        self.nudges = Nudges(self.sessions)
        self.watchers = Watchers()
        self.parker = Parker(
            self.sessions, lambda: self.config.current().park, self.watchers
        )
        # Who the people on this server are, as a link tells the far side. One
        # user per server for now: whoever started it (links.py).
        self.user = user or getpass.getuser()
        # The tokens that may use this server, the admin's first (tokens.py).
        self.principals = TokenStore(roots.state_root, self.user)
        self.links = Links(roots.state_root, server_name, self.user, self.publish)
        # Sessions a pause or a continue is under way for, so a reading that
        # lands before it finishes does not start a second one.
        self._quota_busy: set[str] = set()
        self.quota.listeners.append(self.on_quota)
        reg.gate = self.gate
        self.registry = Ops()
        self.registry.who = self.who
        self.registry.owner_of = self.owner_of
        self.registry.permission_of = lambda log_id: (
            s.spec.permission if (s := self.sessions.sessions.get(log_id)) else None
        )
        self.registry.schedule_of = lambda log_id: (
            s.schedule if (s := self.sessions.sessions.get(log_id)) else None
        )
        self._register()
        register_agent_ops(self)
        register_config_ops(self)
        register_artifact_ops(self)
        register_layout_ops(self)
        register_term_ops(self)
        self.push = register_push_ops(self)
        register_journal_ops(self)
        register_help_ops(self)
        register_schedule_ops(self)
        register_workflow_ops(self)
        self.mcp_server, self.mcp_app = build_mcp(self.registry, self.tokens)

    def who(self, caller: Caller) -> Principal:
        """The principal a caller acts as. An agent acts as its session's
        owner, with the owner's role now: a revoked owner's agent is a member."""
        if caller.principal is not None:
            return caller.principal
        if caller.is_agent:
            name = self.owner_of(caller.log_id or "") or self.user
            return Principal(name, self.principals.role_of(name) or "member")
        return Principal(self.user, "admin")  # the server's own calls

    def gate(self, owner: str | None, harness: str, model: str | None) -> Gate:
        """Whether ``owner`` may start a harness on that pool now. A session
        from before owners is the admin's."""
        return decide(
            self.principals.policy_of(owner or self.user),
            pool_for(harness, model),
            self.quota.snapshot(),
            time.time(),
        )

    def on_quota(self, snapshot: dict) -> None:
        """After every reading, and every minute because pace moves with the
        clock: pause each member's working session whose gate says stopped,
        and continue each paused or holding one whose gate is open. Only open
        continues, so a gauge between the lines leaves a paused session
        paused and a stopped token cannot flap. Level-triggered, so a reading
        missed or a restart in between changes nothing."""
        now = time.time()
        for s in list(self.sessions.sessions.values()):
            # A turn the restart guard declined waits for a person (restarts.py).
            if s.log_id in self._quota_busy or s.log_id in self.sessions.declined:
                continue
            policy = self.principals.policy_of(s.spec.owner or self.user)
            if policy is None:
                continue
            g = decide(policy, pool_for(s.spec.harness, s.spec.model), snapshot, now)
            holding = any(m.get("held_for") == "quota" for m in s.held)
            if g.state == "stopped" and s.status == "working" and not s.quota_paused:
                self._quota_task(s, self._pause(s, g))
            elif g.state == "open" and (s.quota_paused or holding):
                self._quota_task(s, self._continue(s))
        self.schedules.quota_opened()
        self.workflows.changed()
        if any(t.status == "pending" for t in self.queues.tasks.values()):
            t = asyncio.create_task(self.queues.dispatch())
            self._background.add(t)
            t.add_done_callback(self._background.discard)

    def _quota_task(self, s, coro) -> None:
        self._quota_busy.add(s.log_id)
        t = asyncio.create_task(coro)
        self._background.add(t)

        def done(t: asyncio.Task) -> None:
            self._background.discard(t)
            self._quota_busy.discard(s.log_id)
            if not t.cancelled() and t.exception() is not None:
                log.error(
                    "aegis: the quota gate failed on %s",
                    s.handle,
                    exc_info=t.exception(),
                )

        t.add_done_callback(done)

    async def _pause(self, s, g: Gate) -> None:
        """Interrupt a turn the owner's stop line cut; held until it opens."""
        if not self._live(s) or s.status != "working":
            return  # closed, or the turn ended since the reading
        s.report({"kind": "quota_paused", "why": g.why})
        s.quota_paused = g.why
        self.sessions.metas.write(s.meta())
        await s.interrupt(through_lock=True)  # the gate holds a node too
        s._publish_now()

    async def _continue(self, s) -> None:
        """The gate is open again: one message saying so ahead of what the
        session held, in one turn, as continue_after_boot does. One that
        cannot start stays as it was, so the next reading tries again."""
        if not self._live(s):
            return
        paused = s.quota_paused
        msgs = [{k: v for k, v in m.items() if k != "held_for"} for m in s.held]
        if s.quota_paused:
            owner = s.spec.owner or self.user
            msgs.insert(
                0,
                {
                    "header": f"> from aegis:quota · {iso_now()}",
                    "body": continued(owner, s.quota_paused),
                    "ts": time.time(),
                },
            )
        s.quota_paused = None
        s.held = msgs
        self.sessions.metas.write(s.meta())
        if s.in_turn:
            s._publish_now()  # they go out when its turn ends
            return
        try:
            await s._flush_held()
        except Exception:
            # _flush_held empties held before it sends: put the batch back,
            # ahead of anything that arrived meanwhile, still waiting.
            log.exception("aegis: could not continue %s after its quota stop", s.handle)
            s.quota_paused = paused
            if paused:
                msgs = msgs[1:]  # the next continue writes its own
            s.held = [m | {"held_for": "quota"} for m in msgs + s.held]
            self.sessions.metas.write(s.meta())
            s._publish_now()

    def _live(self, s) -> bool:
        return not s.archived and self.sessions.sessions.get(s.log_id) is s

    def owner_of(self, log_id: str) -> str | None:
        """A session's owner by log id, None for no such session. One from
        before owners existed is the admin's."""
        if log_id.startswith("t-"):
            return self.terminals.owner_of(log_id)
        s = self.sessions.sessions.get(log_id)
        meta = s.meta() if s is not None else self.sessions.archived.get(log_id)
        if meta is None:
            return None
        return meta.get("owner") or self.user

    def _bin(self, harness: str) -> str:
        return {"opencode": self.opencode_bin, "codex": self.codex_bin}.get(
            harness, self.claude_bin
        )

    async def boot(self) -> None:
        self._config_task = asyncio.create_task(self.config.watch())
        # Before any session starts, so every harness child gets its cgroup.
        self.sessions.cgroups = SessionCgroups.setup(
            lambda: self.config.current().session_memory_max
        )
        if self.sessions.cgroups is not None:
            self.terminal_cgroups = self.sessions.cgroups.sibling(
                "terminals", lambda: self.config.current().terminal_memory_max
            )
        # Before sessions.boot(), which records server_stopped for every session
        # whose turn the last run's end cut: the journal must see those records.
        self.journal.start()
        self.sessions.boot(restarts.started(self.roots.state_root))
        await self.terminals.boot()
        # No browser's upload survives a restart (attachments.py).
        att.clear_staged(self.roots.state_root)
        self.monitors.boot()
        # Before the monitors are armed, so a wake cannot start a turn ahead of
        # the continue; before the queues, so a worker the registry continued
        # needs no second message from its queue.
        await self.sessions.continue_after_boot()
        self.monitors.arm_all()
        await self.queues.resume_after_boot(self.queues.boot())
        # After the sessions and their continues, so a live run is found live.
        self.schedules.boot()
        self.schedules.start()
        self.workflows.boot()
        self.workflows.start()
        self.quota.start()
        self.host.start()
        self.net.start()
        self.nudges.start()
        self.parker.start()
        self.links.boot()
        # Last: its first snapshot must hold every session the boot loaded,
        # so none of them reads as a transition (push.py).
        self.push.sender.start()

    async def shutdown(self) -> None:
        stopping_at = restarts.stopping(self.roots.state_root)
        await self.push.sender.stop()
        await self.schedules.stop()
        await self.workflows.stop()
        self.queues.shutdown()
        if self._config_task is not None:
            self._config_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._config_task
        await self.links.shutdown()
        await self.parker.stop()
        await self.nudges.stop()
        await self.quota.stop()
        await self.host.stop()
        await self.net.stop()
        await self.monitors.shutdown()
        await self.recaps.shutdown()
        await self.terminals.shutdown()
        await self.side_notes.shutdown()
        await self.sessions.shutdown()
        await asyncio.to_thread(self.journal.stop)
        restarts.stopped(self.roots.state_root, stopping_at)

    def _on_config(self, snap: Snapshot) -> None:
        """Every change to .aegis.yaml, however it was made: the page and the
        composer follow it, and the queues start what it now allows."""
        self.channels.publish("config", [{"set": snap.wire()}])
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        t = loop.create_task(self.queues.dispatch())
        self._background.add(t)
        t.add_done_callback(self._background.discard)

    def publish(self, channel: str, ops: list[dict]) -> None:
        if channel == "sessions":
            self._sessions_out.add(ops)
        else:
            self.channels.publish(channel, ops)

    def toast(self, text: str, kind: str = "info", source=None) -> int:
        """Show a toast in every open browser (client/js/toast.js) and return how
        many there are. ``source`` is the session an agent's toast comes from; a
        click on it opens that session's tab. It skips the throttle: a toast
        must neither wait nor merge with another."""
        t: dict = {"text": text, "kind": kind, "ts": time.time()}
        if source is not None:
            t["from"] = {"handle": source.handle, "id": source.log_id}
        self.channels.publish("toasts", [{"toast": t}])
        self.push.sender.on_toast(t)  # an agent's goes to subscribed devices
        return self.channels.subscribers("toasts")

    def _sessions_key(self, op: dict) -> tuple[str, bool]:
        """A session added or removed, or a change to its state, name, model,
        attention, mark or blink, goes out at once: the page acts on them (Esc
        interrupts only a working session, a spawn shows the new tab, a person
        answers a session that needs them, a mark clearing or starting to blink
        changes what a person acts on). The rest of a card can wait."""
        if "remove" in op:
            self._on_wire.pop(op["remove"], None)
            return op["remove"], True
        m = op["upsert"]
        seen = (
            m["state"],
            m["title"],
            m["handle"],
            m["model"],
            m.get("model_id"),
            m.get("attention"),
            m.get("mark"),
            m.get("blink"),
        )
        urgent = self._on_wire.get(m["log_id"]) != seen
        self._on_wire[m["log_id"]] = seen
        return m["log_id"], urgent

    def _resolve(self, name: str, since: int | None = None):
        if name == "sessions":
            return lambda: (
                [s.wire() for s in self.sessions.open_sessions()]
                + self.terminals.cards()
            )
        if name.startswith("term:"):
            return self.terminals.channel(name.removeprefix("term:"), since)
        if name.startswith("transcript:"):
            return self.sessions.transcript(name.removeprefix("transcript:"), since)
        if name == "quota":
            return self.quota.snapshot
        if name == "host":
            return self.host.snapshot
        if name == "journal":
            return self.journal.snapshot
        if name == "config":
            return lambda: self.config.current().wire()
        if name == "links":
            return self.links.wire
        if name == "schedules":
            return self.schedules.wire_all
        if name == "workflows":
            return self.workflows.wire_all
        if name == "toasts":
            # Always empty: a browser that subscribes later never replays a toast.
            return lambda: []
        return None

    def _agents(self):
        snap = self.config.current()
        return list(snap.agents), snap.default_agent

    def _resolve_cwd(self, raw: str | None, base: Path | None = None) -> Path:
        base = base or self.roots.harness_cwd
        p = Path(raw).expanduser() if raw else base
        if not p.is_absolute():
            p = base / p
        p = p.resolve()
        root = self.roots.config_root.resolve()
        if not (p == root or p.is_relative_to(root)):
            raise OpError("bad_cwd", f"{p} is outside {root}")
        if not p.is_dir():
            raise OpError("bad_cwd", f"{p} is not a directory")
        return p

    async def _configure(
        self,
        s,
        model: str | None,
        effort: str | None,
        permission: str | None,
    ) -> dict:
        if not (model or effort or permission):
            raise OpError("bad_params", "nothing to change")
        cat = await self.catalogs.get(s) if model or effort else None
        if cat is not None:  # without one, claude itself refuses a bad value
            if model:
                m = cat.model(model)
                if m is None:
                    raise OpError(
                        "unknown_model", f"no model {model!r}; /model lists them"
                    )
                model = m.value
            if effort:
                cur = cat.model(model or s.spec.model or "default")
                if cur is not None and effort not in cur.efforts:
                    raise OpError(
                        "bad_effort",
                        f"{cur.label} takes no effort level"
                        if not cur.efforts
                        else f"{cur.label} takes {', '.join(cur.efforts)}",
                    )
        try:
            await s.configure(model=model, effort=effort, permission=permission)
        except ControlError as e:
            raise OpError("refused", f"{s.harness.label} refused: {e}") from e
        except TimeoutError as e:
            raise OpError(
                "timeout", f"{s.harness.label} did not answer within 15 s"
            ) from e
        except (BrokenPipeError, ConnectionResetError) as e:
            raise _dead(e) from e
        return s.wire()

    async def _command(self, s, name: str, arg: str, caller: Caller = Caller("user")):
        """Run an aegis command typed in the composer."""
        cmd = commands.AEGIS[name]
        if cmd.hint.startswith("<") and not arg:
            raise OpError("missing_argument", f"usage: /{name} {cmd.hint}")
        reg = self.sessions
        if name == "model":
            return await self._configure(s, arg, None, None)
        if name == "effort":
            if arg not in EFFORTS:
                raise OpError("bad_effort", f"an effort is one of {', '.join(EFFORTS)}")
            return await self._configure(s, None, arg, None)
        if name == "permission":
            if arg not in PERMISSION_MODE:
                raise OpError(
                    "bad_permission",
                    f"a permission is one of {', '.join(PERMISSION_MODE)}",
                )
            return await self._configure(s, None, None, arg)
        if name == "rename":
            return reg.rename(s.log_id, arg, None)
        if name == "title":
            return reg.rename(s.log_id, None, arg)
        if name == "btw":
            return self.side_notes.ask(s, arg)
        if name == "help":
            return None  # the client opens its menu or the Help tab; nothing goes to claude
        if name == "tutorial":
            # A guide in a new tab, on default_agent in this session's
            # directory (help_ops.help_guide); the client opens its tab.
            params = {"kind": "tutorial", "page": arg or None, "cwd": str(s.spec.cwd)}
            r = await self.registry.call("help.guide", params, caller)
            return {**r, "server": self.server_name}
        if name == "whats-new":
            # A tour of the release notes in a new tab, on default_agent in
            # this session's directory (help_ops.help_guide); the client opens
            # its tab.
            params = {"kind": "whats_new", "cwd": str(s.spec.cwd)}
            r = await self.registry.call("help.guide", params, caller)
            return {**r, "server": self.server_name}
        if name in ("workflow", "loop"):
            # A person's line launches a saved workflow: the only path, beside
            # an approved schedule's fire, that mints a launch token
            # (workflows.Launches). session.send is not open to agents.
            if name == "workflow" and not arg:
                return None  # the browser opens the menu of saved workflows
            line = ("loop " + arg) if name == "loop" else arg
            wid, _, typed = line.partition(" ")
            origin = {
                "by": "person",
                "principal": self.who(caller).name,
                "line": f"/{name} {arg}",
            }
            body = self.workflows.launch(
                s, self.saved.get(wid), typed.strip(), origin
            )
            await s.deliver(f"> from workflow:{wid} · launch · {iso_now()}", body)
            return {"launched": wid}
        if name == "stop":
            await s.stop()
            return s.wire()
        if name == "spawn":
            return await self._spawn_line(commands.parse_spawn(arg), caller, s)
        await reg.close(s.log_id)  # close
        return None

    async def _spawn_line(self, line: commands.SpawnLine, caller: Caller, s) -> dict:
        """``/spawn``: a person starting a session, here or, with ``@server``, on
        a linked server through the link. Nobody's child: no ``spawned_by``, but
        its prompt carries the tail of tab ``s``, where it was typed."""
        far = None if line.server == self.server_name else line.server
        prompt = line.prompt and spawn_opening(s, line.prompt, far and self.server_name)
        params = {
            k: v
            for k, v in (
                ("agent", line.agent),
                ("prompt", prompt),
                ("model", line.model),
                ("effort", line.effort),
                ("permission", line.permission),
                ("cwd", line.cwd),
                ("quiet", line.quiet or None),
            )
            if v is not None
        }
        if far is None:
            r = await self.registry.call("session.spawn", params, caller)
            return {**r, "server": self.server_name}
        if caller.link is not None:
            raise OpError("not_relayed", "a link is not relayed to another server")
        if not self.who(caller).is_admin:
            raise OpError(
                "not_for_members",
                "spawning on a linked server is for an admin of this one",
            )
        link = self.links.get(far)
        if link is None:
            raise OpError("unknown_server", f"this server links no server named {far}")
        if link.state != "linked":
            raise OpError("server_offline", f"{link.describe()}; nothing was started")
        r = await link.call("session.spawn", params)
        return {**r, "server": far}

    async def _spawn_far(
        self, p: SpawnParams, name: str, far: str, parent, caller: Caller
    ) -> dict:
        """An agent here spawns on a linked server, through the link, as the
        token the link presents there; that server's policy for it decides.
        The far session's permission is at most the agent's, the far agent's
        preset read first. Nothing the far server wrote reaches the agent: its
        handle and log id are checked, its errors worded here (agent_ops)."""
        if not self.who(caller).is_admin:
            raise OpError(
                "not_for_members",
                "spawning on a linked server is for an admin's agents",
            )
        link = self.links.get(far)
        if link is None:
            raise OpError("unknown_server", f"this server links no server named {far}")
        if link.state != "linked":
            raise OpError(
                "server_offline", f"{far} is not linked right now; nothing was started"
            )
        cap = parent.spec.permission
        try:
            listed = await link.call("agents.list", {})
        except OpError as e:
            raise far_error(e, far) from None
        # What the far server says of its agents only lowers the permission.
        presets = listed.get("agents") if isinstance(listed, dict) else None
        agent = name or (listed.get("default") if isinstance(listed, dict) else None)
        preset = next(
            (
                a.get("permission")
                for a in presets or []
                if isinstance(a, dict) and a.get("name") == agent
            ),
            None,
        )
        permission = p.permission or preset
        if permission not in PERMISSION_ORDER or PERMISSION_ORDER.index(
            permission
        ) > PERMISSION_ORDER.index(cap):
            if p.permission:
                raise OpError(
                    "not_allowed",
                    f"your session runs with {cap}, so a session you spawn can "
                    f"have at most {cap} (this one would have {p.permission})",
                )
            permission = cap
        params = p.model_dump(exclude_none=True) | {
            "agent": name or None,
            "permission": permission,
            "spawned_from": {"handle": parent.handle, "server": self.server_name},
        }
        try:
            r = await link.call(
                "session.spawn", {k: v for k, v in params.items() if v is not None}
            )
        except OpError as e:
            raise far_error(e, far) from None
        if not isinstance(r, dict):
            raise OpError("far_error", f"{far} answered the spawn with something else")
        handle, log_id = r.get("handle"), r.get("log_id")
        if not (isinstance(handle, str) and valid_handle(handle)) or not (
            isinstance(log_id, str) and LOG_ID.match(log_id)
        ):
            raise OpError("far_error", f"{far} answered the spawn with something else")
        return {"log_id": log_id, "handle": f"{handle}@{far}", "server": far}

    async def _archive(self, p: ArchiveParams, caller: Caller) -> dict:
        positions = archive.decode(p.cursor)
        # A link asking reads this server's archive only: links are not relayed.
        only = self.server_name if caller.link is not None else p.server
        pages: dict[str, tuple[list[dict], archive.Position | None]] = {}
        counts: dict[str, int] = {}
        workers = 0
        for name, fetch in self._archive_sources(only):
            if name in positions and positions[name] is None:
                continue  # exhausted on an earlier page
            got = await fetch(p.query, p.limit, positions.get(name), p.workers)
            if got is None:
                continue  # a linked server that is down keeps its place
            items, total, n_workers, last = got
            pages[name] = ([{**m, "server": name} for m in items], last)
            counts[name] = total
            workers += n_workers
        items, positions = archive.merge(pages, p.limit, positions)
        more = any(v is not None for v in positions.values()) or any(
            name not in positions for name in pages
        )
        # A linked server that is down is named, so its rows are visibly missing.
        offline = [
            link.name
            for link in self.links.links()
            if link.state != "linked"
            and only in (None, link.name)
            and caller.link is None
        ]
        return {
            "items": items,
            "total": sum(counts.values()),
            "counts": counts,
            "workers": workers,
            "offline": offline,
            "cursor": archive.encode(positions) if more else None,
        }

    def _archive_sources(self, only: str | None):
        """Each server an archive listing reads, with how to fetch a page."""

        async def local(query, limit, after, workers):
            items, total, last = self.sessions.archive(query, limit, after, workers)
            return items, total, self.sessions.archived_workers(query), last

        if only in (None, self.server_name):
            yield self.server_name, local
        for link in self.links.up():
            if only not in (None, link.name):
                continue

            async def far(query, limit, after, workers, link=link):
                cursor = archive.encode({link.name: after}) if after else None
                params = {
                    "query": query,
                    "limit": limit,
                    "server": link.name,
                    "cursor": cursor,
                }
                # Sent only when set: a server older than #331 refuses the
                # field, and leaving it out is the default on a newer one.
                if workers:
                    params["workers"] = True
                try:
                    r = await link.call("archive.list", params)
                except OpError:
                    return None  # down since the listing started: keep its place
                last = archive.decode(r.get("cursor")).get(link.name)
                return r.get("items", []), r.get("total", 0), r.get("workers", 0), last

            yield link.name, far

    def _register(self) -> None:
        r = self.registry
        reg = self.sessions

        @r.op("agents.list", agent=True, read=True)
        async def agents_list(_, caller):
            """The agents you can spawn, each a preset of harness, model, effort
            and permission. session_spawn starts one and can override those."""
            agents, default = self._agents()
            return {
                "agents": [a.as_dict() for a in agents],
                "default": default,
                "harnesses": [
                    {"name": h, "supported": h in SUPPORTED_HARNESSES}
                    for h in HARNESSES
                ],
                "models": model_suggestions(agents),
                "cwd": str(self.roots.harness_cwd),
                "config_error": self.config.current().error,
            }

        @r.op("session.spawn", SpawnParams, agent=True)
        async def spawn(p: SpawnParams, caller):
            """Start a new session from an agent, overriding its harness, model,
            effort or permission if you need to, and send it `prompt` as its
            first message. Its permission can be at most yours. Returns its log
            id and handle. It does not report back: read it with peer_read,
            message it with peer_handoff. With agent@server it starts on that
            linked server, as this server's link there, and that server's quota
            policy for the link decides; you can hand off to it, not read it."""
            parent = reg.sessions.get(caller.log_id) if caller.is_agent else None
            if p.agent and "@" in p.agent:
                name, _, server = p.agent.rpartition("@")
                if server != self.server_name:
                    if parent is None:
                        raise OpError(
                            "not_across_links",
                            f"{p.agent}: a person spawns on a linked server with "
                            "/spawn agent@server",
                        )
                    return await self._spawn_far(p, name, server, parent, caller)
                p = p.model_copy(update={"agent": name})
            if p.spawned_from is not None and (
                caller.link is None or p.spawned_from.server != caller.link
            ):
                raise OpError(
                    "not_a_link",
                    "only a link, for its own server, says where a spawn came from",
                )
            agents, default = self._agents()
            spec = resolve(
                agents,
                default,
                p.agent,
                {
                    "harness": p.harness,
                    "model": p.model,
                    "effort": p.effort,
                    "permission": p.permission,
                },
                self._resolve_cwd(p.cwd, parent.spec.cwd if parent else None),
                spawned_by=parent.log_id if parent else None,
                # A child is its parent's owner's, so a member's agent cannot
                # hand work to anyone else; a person's session is theirs.
                owner=parent.spec.owner if parent else self.who(caller).name,
            )
            if p.spawned_from is not None:
                if not valid_handle(p.spawned_from.handle):
                    raise OpError("bad_sender", "spawned_from names a handle")
                spec = dataclasses.replace(
                    spec,
                    spawned_from=f"{p.spawned_from.handle}@{p.spawned_from.server}",
                )
            # An agent cannot hand a session more power than it has itself.
            if parent is not None and PERMISSION_ORDER.index(
                spec.permission
            ) > PERMISSION_ORDER.index(parent.spec.permission):
                raise OpError(
                    "not_allowed",
                    f"your session runs with {parent.spec.permission}, so a session "
                    f"you spawn can have at most {parent.spec.permission} "
                    f"(this one would have {spec.permission}); pass permission",
                )
            if parent is not None:
                # A node's helpers count in the workflow pool (workflows._room).
                self.workflows.admit(parent, spec.agent)
            if p.quiet and not p.prompt:
                raise OpError(
                    "quiet_needs_prompt",
                    "a quiet session needs a prompt: without one it is idle "
                    "and nothing would ever bring its tab back",
                )
            g = self.gate(spec.owner, spec.harness, spec.model)
            if not g.may_start:
                raise OpError("quota_closed", refusal(spec.owner or self.user, g))
            try:
                # A run's child carries its schedule, so spawn: false holds for
                # grandchildren too (ops.NO_SPAWN).
                s = await reg.spawn(
                    spec,
                    quiet=p.quiet,
                    schedule=parent.schedule if parent else None,
                )
            except FileNotFoundError as e:
                raise OpError(
                    "harness_not_found", f"cannot run {self._bin(spec.harness)!r}: {e}"
                ) from e
            except (OSError, TimeoutError) as e:
                raise OpError(
                    "harness_failed", f"{self._bin(spec.harness)!r} did not start: {e}"
                ) from e
            if p.prompt:
                try:
                    await s.send(p.prompt)
                except (BrokenPipeError, ConnectionResetError, FileNotFoundError) as e:
                    raise OpError(
                        "send_failed",
                        f"{s.handle} ({s.log_id}) started, but its first message "
                        f"failed: {e}",
                    ) from e
            return {"log_id": s.log_id, "handle": s.handle}

        async def deliver(p: SendParams, caller):
            s = reg.open(p.log_id)
            s.check_lock()  # before a / command or an upload acts on it
            # A message written offline carries the transcript's revision as the
            # writer saw it: if anything a person reads has happened since, it
            # goes back to ask instead of answering a question no longer asked.
            if p.expect_rev is not None and s.fold().content_index > p.expect_rev:
                raise OpError(
                    "stale",
                    "the session moved on since this was written; "
                    "send it again to send it anyway",
                )
            cmd = commands.split(p.text)
            if cmd is not None:
                if p.attachments:
                    raise OpError(
                        "attachments_with_command",
                        "a / line takes no files; send them with a message",
                    )
                name, arg = cmd
                if name in commands.AEGIS:
                    return await self._command(s, name, arg, caller)
                cat = await self.catalogs.get(s)
                if cat is not None and cat.commands and not cat.has(name):
                    raise OpError(
                        "unknown_command",
                        f"no command /{name} in this session; "
                        "start the line with // to send it as text",
                    )
            text = commands.escape(p.text)
            if (why := reg.quota_hold(s)) is not None:
                owner = s.spec.owner or self.user
                raise OpError(
                    "quota_closed",
                    f"{owner}'s quota gate holds this session: {why}. "
                    "It continues when the gate opens.",
                )
            try:
                # The harness first: a send that cannot reach the agent leaves
                # its uploads staged, to send again.
                await s.ensure_running()
                sent: list[dict] = []
                if p.attachments:
                    try:
                        sent = await asyncio.to_thread(
                            att.commit, self.roots.state_root, p.log_id, p.attachments
                        )
                    except files.FileError as e:
                        raise OpError(e.code, e.message) from e
                await s.send(att.message(text, sent), typed=text, attached=sent)
            except FileNotFoundError as e:
                raise OpError(
                    "harness_not_found",
                    f"cannot run {self._bin(s.spec.harness)!r}: {e}",
                ) from e
            except (BrokenPipeError, ConnectionResetError) as e:
                raise _dead(e) from e

        @r.op("session.handoff", LogParams, owned="log_id")
        async def handoff(p: LogParams, caller):
            """Continue a session's work in a fresh one: the same agent, folder,
            model and effort, opened with its handoff brief and last turns. The
            old session is archived when the new one finishes its first turn."""
            old = reg.open(p.log_id)
            old.check_lock()
            if old.worker:
                raise OpError(
                    "worker", f"{old.handle} is a queue worker; its task owns it"
                )
            if reg.successor(old) is not None:
                raise OpError("handed_off", f"{old.handle} is already being continued")
            if old.in_turn:
                raise OpError(
                    "busy", f"{old.handle} is working; hand it off when its turn ends"
                )
            report = old.standing.get("report") or {}
            prompt = handoff_opening(
                old.handle, report.get("handoff"), spawn_tail(old.entries())
            )
            spec = dataclasses.replace(
                old.spec, continues=old.log_id, spawned_by=None, spawned_from=None
            )
            g = self.gate(spec.owner, spec.harness, spec.model)
            if not g.may_start:
                raise OpError("quota_closed", refusal(spec.owner or self.user, g))
            # Set before the spawn awaits, so a second click in that window is
            # refused rather than starting a second session.
            old.mark_continued("starting")
            try:
                s = await reg.spawn(spec)
            except (OSError, TimeoutError) as e:
                old.mark_continued(None)
                raise OpError(
                    "harness_failed", f"{self._bin(spec.harness)!r} did not start: {e}"
                ) from e
            except BaseException:
                old.mark_continued(None)
                raise
            old.mark_continued(s.log_id)
            try:
                await s.send(prompt)
            except (BrokenPipeError, ConnectionResetError, FileNotFoundError) as e:
                old.mark_continued(None)
                raise OpError(
                    "harness_failed", f"{s.handle} did not take its first message: {e}"
                ) from e
            return {"log_id": s.log_id, "handle": s.handle}

        @r.op("session.send", SendParams, owned="log_id")
        async def send(p: SendParams, caller):
            # A page that got no answer in time sends again with the same nonce,
            # and a stalled socket can flush every copy at once (#350): each
            # copy answers with the first one's delivery. One that failed is
            # forgotten, so its retry is delivered. Shielded: a caller that
            # goes away does not cut a delivery short.
            if p.nonce is None:
                return await deliver(p, caller)
            key = (p.log_id, p.nonce)
            first = self._sends.get(key)
            if first is None:
                first = self._sends[key] = asyncio.ensure_future(deliver(p, caller))
                if len(self._sends) > SEND_NONCES:
                    self._sends.popitem(last=False)

                def forget(f: asyncio.Future) -> None:
                    if (f.cancelled() or f.exception()) and self._sends.get(key) is f:
                        del self._sends[key]

                first.add_done_callback(forget)
            return await asyncio.shield(first)

        @r.op("attachment.begin", AttachBegin, owned="log_id")
        async def attachment_begin(p: AttachBegin, caller):
            """Start uploading a file to a session: its upload id. The file
            waits, staged, until a session.send names it (attachments.py)."""
            reg.open(p.log_id)
            try:
                uid = att.begin(self.roots.state_root, p.log_id, p.name, p.size)
            except files.FileError as e:
                raise OpError(e.code, e.message) from e
            return {"upload_id": uid}

        @r.op("attachment.put", AttachPut, owned="log_id")
        async def attachment_put(p: AttachPut, caller):
            """One base64 chunk of an upload at ``offset``: the bytes now
            staged. No await between reading the staged size and appending,
            so two chunks of one file cannot interleave."""
            reg.open(p.log_id)
            try:
                data = base64.b64decode(p.data, validate=True)
            except binascii.Error as e:
                raise OpError("bad_chunk", f"the chunk is not base64: {e}") from e
            if len(data) > att.CHUNK_BYTES:
                raise OpError(
                    "bad_chunk", f"a chunk carries at most {att.CHUNK_BYTES} bytes"
                )
            try:
                size = att.put(
                    self.roots.state_root, p.log_id, p.upload_id, p.offset, data
                )
            except files.FileError as e:
                raise OpError(e.code, e.message) from e
            return {"size": size}

        @r.op("attachment.drop", AttachDrop, owned="log_id")
        async def attachment_drop(p: AttachDrop, caller):
            """Forget an upload the person removed before sending."""
            reg.open(p.log_id)
            try:
                att.drop(self.roots.state_root, p.log_id, p.upload_id)
            except files.FileError as e:
                raise OpError(e.code, e.message) from e

        @r.op("session.read", ReadParams)
        async def read(p: ReadParams, caller):
            """A person read these agent messages, on any browser."""
            s = reg.open(p.log_id)
            n = s.read(p.ids)
            return {"read": n, "unread": len(s.unread)}

        @r.op("recap.request", RecapParams, owned="log_id")
        async def recap_request(p: RecapParams, caller):
            """A recap of where the session stands, for a person landing on its tab."""
            return await self.recaps.request(reg.open(p.log_id), p.force)

        @r.op("btw.cancel", BtwCancelParams, owned="log_id")
        async def btw_cancel(p: BtwCancelParams, caller):
            """Cancel a running side note; its block keeps the question, folded."""
            return self.side_notes.cancel(reg.open(p.log_id), p.id)

        @r.op("dictation.prepare", NoParams)
        async def dictation_prepare(p: NoParams, caller):
            """The engine and model the browser transcribes with, on disk and
            verified, and the words to bias it toward (dictation.py)."""
            try:
                await self.dictation.ensure()
            except dictation.Unavailable as e:
                raise OpError("dictation_unavailable", str(e)) from e
            snap = self.config.current()
            live = reg.open_sessions()
            return {
                "base": f"/dictation/{self.dictation.id}/",
                "keywords": dictation.keywords(
                    [s.handle for s in live],
                    [s.spec.cwd for s in live],
                    [*(a.name for a in snap.agents), *snap.queues],
                ),
            }

        @r.op("session.configure", ConfigureParams, owned="log_id")
        async def configure(p: ConfigureParams, caller):
            s = reg.open(p.log_id)
            s.check_lock()
            return await self._configure(s, p.model, p.effort, p.permission)

        @r.op("commands.list", LogParams)
        async def commands_list(p: LogParams, caller):
            """What the composer's menu offers this session."""
            cat = await self.catalogs.get(reg.open(p.log_id))
            return {
                "commands": commands.aegis_wire()
                + (cat.wire_commands(shadowed=commands.AEGIS) if cat else []),
                "models": cat.wire_models() if cat else [],
                "permissions": list(PERMISSION_MODE),
                # What /workflow completes: the saved workflows that lint.
                "workflows": [
                    {"id": w["id"], "title": w["title"], "source": w["source"]}
                    for w in self.saved.listing(self.workflows._agents())
                    if "error" not in w
                ],
                # False: claude gave no command list, so the menu cannot tell
                # an unknown name from one of claude's.
                "complete": bool(cat and cat.commands),
            }

        @r.op("transcript.detail", DetailParams)
        async def detail(p: DetailParams, caller):
            """The whole entries for rows the wire sent without their detail."""
            return reg.detail(p.log_id, p.ids)

        @r.op("transcript.search", SearchParams)
        async def search(p: SearchParams, caller):
            """The entries whose detail the wire left out hold ``q``: the find
            bar searches the rest in the browser (js/find.js)."""
            return {"ids": reg.search(p.log_id, p.q)}

        @r.op("transcript.output", OutputParams)
        async def output(p: OutputParams, caller):
            """A tool row's whole output, for its copy button: the entry holds
            only the tail. A thread, because it parses the whole store."""
            text = await asyncio.to_thread(reg.output, p.log_id, p.id)
            if text is None:
                raise OpError("no_output", f"no output for {p.id!r}")
            return {"text": text}

        @r.op("session.interrupt", LogParams, owned="log_id")
        async def interrupt(p: LogParams, caller):
            s = reg.open(p.log_id)
            try:
                await s.interrupt()
            except (BrokenPipeError, ConnectionResetError) as e:
                raise _dead(e) from e

        @r.op("session.stop", LogParams, owned="log_id")
        async def stop(p: LogParams, caller):
            s = reg.open(p.log_id)
            s.check_lock()
            await s.stop()

        @r.op("session.warm", LogParams, owned="log_id")
        async def warm(p: LogParams, caller):
            # Someone started typing to a parked session: start its harness while
            # they write (park.py). A closed quota gate starts nothing.
            s = reg.open(p.log_id)
            if s.status == "parked" and not reg.quota_hold(s):
                await s.ensure_running()

        @r.op("session.unquiet", LogParams, owned="log_id")
        async def unquiet(p: LogParams, caller):
            # A workflow's node has no tab while its run lives (spec §05):
            # the person reads it from the run's card or the Fleet.
            if (why := reg.workflow_lock(reg.open(p.log_id))) is not None:
                raise OpError("in_workflow", f"{why}; it gets a tab when the run ends")
            return reg.set_quiet(p.log_id, False)

        @r.op("session.reopen", LogParams, owned="log_id")
        async def reopen(p: LogParams, caller):
            return reg.reopen(p.log_id).wire()

        @r.op("session.rename", RenameParams, agent=True, read=True, owned="log_id")
        async def rename(p: RenameParams, caller):
            """Rename your session: a handle (2 or 3 lowercase segments joined by
            hyphens) and/or a title."""
            log_id = p.log_id
            if caller.is_agent:
                if log_id not in (None, caller.log_id):
                    raise OpError(
                        "not_yours", "agents can rename only their own session"
                    )
                log_id = caller.log_id
            if log_id is None:
                raise OpError("bad_params", "log_id is required")
            if (s := reg.sessions.get(log_id)) is not None:
                s.check_lock()
            return reg.rename(log_id, p.handle, p.title)

        @r.op("archive.list", ArchiveParams)
        async def archive_list(p: ArchiveParams, caller):
            """A page of closed sessions, newest first, across this server and
            the servers it links (archive.py). ``total`` and ``counts`` are of
            the servers this page read; the first page reads every one.
            Queue workers are left out unless ``workers`` is set; ``workers``
            in the reply is how many match either way."""
            return await self._archive(p, caller)

        @r.op("file.open", FileRef)
        async def file_open(p: FileRef, caller):
            """Open a sent file in the desktop's app for it. Only for a person
            whose browser runs on the server's desktop; never an agent tool,
            or an agent could launch apps on that desktop."""
            if not caller.desktop:
                raise OpError(
                    "not_local", "only a browser on the server's desktop can do this"
                )
            path = files.find(self.roots.state_root, p.file_id, p.name)
            if path is None:
                raise OpError("no_file", f"no sent file {p.name!r}")
            try:
                files.open_natively(path)
            except (files.FileError, OSError) as e:
                raise OpError("open_failed", str(e)) from e

        async def peek_path(s, row, index: int, pick: str | None):
            """A path a reply named, resolved now: as written when absolute,
            else under the config root and under the session's cwd (#323).
            When both hold a file and they differ, it opens neither and asks:
            `pick` ("root" or "cwd") is the person's answer. The browser sends
            only the index and the pick, so it can open nothing the agent did
            not write. Nothing is recorded on the reply."""
            paths = (
                row["detail"].get("paths", []) if row and row["kind"] == "prose" else []
            )
            if not 0 <= index < len(paths):
                raise OpError("no_path", "that reply names no such path")
            raw = refs.LINE.sub("", paths[index])
            tried = refs.tried(raw, s.spec.cwd, self.roots.config_root)
            where = (
                ""
                if "absolute" in tried
                else f" under {self.roots.config_root} or {s.spec.cwd}"
            )
            found = {k: t for k, t in tried.items() if t.is_file()}
            if not found:
                raise OpError("no_file", f"no file {raw}{where}")
            if len(found) == 2 and found["root"].resolve() != found["cwd"].resolve():
                if pick is None:
                    raise OpError(
                        "ambiguous_path",
                        f"{raw} names two files: {found['root']} and {found['cwd']}",
                    )
                path = found[pick]
            else:
                path = next(iter(found.values()))
            try:
                (rec,) = await asyncio.to_thread(
                    files.store_all, self.roots.state_root, [path]
                )
            except files.FileError as e:
                raise OpError(e.code, e.message) from e
            return [_sent_file(rec)]

        @r.op("file.peek", PeekParams)
        async def file_peek(p: PeekParams, caller):
            """Copy the file a Read, Write or Edit row used, as it is now, and
            open it inside that row. The person's way to see a file the agent
            did not send; the agent never sees it."""
            s = reg.open(p.log_id)
            row = s.fold().entry(p.entry_id)
            if p.index is not None:
                return await peek_path(s, row, p.index, p.pick)
            raw = row["detail"].get("path") if row and row["kind"] == "tool" else None
            if not raw:
                raise OpError("no_path", "that row used no file")
            path = Path(raw).expanduser()
            if not path.is_absolute():
                path = s.spec.cwd / path
            try:
                (rec,) = await asyncio.to_thread(
                    files.store_all, self.roots.state_root, [path]
                )
            except files.FileError as e:
                raise OpError(e.code, e.message) from e
            sent = {k: v for k, v in rec.items() if k != "kind"}
            s.record_peek({"kind": "peek", "entry": p.entry_id, "files": [sent]})
            url = files.url(rec["file_id"], rec["name"])
            return [{"url": url, "name": rec["name"], "size": rec["size"]}]

        @r.op("path.stat", StatParams)
        async def path_stat(p: StatParams, caller):
            """What each path a reply named is now: file, dir, missing, repo or
            code, one per entry of its detail.paths, at most refs.MAX. The
            client draws each reference by it (#393). Records nothing."""
            s = reg.open(p.log_id)
            row = s.fold().entry(p.entry_id)
            paths = (
                row["detail"].get("paths", []) if row and row["kind"] == "prose" else []
            )
            cwd, root = s.spec.cwd, self.roots.config_root
            head = paths[: refs.MAX]
            kinds = await asyncio.to_thread(
                lambda: [refs.kind(t, cwd, root) for t in head]
            )
            return {"kinds": kinds + ["code"] * (len(paths) - len(head))}

        @r.op("link.list")
        async def link_list(_, caller):
            """The servers this one links, with their state; never their tokens."""
            return self.links.wire()

        @r.op("link.add", LinkAdd, admin=True)
        async def link_add(p: LinkAdd, caller):
            """Link another aegis server: connect once with its token, take the
            name it gives itself, and keep the link in links.json."""
            try:
                welcome = await probe(p.url, p.token, self.server_name, self.user)
            except LinkError as e:
                raise OpError("link_refused", e.message) from e
            name = str(welcome.get("server"))
            if p.name and p.name != name:
                raise OpError(
                    "bad_link", f"that server calls itself {name}, not {p.name}"
                )
            self.links.add(name, p.url, p.token)
            return {"name": name}

        @r.op("link.remove", LinkName, admin=True)
        async def link_remove(p: LinkName, caller):
            self.links.remove(p.name)

        @r.op("token.list", admin=True)
        async def token_list(_, caller):
            """The tokens that may use this server, the admin's first, the
            policy of every member without its own, the pools a policy names,
            and each member's gate on each pool now."""
            snap, now = self.quota.snapshot(), time.time()

            def gates(policy: dict | None) -> dict | None:
                if policy is None:
                    return None
                return {pool: decide(policy, pool, snap, now).state for pool in POOLS}

            rows = self.principals.rows()
            for row in rows:
                row["gates"] = gates(self.principals.policy_of(row["name"]))
            default = self.principals.default_policy()
            return {
                "tokens": rows,
                "default_policy": default,
                "default_gates": gates(default or {}),
                "pools": pools_wire(),
            }

        @r.op("token.add", TokenAdd, admin=True)
        async def token_add(p: TokenAdd, caller):
            """Mint a token; the only time it is shown."""
            token = self.principals.add(p.name, p.role)
            return {"name": p.name, "role": p.role, "token": token}

        @r.op("token.revoke", TokenName, admin=True)
        async def token_revoke(p: TokenName, caller):
            """End a token. The admin's is replaced, and the new one returned
            to whoever revoked it, so that browser can sign in again. Its
            devices stop hearing pushes (push.py)."""
            token = self.principals.revoke(p.name)
            self.push.subscriptions.remove_owner(p.name)
            return {"token": token}

        @r.op("token.policy", TokenPolicy, admin=True)
        async def token_policy(p: TokenPolicy, caller):
            """Set a member's quota policy, or with name * the default one.
            The gate applies it at the next reading."""
            if p.name == "*":
                self.principals.set_default_policy(p.policy)
            else:
                self.principals.set_policy(p.name, p.policy)

        @r.op("server.version")
        async def server_version(_, caller):
            return await self.versions.wire()

        @r.op("net.measure")
        async def net_measure(_, caller):
            """A speed reading now, whatever its cadence says; the host channel
            carries the result, and so does the return value."""
            if not self.config.current().network.enabled:
                raise OpError("disabled", "network readings are off in network:")
            await self.net.measure()
            return self.net.wire()

        @r.op("quota.read", agent=True, read=True)
        async def quota_read(_, caller):
            """How much of each subscription window is left: per provider, each
            window's percent used, severity, projected percent at reset and
            reset time (epoch seconds). Reads the last reading; never asks the
            vendor, so calling it costs nothing."""
            return self.quota.snapshot()
