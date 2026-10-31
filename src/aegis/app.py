"""The server's state and its operations, independent of any transport.

Operations: ``agents.list``, ``session.spawn``, ``session.send`` (which
resolves a ``/`` line first, ``commands.py``), ``session.read``,
``recap.request``, ``btw.cancel``, ``dictation.prepare``, ``speech.prepare``, ``session.configure``,
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
Channels: ``sessions`` (every open session's meta; patches ``upsert``,
``merge`` to a subscriber that asks, and ``remove``, which carries the
``archived`` row when a close archived it), ``transcript:<log_id>`` (any session, archived included; a
subscribe ``since`` a revision gets what changed after it), ``quota`` (each
provider's windows; patches ``set``), ``host`` (CPU, RAM and disk while someone
watches; patches ``set``), ``journal`` (when its rows last changed; patches
``set``), ``repos:<log_id>`` (the repos a session wrote to and their state;
patches ``set``, ``repos/service.py``), ``schedules`` (every schedule's state; patches ``upsert`` and
``remove``, ``schedules.py``), ``workflow-runs`` (every workflow run's summary,
without its tree and story; patches ``upsert``, or ``merge`` to a subscriber
that asks), ``workflow:<run>`` (one run whole, for its card; patches ``set``),
``workflows`` (every run whole, for a client older than #525) and ``config``
(``.aegis.yaml`` as aegis holds it; patches ``set``).
"""

from __future__ import annotations

from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as pkg_version

import asyncio
import base64
import binascii
import contextlib
import dataclasses
import getpass
import json
import logging
import os
import pwd
import re
import time
from collections import OrderedDict
from pathlib import Path
from collections.abc import Callable
from typing import Literal

from pydantic import BaseModel, Field, model_validator
from pydantic.json_schema import SkipJsonSchema

from . import archive, attachments as att, commands, dictation, files, restarts, speech
from . import pause
from . import feedback, harness_versions, multiuser, setup
from . import layout as canvas
from . import workflow_store
from .agent_ops import (
    far_error,
    handoff_opening,
    register_agent_ops,
    spawn_opening,
    spawn_tail,
)
from .artifact_ops import register_artifact_ops
from .config_suggest import register_config_suggest
from .secret_ops import register_secret_ops
from .claude.login import SignIn
from .agents import (
    EFFORTS,
    HARNESSES,
    PERMISSION_ORDER,
    SUPPORTED_HARNESSES,
    model_suggestions,
    resolve,
)
from .cascade import Cooling, Fall, classify, pick
from .cgroups import SessionCgroups
from .channels import Channels, Throttle
from .claude.process import PERMISSION_MODE, ControlError
from .config import Config, Snapshot, accept_version, write
from .harness import harness_for
from .config_ops import register_config_ops
from .doctor_watch import DoctorWatch
from .confirm import Confirmations
from .help_ops import register_help_ops
from .host import HostSampler
from .load import LoadSampler
from .net.service import NetService
from .exit import Exits
from .links import LinkError, Links, probe
from .mcp import PATH as MCP_PATH, Tokens, build_mcp
from . import refs
from .monitors import Monitors, iso_now
from .names import valid_handle
from .notice_ops import register_notice_ops
from .notices import Notices
from .nudges import Nudges
from . import park
from .park import Parker, Watchers
from .push import register_push_ops
from .queues import Queues
from .journal.ops import register_journal_ops
from .journal.service import Journal
from .knowledge.know_how import KnowHow
from .knowledge.service import Knowledge
from .knowledge.items import NAME_RE, Item
from .skill_mcp import SkillServers
from .knowledge_ops import (
    consolidate_turn,
    offered,
    register_knowledge_ops,
    remember_turn,
    skill_turn,
)
from .repos.service import Repos
from .layout_ops import register_layout_ops
from .fs_ops import register_fs_ops
from . import fs_paths, fs_trash
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
from . import as_user, launch
from .opencode import sweep as opencode_sweep
from .tokens import TokenStore
from .as_user import Place
from .member_view import MemberView
from .users import MultiUser
from .registry import Registry
from .roots import Roots
from .usage.ops import register_usage_ops
from .session import PUBLISH_EVERY_S, SpawnSpec
from .term_ops import register_term_ops
from .browser_ops import register_browser_ops
from .terminals import Terminals
from .browsers import Browsers
from .version import Versions

log = logging.getLogger("aegis.app")
# How often every principal's memories are checked for expiry and staleness.
KNOWLEDGE_CHECK_S = 86400
# The most one principal's staleness check may take: a hung mount must not
# hold the pass, or the others, forever.
KNOWLEDGE_CHECK_TIMEOUT_S = 30.0
# A log id as registry.new_log_id mints it, checked on what a link answers.
LOG_ID = re.compile(r"^\d{8}-\d{6}-[0-9a-f]{6}$")
# How many nonced sends the server remembers, across every session.
SEND_NONCES = 512

Effort = Literal["low", "medium", "high", "xhigh", "max"]
Permission = Literal["read", "write", "full", "auto"]


class _Strict(BaseModel):
    model_config = {"extra": "forbid"}


# The param that names a session by handle in each op that takes one
# (App._hides): a member may not learn of another's session through it.
HANDLE_FIELDS = {
    "peer.read": "target",
    "peer.handoff": "target",
    "peer.deliver": "target",
    "session.close": "handle",
    "session.quiet": "handle",
    "monitor.sessions": "sessions",
    "schedule.test": "session",
    "journal.search": "session",
}


class SpawnedFrom(_Strict):
    handle: str
    server: str
    group: str | None = None


def far_group(key: str, here: str, there: str) -> str:
    """The group key a spawn from server ``here`` sends to server ``there``
    (#586): a local log id gains ``@here``, a key that already names its
    server keeps it, and one that names ``there`` is a plain log id again."""
    if "@" not in key:
        return f"{key}@{here}"
    return key.removesuffix(f"@{there}")


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
        None,
        min_length=1,
        description=(
            "The new session's first message. A / line runs as if typed in "
            "that session's box: an aegis command runs, // sends a leading /."
        ),
    )
    # Set by a server that links this one, for an agent of its own that
    # spawns here (app.spawn); refused from anything but a link.
    quiet: bool = Field(
        False,
        description="Start it without a tab, for background work: its tab "
        "appears when its turn ends. Needs a prompt.",
    )
    spawned_from: SkipJsonSchema[SpawnedFrom | None] = Field(None, exclude=True)
    # The session a person spawned this from (a typed /spawn, a card's New
    # session): it joins that session's group, and nothing else follows (#586).
    from_session: SkipJsonSchema[str | None] = Field(None, exclude=True)
    # A person's answer to harness_untested (harness_versions.py): start this
    # once, or allow the version for good. Agents never get asked.
    untested: SkipJsonSchema[Literal["once", "always"] | None] = Field(
        None, exclude=True
    )


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
    # The answer to harness_untested, for a send that starts a stopped session
    # or a /spawn line (SpawnParams.untested).
    untested: Literal["once", "always"] | None = None
    # The box got dictated text since the last send: the agent reads the text
    # with dictation.NOTE after it (#579).
    dictated: bool = False

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


class HandoffParams(_Strict):
    """What a handoff starts, when it is not what the old session ran (#874)."""

    # A person names the session; an agent hands off its own.
    log_id: SkipJsonSchema[str | None] = Field(None, exclude=True)
    agent: str | None = Field(
        None, description="The agent that continues; omitted, your own."
    )
    msg: str | None = Field(
        None, description="What the new session should do, the last thing it reads."
    )
    host: str | None = Field(
        None, description="A linked server to continue on; omitted, this one."
    )
    model: str | None = None
    effort: Effort | None = None
    permission: Permission | None = None
    cwd: str | None = None


class PreviewParams(_Strict):
    harness: str
    cwd: str | None = None


def _held(owner: str, why: str) -> str:
    return f"{owner}'s quota gate holds this session: {why}. It continues when the gate opens."


class AutocloseParams(LogParams):
    on: bool


class RestartParams(LogParams):
    untested: Literal["once", "always"] | None = None


class WarmParams(RestartParams):
    # A person's Wake, not the first keystroke: a closed gate is said.
    wake: bool = False


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
    trusted: bool = Field(
        False,
        description="Ask for a trusted link: both ways, agents unrestricted. "
        "Refused unless the far server is used by its admin alone.",
    )


class LinkTrust(_Strict):
    name: str
    trusted: bool


class LinkExit(_Strict):
    name: str | None = None


class LinkName(_Strict):
    name: str


class TokenAdd(_Strict):
    name: str = Field(description="Who the token is: lowercase, e.g. ben or laptop")
    role: Literal["admin", "member"] = "member"
    linux_user: str | None = Field(
        None, description="The Linux user it runs as, in multi-user mode; default: name"
    )
    no_linux_user: bool = Field(
        False, description="An admin that runs as the server's own user"
    )


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
    # Set by register_config_ops, which holds the harness binaries it runs.
    doctor_watch: DoctorWatch

    def __init__(
        self,
        roots: Roots,
        claude_bin: str = "claude",
        interrupt_timeout: float = 10.0,
        base_url: str | None = None,
        server_name: str = "aegis",
        opencode_bin: str = "opencode",
        codex_bin: str = "codex",
        cursor_bin: str = "cursor-agent",
        dictation_dir: Path | None = None,
        user: str | None = None,
        multi_user: MultiUser | None = None,
    ) -> None:
        self.roots = roots
        # The multi-user mode, fixed at start (users.py); a later edit of the
        # key is logged once per value and waits for a restart.
        self.multi_user = multi_user
        self._mu_told: list[MultiUser | None] = []
        launch.configure(multi_user)
        # Threads that open files as one person's Linux user (as_user.py).
        self.runners = as_user.Runners()
        self.server_name = server_name
        self.claude_bin = claude_bin
        self.opencode_bin = opencode_bin
        self.codex_bin = codex_bin
        self.cursor_bin = cursor_bin
        self.dictation = dictation.Store(dictation_dir or dictation.default_dir())
        self.speech = speech.SpeechStore(speech.default_dir())
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
            cursor_bin=cursor_bin,
        )
        self.terminal_cgroups = None
        self.terminals = Terminals(
            roots.state_root,
            roots.config_root,
            lambda: self.config.current().terminals,
            self.publish,
            lambda: self.terminal_cgroups,
            acts_for=lambda owner: self.principals.acts_for(owner),
            role_of=lambda owner: self.principals.role_of(owner),
            multi_user=multi_user is not None,
        )
        self.browsers = Browsers(
            roots.state_root,
            self.publish,
            lambda channel: self.channels.subscribers(channel),
            self._browser_deliver,
            multi_user=multi_user is not None,
        )
        self.tokens = Tokens()
        self.monitors = Monitors(self.sessions, roots.state_root / "monitors.json")
        self.config = Config(roots.config_root, self._on_config)
        self._config_task: asyncio.Task | None = None
        self._versions_task: asyncio.Task | None = None
        self._knowledge_task: asyncio.Task | None = None
        # Checks a save started in the background, held so none is collected.
        self._knowledge_soon: set[asyncio.Task] = set()
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
            mounts=lambda: self.config.current().host,
        )
        self.net = NetService(
            lambda: self.config.current().network,
            watched=lambda: self.channels.subscribers("host") > 0,
            on_change=self.host.check,
            exit_now=self._exit_now,
        )
        reg = self.sessions
        reg.tokens, reg.monitors, reg.queues, reg.server_name = (
            self.tokens,
            self.monitors,
            self.queues,
            server_name,
        )
        reg.context = lambda: self.config.current().context
        reg.version_policy = lambda: self.config.current().harness_versions
        reg.quota = self.quota
        reg.sign_in = SignIn(
            reg.sessions,
            lambda: self.claude_bin,
            lambda text, s: self.toast(text, "error", source=s),
        )
        self.journal = Journal(
            roots.state_root,
            self.publish,
            user_of=self._linux_user_of_session,
            meta_of=self._meta_of_session,
            accounts=lambda: {
                p["harness"]: f"{p['name']}:{p['account']}"
                for p in self.quota.snapshot()["providers"]
                if p["account"]
            },
        )
        reg.journal = self.journal
        # knowledge.embeddings is read once, here: a change needs a restart.
        self.knowledge = Knowledge(
            roots.state_root, self.config.current().knowledge["embeddings"]
        )
        reg.knowledge = self.knowledge
        # knowledge.know_how too.
        if self.config.current().knowledge["know_how"]:
            reg.know_how = KnowHow(self._linux_user_of_session)
        self.skill_servers = SkillServers()
        reg.skill_servers = self.skill_servers
        reg.principal_of = lambda s: s.spec.owner or self.user
        reg.native_of = lambda owner: self.knowledge_native(owner or self.user)
        self.repos = Repos(
            self.journal.path,
            self.publish,
            lambda: {s.log_id: s.handle for s in self.sessions.open_sessions()},
            lambda name: self.channels.subscribers(name),
            roots.harness_cwd,
            user_of=self._linux_user_of_session,
        )
        self.journal.on_live = self.repos.live
        self.schedules = Schedules(self)
        reg.schedules = self.schedules
        self.workflows = Workflows(self)
        self.saved = workflow_store.Store(roots.config_root, roots.state_root)
        # A pinned version's text outlives later saves: its card diffs from it.
        self.saved.pinned = lambda: {
            (s.pin["id"], s.pin["sha"]) for s in self.schedules.items.values() if s.pin
        }
        reg.workflows = self.workflows
        reg.browsers = self.browsers
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
        self.nudges = Nudges(self.sessions, self.config)
        self.sessions.nudges = self.nudges
        self.notices = Notices(self)
        self.sessions.notices = self.notices
        self.load = LoadSampler(
            lambda: {
                s.log_id: (s.pid, self.sessions.cgroup_dir(s))
                for s in self.sessions.open_sessions()
            },
            self.sessions.load_changed,
        )
        self.sessions.load = self.load
        self.watchers = Watchers()
        self.parker = Parker(
            self.sessions, lambda: self.config.current().park, self.watchers
        )
        # Who the people on this server are, as a link tells the far side. One
        # user per server for now: whoever started it (links.py).
        self.user = user or getpass.getuser()
        # The tokens that may use this server, the admin's first (tokens.py).
        self.principals = TokenStore(
            roots.state_root, self.user, multi_user=multi_user is not None
        )
        for name, linux_user in self.principals.missing_users():
            log.warning(
                "aegis: token %s names Linux user %s, which does not exist",
                name,
                linux_user,
            )
        self.links = Links(roots.state_root, server_name, self.user, self.publish)
        self.links.answer = self.answer_link
        self.exits = Exits(
            self.links,
            lambda: (
                None
                if self.multi_user is None
                else "an exit is off on a multi-user server"
            ),
        )
        launch.configure_exits(self.exits, self._exit_for)
        # Bytes each linked server took through this one as its exit (exit.py).
        self.exit_bytes: dict[str, int] = {}
        self.queues.far_callback = self._far_callback
        self.monitors.far_list = self._far_list
        # Sessions a pause or a continue is under way for, so a reading that
        # lands before it finishes does not start a second one.
        self._quota_busy: set[str] = set()
        # Every session paused by the person, and the ones resumed since
        # (pause.py); read before boot, so a cut turn waits.
        self.paused = pause.read(roots.state_root)
        self.resumed: set[str] = set()
        self.quota.listeners.append(self.on_quota)
        self.quota.listeners.append(self.journal.quota)
        self.quota.history = self.journal.usual_spend
        reg.gate = self.gate
        reg.pause_hold = self.pause_hold
        # Who each session's processes run as, in multi-user mode (users.py).
        reg.multi_user = multi_user is not None
        reg.acts_for = lambda name: self.principals.acts_for(name)
        reg.role_of = lambda name: self.principals.role_of(name) if name else None
        reg.runners = self.runners
        # Models that fell, and the one timer that releases held work when the
        # first of them is free again (cascade.py).
        self.cooling = Cooling()
        self._cool_timer: asyncio.TimerHandle | None = None
        self._cool_at: float | None = None
        reg.start_spec = self.start_spec
        reg.cascade = self.cascade_next
        reg.start_pending_handoff = self.start_pending_handoff
        self.registry = Ops()
        self.registry.who = self.who
        self.registry.owner_of = self.owner_of
        self.registry.hides = self._hides
        self.registry.permission_of = lambda log_id: (
            s.spec.permission if (s := self.sessions.sessions.get(log_id)) else None
        )
        self.registry.schedule_of = lambda log_id: (
            s.schedule if (s := self.sessions.sessions.get(log_id)) else None
        )
        self._register()
        register_agent_ops(self)
        register_config_ops(self)
        register_config_suggest(self)
        self.setup_records = setup.register_setup(self)
        register_artifact_ops(self)
        self.secrets = self.sessions.secrets = register_secret_ops(self)
        register_layout_ops(self)
        self.fs_index, self.fs_tickets = register_fs_ops(self)
        register_term_ops(self)
        register_browser_ops(self)
        self.push = register_push_ops(self)
        register_journal_ops(self)
        register_usage_ops(self)
        register_help_ops(self)
        register_knowledge_ops(self)
        register_notice_ops(self)
        register_schedule_ops(self)
        register_workflow_ops(self)
        self.mcp_server, self.mcp_app = build_mcp(
            self.registry, self.tokens, self.sessions
        )

    def trust_refusal(self, principal: Principal, server: str) -> str | None:
        """Why this server will not trust ``server``'s link, or None: trust is
        for two servers one person alone uses, so the link must hold the
        admin's own token and nobody else may hold one (links.py, #684)."""
        me = self.server_name
        if principal.name != self.principals.owner or not principal.is_admin:
            return f"the link does not hold {me}'s admin token"
        if self.principals.has_members():
            return (
                f"{me} has tokens besides its admin's; a trusted link needs "
                "a server only its admin uses"
            )
        if self.links.outbound(server) is not None:
            return (
                f"{me} links a server named {server} itself; remove that "
                "link to trust this one"
            )
        return None

    def _exit_for(self, agent: str | None) -> str | None:
        """The link a process of ``agent`` exits through; None for direct."""
        snap = self.config.current()
        named = next((a.exit for a in snap.agents if a.name == agent), "")
        if named:
            return None if named == "off" else named
        return snap.exit_via

    def _exit_now(self):
        """The exit the Host rows show: the server's, or the first a profile names."""
        snap = self.config.current()
        name = snap.exit_via or next(
            (a.exit for a in snap.agents if a.exit and a.exit != "off"), None
        )
        return None if name is None else self.exits.get(name)

    def count_exit(self, server: str, n: int) -> None:
        self.exit_bytes[server] = self.exit_bytes.get(server, 0) + n

    async def answer_link(self, server: str, op: str, params) -> dict:
        """Run a call a trusted linked server sent back down its link: as this
        server's admin acting through that link, the same caller a link socket
        is on the far server, so every op keeps one rule for linked calls."""
        caller = Caller(
            "user", link=server, principal=Principal(self.principals.owner, "admin")
        )
        log.info("call %s from linked server %s", op, server)
        try:
            return {"result": await self.registry.call(op, params, caller)}
        except OpError as e:
            error = {"code": e.code, "message": e.message}
            if e.data is not None:
                error["data"] = e.data
            return {"error": error}
        except Exception as e:  # an operation bug must not kill the link
            log.exception("call %s from %s failed", op, server)
            return {
                "error": {"code": "internal", "message": f"{type(e).__name__}: {e}"}
            }

    async def _far_callback(self, t, outcome: str, body: str) -> None:
        """A task an agent on a server linked with trust enqueued here
        finished: its result goes back to that agent over the link."""
        far = t.far_enqueuer or {}
        server = str(far.get("server"))
        link = self.links.get(server)
        if link is None or not self.links.trusted(server):
            log.warning("the result of %s has no trusted link to %s", t.id, server)
            return
        try:
            await link.call(
                "queue.deliver",
                {
                    "target": far.get("handle"),
                    "queue": t.queue,
                    "task_id": t.id,
                    "outcome": outcome,
                    "body": body,
                },
            )
        except OpError as e:
            log.warning("%s refused the result of %s: %s", server, t.id, e.message)

    async def _far_list(self, server: str) -> list | None:
        """A trusted server's open sessions, for a monitor here; None when
        they cannot be read now."""
        link = self.links.get(server)
        if link is None or not self.links.trusted(server):
            return None
        try:
            listed = await link.call("session.list")
        except OpError:
            return None
        return listed if isinstance(listed, list) else None

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

    def start_spec(self, spec: SpawnSpec) -> tuple[SpawnSpec, Gate]:
        """The spec a start runs and the gate it passes. A cascade starts on
        its first model that is neither cooling nor gated, or is closed with
        why. A spec with no cascade, or whose model a person moved outside it
        (a handoff after /model), asks that model's gate alone. Nothing new
        starts while every session is paused (pause.py)."""
        if self.paused is not None:
            return spec, Gate("stopped", pause.WHY)
        if spec.model not in spec.cascade:
            return spec, self.gate(spec.owner, spec.harness, spec.model)
        model, why = pick(
            spec.cascade,
            -1,
            spec.harness,
            lambda m: self.gate(spec.owner, spec.harness, m),
            self.cooling,
            time.time(),
        )
        if model is None:
            return spec, Gate("closed", why)
        return dataclasses.replace(spec, model=model), Gate("open")

    def cascade_next(self, s, text: str) -> tuple[str | None, float] | None:
        """A cascade's turn failed: cool its model and pick the next one below
        it, with the gate of the session's owner (Host.cascade_next)."""
        spec = s.spec
        fall = classify(spec.harness, text)
        if fall is None:
            return None
        until = self.cool(spec.harness, spec.model, fall)
        model, _ = pick(
            spec.cascade,
            spec.cascade.index(spec.model),
            spec.harness,
            lambda m: self.gate(spec.owner, spec.harness, m),
            self.cooling,
            time.time(),
        )
        return model, until

    def cool(self, harness: str, model: str, fall: Fall) -> float:
        """Cool a model that fell, and arm the timer that releases what it
        held when the first cooling model is free again."""
        end = self.cooling.cool(harness, model, fall, time.time())
        self._arm_cooling()
        return end

    def _arm_cooling(self) -> None:
        at = self.cooling.earliest(time.time())
        if at is None or (self._cool_at is not None and self._cool_at <= at):
            return
        if self._cool_timer is not None:
            self._cool_timer.cancel()
        self._cool_at = at
        delay = max(0.0, at - time.time())
        self._cool_timer = asyncio.get_running_loop().call_later(delay, self._cooled)

    def _cooled(self) -> None:
        self._cool_timer = self._cool_at = None
        self._release_held()
        self._arm_cooling()

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
            if self.pause_hold(s) is not None:
                continue  # the person's pause outranks the gate (pause.py)
            policy = self.principals.policy_of(s.spec.owner or self.user)
            if policy is None:
                continue
            g = decide(policy, pool_for(s.spec.harness, s.spec.model), snapshot, now)
            holding = any(m.get("held_for") == "quota" for m in s.held)
            if g.state == "stopped" and s.status == "working" and not s.quota_paused:
                self._quota_task(s, self._pause(s, g))
            elif g.state == "open" and (s.quota_paused or holding):
                self._quota_task(s, self._continue(s))
        self._release_held()

    def _release_held(self) -> None:
        """Wake what a closed gate or a cooling cascade held: schedule ticks,
        workflow nodes and pending queue tasks."""
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

    def pause_hold(self, s) -> str | None:
        """Why ``s`` may not start a turn while every session is paused, or
        None: no pause, or the person resumed this one."""
        if self.paused is None or s.log_id in self.resumed:
            return None
        return pause.WHY

    async def pause_all(self, by: str) -> int:
        """Pause every session (pause.py): mark and cut each working turn,
        then end every process, which kills what its agent left running.
        The number of sessions whose process it ended."""
        self.paused = pause.write(self.roots.state_root, by)
        self.resumed.clear()
        ended = 0
        for s in list(self.sessions.sessions.values()):
            if not self._live(s):
                continue
            if s.status == "working" and not s.quota_paused:
                s.report({"kind": "server_paused"})
                s.quota_paused = pause.WHY
                self.sessions.metas.write(s.meta())
            if s.running:
                await s.stop()
                ended += 1
            s._publish_now()
        self.toast("Every session is paused: nothing runs until you resume it.")
        return ended

    async def resume_all(self) -> None:
        """Remove the pause, continue every turn it cut and release what the
        gate held."""
        self.paused = None
        self.resumed.clear()
        pause.clear(self.roots.state_root)
        for s in list(self.sessions.sessions.values()):
            if s.quota_paused == pause.WHY or s.held:
                self._quota_task(s, self._unpause(s))
        self._release_held()
        self.toast("Every session is resumed.")

    async def resume_one(self, s) -> None:
        """A person's message resumes this session alone, its cut turn first."""
        if self.pause_hold(s) is None:
            return
        self.resumed.add(s.log_id)
        await self._unpause(s)

    async def _unpause(self, s) -> None:
        """Continue a session the pause held: ``RESUMED`` ahead of what it
        held when the pause cut its turn, in one turn. One the quota gate
        still holds keeps waiting for it."""
        if not self._live(s) or self.pause_hold(s) is not None:
            return
        cut = s.quota_paused == pause.WHY
        if cut:
            s.quota_paused = None
        if self.sessions.quota_hold(s) is not None:
            self.sessions.metas.write(s.meta())
            return
        msgs = [{k: v for k, v in m.items() if k != "held_for"} for m in s.held]
        if cut:
            msgs.insert(
                0,
                {
                    "header": f"> from aegis:pause · {iso_now()}",
                    "body": pause.RESUMED,
                    "ts": time.time(),
                },
            )
        s.held = msgs
        self.sessions.metas.write(s.meta())
        if s.in_turn or not msgs:
            s._publish_now()
            return
        try:
            await s._flush_held()
        except Exception:
            log.exception("aegis: could not continue %s after the pause", s.handle)
            s.held = msgs + s.held
            self.sessions.metas.write(s.meta())
            s._publish_now()

    def _live(self, s) -> bool:
        return not s.archived and self.sessions.sessions.get(s.log_id) is s

    def sees(self, who: Principal, owner: str | None) -> bool:
        """Whether ``who`` may know of a thing ``owner`` owns: always outside
        multi-user mode and for an admin; in it, a member sees its own only
        (the spec's section 08). A thing from before owners is the admin's."""
        if self.multi_user is None or who.is_admin:
            return True
        return (owner or self.user) == who.name

    def view_of(self, caller: Caller) -> MemberView | None:
        """What ``caller``'s next channel subscription may see: None for
        everything, which is everyone outside multi-user mode and an admin."""
        who = self.who(caller)
        if self.multi_user is None or who.is_admin:
            return None
        return MemberView(self, who)

    def own_log_ids(self, who: Principal) -> set[str] | None:
        """The log ids of every session ``who`` may see, open or archived;
        None for all of them (``sees``)."""
        if self.multi_user is None or who.is_admin:
            return None
        metas = [s.meta() for s in self.sessions.sessions.values()]
        metas += list(self.sessions.archived.values())
        return {m["log_id"] for m in metas if self.sees(who, m.get("owner"))}

    def owner_of_handle(self, handle: str) -> str | None:
        """The owner of the session a handle or a log id names, open or
        archived; None when it names none here (or a session on another
        server)."""
        local, _, server = handle.partition("@")
        if server and server != self.server_name:
            return None
        s = self.sessions.by_handle(local) or self.sessions.sessions.get(local)
        if s is not None:
            return self.owner_of(s.log_id)
        for log_id, m in self.sessions.archived.items():
            if local in (m.get("handle"), log_id):
                return m.get("owner") or self.user
        return None

    def _hides(self, who: Principal, op: str, params) -> OpError | None:
        """The not-found error an op gives for a thing ``who`` may not see,
        named in its params; None when it names nothing hidden. A member
        asking about another's thing gets the answer for no such thing."""
        if self.multi_user is None or who.is_admin:
            return None
        log_id = getattr(params, "log_id", None)
        if isinstance(log_id, str):
            if log_id.startswith("t-"):
                if (o := self.terminals.owner_of(log_id)) and not self.sees(who, o):
                    return OpError("no_terminal", f"no open terminal {log_id}")
            elif (o := self.owner_of(log_id)) is not None and not self.sees(who, o):
                return OpError("no_session", f"no session {log_id!r}")
        if op.startswith("term.") and isinstance(
            tid := getattr(params, "id", None), str
        ):
            if (o := self.terminals.owner_of(tid)) and not self.sees(who, o):
                return OpError("no_terminal", f"no open terminal {tid}")
        field = HANDLE_FIELDS.get(op)
        named = getattr(params, field, None) if field else None
        for h in named if isinstance(named, list) else [named]:
            if isinstance(h, str) and (o := self.owner_of_handle(h)) is not None:
                if not self.sees(who, o):
                    return OpError("no_session", f"no open session {h!r}")
        fire = getattr(params, "fire", None)
        target = getattr(fire, "session", None) if op.startswith("schedule.") else None
        if isinstance(target, str) and (o := self.owner_of_handle(target)) is not None:
            if not self.sees(who, o):
                return OpError("no_session", f"no open session {target!r}")
        run = getattr(params, "run", None)
        if isinstance(run, str) and (st := self.workflows.runs.get(run)) is not None:
            if not self.sees(who, self.owner_of(st.owner)):
                return OpError("no_run", f"no workflow run {run!r}")
        task_id = getattr(params, "task_id", None)
        if isinstance(task_id, str) and (t := self.queues.tasks.get(task_id)):
            if not self.sees(who, t.owner):
                return OpError("no_task", f"no task {task_id!r}")
        if op.startswith("schedule.") and op != "schedule.create":
            name = getattr(params, "name", None)
            if isinstance(name, str) and (s := self.schedules.items.get(name)):
                if not self.sees(who, s.owner.get("principal")):
                    return OpError("no_schedule", f"no schedule {name!r}")
        return None

    def owner_of(self, log_id: str) -> str | None:
        """A session's owner by log id, None for no such session. One from
        before owners existed is the admin's."""
        if log_id.startswith("t-"):
            return self.terminals.owner_of(log_id)
        if log_id.startswith("b-"):
            return self.browsers.owner_of(log_id)
        s = self.sessions.sessions.get(log_id)
        meta = s.meta() if s is not None else self.sessions.archived.get(log_id)
        if meta is None:
            return None
        return meta.get("owner") or self.user

    async def _browser_deliver(self, log_id: str, header: str, body: str) -> bool:
        """Wake the session that drives a browser (browsers.py)."""
        s = self.sessions.sessions.get(log_id)
        if s is None or s.archived:
            return False
        await s.deliver(header, body, through_lock=True)
        return True

    def _bin(self, harness: str) -> str:
        return {
            "opencode": self.opencode_bin,
            "codex": self.codex_bin,
            "cursor": self.cursor_bin,
        }.get(harness, self.claude_bin)

    async def boot(self) -> None:
        # Before any session starts its own: the OpenCode servers a server or
        # test that is gone left behind (#620).
        opencode_sweep.sweep()
        self._config_task = asyncio.create_task(self.config.watch())
        # Before any session starts, so every harness child gets its cgroup.
        self.sessions.cgroups = SessionCgroups.setup(
            lambda: self.config.current().session_memory_max
        )
        if self.sessions.cgroups is not None:
            self.terminal_cgroups = self.sessions.cgroups.sibling(
                "terminals", lambda: self.config.current().terminal_memory_max
            )
            # One cgroup for every browser, capped only by the work's (#782).
            if browsers := self.sessions.cgroups.sibling("browsers", lambda: "max"):
                self.browsers.cgroup = browsers.create("chromium")
        # Before sessions.boot(), which records server_stopped for every session
        # whose turn the last run's end cut: the journal must see those records.
        self.journal.start()
        self.repos.start()
        self.sessions.boot(restarts.started(self.roots.state_root))
        await self.terminals.boot()
        await self.browsers.boot()
        # No browser's upload survives a restart (attachments.py).
        att.clear_staged(self.roots.state_root)
        # The Files view's deletes are kept 30 days (fs_trash.py).
        fs_trash.purge(self.roots.state_root)
        self.monitors.boot()
        # No session notice until every source has set its items (notices.py).
        self.notices.boot_begin()
        # The tasks load first, so a worker continued below that ends its turn
        # at once finds its task and completes it (#505).
        resume = self.queues.boot()
        # Before the monitors are armed, so a wake cannot start a turn ahead of
        # the continue; before the queues resume, so a worker the registry
        # continued needs no second message from its queue.
        await self.sessions.continue_after_boot()
        # A turn that ended before the stop and whose handoff had not started:
        # nothing else will end it. A cut turn continues above, and starts it
        # when it ends.
        for s in list(self.sessions.sessions.values()):
            if s.continued_by == "pending" and not s.in_turn:
                await self.start_pending_handoff(s)
        self.monitors.arm_all()
        await self.queues.resume_after_boot(resume)
        for s in list(self.sessions.sessions.values()):
            self.nudges.refresh(s)
        self.notices.boot_end()
        # After the sessions and their continues, so a live run is found live.
        self.schedules.boot()
        self.schedules.start()
        self.workflows.boot()
        self.workflows.start()
        self.quota.start()
        self.host.start()
        self.load.start()
        self.net.start()
        self.nudges.start()
        self.notices.start()
        self.parker.start()
        self.links.boot()
        # Last: its first snapshot must hold every session the boot loaded,
        # so none of them reads as a transition (push.py).
        self.push.sender.start()
        # In the background: a harness that hangs on --version must not hold
        # the server's start (harness_versions.py, spec §08).
        self._versions_task = asyncio.create_task(self._startup_versions())
        # The knowledge flags, at boot and then daily, off the loop.
        self._knowledge_task = asyncio.create_task(self._knowledge_checks())
        # In the background too, for the new tab's doctor row (doctor_watch.py).
        self.doctor_watch.start()

    async def _startup_versions(self) -> None:
        """Log one warning per harness the config's agents name whose version
        is outside the tested range. Never blocks, never fails the start."""
        snap = self.config.current()
        hv = snap.harness_versions
        if hv["startup"] == "off":
            return
        names = sorted({a.harness for a in snap.agents if a.harness in HARNESSES})

        # harness_for runs inside the coroutine, so its exception is gathered
        # too, not raised past return_exceptions (#455).
        async def one(n: str) -> harness_versions.Verdict:
            h = harness_for(
                n, self.claude_bin, self.opencode_bin, self.codex_bin, self.cursor_bin
            )
            return await harness_versions.check(h, hv["accepted"])

        verdicts = await asyncio.gather(
            *(one(n) for n in names), return_exceptions=True
        )
        for v in verdicts:
            if isinstance(v, BaseException):
                log.warning("reading a harness version failed: %s", v)
            elif v.path is not None and v.outside:
                log.warning(
                    "untested harness version: %s", v.message().replace("\n", "; ")
                )

    async def shutdown(self) -> None:
        stopping_at = restarts.stopping(self.roots.state_root)
        await self.push.sender.stop()
        await self.schedules.stop()
        await self.workflows.stop()
        if self._cool_timer is not None:
            # Its release dispatches the queues, which could start a worker.
            self._cool_timer.cancel()
        self.queues.shutdown()
        if self._versions_task is not None:
            self._versions_task.cancel()
        if self._knowledge_task is not None:
            self._knowledge_task.cancel()
        for task in list(self._knowledge_soon):
            task.cancel()
        await self.doctor_watch.stop()
        if self._config_task is not None:
            self._config_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._config_task
        await self.exits.shutdown()
        if launch.exits_in_force() is self.exits:
            launch.configure_exits(None, None)
        await self.links.shutdown()
        await self.parker.stop()
        await self.nudges.stop()
        await self.notices.stop()
        await self.quota.stop()
        await self.host.stop()
        await self.load.stop()
        await self.net.stop()
        await self.monitors.shutdown()
        await self.recaps.shutdown()
        await self.terminals.shutdown()
        await self.browsers.shutdown()
        await self.side_notes.shutdown()
        await self.sessions.shutdown()
        try:
            await self.skill_servers.shutdown()
        except Exception:
            log.exception("aegis: the skill servers did not stop")
        await self.repos.stop()
        await asyncio.to_thread(self.journal.stop)
        await asyncio.to_thread(self.knowledge.usage.stop)
        self.runners.shutdown()
        restarts.stopped(self.roots.state_root, stopping_at)

    def _meta_of_session(self, log_id: str) -> dict:
        """A session's meta, live or archived; {} when it has none (an old
        store, a crash before the meta was written)."""
        try:
            meta = json.loads(self.sessions.metas.path(log_id).read_text())
        except (OSError, ValueError):
            return {}
        return meta if isinstance(meta, dict) else {}

    def _linux_user_of_session(self, log_id: str) -> str | None:
        """The Linux user a session runs as (users.py): the live session's,
        else its meta's, since the journal backfills closed sessions too."""
        if self.multi_user is None:
            return None
        s = self.sessions.sessions.get(log_id)
        if s is not None:
            return s.spec.linux_user
        try:
            meta = json.loads(self.sessions.metas.path(log_id).read_text())
        except (OSError, ValueError):
            return None
        return meta.get("linux_user") if isinstance(meta, dict) else None

    def _deliver(self, s) -> Callable[[Path, str], Path] | None:
        """How an upload reaches ``s``'s inbox in its user's home, as that
        user: the server opens the staged file, the user's runner writes the
        copy (attachments.commit). None for an inbox under the state root."""
        user = self.sessions.user_for(s.spec)
        if user is None:
            return None
        run, box = self.runners.get(user), s.inbox

        def deliver(staged: Path, name: str) -> Path:
            fd = os.open(staged, os.O_RDONLY | os.O_CLOEXEC)
            try:
                return run.call(att.write_into, box, name, fd)
            finally:
                os.close(fd)

        return deliver

    def place_of(self, caller: Caller) -> Place:
        """Where the files a person reaches are, and the runner that opens
        them as their Linux user (as_user.py). Raises PermissionError for a
        member with no user in multi-user mode."""
        who = self.who(caller)
        return self._place(self.principals.acts_for(who.name), who.role == "member")

    def place_of_session(self, log_id: str) -> Place:
        """The place of a session's owner, as the user its process runs as."""
        spec = self.sessions.open(log_id).spec
        member = self.principals.role_of(spec.owner or self.user) != "admin"
        return self._place(self.sessions.user_for(spec), member)

    def knowledge_reader(self, principal: str) -> tuple[as_user.Runner, list[str]]:
        """What a staleness check of ``principal``'s memories reads with: the
        runner that opens files as their Linux user (the server's own for
        the owner, or outside multi-user mode), and the roots a quoted path
        is looked for under, their Files root then their home. Raises
        PermissionError for a member with no user."""
        user = self.principals.acts_for(principal)
        place = self._place(user, self.principals.role_of(principal) != "admin")
        home = pwd.getpwnam(user).pw_dir if user else str(Path.home())
        return place.run, list(dict.fromkeys([str(place.root), home]))

    async def knowledge_check(
        self, principal: str, items: list[Item] | None = None
    ) -> None:
        """Flag ``principal``'s memories (knowledge/checks.py), all of them
        when ``items`` is None. A principal nothing may read for is skipped
        and logged."""
        try:
            run, roots = self.knowledge_reader(principal)
        except PermissionError as e:
            log.warning("aegis: knowledge of %s not checked: %s", principal, e)
            return
        whole = items is None
        some = await self.knowledge.items(principal) if items is None else items
        await self.knowledge.check(principal, some, run, roots, everything=whole)

    async def bounded_check(self, principal: str, items: list[Item] | None) -> None:
        """knowledge_check within ``KNOWLEDGE_CHECK_TIMEOUT_S``; a timeout or
        an error is logged, never raised. A file read already in a thread
        runs on to its end, but nothing waits for it."""
        try:
            await asyncio.wait_for(
                self.knowledge_check(principal, items), KNOWLEDGE_CHECK_TIMEOUT_S
            )
        except TimeoutError:
            log.warning(
                "aegis: the knowledge check of %s took over %ss; skipped",
                principal,
                KNOWLEDGE_CHECK_TIMEOUT_S,
            )
        except Exception:
            log.exception("aegis: the knowledge check of %s failed", principal)

    def knowledge_check_soon(self, principal: str, items: list[Item]) -> None:
        """Check ``items`` in the background, as a save does: the save has
        already committed, so nothing the check does may fail or hold it."""
        task = asyncio.create_task(self.bounded_check(principal, items))
        self._knowledge_soon.add(task)
        task.add_done_callback(self._knowledge_soon.discard)

    async def _knowledge_checks(self) -> None:
        """The daily pass: every principal's memories flagged once at boot,
        then every ``KNOWLEDGE_CHECK_S``. Never fails the server, and one
        principal's failure never skips the others."""
        while True:
            try:
                names = await asyncio.to_thread(self.knowledge.store.principals)
            except Exception:
                log.exception("aegis: the knowledge check could not list stores")
                names = []
            for name in names:
                await self.bounded_check(name, None)
            await asyncio.sleep(KNOWLEDGE_CHECK_S)

    def _place(self, user: str | None, member: bool) -> Place:
        root, state = self.roots.config_root, self.roots.state_root
        run = self.runners.get(user)
        if user is None:
            return Place(root, state, state, run)
        own = Path(pwd.getpwnam(user).pw_dir) / ".aegis"
        # A member's view is its home; an admin with a user keeps the root.
        # Neither may write the server's state, so both trash in their home.
        if member:
            return Place(own.parent, own / "trash", own, run)
        return Place(root, state, own, run)

    def _on_config(self, snap: Snapshot) -> None:
        """Every change to .aegis.yaml, however it was made: the page and the
        composer follow it, and the queues start what it now allows."""
        self.channels.publish("config", [{"set": snap.wire()}])
        if hasattr(self, "doctor_watch"):
            self.doctor_watch.kick()
        if snap.multi_user != self.multi_user and snap.multi_user not in self._mu_told:
            self._mu_told.append(snap.multi_user)
            log.warning(
                "multi_user changed in .aegis.yaml; restart the server to apply it"
            )
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

    def toast(
        self, text: str, kind: str = "info", source=None, owner: str | None = None
    ) -> int:
        """Show a toast in every open browser (client/js/toast.js) and return how
        many there are. ``source`` is the session an agent's toast comes from; a
        click on it opens that session's tab. It skips the throttle: a toast
        must neither wait nor merge with another."""
        t: dict = {"text": text, "kind": kind, "ts": time.time()}
        if source is not None:
            t["from"] = {"handle": source.handle, "id": source.log_id}
        if owner is not None:
            # About a principal's own thing (a schedule): its browsers only,
            # in multi-user mode (member_view.py).
            t["owner"] = owner
        self.channels.publish("toasts", [{"toast": t}])
        self.push.sender.on_toast(t)  # an agent's goes to subscribed devices
        return self.channels.reached("toasts", {"toast": t})

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
                + self.browsers.cards()
            )
        if name.startswith("term:"):
            return self.terminals.channel(name.removeprefix("term:"), since)
        if name.startswith("browser:"):
            return self.browsers.channel(name.removeprefix("browser:"))
        if name.startswith("transcript:"):
            return self.sessions.transcript(name.removeprefix("transcript:"), since)
        if name == "quota":
            return self.quota.snapshot
        if name == "host":
            return self.host.snapshot
        if name == "journal":
            return self.journal.snapshot
        if name == "usage":
            return self.journal.usage_snapshot
        if name.startswith("repos:"):
            log_id = name.removeprefix("repos:")
            return lambda: self.repos.snapshot(log_id)
        if name == "config":
            return lambda: self.config.current().wire()
        if name == "links":
            return self.links.wire
        if name == "schedules":
            return self.schedules.wire_all
        if name == "workflows":
            return self.workflows.wire_all
        if name == "workflow-runs":
            return self.workflows.summaries
        if name.startswith("workflow:"):
            run = name.removeprefix("workflow:")
            if run not in self.workflows.runs:
                return None
            return lambda: self.workflows.wire(run)
        if name == "toasts":
            # Always empty: a browser that subscribes later never replays a toast.
            return lambda: []
        return None

    def _agents(self):
        snap = self.config.current()
        return list(snap.agents), snap.default_agent

    def _group_for(
        self, parent, from_session: str | None, caller: Caller
    ) -> str | None:
        """The group a new session joins (#586): its agent parent's, else the
        group of the session a person spawned it from, when that session is
        theirs to change."""
        if parent is not None:
            return parent.group_key()
        src = self.sessions.sessions.get(from_session or "")
        if src is None or caller.is_agent:
            return None
        who = self.who(caller)
        if not who.is_admin and src.spec.owner != who.name:
            return None
        return src.group_key()

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

    async def _offered_skills(self, principal: str) -> list[tuple[str, str]]:
        """The (name, description) of the skills ``principal`` is offered as
        commands. A store that cannot be read offers none: the menu outlives it."""
        try:
            items = await self.knowledge.items(principal)
        except Exception:
            log.exception("aegis: the skills of %s could not be listed", principal)
            return []
        return [(it.name, it.description) for it in items if offered(it)]

    def knowledge_native(self, principal: str) -> bool:
        """Whether ``principal``'s knowledge setting is native: their
        sessions start without the harness's own skills and memory, and a
        store skill takes the name of a harness command. Set from Settings
        (``knowledge.setting``) or by /setup (``setup_record``)."""
        return self.setup_records.native(principal)

    async def _skill_command(self, s, name: str):
        """The store skill ``/name`` runs in ``s``, or None. ``skill:<name>``
        reaches one named like an aegis command, or like a harness command
        the skill does not shadow because the session was not spawned native
        (SpawnSpec.knowledge_native)."""
        bare = name.removeprefix("skill:")
        if not NAME_RE.match(bare):
            return None
        owner = s.spec.owner or self.user
        it = self.knowledge.store.get(owner, bare)
        if it is None or not offered(it):
            return None
        if bare == name and not s.spec.knowledge_native:
            cat = await self.catalogs.get(s)
            if cat is not None and cat.has(name):
                return None
        return it

    async def _run_skill(
        self, s, it, arg: str, caller: Caller, untested: str | None
    ) -> dict:
        """Deliver a skill's text as one turn, mark it loaded and write an
        ``invoked`` row. aegis delivers it, so every harness runs it alike.
        Gated as a typed message is: refused behind a closed quota gate, and
        a resume asks the version gate; mid-turn it is held, and says so."""
        if (why := self.sessions.quota_hold(s)) is not None:
            raise OpError("quota_closed", _held(s.spec.owner or self.user, why))
        not_saved = None
        if not s.running and s.asleep_why != "idle":
            not_saved = await self._version_gate(caller, s.spec.harness, untested, s)
            s.launch_by = "agent" if caller.is_agent else "person"
        try:
            did = await s.deliver(
                *skill_turn(it.name, it.body, arg, iso_now()), by_person=True
            )
        finally:
            s.launch_by = "agent"
        s.load_skill(it.name)
        self.knowledge.usage.add(
            s.spec.owner or self.user, s.log_id, "skill", it.name, "invoked"
        )
        out = {"status": "held" if did == "held" else "delivered"}
        return out | {"not_saved": not_saved} if not_saved else out

    async def _command(
        self,
        s,
        name: str,
        arg: str,
        caller: Caller = Caller("user"),
        untested: str | None = None,
    ):
        """Run an aegis command typed in the composer."""
        name = commands.ALIASES.get(name, name)
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
            if untested:
                params["untested"] = untested
            r = await self.registry.call("help.guide", params, caller)
            return {**r, "server": self.server_name}
        if name == "whats-new":
            # A tour of the release notes in a new tab, on default_agent in
            # this session's directory (help_ops.help_guide); the client opens
            # its tab.
            params = {"kind": "whats_new", "cwd": str(s.spec.cwd)}
            if untested:
                params["untested"] = untested
            r = await self.registry.call("help.guide", params, caller)
            return {**r, "server": self.server_name}
        if name == "doctor":
            # The doctor's report in a new tab, on default_agent in the config
            # root (help_ops.help_guide); the client opens its tab.
            params = {"kind": "doctor"}
            if untested:
                params["untested"] = untested
            r = await self.registry.call("help.guide", params, caller)
            return {**r, "server": self.server_name}
        if name == "feedback":
            # The agent drafts the issue from the help page feedback; the
            # turn grants nothing, so it needs no launch token.
            v = s.last_verdict
            facts = feedback.facts(s.harness.label, v.version if v else None)
            await s.deliver(
                *feedback.turn(arg, facts, feedback.issues_url(), iso_now())
            )
            return {"status": "delivered"}
        if name == "multiuser":
            # The agent runs the host work from the help page multi-user with
            # the person; only an admin may start it, since it changes the server.
            if not self.who(caller).is_admin:
                raise OpError(
                    "not_for_members", "/multiuser is for an admin of this server"
                )
            on = self.multi_user is not None
            await s.deliver(*multiuser.turn(arg, on, multiuser.facts(on), iso_now()))
            return {"status": "delivered"}
        if name == "setup":
            # The agent moves the person's habits into aegis from the help
            # page setup; anyone may run it, for what they own.
            cfg = self.config.current()
            try:
                ver = pkg_version("aegis-harness")
            except PackageNotFoundError:
                ver = "unknown"
            found = await self.registry.call("config.detect", {})
            line = setup.facts(
                ver, found, [a.name for a in cfg.agents], list(cfg.queues)
            )
            rec = self.setup_records.get(self.who(caller).name)
            await s.deliver(*setup.turn(arg, line, rec, iso_now()), by_person=True)
            return {"status": "delivered"}
        if name == "remember":
            # The agent saves the text as a memory the person asked for
            # (knowledge_ops.py), in their own store. An agent with read
            # permission cannot call memory_save, so the turn would only fail.
            if s.spec.permission == "read":
                raise OpError(
                    "read_only",
                    "this session has read permission, so its agent cannot save"
                    " a memory; use /remember in a session that can write",
                )
            await s.deliver(*remember_turn(arg, iso_now()), by_person=True)
            return {"status": "delivered"}
        if name == "consolidate":
            # The agent reviews the person's store from the help page
            # consolidate. The person began the turn, as with /setup, so what
            # the turn applies after their submit saves approved, with Undo.
            if s.spec.permission == "read":
                raise OpError(
                    "read_only",
                    "this session has read permission, so its agent cannot apply"
                    " what you tick; use /consolidate in a session that can write",
                )
            await s.deliver(*consolidate_turn(arg, iso_now()), by_person=True)
            return {"status": "delivered"}
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
            body = self.workflows.launch(s, self.saved.get(wid), typed.strip(), origin)
            await s.deliver(f"> from workflow:{wid} · launch · {iso_now()}", body)
            return {"launched": wid}
        if name == "sleep":
            await s.stop()
            return s.wire()
        if name == "quiet":
            return reg.set_quiet(s.log_id, True)
        if name == "autoclose":
            if arg not in ("", "on", "off"):
                raise OpError("bad_params", "usage: /autoclose [on|off]")
            return reg.set_autoclose(
                s.log_id, not s.autoclose if not arg else arg == "on"
            )
        if name == "restart":
            return await self._launch(s, caller, untested, s.restart)
        if name == "spawn":
            return await self._spawn_line(
                commands.parse_spawn(arg), caller, s, untested
            )
        if name == "handoff":
            line = commands.parse_handoff(arg)
            params = {
                k: v
                for k, v in dataclasses.asdict(line).items()
                if v is not None and k not in ("server", "text")
            }
            r = await self.registry.call(
                "session.handoff",
                {"log_id": s.log_id, "msg": line.text, "host": line.server, **params},
                caller,
            )
            return {"server": self.server_name, **r}
        if name == "announce":
            text, level, until = commands.parse_announce(arg)
            params = {"text": text, "level": level, "until": until}
            return await self.registry.call("notice.announce", params, caller)
        if name == "archive":
            await reg.close(s.log_id)
            return None
        raise OpError("unknown_command", f"/{name} has no handler")

    async def _launch(self, s, caller: Caller, untested: str | None, start):
        """Start a session's harness through ``start`` (Session.restart, or
        ensure_running for a Wake). Refused like a send that would start one:
        on a workflow's locked step, behind a closed quota gate, and on a
        harness version outside the tested range until the person says so."""
        s.check_lock()
        if not caller.is_agent:
            await self.resume_one(s)
        if (why := self.sessions.quota_hold(s)) is not None:
            raise OpError("quota_closed", _held(s.spec.owner or self.user, why))
        not_saved = await self._version_gate(caller, s.spec.harness, untested, s)
        s.launch_by = "agent" if caller.is_agent else "person"
        try:
            await start()
        except FileNotFoundError as e:
            raise OpError(
                "harness_not_found",
                f"cannot run {self._bin(s.spec.harness)!r}: {e}",
            ) from e
        finally:
            s.launch_by = "agent"
        return {"not_saved": not_saved} if not_saved else s.wire()

    async def _version_gate(
        self, caller: Caller, harness: str, untested: str | None, s=None
    ) -> str | None:
        """A person's launch on a harness version outside the tested range:
        refuse it once with harness_untested and what the browser shows, then
        start on "once" or "always". Returns why "always" was not saved, or
        None. An agent and a link are never asked (spec §06): their launches
        write the notice instead (Session._check_version)."""
        if caller.is_agent or caller.link is not None:
            return None
        cfg = self.config.current().harness_versions
        h = (
            s.harness
            if s is not None
            else harness_for(
                harness,
                self.claude_bin,
                self.opencode_bin,
                self.codex_bin,
                self.cursor_bin,
            )
        )
        v = await harness_versions.check(h, cfg["accepted"])
        if v.path is None or not v.outside or cfg["person"] != "ask":
            return None  # a missing binary fails the launch as harness_not_found
        if s is not None and s.checked_version == (v.version or "?"):
            return None  # started once on this version already
        may = self.who(caller).is_admin and v.version is not None
        if untested is None:
            raise OpError(
                "harness_untested", v.message(), data=v.wire() | {"may_always": may}
            )
        if untested != "always":
            return None
        if v.version is None:
            return "a version that could not be read cannot be allowed for good"
        if not self.who(caller).is_admin:
            return "only an admin can allow a version for good; it started this once"
        try:
            accept_version(self.config.path, v.harness, v.version)
        except OpError as e:
            return f"{v.version} was not saved: {e.message}"
        return None

    async def _spawn_line(
        self,
        line: commands.SpawnLine,
        caller: Caller,
        s,
        untested: str | None = None,
    ) -> dict:
        """``/spawn``: a person starting a session, here or, with ``@server``, on
        a linked server through the link. Nobody's child: no ``spawned_by``, but
        its prompt carries the tail of tab ``s``, where it was typed, and here it
        joins that tab's group (#586)."""
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
                ("untested", untested),
            )
            if v is not None
        }
        if far is None:
            params["from_session"] = s.log_id
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

    def _handoff_spec(self, old, p: HandoffParams, capped: bool) -> SpawnSpec:
        """What a handoff from ``old`` starts: its own spec with what ``p``
        names changed, so a model chosen by hand stays, or with ``p.agent`` the
        spec a spawn of that agent gets. ``capped``: an agent asked, and cannot
        hand a session more power than it has itself."""
        spec = old.spec
        overrides = {"model": p.model, "effort": p.effort, "permission": p.permission}
        if p.agent:
            agents, default = self._agents()
            spec = resolve(
                agents,
                default,
                p.agent,
                {"harness": None, **overrides},
                spec.cwd,
                spawned_by=spec.spawned_by,
                owner=spec.owner,
            )
        else:
            spec = dataclasses.replace(
                spec, **{k: v for k, v in overrides.items() if v is not None}
            )
        if p.cwd:
            spec = dataclasses.replace(spec, cwd=self._resolve_cwd(p.cwd, old.spec.cwd))
        if capped and PERMISSION_ORDER.index(spec.permission) > PERMISSION_ORDER.index(
            old.spec.permission
        ):
            raise OpError(
                "not_allowed",
                f"your session runs with {old.spec.permission}, so a session you "
                f"hand off to can have at most that (this one would have "
                f"{spec.permission}); pass permission",
            )
        return spec

    def _check_far_handoff(self, old, far: str, caller: Caller) -> None:
        """What refuses a handoff across a link before anything changes.
        ``_spawn_far`` checks the rest when it starts."""
        if caller.link is not None:
            raise OpError("not_relayed", "a link is not relayed to another server")
        if not self.who(caller).is_admin:
            raise OpError(
                "not_for_members",
                "handing off to a linked server is for an admin of this one",
            )
        link = self.links.get(far)
        if link is None:
            raise OpError("unknown_server", f"this server links no server named {far}")
        if link.state != "linked":
            raise OpError("server_offline", f"{link.describe()}; nothing was started")
        # They report to their parent, and nothing reaches one across a link.
        if kids := self.sessions.descendants(old.log_id):
            names = ", ".join(k.handle for k in kids)
            raise OpError(
                "has_children",
                f"{old.handle} still has {names} open; close them or let them "
                "finish first",
            )

    def _far_of(self, p: HandoffParams) -> str | None:
        """The linked server a handoff goes to, or None for this one."""
        return None if p.host in (None, self.server_name) else p.host

    async def _start_far_handoff(self, old, p: HandoffParams, far, caller) -> dict:
        """Start the session that continues ``old`` on a linked server, under
        the policies a far spawn has. Nothing here hears when its first turn
        ends, so the old one is archived once the far side has taken the first
        message; a refusal leaves it open."""
        report = old.standing.get("report") or {}
        prompt = handoff_opening(
            old.handle,
            report.get("handoff"),
            spawn_tail(old.entries()),
            instruction=p.msg,
            far=True,
        )
        name = p.agent or old.spec.agent
        params = SpawnParams(
            agent=name,
            prompt=prompt,
            model=p.model,
            effort=p.effort,
            permission=p.permission,
            cwd=p.cwd,
        )
        old.mark_continued("starting")
        try:
            r = await self._spawn_far(params, name, far, old, caller)
        except BaseException:
            old.mark_continued(None)
            raise
        old.mark_continued(r["handle"])
        await self.sessions.close(old.log_id)
        return r

    async def _start_handoff(
        self, old, p: HandoffParams, capped: bool, caller: Caller
    ) -> dict:
        """Start the session that continues ``old``, and send its first message.
        The old one is archived when it finishes its first turn."""
        if (far := self._far_of(p)) is not None:
            return await self._start_far_handoff(old, p, far, caller)
        reg = self.sessions
        report = old.standing.get("report") or {}
        # The new session takes the old one's place in its group (#641):
        # it joins it, heads it when the old one did, and adopts the old
        # one's children once the old one is archived (_retire_predecessor).
        leads = old.spec.group is None or old.spec.leads
        kids = tuple(
            k.handle for k in reg.sessions.values() if k.spec.spawned_by == old.log_id
        )
        prompt = handoff_opening(
            old.handle,
            report.get("handoff"),
            spawn_tail(old.entries()),
            kids=kids,
            leads=leads and bool(kids),
            instruction=p.msg,
        )
        spec = dataclasses.replace(
            self._handoff_spec(old, p, capped),
            continues=old.log_id,
            spawned_from=None,
            group=old.group_key(),
            leads=leads,
        )
        spec, g = self.start_spec(spec)
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

    async def start_pending_handoff(self, old) -> None:
        """The agent's turn ended: start the handoff it asked for. Nobody waits
        on an answer here, so a refusal gives the session back and says why."""
        params = HandoffParams(**(old.handoff_pending or {}))
        try:
            caller = Caller("agent", old.log_id)
            await self._start_handoff(old, params, capped=True, caller=caller)
        except OpError as e:
            self.sessions.call_off_pending(old, f"did not start: {e.message}")

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
            raise far_error(e, far, trusted=self.links.trusted(far)) from None
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
            "spawned_from": {"handle": parent.handle, "server": self.server_name}
            | (
                {"group": far_group(parent.group_key(), self.server_name, far)}
                if (link.remote or {}).get("groups")
                else {}
            ),
        }
        try:
            r = await link.call(
                "session.spawn", {k: v for k, v in params.items() if v is not None}
            )
        except OpError as e:
            raise far_error(e, far, trusted=self.links.trusted(far)) from None
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
        for name, fetch in self._archive_sources(only, self.who(caller)):
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

    def _archive_sources(self, only: str | None, who: Principal):
        """Each server an archive listing reads, with how to fetch a page. A
        member in multi-user mode reads its own sessions here, and no linked
        server's: links are the admin's."""
        member = self.multi_user is not None and not who.is_admin

        def keep(m: dict) -> bool:
            return self.sees(who, m.get("owner"))

        async def local(query, limit, after, workers):
            k = keep if member else None
            items, total, last = self.sessions.archive(query, limit, after, workers, k)
            return items, total, self.sessions.archived_workers(query, k), last

        if only in (None, self.server_name):
            yield self.server_name, local
        if member:
            return
        # Links this server opened: a way back is reached by calls, and its
        # archive rows could not be read from here (links.Backlink).
        for link in self.links.links():
            if link.state != "linked" or only not in (None, link.name):
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
                group=self._group_for(parent, p.from_session, caller),
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
                    group=p.spawned_from.group,
                )
            elif caller.link is not None:
                # A person's /spawn on the far side: its questions would reach
                # someone on another server, so it is never a notice's asker.
                spec = dataclasses.replace(spec, spawned_from=f"person@{caller.link}")
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
            spec, g = self.start_spec(spec)
            if not g.may_start:
                raise OpError("quota_closed", refusal(spec.owner or self.user, g))
            person = parent is None and caller.link is None and not caller.is_agent
            not_saved = (
                await self._version_gate(caller, spec.harness, p.untested)
                if person
                else None
            )
            try:
                # A run's child carries its schedule, so spawn: false holds for
                # grandchildren too (ops.NO_SPAWN).
                s = await reg.spawn(
                    spec,
                    quiet=p.quiet,
                    schedule=parent.schedule if parent else None,
                    launch_by="person" if person else "agent",
                )
            except FileNotFoundError as e:
                raise OpError(
                    "harness_not_found", f"cannot run {self._bin(spec.harness)!r}: {e}"
                ) from e
            except (OSError, TimeoutError) as e:
                raise OpError(
                    "harness_failed", f"{self._bin(spec.harness)!r} did not start: {e}"
                ) from e
            ran = None
            if p.prompt and commands.split(p.prompt) is not None:
                # A / line runs as if typed in the new session (#545), as the
                # new tab does (#570): as text, the harness answered an aegis
                # command with its own builtin of that name.
                try:
                    ran = await deliver(
                        SendParams(log_id=s.log_id, text=p.prompt), caller
                    )
                except OpError as e:
                    raise OpError(
                        e.code,
                        f"{s.handle} ({s.log_id}) started, but its first line "
                        f"failed: {e.message}",
                        e.data,
                    ) from e
            elif p.prompt:
                try:
                    await s.send(p.prompt, by_person=person)
                except (BrokenPipeError, ConnectionResetError, FileNotFoundError) as e:
                    raise OpError(
                        "send_failed",
                        f"{s.handle} ({s.log_id}) started, but its first message "
                        f"failed: {e}",
                    ) from e
            out = {"log_id": s.log_id, "handle": s.handle}
            if ran is not None:
                out["command"] = ran
            if parent is not None and s.last_notice:
                out["untested"] = s.last_notice
            if not_saved:
                out["not_saved"] = not_saved
            return out

        async def deliver(p: SendParams, caller):
            s = reg.open(p.log_id)
            s.check_lock()  # before a / command or an upload acts on it
            s.check_handed_off()
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
                    return await self._command(s, name, arg, caller, p.untested)
                if (sk := await self._skill_command(s, name)) is not None:
                    return await self._run_skill(s, sk, arg, caller, p.untested)
                if name.startswith("skill:"):
                    raise OpError(
                        "unknown_command", f"no skill {name[6:]!r} to run as /{name}"
                    )
                cat = await self.catalogs.get(s)
                if cat is not None and cat.commands and not cat.has(name):
                    raise OpError(
                        "unknown_command",
                        f"no command /{name} in this session; "
                        "start the line with // to send it as text",
                    )
            text = commands.escape(p.text)
            if p.dictated and cmd is None:
                text = dictation.with_note(text)
            if not caller.is_agent:
                await self.resume_one(s)
            if (why := reg.quota_hold(s)) is not None:
                raise OpError("quota_closed", _held(s.spec.owner or self.user, why))
            not_saved = None
            if not s.running and s.asleep_why != "idle":
                # A person resuming a session that slept; a parked one wakes
                # unattended (spec §06).
                not_saved = await self._version_gate(
                    caller, s.spec.harness, p.untested, s
                )
                s.launch_by = "agent" if caller.is_agent else "person"
            try:
                # The harness first: a send that cannot reach the agent leaves
                # its uploads staged, to send again.
                try:
                    await s.ensure_running()
                finally:
                    s.launch_by = "agent"
                sent: list[dict] = []
                if p.attachments:
                    try:
                        sent = await asyncio.to_thread(
                            att.commit,
                            self.roots.state_root,
                            p.log_id,
                            p.attachments,
                            deliver=self._deliver(s),
                        )
                    except files.FileError as e:
                        raise OpError(e.code, e.message) from e
                await s.send(
                    att.message(text, sent),
                    typed=text,
                    attached=sent,
                    by_person=not caller.is_agent,
                )
                if not_saved:
                    return {"not_saved": not_saved}
            except FileNotFoundError as e:
                raise OpError(
                    "harness_not_found",
                    f"cannot run {self._bin(s.spec.harness)!r}: {e}",
                ) from e
            except (BrokenPipeError, ConnectionResetError) as e:
                raise _dead(e) from e

        @r.op("session.handoff", HandoffParams, agent=True, owned="log_id")
        async def handoff(p: HandoffParams, caller):
            """Continue your work in a new session and end this one: from this
            call nobody can write to it. The new session starts when your turn
            ends, with `msg`, your turn_end brief and your last turns, so end
            your turn right after this call. Pass `agent` to continue on another
            agent, with at most your permission, and `host` to continue on a
            linked server. Refused while a monitor, a background task, a queue
            task or a held message waits for you. A workflow you own follows the
            new session, except across a link, where it and any open session you
            started refuse the call."""
            if caller.is_agent:
                if p.log_id not in (None, caller.log_id):
                    raise OpError(
                        "not_yours", "an agent can hand off only its own session"
                    )
                old = reg.open(caller.log_id)
            else:
                if p.log_id is None:
                    raise OpError("bad_params", "session.handoff needs a log_id")
                old = reg.open(p.log_id)
            old.check_lock()
            if old.worker:
                raise OpError(
                    "worker", f"{old.handle} is a queue worker; its task owns it"
                )
            if reg.successor(old) is not None:
                raise OpError("handed_off", f"{old.handle} is already being continued")
            far = self._far_of(p)
            # What would wake a session nobody can write to, or lose its result.
            # A run follows its owner's successor only on this server.
            if wakers := reg.wakers(old, run=far is not None):
                raise OpError(
                    "has_wakers",
                    f"{old.handle} still waits on {', '.join(wakers)}; "
                    "cancel them or let them finish first",
                )
            # Refused now, not when the turn ends and nobody is there to hear it.
            if far is None:
                self._handoff_spec(old, p, capped=caller.is_agent)
            else:
                self._check_far_handoff(old, far, caller)
            if caller.is_agent and old.in_turn:
                if old.held:
                    raise OpError(
                        "held_messages",
                        f"{len(old.held)} messages wait for your next turn; "
                        "read them first",
                    )
                old.mark_handoff_pending(p.model_dump(exclude_none=True))
                return (
                    "Handed off: this session takes no more messages. End your turn "
                    "now with a short final message; the new session starts then."
                )
            if old.in_turn:
                raise OpError(
                    "busy", f"{old.handle} is working; hand it off when its turn ends"
                )
            return await self._start_handoff(
                old, p, capped=caller.is_agent, caller=caller
            )

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

        @r.op("speech.prepare", NoParams)
        async def speech_prepare(p: NoParams, caller):
            """Phonon's package and model, on disk and verified, for a browser
            that opted in to it (speech.py)."""
            try:
                await self.speech.ensure()
            except dictation.Unavailable as e:
                raise OpError("speech_unavailable", str(e)) from e
            return {"base": f"/speech/{self.speech.id}/"}

        @r.op("session.configure", ConfigureParams, owned="log_id")
        async def configure(p: ConfigureParams, caller):
            s = reg.open(p.log_id)
            s.check_lock()
            return await self._configure(s, p.model, p.effort, p.permission)

        @r.op("commands.list", LogParams)
        async def commands_list(p: LogParams, caller):
            """What the composer's menu offers this session."""
            s = reg.open(p.log_id)
            owner = s.spec.owner or self.user
            skills = await self._offered_skills(owner)
            cat = await self.catalogs.get(s)
            return listing(cat, caller, skills, s.spec.knowledge_native)

        @r.op("commands.preview", PreviewParams)
        async def commands_preview(p: PreviewParams, caller):
            """What the new tab's menu offers before its session exists (#632):
            aegis's commands, and the harness's when a session in that
            directory has already listed them."""
            owner = self.who(caller).name
            skills = await self._offered_skills(owner)
            # The session about to start takes the setting as it is now.
            native = self.knowledge_native(owner)
            try:
                cwd = self._resolve_cwd(p.cwd)
            except OpError:
                return listing(None, caller, skills, native)
            cat = self.catalogs.cached(p.harness, cwd)
            return listing(cat, caller, skills, native)

        def listing(cat, caller, skills: list[tuple[str, str]], native: bool) -> dict:
            try:
                saved = self.saved.listing(self.workflows._agents())
            except Exception:
                # The menu outlives a broken workflows folder; it lists none.
                log.exception("aegis: the saved workflows could not be listed")
                saved = []
            # /doctor only for an admin while the doctor finds a problem.
            doctor = bool(self.doctor_watch.problems()) and self.who(caller).is_admin
            # A harness command keeps its name beside a skill of that name
            # unless the session is native (SpawnSpec.knowledge_native).
            taken = (
                {c["name"] for c in cat.commands}.difference(commands.AEGIS)
                if cat and not native
                else set()
            )
            return {
                "commands": [
                    c for c in commands.aegis_wire() if doctor or c["name"] != "doctor"
                ]
                + commands.skills_wire(skills, taken)
                + (
                    cat.wire_commands(
                        shadowed={*commands.AEGIS, *(n for n, _ in skills)} - taken
                    )
                    if cat
                    else []
                ),
                "models": cat.wire_models() if cat else [],
                "permissions": list(PERMISSION_MODE),
                # What /workflow completes: the saved workflows that lint.
                "workflows": [
                    {"id": w["id"], "title": w["title"], "source": w["source"]}
                    for w in saved
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

        @r.op("session.restart", RestartParams, owned="log_id")
        async def restart(p: RestartParams, caller):
            s = reg.open(p.log_id)
            return await self._launch(s, caller, p.untested, s.restart)

        @r.op("session.warm", WarmParams, owned="log_id")
        async def warm(p: WarmParams, caller):
            # A person's Wake (wake: true) is refused like a send that would
            # start the session (#479). Without it, someone started typing to
            # a parked session: start its harness while they write
            # (park.py); a closed quota gate starts nothing and says nothing,
            # and one that slept waits for the message itself.
            s = reg.open(p.log_id)
            if s.status != "asleep":
                return None
            if p.wake:
                return await self._launch(s, caller, p.untested, s.ensure_running)
            if s.asleep_why == "idle" and reg.quota_hold(s) is None:
                await s.ensure_running()
            return None

        @r.op("session.kills", LogParams, owned="log_id")
        async def sleep_kills(p: LogParams, caller):
            """What Sleep would end with the process, one plain line each: what
            a park waits for, the stray processes included (park.kills). Asked
            when Sleep is chosen, since the /proc scan is too dear per publish."""
            s = reg.open(p.log_id)
            if not s.running or s.pid is None:
                return []
            keep = s.spec.harness == "claude-code"
            found = await asyncio.to_thread(
                park.strays, s.pid, s.log_id, reg.cgroup_dir(s), keep_servers=keep
            )
            return park.kills(len(s.open_tasks), frozenset(s.timers), len(found))

        @r.op("session.unquiet", LogParams, owned="log_id")
        async def unquiet(p: LogParams, caller):
            # A workflow's node has no tab while its run lives (spec §05):
            # the person reads it from the run's card or the Fleet.
            if (why := reg.workflow_lock(reg.open(p.log_id))) is not None:
                raise OpError("in_workflow", f"{why}; it gets a tab when the run ends")
            return reg.set_quiet(p.log_id, False)

        # The person's, never an agent's: autoclose is their word to close (#691).
        @r.op("session.autoclose", AutocloseParams, owned="log_id")
        async def autoclose(p: AutocloseParams, caller):
            return reg.set_autoclose(p.log_id, p.on)

        @r.op("session.reopen", LogParams, owned="log_id")
        async def reopen(p: LogParams, caller):
            return reg.reopen(p.log_id).wire()

        @r.op("session.rename", RenameParams, agent=True, read=True, owned="log_id")
        async def rename(p: RenameParams, caller):
            """Rename your session: a handle (2 or 3 lowercase segments joined by
            hyphens) and a title, both saying what it works on. Set both in
            one call: turn_end reminds you while either is still the one
            aegis gave."""
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

        async def root_path(path: Path, caller) -> dict:
            """``{"root_path": ...}``, the file relative to the viewer's Files
            root, when their Files view may open it (fs_paths.resolve passes,
            as the viewer): their "Open in Files". Only in the answer; a
            stored peek keeps its shape."""
            place = self.place_of(caller)

            def rel() -> dict:
                try:
                    r = path.resolve().relative_to(place.root.resolve()).as_posix()
                    fs_paths.resolve(place.root, place.guard, r)
                except (ValueError, OpError, OSError):
                    return {}
                return {"root_path": r}

            return await place.run(rel)

        async def peek_path(s, row, index: int, pick: str | None, caller):
            """A path a reply named, resolved now: as written when absolute,
            else under the config root and under the session's cwd (#323).
            When both hold a file and they differ, it opens neither and asks:
            `pick` ("root" or "cwd") is the person's answer. The browser sends
            only the index and the pick. Nothing is recorded on the reply."""
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
            run = self.place_of_session(s.log_id).run

            def look() -> tuple[dict[str, Path], bool]:
                found = {k: t for k, t in tried.items() if t.is_file()}
                two = len(found) == 2
                return found, two and found["root"].resolve() != found["cwd"].resolve()

            found, differ = await run(look)
            if not found:
                raise OpError("no_file", f"no file {raw}{where}")
            if differ:
                if pick is None:
                    raise OpError(
                        "ambiguous_path",
                        f"{raw} names two files: {found['root']} and {found['cwd']}",
                    )
                path = found[pick]
            else:
                path = next(iter(found.values()))
            try:
                (rec,) = await files.store_as(self.roots.state_root, [path], run)
            except files.FileError as e:
                raise OpError(e.code, e.message) from e
            sent = {k: v for k, v in rec.items() if k != "kind"}
            sent["pane_key"] = canvas.file_key(f"peek:{row['id']}:{index}")
            s.record_peek(
                {
                    "kind": "peek",
                    "entry": row["id"],
                    "index": index,
                    "files": [sent],
                }
            )
            saved = s.fold().entry(row["id"])["detail"]["path_peeks"][str(index)]
            return [saved["files"][0] | await root_path(path, caller)]

        @r.op("file.peek", PeekParams)
        async def file_peek(p: PeekParams, caller):
            """Copy the file a Read, Write or Edit row used, as it is now, and
            open it inside that row. The person's way to see a file the agent
            did not send; the agent never sees it."""
            s = reg.open(p.log_id)
            row = s.fold().entry(p.entry_id)
            if p.index is not None:
                return await peek_path(s, row, p.index, p.pick, caller)
            raw = row["detail"].get("path") if row and row["kind"] == "tool" else None
            if not raw:
                raise OpError("no_path", "that row used no file")
            path = Path(raw).expanduser()
            if not path.is_absolute():
                path = s.spec.cwd / path
            try:
                (rec,) = await files.store_as(
                    self.roots.state_root, [path], self.place_of_session(s.log_id).run
                )
            except files.FileError as e:
                raise OpError(e.code, e.message) from e
            sent = {k: v for k, v in rec.items() if k != "kind"}
            sent["pane_key"] = canvas.file_key(f"peek:{p.entry_id}:tool")
            s.record_peek({"kind": "peek", "entry": p.entry_id, "files": [sent]})
            url = files.url(rec["file_id"], rec["name"])
            return [
                {"url": url, "name": rec["name"], "size": rec["size"]}
                | await root_path(path, caller)
            ]

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
                welcome = await probe(
                    p.url, p.token, self.server_name, self.user, p.trusted
                )
            except LinkError as e:
                raise OpError("link_refused", e.message) from e
            name = str(welcome.get("server"))
            if p.name and p.name != name:
                raise OpError(
                    "bad_link", f"that server calls itself {name}, not {p.name}"
                )
            self.links.add(name, p.url, p.token, p.trusted)
            return {"name": name}

        @r.op("link.remove", LinkName, admin=True)
        async def link_remove(p: LinkName, caller):
            self.links.remove(p.name)

        @r.op("link.trust", LinkTrust, admin=True)
        async def link_trust(p: LinkTrust, caller):
            """Ask a link for trust (both ways, agents unrestricted), or stop
            asking. The link reconnects; the far server decides."""
            self.links.trust(p.name, p.trusted)

        @r.op("link.incoming")
        async def link_incoming(_, caller):
            """The servers that linked this one with trust, so their agents
            reach this one over their link, and what each took as an exit."""
            return [
                {**row, "exit_bytes": self.exit_bytes.get(row["name"], 0)}
                for row in self.links.incoming()
            ]

        @r.op("link.exit", LinkExit, admin=True)
        async def link_exit(p: LinkExit, caller):
            """Make a link this server's exit, or stop exiting: what aegis
            starts from now on reaches the internet from there (exit.py)."""
            snap = self.config.current()
            if p.name is not None and self.links.outbound(p.name) is None:
                raise OpError("not_found", f"no link named {p.name}")
            doc = snap.doc.model_copy(update={"exit_via": p.name})
            if problems := write(self.config.path, doc, snap.stamp):
                raise OpError("bad_config", problems[0].message)
            return {"exit_via": p.name}

        @r.op("server.paused", admin=True)
        async def server_paused(p, caller):
            """The pause in force (pause.py), or null."""
            return {"paused": self.paused}

        @r.op("server.pause", admin=True)
        async def server_pause(p, caller):
            """Pause every session: end their processes, hold all new work."""
            ended = await self.pause_all(
                by=caller.principal.name if caller.principal else self.user
            )
            return {"paused": self.paused, "ended": ended}

        @r.op("server.resume", admin=True)
        async def server_resume(p, caller):
            """Resume every session the pause held, and the work it held."""
            await self.resume_all()
            return {"paused": None}

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
                "multi_user": self.multi_user is not None,
            }

        @r.op("token.add", TokenAdd, admin=True)
        async def token_add(p: TokenAdd, caller):
            """Mint a token; the only time it is shown."""
            token = self.principals.add(p.name, p.role, p.linux_user, p.no_linux_user)
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
