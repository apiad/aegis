"""One Claude Code session: its store, its fold, its status, and a process when
it has one.

A session exists without a process. ``asleep`` means there is none, and
``asleep_why`` says how it lost it (``ASLEEP_WHY``); the first
prompt starts ``claude``, with ``--resume`` once the harness has given the session
an id, and the store and the fold simply continue (Claude prints nothing old on
resume, measured in issue #127). Only shutdown, a park, Sleep, Restart and Archive end a process:
a session waiting on its own background task wakes itself, so nothing stops an
idle-looking one.

The spec is the session's model, effort and permission. ``configure`` changes
them, live through control requests when there is a process, and the next
``--resume`` is built from the spec, so a change outlives the process. Each
process start also asks ``claude`` for its commands and models (``initialize``)
and hands the answer to the host, which keeps it by cwd.

The session is a continuous fold. Every stdout line is stored, parsed once,
folded into entry patches and published, whatever the turn state. Status: only a
sent prompt or a turn-bearing event moves it to ``working``, and only ``result``
moves it back to ``idle``; system notices never do. ``error`` is an interrupt
that went unanswered. A child that exits leaves the session asleep, ``exited``.

A line that cannot be stored (a full disk) is logged and dropped, never raised:
the reader that called ``_on_line`` would die with it, nothing would read the
child's stdout again, and the session would freeze until a restart (#395).

A clean server shutdown records nothing in the store. The meta keeps the last
status, and the registry marks a session that was ``working`` at its next boot,
so a crash and a clean stop read the same.
"""

from __future__ import annotations

import asyncio
import contextlib
import dataclasses
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
import pwd
from typing import Any, Literal

from .as_user import Runners
from . import attachments, cache, harness_versions, launch as launching, restarts
from . import layout as canvas
from .artifacts import ArtifactError, Board
from .claude.control import Catalog
from .claude.process import ControlError
from .claude.stream import (
    TURN_BEARING,
    Compact,
    Delta,
    Init,
    Notice,
    RateLimit,
    Result,
    Title,
    ToolCall,
)
from .harness import Launch, Process, harness_for
from .meta import MetaStore
from .names import default_title
from .ops import OpError
from .park import TIMER_TOOLS
from .transcript.entries import EMPTY_STANDING, Fold, fold_records
from .transcript.store import Store, read_store
from .transcript.wire import wire, wire_ops

log = logging.getLogger("aegis.session")

Publish = Callable[[str, list[dict]], None]

# What a fleet card shows. A change to one of _NOW publishes the session at
# once; the others change on nearly every line of a turn (context grows with
# each message, activity with each call), so they wait up to PUBLISH_EVERY_S
# and go out together. Publishing them per line doubled the server's cost per
# line in the bench (issue #127).
_NOW = (
    "status",
    "untested_version",
    "asleep_why",
    "handle",
    "title",
    "model_id",
    "standing",
    "continued_by",
    "handoff_pending",
    "quiet",
    "autoclose",
    "workflow",
    "skills",
    "skill_runs",
)
_SOON = (
    "activity",
    "cost_usd",
    "context_tokens",
    "context_window",
    "unread",
    "recap_cost_usd",
    "btw_cost_usd",
    "tool_calls",
    "cache_recent",
    "compactions",
)
PUBLISH_EVERY_S = 0.25

# Why an asleep session has no process: a Stop or /sleep, a park, the harness
# ending on its own, the server restarting under it (#479).
ASLEEP_WHY = ("slept", "idle", "exited", "server")


def asleep_from(last_status: str, why: str | None) -> tuple[str, str | None]:
    """The state a loaded session starts in. A meta written before #479 says
    ``stopped`` or ``parked``; one that says a live state was cut by the server."""
    if why in ASLEEP_WHY:
        return "asleep", why
    old = {"stopped": "slept", "parked": "idle", "asleep": "slept"}
    return "asleep", old.get(last_status, "server")


class Host:
    """What a session asks of the server around it. The registry implements
    it; the default does nothing, which is what a bare session in a test or
    the bench needs."""

    # Threads that open files as a session's user (as_user.py).
    runners = Runners()

    def spawn_args(
        self, session: "Session"
    ) -> tuple[tuple[str, str] | None, str | None]:
        """The aegis MCP URL and this session's token, and the appended system
        prompt, for a new process."""
        return None, None

    def cgroup(self, session: "Session") -> Path | None:
        """The cgroup a new process starts in (cgroups.py), or None."""
        return None

    def user_for(self, spec: "SpawnSpec") -> str | None:
        """The Linux user a new process runs as (launch.py); None for the
        server's own."""
        return spec.linux_user

    def released(self, session: "Session") -> None:
        """The session's process ended: empty and remove its cgroup."""

    def turn_ended(self, session: "Session") -> None: ...

    def flush_ended(self, session: "Session") -> None:
        """A held flush ended with the session idle: the turn it started
        ended before its send returned, so what reads finished() looks again,
        without a second turn end."""

    def recorded(self, session: "Session", record: dict, events: list | None) -> None:
        """A record was stored and folded: the journal derives from it."""

    def status_changed(self, session: "Session") -> None:
        """The session's status, or what it waits on, changed: a parent waiting
        on it re-derives."""

    def exited(self, session: "Session", code: int, stderr_tail: list[str]) -> None: ...

    def card(self, session: "Session") -> dict:
        """Extra fields for the session's card on the ``sessions`` channel."""
        return {}

    def harness_versions(self) -> dict | None:
        """The config's harness_versions block (config.py), or None to skip
        the version check, as a bare session in a test does."""
        return None

    def catalog_ready(self, session: "Session", catalog: Catalog) -> None:
        """A process answered ``initialize``: its cwd's commands and models."""

    def quota_hold(self, session: "Session") -> str | None:
        """Why the session's owner may not start a turn now (quota_gate.py),
        or None when it may."""
        return None

    def workflow_lock(self, session: "Session") -> str | None:
        """Why nobody but the engine may prompt or interrupt the session (a
        node of a live workflow run, workflows.py), or None."""
        return None

    def successor_name(self, session: "Session") -> str | None:
        """What continues the session after a handoff, for a refusal to name,
        or None when nothing does (registry.py)."""
        return None

    def cascade_next(
        self, session: "Session", text: str
    ) -> tuple[str | None, float] | None:
        """A turn of a cascade failed with ``text`` (cascade.py): None when it
        stays; else the model it falls to, None when none is left, and the
        time the failed model cools until."""
        return None

    def notice_blocks(self, session: "Session", at: str) -> str:
        """The notices the session has not been told yet (notices.py), or "".
        ``at`` is "send" for a prompt, "tool" for a tool result."""
        return ""

    def know_how_touched(self, session: "Session", tool_input: dict) -> None:
        """A tool call of the session's own named these paths."""

    async def know_how_ready(self, session: "Session") -> None:
        """Before a send: a first one waits for its directory's know-how."""

    def knowledge_hint(self, session: "Session", text: str) -> str:
        """The skills and memories that may matter to a send of ``text``
        (knowledge/hint.py), or ""."""
        return ""

    def knowledge_native(self, owner: str | None) -> bool:
        """Whether ``owner``'s knowledge setting is native now, read once at
        a spawn (``SpawnSpec.knowledge_native``)."""
        return False

    def compacted(self, session: "Session") -> None:
        """The harness compacted the session's context."""

    def sign_in_failed(self, session: "Session") -> None:
        """A turn failed because the harness could not sign in (claude/login.py)."""

    def rate_limit(self, session: "Session", windows: dict) -> None:
        """The harness reported the account's usage windows (quota/claude.py)."""


NO_HOST = Host()

# What a cascade sends the model it fell to, in the same turn.
CONTINUE_PROMPT = (
    "The previous model failed with: {error}. You are now running on {model}. "
    "Continue the task from where it stopped."
)


class Archived(Exception):
    """A message was sent to an archived session."""


@dataclass(frozen=True)
class SpawnSpec:
    """What a session's process runs with, fixed at spawn and recorded in the
    spawn record and the meta. A resume reads it from there, never from
    ``.aegis.yaml``: Claude Code does not keep the system prompt in its own
    session file, so editing an agent must not change its old sessions. Only
    model, effort and permission change later, through ``Session.configure``,
    which records each change for the meta and a rebuild."""

    agent: str
    model: str
    effort: str
    permission: str
    cwd: Path
    harness: str = "claude-code"
    priming: str | None = None
    # The fields this spawn changed from its agent, such as ("model",).
    overridden: tuple[str, ...] = ()
    # The log id of the agent session that spawned this one.
    spawned_by: str | None = None
    # The group this session belongs to (#586): the log id of the session that
    # started the chain it was spawned or enqueued in, fixed at spawn, so it
    # outlives that session. ``key@server`` when the chain began on a linked
    # server. None for a session nobody started: its own log id is its group.
    group: str | None = None
    # A handoff of the session that headed its group heads it in its place
    # (#641): the tab bar and the Fleet draw it first, though it is newer.
    leads: bool = False
    # The principal whose session this is (tokens.py); None from before owners,
    # which reads as the admin's.
    owner: str | None = None
    # ``handle@server`` of the agent on a linked server that asked a link for
    # this session. Provenance for the transcript and the journal, never
    # authorization: this server cannot check it.
    spawned_from: str | None = None
    # The log id of the session this one continues after a handoff (#354).
    continues: str | None = None
    # The agent's keep_harness_tools, fixed at spawn like the priming.
    keep_harness_tools: tuple[str, ...] = ()
    # The agent's models when it names two or more and the spawn kept its
    # model; () otherwise. Fixed at spawn: the position in use is the index
    # of ``model`` in it.
    cascade: tuple[str, ...] = ()
    # The Linux user its processes run as, in multi-user mode (users.py):
    # fixed at creation, so a later change of the token's user reaches new
    # sessions only. None runs as the server's own user.
    linux_user: str | None = None
    # The owner's knowledge setting when it was spawned (setup.Records):
    # native starts every process of it without the harness's own skills and
    # memory, and lets a store skill take a harness command's name. Fixed at
    # spawn, so the menu and the process never disagree; the switch reaches
    # new sessions.
    knowledge_native: bool = False

    def record(self) -> dict:
        """The fields a spawn record and a meta carry."""
        d: dict = {
            "agent": self.agent,
            "harness": self.harness,
            "model": self.model,
            "effort": self.effort,
            "permission": self.permission,
            "cwd": str(self.cwd),
        }
        if self.priming:
            d["priming"] = self.priming
        if self.overridden:
            d["overridden"] = list(self.overridden)
        if self.spawned_by:
            d["spawned_by"] = self.spawned_by
        if self.group:
            d["group"] = self.group
        if self.leads:
            d["leads"] = True
        if self.owner:
            d["owner"] = self.owner
        if self.spawned_from:
            d["spawned_from"] = self.spawned_from
        if self.continues:
            d["continues"] = self.continues
        if self.keep_harness_tools:
            d["keep_harness_tools"] = list(self.keep_harness_tools)
        if self.cascade:
            d["cascade"] = list(self.cascade)
        if self.linux_user:
            d["linux_user"] = self.linux_user
        if self.knowledge_native:
            d["knowledge_native"] = True
        return d

    @classmethod
    def from_record(cls, d: dict, cwd_default: Path) -> "SpawnSpec":
        # Records from before #155 carry `profile` and no harness; every one of
        # them ran Claude Code.
        return cls(
            agent=str(d.get("agent") or d.get("profile") or ""),
            model=str(d.get("model") or ""),
            effort=str(d.get("effort") or "high"),
            permission=str(d.get("permission") or "auto"),
            cwd=Path(d.get("cwd") or cwd_default),
            harness=str(d.get("harness") or "claude-code"),
            priming=d.get("priming") or None,
            overridden=tuple(d.get("overridden") or ()),
            spawned_by=d.get("spawned_by"),
            group=d.get("group"),
            leads=bool(d.get("leads")),
            owner=d.get("owner"),
            spawned_from=d.get("spawned_from"),
            continues=d.get("continues"),
            keep_harness_tools=tuple(d.get("keep_harness_tools") or ()),
            cascade=_cascade_of(d.get("cascade")),
            linux_user=d.get("linux_user") or None,
            knowledge_native=bool(d.get("knowledge_native")),
        )


class Session:
    def __init__(
        self,
        *,
        log_id: str,
        spec: SpawnSpec,
        handle: str,
        store: Store,
        stderr_path: Path,
        claude_bin: str,
        publish: Publish,
        metas: MetaStore,
        title: str = "",
        resume_id: str | None = None,
        archived: bool = False,
        created_at: float | None = None,
        last_activity: float | None = None,
        last_status: str = "asleep",
        asleep_why: str | None = None,
        cost_usd: float | None = None,
        context_tokens: int | None = None,
        context_window: int | None = None,
        activity: str = "",
        model_id: str | None = None,
        interrupt_timeout: float = 10.0,
        host: Host = NO_HOST,
        held: list[dict] | None = None,
        worker: dict | None = None,
        schedule: dict | None = None,
        workflow: dict | None = None,
        opencode_bin: str = "opencode",
        codex_bin: str = "codex",
        cursor_bin: str = "cursor-agent",
        title_set: bool = False,
        handle_set: bool = True,
        standing: dict | None = None,
        unread: list[str] | None = None,
        last_read_at: float | None = None,
        recap_cost_usd: float = 0.0,
        btw_cost_usd: float = 0.0,
        last_exit: dict | None = None,
        restart_continues: int = 0,
        quota_paused: str | None = None,
        notices_sent: dict[str, str] | None = None,
        know_how_sent: list[str] | None = None,
        items_sent: dict[str, str] | None = None,
        skills: list[str] | None = None,
        skill_runs: dict[str, int] | None = None,
        tool_calls: int = 0,
        parked_rss: int = 0,
        parked_idle_s: int = 0,
        cache_recent: list[list] | None = None,
        compactions: int = 0,
        continued_by: str | None = None,
        handoff_pending: dict | None = None,
        quiet: bool = False,
        autoclose: bool = False,
        checked_version: str | None = None,
        cascade_exhausted: bool = False,
    ) -> None:
        self.log_id = log_id
        self.spec = spec
        self.handle = handle
        self.title = title
        # A person or an agent named it; the harness's own title then waits.
        self.title_set = title_set
        # Whether session_rename gave this session its handle (#586); a meta
        # from before the field reads as set, so old sessions are not nagged.
        self.handle_set = handle_set
        self.store = store
        self.channel = f"transcript:{log_id}"
        self.resume_id = resume_id
        self.archived = archived
        self.created_at = created_at or time.time()
        self.last_activity = last_activity or self.created_at
        self.last_status = last_status
        self.cost_usd = cost_usd
        self.context_tokens = context_tokens
        self.context_window = context_window
        self.activity = activity
        self.model_id = model_id
        self.status, self.asleep_why = asleep_from(last_status, asleep_why)
        self._stderr_path = stderr_path
        self.harness = harness_for(
            spec.harness, claude_bin, opencode_bin, codex_bin, cursor_bin
        )
        self._publish = publish
        self._metas = metas
        self._interrupt_timeout = interrupt_timeout
        self._fold: Fold | None = None
        # A read makes no store record, so the fold's revisions never see it. Per
        # agent message, the fold's rev when it was read, so a delta since a rev
        # still carries the flag; and the fold's rev when this process loaded it,
        # since reads before then were not kept (snapshot).
        self._read_rev: dict[str, int] = {}
        self._reads_from: int | None = None
        self._proc: Process | None = None
        self._stopping = False
        self._interrupt_timer: asyncio.Task | None = None
        self._publish_timer: asyncio.TimerHandle | None = None
        self._host = host
        # Inbox messages waiting for this session's turn to end.
        self.held: list[dict] = list(held or [])
        self._flushing = False
        # Whether a person's message began the current turn (a typed send, or
        # a / line such as /remember), not an agent's, a queue's or aegis's
        # delivery: memory_save honours asked only then (knowledge_ops.py).
        # In memory only; a restart starts with it off.
        self.person_turn = False
        # One start at a time: an OpenCode start takes seconds, and two sends
        # in that window must not start two children.
        self._starting = asyncio.Lock()
        # Bumped per process start, so a stale process's exit changes nothing.
        self._generation = 0
        # Claude's own tasks started and not yet notified (Bash, background).
        self.open_tasks: set[str] = set()
        self.timers: set[str] = set()  # TIMER_TOOLS this process called (park.py)
        self.worker = worker
        # No tab until its turn ends (#386); a card in the Fleet meanwhile.
        self.quiet = quiet
        # The person's word, ahead of time, to close once the agent ends done
        # (#691): turn_end refuses done without close while it is on.
        self.autoclose = autoclose
        # A schedule's run: {"name", "run", "spawn"}, inherited by its children
        # (schedules.py); None for any other session.
        self.schedule = schedule
        # A workflow node's session: {"run", "node"} (workflows.py); None for
        # any other session.
        self.workflow = workflow
        # The fold's view of the agent's plan and last report; persisted so a
        # card at boot needs no store (DESIGN.md, boot reads meta files).
        self.standing: dict = standing or EMPTY_STANDING
        # Agent messages (prose entries) no person has read yet, on any browser,
        # and when someone last read. In the meta, so boot needs no store; a meta
        # from before this has neither, and nothing old turns up unread.
        self.unread: set[str] = set(unread or ())
        self.last_read_at = last_read_at
        # What this session's recaps cost (recaps.py), apart from its own turns.
        self.recap_cost_usd = recap_cost_usd
        self.btw_cost_usd = btw_cost_usd
        # How the last process ended ({"code", "at"}), and how many times in a
        # row a restart has continued this turn (restarts.py). Both in the meta,
        # because boot decides from metas alone.
        self.last_exit = last_exit
        self.restart_continues = restart_continues
        # Why the quota gate interrupted this session's turn (app.on_quota),
        # until the gate opens and continues it. In the meta, so a restart
        # keeps it paused.
        self.quota_paused = quota_paused
        # What notices this session was told, kind to level (notices.py),
        # {"disk": "warn", "announce:<id>": "warn"}. In the meta, so a restart
        # neither repeats a notice nor forgets a clear.
        self.notices_sent: dict[str, str] = dict(notices_sent or {})
        # The main checkouts whose menu it was told (knowledge/know_how.py).
        self.know_how_sent: list[str] = list(know_how_sent or [])
        # What per-session notice sources told it, apart from the host kinds
        # (notices.py): their restore reads every key in notices_sent.
        self.items_sent: dict[str, str] = dict(items_sent or {})
        # The skills it loaded with skill_read or a /<skill> command, in the
        # order loaded (knowledge_ops.py). In the meta, so the card keeps them.
        self.skills: list[str] = list(skills or [])
        # How many times skill_run ran each loaded skill's scripts.
        self.skill_runs: dict[str, int] = dict(skill_runs or {})
        # What the cache did (cache.py): tool calls and compactions of the main
        # thread, and the last calls' [cache_read, total_input, at]. In the meta,
        # so the counters survive a restart.
        self.tool_calls = tool_calls
        self.parked_rss = parked_rss
        # How long it sat idle before its park, for "asleep after N min idle".
        self.parked_idle_s = parked_idle_s
        self.cache_recent: list[list] = list(cache_recent or [])
        self.compactions = compactions
        # The last usage counted: one call written as several lines repeats it.
        self._last_usage = None
        # The session a handoff started from this one (#354): "starting" while
        # it spawns, its log id after, None when the archive was called off.
        self.continued_by = continued_by
        self.handoff_pending = handoff_pending
        # The process's start time from /proc, so a crashed server's successor
        # can tell this harness from a later process that reused its pid.
        self._pid_started: int | None = None
        # The current process's catalog; None as a result when it did not answer.
        self.catalog_task: asyncio.Task[Catalog | None] | None = None
        self.artifacts = Board(self)
        # The harness version the last untested notice, or a person's "start
        # once", covered (harness_versions.py): a relaunch on it says nothing
        # again. "?" for a version that could not be read. In the meta.
        self.checked_version = checked_version
        # Who asked for the next launch: a person's op sets "person" just
        # before ensure_running and puts it back after; anything else, a
        # parked session's wake included, is unattended.
        self.launch_by = "agent"
        self.last_verdict: harness_versions.Verdict | None = None
        # The link the last launch exits through (exit.py); None for direct.
        self.exit_via: str | None = None
        # The last launch's version when it was outside the tested range, for
        # the panel's warning colour (#456); None in range or before a launch.
        self.untested_version: dict | None = None
        # The notice the last launch wrote, for the spawn op's result.
        self.last_notice: str | None = None
        # A cascade falling to its next model inside a turn, and whether an
        # interrupt came while it did; the card says "cascade exhausted"
        # from the last model's failure until the next turn starts.
        self._falling: asyncio.Task | None = None
        self._fall_stop = False
        self._fall_to: str | None = None
        self.cascade_exhausted = cascade_exhausted

    # -- what the outside sees -------------------------------------------
    def meta(self) -> dict:
        """The persisted form (``<state>/sessions/<log_id>.json``)."""
        s = self.spec
        return {
            "log_id": self.log_id,
            "handle": self.handle,
            "title": self.title,
            "title_set": self.title_set,
            "handle_set": self.handle_set,
            **s.record(),
            "model_id": self.model_id,
            "resume_id": self.resume_id,
            "archived": self.archived,
            "created_at": self.created_at,
            "last_activity": self.last_activity,
            "last_status": self.status
            if self._proc or self.asleep_why == "idle"
            else self.last_status,
            "asleep_why": self.asleep_why,
            "cost_usd": self.cost_usd,
            "context_tokens": self.context_tokens,
            "context_window": self.context_window,
            "activity": self.activity,
            "held": self.held,
            "worker": self.worker,
            "schedule": self.schedule,
            "workflow": self.workflow,
            "standing": self.standing,
            "unread": sorted(self.unread),
            "last_read_at": self.last_read_at,
            "recap_cost_usd": self.recap_cost_usd,
            "btw_cost_usd": self.btw_cost_usd,
            "pid": self.pid,
            "pid_started": self._pid_started if self._proc else None,
            "last_exit": self.last_exit,
            "restart_continues": self.restart_continues,
            "quota_paused": self.quota_paused,
            "notices_sent": self.notices_sent,
            "know_how_sent": self.know_how_sent,
            "items_sent": self.items_sent,
            "skills": self.skills,
            "skill_runs": self.skill_runs,
            "tool_calls": self.tool_calls,
            "parked_rss": self.parked_rss,
            "parked_idle_s": self.parked_idle_s,
            "cache_recent": self.cache_recent,
            "compactions": self.compactions,
            "continued_by": self.continued_by,
            "handoff_pending": self.handoff_pending,
            "quiet": self.quiet,
            "autoclose": self.autoclose,
            "checked_version": self.checked_version,
            "cascade": self._cascade_meta(),
        }

    def _cascade_meta(self) -> dict | None:
        """The cascade and the position in use, None for a session with no
        cascade. ``at`` is None while a person has moved the model outside
        it, which turns falling off until they move back."""
        c = self.spec.cascade
        if not c:
            return None
        at = c.index(self.spec.model) if self.spec.model in c else None
        out: dict = {"models": list(c), "at": at}
        if self.cascade_exhausted:
            out["exhausted"] = True
        return out

    def group_key(self) -> str:
        """The group a session this one starts belongs to (#586)."""
        return self.spec.group or self.log_id

    def wire(self) -> dict:
        """What the ``sessions`` channel carries."""
        m = self.meta()
        m.pop("held")
        m.pop("standing")
        m.pop("priming", None)  # the agent's text stays on the server
        m["unread"] = len(self.unread)
        m["held_count"] = len(self.held)
        m["state"] = self.status
        # What was configured, from spawn on; model_id (in the meta already) is
        # the id the harness resolved it to, null until its first init. One field
        # holding the alias and then the id made two sessions of one agent read
        # differently depending on whether a turn had run (#243).
        m["model"] = self.spec.model or "default"
        m["harness_label"] = self.harness.label
        m["exit"] = self.exit_via
        m["untested_version"] = self.untested_version
        m["cache_hit"] = cache.hit_rate(self.cache_recent)
        m["cache_alarm"] = cache.alarm(self.cache_recent, self.spec.harness)
        m.update(self._host.card(self))
        return m

    def load_skill(self, name: str) -> bool:
        """Mark a skill loaded, once; publishes the card and writes the meta.
        False when it was loaded already."""
        if name in self.skills:
            return False
        self._set(skills=[*self.skills, name])
        self._metas.write(self.meta())
        return True

    def ran_skill(self, name: str) -> None:
        """Count one skill_run of ``name``; publishes the card."""
        self._set(
            skill_runs={**self.skill_runs, name: self.skill_runs.get(name, 0) + 1}
        )
        self._metas.write(self.meta())

    @property
    def busy(self) -> bool:
        """Mid-turn, or about to start one to receive held messages."""
        return self.status == "working" or self._flushing or bool(self.held)

    def mark_continued(self, log_id: str | None) -> None:
        """Name the session a handoff started from this one, or clear it when
        the archive was called off (registry._retire_predecessor)."""
        self._set(continued_by=log_id, handoff_pending=None)

    def mark_handoff_pending(self, params: dict) -> None:
        """An agent asked for a handoff in this turn: the lock starts now, the
        new session when the turn ends (registry.turn_ended, #874)."""
        self._set(continued_by="pending", handoff_pending=params)

    @property
    def in_turn(self) -> bool:
        """Mid-turn, or starting one to deliver held messages."""
        return self.status == "working" or self._flushing

    @property
    def state_root(self) -> Path:
        """Where this session's drafts and sent files live."""
        return self.store.path.parents[1]

    def fold(self) -> Fold:
        if self._fold is None:
            if self.store.path.exists():
                records, _ = read_store(self.store.path)
                self._fold = fold_records(records)
            else:
                self._fold = Fold()
            self._reads_from = self._fold.rev
            self.artifacts.load(self._fold.entries())
        return self._fold

    def entries(self) -> list[dict]:
        return self.fold().entries()

    def view(self) -> list[dict]:
        """The entries as the transcript channel serves them (snapshot)."""
        return self.snapshot()["entries"]

    def snapshot(self, since: int | None = None) -> dict:
        """The fold's snapshot as the channel serves it: wired, and each agent
        message carrying whether it is unread. The fold's entries never carry
        the flag, so a refold of the store still equals them. A read makes no
        store record, so a delta also carries every agent message read since
        ``since``; and every one, when ``since`` is from before this process
        loaded the fold, whose earlier reads it does not know."""
        fold = self.fold()
        snap = fold.snapshot(since)
        if "since" in snap:
            since = snap["since"]
            known = self._reads_from is not None and since > self._reads_from
            read = {
                e["id"]
                for e in fold.entries()
                if e["kind"] == "prose"
                and (not known or self._read_rev.get(e["id"], -2) >= since)
            }
            if read:
                # In the fold's order, as the delta lists its own entries.
                got = {e["id"]: e for e in snap["entries"]}
                snap["entries"] = [
                    got.get(e["id"]) or wire(e)
                    for e in fold.entries()
                    if e["id"] in got or e["id"] in read
                ]
        snap["entries"] = [self._dress(e) for e in snap["entries"]]
        return snap

    def _dress(self, e: dict) -> dict:
        return {**e, "unread": e["id"] in self.unread} if e["kind"] == "prose" else e

    def _out(self, ops: list[dict]) -> list[dict]:
        """Patch ops as the channel serves them: wired and dressed."""
        return [
            {"upsert": self._dress(op["upsert"])} if "upsert" in op else op
            for op in wire_ops(ops)
        ]

    def read(self, ids: list[str]) -> int:
        """A person read these agent messages; ids that are not unread are
        ignored. Publishes the changed entries and the card at once."""
        hit = [i for i in dict.fromkeys(ids) if i in self.unread]
        if not hit:
            return 0
        fold = self.fold()
        for i in hit:
            self._read_rev[i] = fold.rev
        # _set writes the meta; the card goes out at once below, not in a batch.
        self._set(unread=self.unread - set(hit), last_read_at=time.time())
        self._publish(
            self.channel,
            self._out([{"upsert": e} for i in hit if (e := fold.entry(i))]),
        )
        self._publish_now()
        return len(hit)

    @property
    def pid(self) -> int | None:
        return self._proc.pid if self._proc else None

    @property
    def running(self) -> bool:
        return self._proc is not None and self._proc.running

    # -- operations --------------------------------------------------------
    async def start(self) -> None:
        """A brand-new session: record the spawn, then start ``claude``."""
        self._record({"kind": "spawn", **self.spec.record(), "handle": self.handle})
        await self.ensure_running()

    async def ensure_running(self) -> None:
        """Start the harness if there is no process. Raises FileNotFoundError
        when its binary is missing, leaving the session stopped."""
        async with self._starting:
            if not self.running:
                await self._start_process()

    async def _start_process(self) -> None:
        self._generation += 1
        generation = self._generation

        def on_exit(code: int, tail: list[str]) -> None:
            if generation == self._generation:
                self._on_exit(code, tail)

        await self._check_version()
        resume = self.resume_id
        mcp, system_prompt = self._host.spawn_args(self)
        user = self._host.user_for(self.spec)
        inbox = await self._host.runners.get(user)(
            attachments.ensure_inbox, self.state_root, self.log_id, self._home()
        )
        self.exit_via = launching.resolve_via(self.spec.agent)
        proc = self.harness.process(
            Launch(
                via=self.exit_via,
                cwd=self.spec.cwd,
                model=self.spec.model,
                effort=self.spec.effort,
                permission=self.spec.permission,
                resume_id=resume,
                mcp=mcp,
                system_prompt=system_prompt,
                stderr_path=self._stderr_path,
                on_line=self._on_line,
                on_exit=on_exit,
                on_error=self._on_error,
                read_dirs=(inbox,),
                cgroup=self._host.cgroup(self),
                log_id=self.log_id,
                user=user,
                keep_harness_tools=self.spec.keep_harness_tools,
                knowledge_native=self.spec.knowledge_native,
            )
        )
        # Before the start: a harness can speak while it starts (OpenCode's
        # session.created), and a line read while stopping is dropped.
        self._stopping = False
        await proc.start()
        self._proc = proc
        self._pid_started = restarts.started_at(proc.pid) if proc.pid else None
        self.last_exit = None
        self.catalog_task = asyncio.create_task(self._fetch_catalog(proc))
        self.open_tasks.clear()
        self.timers.clear()
        if resume:
            self._record({"kind": "resume", "resume_id": resume})
        sid = proc.session_id
        if resume and sid and sid != resume:
            self._record(
                {
                    "kind": "reset",
                    "text": f"{self.harness.label} no longer had this conversation; "
                    "it started a new one",
                }
            )
        if sid:
            self._set(resume_id=sid)
        self._set(parked_rss=0, parked_idle_s=0)
        self._set(status="idle", asleep_why=None)

    async def _check_version(self) -> None:
        """Judge the harness version before a launch, and note one outside the
        tested range in the transcript, once per version."""
        cfg = self._host.harness_versions()
        if cfg is None:
            return
        v = await harness_versions.check(self.harness, cfg["accepted"])
        self.last_verdict, self.last_notice = v, None
        self._set(
            untested_version={
                "version": v.version,
                "state": v.state,
                "range": v.range.text(),
            }
            if v.path is not None and v.outside
            else None
        )
        token = v.version or "?"
        if v.path is None or not v.outside or token == self.checked_version:
            return
        if cfg[self.launch_by] == "off":
            return
        text = v.notice(started_anyway=self.launch_by == "agent")
        log.warning("%s (%s): %s", self.handle, self.log_id, text.replace("\n", "; "))
        self._record(
            {
                "kind": "untested",
                "text": text,
                "harness": v.harness,
                "version": v.version,
                "state": v.state,
            }
        )
        self._set(checked_version=token)
        self.last_notice = text

    async def _fetch_catalog(self, proc: Process) -> Catalog | None:
        try:
            cat = await proc.catalog()
        except (
            ControlError,
            TimeoutError,
            BrokenPipeError,
            ConnectionResetError,
        ):
            return None
        self._host.catalog_ready(self, cat)
        self._window_from(cat)
        return cat

    def _window_from(self, cat: Catalog | None) -> None:
        """A harness whose events name no context window (OpenCode) has it in
        its catalog."""
        m = cat.model(self.spec.model) if cat is not None else None
        if m is not None and m.window:
            self._set(context_window=m.window)

    async def configure(
        self,
        model: str | None = None,
        effort: str | None = None,
        permission: str | None = None,
    ) -> None:
        """Change the model, effort or permission. A live process gets each
        change through a control request first; the spec, which the next
        ``--resume`` is built from, takes only what applied, and a refusal is
        raised after that is recorded."""
        proc = self._proc if self.running else None
        when = (
            "on_resume"
            if proc is None
            else "next_turn"
            if self.status == "working"
            else ""
        )
        applied: dict[str, str] = {}
        try:
            for kind, value in (
                ("model", model),
                ("effort", effort),
                ("permission", permission),
            ):
                if not value:
                    continue
                if proc is not None:
                    await proc.set(kind, value)
                applied[kind] = value
        finally:
            if applied:
                self.spec = dataclasses.replace(self.spec, **applied)
                if "model" in applied:
                    self.model_id = None  # the next init names the resolved id
                    t = self.catalog_task
                    if t is not None and t.done() and not t.cancelled():
                        self._window_from(t.result())
                self._record(
                    {"kind": "configure", **applied, **({"when": when} if when else {})}
                )
                self._publish_now()
                self._metas.write(self.meta())

    def check_lock(self) -> None:
        """Refuse what would steer a live workflow's node. Every way in goes
        through deliver, send and interrupt, which call this unless the caller
        passes ``through_lock``: the engine, and what the node itself started
        (its monitors, its queue tasks) or the server's own quota pause."""
        if (why := self._host.workflow_lock(self)) is not None:
            raise OpError("in_workflow", why)

    def check_handed_off(self) -> None:
        """Refuse a message to a session a handoff has replaced (#874). Apart
        from check_lock, which also guards an interrupt and a close: those stay
        open here. ``through_lock`` passes this too, for the server's own
        continue after a restart."""
        if (next_ := self._host.successor_name(self)) is not None:
            raise OpError(
                "handed_off", f"{self.handle} was handed off; {next_} continues it"
            )

    async def deliver(
        self,
        header: str,
        body: str,
        *,
        through_lock: bool = False,
        urgent: bool = False,
        by_person: bool = False,
    ) -> Literal["sent", "held", "steered"]:
        """An inbox message. Idle or stopped: sent now (a stopped session is
        resumed to receive it). Mid-turn: held, and sent with any others when
        the turn ends, because a prompt written mid-turn would be injected at
        the next tool boundary instead of waiting; ``urgent`` wants exactly
        that, and gets it from a harness that reads mid-turn input. When the
        owner's quota gate says no turn may start, held with ``held_for:
        "quota"`` until it opens (app.on_quota). ``by_person``: a person's
        / line delivered it (``person_turn``). Returns what it did."""
        if self.archived:
            raise Archived(self.log_id)
        if not through_lock:
            self.check_lock()
            self.check_handed_off()
        msg = {"header": header, "body": body, "ts": time.time()}
        if by_person:
            msg["by_person"] = True
        if urgent and self.status == "working" and self.harness.steers_mid_turn:
            # Steered in as a person's mid-turn message is: a user message at
            # the next tool boundary (Claude Code) or a turn/steer (Codex).
            await self._send(_inbox_text([msg]), steered=True, by_person=by_person)
            return "steered"
        if self.status == "working" or self._flushing:
            self._hold(msg)
            return "held"
        if self._host.quota_hold(self) is not None:
            self._hold(msg | {"held_for": "quota"})
            return "held"
        await self._send(_inbox_text([msg]), by_person=by_person)
        return "sent"

    def _hold(self, msg: dict) -> None:
        self.held.append(msg)
        self._metas.write_soon(self.log_id, self.meta)
        self._publish_now()
        self._host.status_changed(self)

    async def _flush_held(self) -> None:
        self._flushing = True
        batch, self.held = self.held, []
        try:
            if batch:
                # Before the send gathers notices: the "held" warning ends here.
                self._host.status_changed(self)
                await self._send(
                    _inbox_text(batch),
                    by_person=any(m.get("by_person") for m in batch),
                )
        finally:
            self._flushing = False
            if batch and not self.busy:
                # The turn the send started ended before it returned, and its
                # end found the session busy flushing (#536).
                self._host.flush_ended(self)

    @property
    def inbox(self) -> Path:
        """Where the files a person attaches land (attachments.py)."""
        return attachments.inbox(self.state_root, self.log_id, self._home())

    @property
    def drafts(self) -> Path:
        """Where this session's artifact drafts go (artifacts.draft_dir)."""
        home = self._home()
        return home / ".aegis" if home is not None else self.state_root

    def _home(self) -> Path | None:
        """The home of the Linux user this session runs as (Host.user_for),
        or None for the server's own; PermissionError when that user is gone."""
        user = self._host.user_for(self.spec)
        if user is None:
            return None
        try:
            return Path(pwd.getpwnam(user).pw_dir)
        except KeyError:
            raise PermissionError(f"Linux user {user} does not exist") from None

    async def send(
        self,
        text: str,
        typed: str | None = None,
        attached: list[dict] | None = None,
        *,
        through_lock: bool = False,
        by_person: bool = False,
    ) -> None:
        """Send ``text`` to the harness. With files ``attached``, ``text`` is
        the typed text plus their paths, and ``typed`` what the person wrote,
        which their row shows (transcript/entries.py). ``by_person``: a
        person typed it (``person_turn``)."""
        if not through_lock:
            self.check_lock()
            self.check_handed_off()
        await self._send(text, typed, attached, by_person=by_person)

    async def _send(
        self,
        text: str,
        typed: str | None = None,
        attached: list[dict] | None = None,
        *,
        steered: bool = False,
        by_person: bool = False,
    ) -> None:
        await self.ensure_running()
        assert self._proc is not None
        if not self.title and not text.startswith("/"):
            # A slash command names no task; the first prompt does.
            self._set(title=default_title(typed or text))
        if self.cascade_exhausted:
            self.cascade_exhausted = False  # a new turn starts
        if not text.startswith("/"):
            await self._host.know_how_ready(self)
            # The hint first, then the notices. The record and the harness get
            # the same text, so the fold pairs them; the fold splits the
            # blocks off into their own rows.
            blocks = [
                self._host.knowledge_hint(self, text),
                self._host.notice_blocks(self, "send"),
            ]
            if extra := "\n\n".join(b for b in blocks if b):
                text = f"{text}\n\n{extra}"
        record: dict = {"kind": "send", "text": text}
        if attached:
            record |= {"typed": typed or "", "files": attached}
        if steered:
            record["steered"] = True  # an inbox message sent into a running turn
        if by_person:
            self.person_turn = True  # a person's words now steer this turn
        elif not steered:
            self.person_turn = False  # a turn someone else began
        self._record(record)
        # Working before the send returns: a harness can answer during it (an
        # OpenCode command refused before it starts a turn).
        self._set(status="working")
        try:
            await self._proc.send(text)
        except Exception as e:
            self._on_error(f"the message did not reach the agent: {e}", True, text)
            raise

    async def interrupt(self, *, through_lock: bool = False) -> None:
        if not through_lock:
            self.check_lock()
        if self._falling is not None:
            # Between the failed turn and the continue prompt nothing runs in
            # the harness: the fall stops before it sends.
            self._fall_stop = True
            return
        if self.status != "working" or self._proc is None:
            return
        self._record({"kind": "interrupt"})
        # The deadline first: an interrupt that cannot reach the child still
        # ends in "error" instead of leaving the session working.
        if self._interrupt_timer is None or self._interrupt_timer.done():
            self._interrupt_timer = asyncio.create_task(self._interrupt_deadline())
        await self._proc.interrupt()

    async def stop(self) -> None:
        """End the process and keep the session. A fall under way stops
        before it continues, and the failed turn it held open ends."""
        fell_to = await self._drop_fall()
        if self._proc is None:
            if self.asleep_why == "idle":  # a person's Stop outranks a park
                self._record({"kind": "stop"})
                self._set(asleep_why="slept")
                self._metas.write(self.meta())
        else:
            await self._end_process()
            self._record({"kind": "stop"})
            self._set(status="asleep", asleep_why="slept")
            self._metas.write(self.meta())
        if fell_to is not None:
            self._record({"kind": "cascade_stopped", "to": fell_to, "by": "stop"})
            self._host.turn_ended(self)

    async def restart(self) -> None:
        """A fresh process on the same conversation: end the one there is, if
        any, and start another, which resumes it (#479). Under the start lock,
        so a send waits for the new process. Calls neither stop() nor
        ensure_running(): the lock is not reentrant. A start that fails leaves
        the session asleep."""
        async with self._starting:
            await self._end_process()
            self._record({"kind": "restart"})
            self._set(status="asleep", asleep_why="slept")
            await self._start_process()

    async def park(self, idle_s: float) -> bool:
        """End the process of a session nothing needs (park.py); the next
        message resumes it. Under the start lock, so a send waits for the park
        to finish and then starts the process again. False when a turn, a held
        message or a flush got there first."""
        async with self._starting:
            if not self.running or self.status != "idle" or self.held or self._flushing:
                return False
            rss = _rss(self.pid)
            await self._end_process()
            self._record({"kind": "park", "idle_s": round(idle_s), "rss": rss})
            self._set(
                status="asleep",
                asleep_why="idle",
                parked_rss=rss,
                parked_idle_s=round(idle_s),
            )
            return True

    async def shutdown(self) -> None:
        """The server is stopping: end the process, record nothing, and keep the
        last status in the meta so a mid-turn session is marked at next boot.
        A fall under way is dropped there: the turn is still working, so the
        next boot continues it."""
        await self._drop_fall()
        self.artifacts.flush_all()
        if self._proc is not None:
            self.last_status = self.status
            await self._end_process()
        self.store.close()
        self._metas.write(self.meta())

    # -- internals ---------------------------------------------------------
    async def _end_process(self) -> None:
        if self._interrupt_timer:
            self._interrupt_timer.cancel()
        if self.catalog_task is not None and not self.catalog_task.done():
            self.catalog_task.cancel()
        proc, self._proc = self._proc, None
        if proc is not None:
            self._stopping = True
            await proc.terminate()
            self._host.released(self)

    async def _interrupt_deadline(self) -> None:
        await asyncio.sleep(self._interrupt_timeout)
        if self.status == "working" and self._proc is not None:
            self._record(
                {"kind": "interrupt_timeout", "after_s": self._interrupt_timeout}
            )
            self._set(status="error")

    def record_file(self, record: dict) -> None:
        """The files one file_send sent (files.store's records) and its caption."""
        self._record(record)

    def record_peek(self, record: dict) -> None:
        """A tool row's file, copied because the person asked to see it."""
        self._record(record)

    def record_artifact(self, record: dict) -> None:
        """A record from the session's artifacts board (artifacts.py)."""
        self._record(record)

    def record_secret(self, record: dict) -> None:
        """A secret's card or its next state (secret_ops): never plaintext."""
        self._record(record)

    def record_config(self, record: dict) -> None:
        """A config suggestion's card or its outcome (config_suggest.py)."""
        self._record(record)

    def record_knowledge(self, record: dict) -> None:
        """A knowledge card or its outcome (knowledge_ops.py)."""
        self._record(record)

    def record_card(self, record: dict) -> None:
        """A card asking a person to approve what this session's agent made:
        a schedule now, a workflow next (schedule_ops, workflow_ops)."""
        self._record(record)

    # -- the canvas (layout.py) ---------------------------------------------
    def dockable(self, pane: str) -> bool:
        """Whether ``pane`` can sit in this session's canvas: a live page of
        this session, a file it sent (by key), or a workflow it armed (by its
        card's entry id, ``wf-<run>``) and has not armed again over."""
        if pane.startswith("wf-"):
            e = self.fold().entry(pane)
            if e is None or e["kind"] != "workflow":
                return False
            wfs = getattr(self._host, "workflows", None)
            st = wfs.runs.get(pane[3:]) if wfs is not None else None
            return st is None or st.status != "replaced"
        if pane.startswith("b-"):
            # A browser this session drives (browsers.py).
            brs = getattr(self._host, "browsers", None)
            b = brs.open.get(pane) if brs is not None else None
            return b is not None and b.meta.get("session") == self.log_id
        if pane.startswith("art-"):
            try:
                return self.artifacts.get(pane).status == "live"
            except ArtifactError:
                return False
        return pane in self.fold().file_keys

    @property
    def layout(self) -> dict:
        """The standing layout, less any pane that can no longer dock. A crash
        between the store append and the meta write can boot with a layout
        naming a page closed since, and every move would then fail on it."""
        lay = self.standing.get("layout") or canvas.DEFAULT
        for pid in canvas.ids(lay):
            if pid != canvas.TRANSCRIPT and not self.dockable(pid):
                lay = canvas.trim(lay, pid)
        return lay

    def set_layout(self, new: dict, by: str) -> dict:
        """Record ``new``, already normalised; every browser on the session
        redraws from the card."""
        self._record({"kind": "layout", "by": by, "windows": new["windows"]})
        return new

    def notify(self, ops: list[dict]) -> None:
        """An op on the transcript channel that is no entry: a probe request. It
        is not in any snapshot, so a browser that subscribes later never sees it."""
        self._publish(self.channel, ops)

    def report(self, record: dict) -> None:
        """A plan or a turn report from the agent (agent_ops)."""
        self._record(record)

    def add_recap_cost(self, cost: float) -> None:
        self._set(recap_cost_usd=round(self.recap_cost_usd + cost, 6))

    def add_btw_cost(self, cost: float) -> None:
        self._set(btw_cost_usd=round(self.btw_cost_usd + cost, 6))

    def _record(self, record: dict, events: list | None = None) -> None:
        fold = self.fold()
        stored = self.store.append({"ts": time.time(), "src": "aegis", **record})
        if record.get("kind") not in (
            "recap",
            "peek",
            "artifact_state",
            "artifact_event",
            "secret_gone",
            "name",
            "layout",
            "btw",
            "btw_answer",
            "park",
        ):
            # aegis talking to the person, not the session doing anything; nor
            # a person acting on a page, so the needs-you order holds still
            # while they drag a slider; nor a rename; nor arranging the canvas;
            # nor a side note, which the person asked and the agent never sees;
            # nor a park, which the Fleet would otherwise order as activity.
            self.last_activity = stored["ts"]
        ops = fold.apply(stored, events)
        new = [
            op["upsert"]["id"]
            for op in ops
            if op.get("upsert", {}).get("kind") == "prose" and op["upsert"]["md"]
        ]
        if new:
            # Before dressing the ops, so the new rows go out unread. "unread" is
            # in _SOON: the card's count follows within PUBLISH_EVERY_S.
            self._set(unread=self.unread | set(new))
        self._publish(self.channel, self._out(ops))
        if fold.standing is not self.standing:
            self._set(standing=fold.standing)
            self.standing = fold.standing
        if any(
            op.get("upsert", {}).get("kind")
            in ("user", "prose", "tool", "file", "artifact")
            for op in ops
        ):
            self._set(activity=fold.activity())
        self._host.recorded(self, stored, events)

    def _set(self, **changes: object) -> None:
        changes = {k: v for k, v in changes.items() if getattr(self, k) != v}
        if not changes:
            return
        for k, v in changes.items():
            setattr(self, k, v)
        if "status" in changes:
            self.last_status = self.status
            self._host.status_changed(self)
        if any(k in _NOW for k in changes):
            self._publish_now()
        elif any(k in _SOON for k in changes) and self._publish_timer is None:
            try:
                loop = asyncio.get_running_loop()
            except RuntimeError:
                self._publish_now()
            else:
                self._publish_timer = loop.call_later(
                    PUBLISH_EVERY_S, self._publish_now
                )
        if changes.get("status") in ("idle", "asleep", "error"):
            self._metas.write(self.meta())
        else:
            self._metas.write_soon(self.log_id, self.meta)

    def _publish_now(self) -> None:
        if self._publish_timer is not None:
            self._publish_timer.cancel()
            self._publish_timer = None
        # A closed session's row is gone from every browser (Registry.close); a
        # timer armed before the close, or a cost paid after it, must not put
        # it back as a tab that can be neither closed nor reopened (#856).
        if not self.archived:
            self._publish("sessions", [{"upsert": self.wire()}])

    def _on_line(self, line: str) -> None:
        try:
            self._take_line(line)
        except Exception:
            log.exception("%s: a line from the harness was dropped", self.handle)

    def _take_line(self, line: str) -> None:
        if self._stopping:
            return
        fold = self.fold()
        events = fold.parse(self.harness.src, line)
        if events and all(isinstance(ev, Delta) for ev in events):
            # Never stored: the part's closing update carries the whole text.
            self._publish(self.channel, self._out(fold.live(events)))
            self._set(
                activity=fold.activity(),
                **({"status": "working"} if self.status == "idle" else {}),
            )
            return
        self._record({"src": self.harness.src, "line": line}, events)
        changes: dict[str, object] = {}
        tasks = len(self.open_tasks)
        recent, tools, compactions = (
            self.cache_recent,
            self.tool_calls,
            self.compactions,
        )
        falling = False
        for ev in events:
            if isinstance(ev, TURN_BEARING) and self.status == "idle":
                changes["status"] = "working"
            usage = getattr(ev, "usage", None)
            if usage is not None and getattr(ev, "parent", None) is None:
                changes["context_tokens"] = usage.context
                if usage != self._last_usage:
                    self._last_usage = usage
                    recent = cache.push(recent, usage, time.time())
                    changes["cache_recent"] = recent
            if isinstance(ev, ToolCall) and ev.parent is None:
                tools += 1
                changes["tool_calls"] = tools
                self._host.know_how_touched(self, ev.input)
                self._steer_owed()
            if isinstance(ev, ToolCall) and ev.name in TIMER_TOOLS:
                self.timers.add(ev.name)
            if isinstance(ev, Compact):
                compactions += 1
                changes["compactions"] = compactions
                self._host.compacted(self)
            if isinstance(ev, Title) and not self.title_set:
                changes["title"] = ev.text
            if isinstance(ev, Init):
                if ev.model:
                    changes["model_id"] = ev.model
                if ev.session_id:
                    changes["resume_id"] = ev.session_id
            if isinstance(ev, RateLimit):
                self._host.rate_limit(self, ev.windows)
            if isinstance(ev, Result) and ev.auth_failed:
                self._host.sign_in_failed(self)
            if isinstance(ev, Notice) and ev.task_id:
                if ev.subtype == "task_started":
                    self.open_tasks.add(ev.task_id)
                elif ev.subtype == "task_notification":
                    self.open_tasks.discard(ev.task_id)
            if isinstance(ev, Result):
                interrupting = (
                    self._interrupt_timer is not None
                    and not self._interrupt_timer.done()
                )
                if self._interrupt_timer:
                    self._interrupt_timer.cancel()
                falling = self._cascade(ev, interrupting)
                if not falling:
                    changes["status"] = "idle"
                if not ev.is_error:
                    changes["restart_continues"] = 0
                if ev.cost_usd is not None:
                    changes["cost_usd"] = ev.cost_usd
                if ev.context_window:
                    changes["context_window"] = ev.context_window
        if changes:
            self._set(**changes)
        if len(self.open_tasks) != tasks:
            self._publish_now()  # waiting on a background task shows on the card
            self._host.status_changed(self)
        if any(isinstance(ev, Result) for ev in events) and not falling:
            self._turn_over()

    def _steer_owed(self) -> None:
        """What the session is owed goes into the running turn: this tool
        call's result has yet to reach the model, so another request is coming
        and the write joins it instead of starting a turn. A write outside a
        running turn would start one, so nothing goes then. Stored only as
        the harness's echo: this is no send (no clock, no nudge, no recap)."""
        if (
            not self.harness.steers_notices
            or self.status != "working"
            or self._proc is None
            or self._falling is not None
            or (self._interrupt_timer is not None and not self._interrupt_timer.done())
        ):
            return
        if blocks := self._host.notice_blocks(self, "tool"):
            asyncio.get_running_loop().create_task(self._steer(blocks))

    async def _steer(self, text: str) -> None:
        proc = self._proc
        if proc is None:
            return
        try:
            await proc.send(text)
        except Exception:
            log.warning("%s: a notice did not reach the agent", self.handle)

    def _turn_over(self) -> None:
        """A turn ended: send what it held, unless the gate says no new turn."""
        if self.held and self._host.quota_hold(self) is not None:
            # The gate says no new turn: they wait for it to open.
            self.held = [m | {"held_for": "quota"} for m in self.held]
            self._metas.write_soon(self.log_id, self.meta)
            self._publish_now()
        elif self.held:
            self._flushing = True
            asyncio.get_running_loop().create_task(self._flush_held())
        self._host.turn_ended(self)

    def _cascade(self, ev: Result, interrupting: bool) -> bool:
        """Hand a failed turn of a cascade to the host; True when the session
        falls to its next model, which continues this same turn."""
        c, model = self.spec.cascade, self.spec.model
        if not ev.is_error or not c or model not in c or interrupting:
            return False
        error = ev.error_text or ev.subtype
        answer = self._host.cascade_next(self, error)
        if answer is None:
            return False  # an error that stays: the turn ends as before
        to, until = answer
        if to is None:
            self.cascade_exhausted = True
            self._record({"kind": "cascade_exhausted", "error": error})
            return False
        self._fall_stop, self._fall_to = False, to
        self._falling = asyncio.get_running_loop().create_task(
            self._fall({"from": model, "to": to, "error": error, "until": until})
        )
        return True

    async def _fall(self, row: dict) -> None:
        """Switch to the next model, then say so and continue the turn on it.
        Stop and shutdown cancel this task while it switches (_drop_fall); an
        interrupt lets the switch finish and sends nothing."""
        to, error = row["to"], row["error"]
        try:
            await self.configure(model=to)
        except Exception as e:
            log.warning("%s: the cascade could not move to %s: %s", self.handle, to, e)
            self._falling = None
            why = getattr(e, "message", None) or str(e)
            self._record({"kind": "cascade", **row, "refused": why})
            self._end_fallen_turn()
            return
        self._falling = None
        if self._fall_stop:
            self._record({"kind": "cascade_stopped", "to": to})
            self._end_fallen_turn()
            return
        self._record({"kind": "cascade", **row})
        try:
            # Under an aegis header, as a restart's continue is: the
            # transcript shows it as aegis's message, not the person's.
            at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
            body = CONTINUE_PROMPT.format(error=error, model=to)
            await self._send(f"> from aegis:cascade · {at}\n{body}")
        except Exception:
            log.exception("%s: the cascade could not continue on %s", self.handle, to)
            if self.status == "working":  # failed before _send's _on_error ended it
                self._end_fallen_turn()

    def _end_fallen_turn(self) -> None:
        self._set(status="idle")
        self._turn_over()

    async def _drop_fall(self) -> str | None:
        """Cancel a fall that has not continued yet (stop, close, shutdown):
        the model it was moving to, or None when no fall was under way."""
        t, self._falling = self._falling, None
        if t is None:
            return None
        t.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await t
        return self._fall_to

    def _on_exit(self, code: int, stderr_tail: list[str]) -> None:
        if self._stopping:
            return
        self._proc = None
        self._host.released(self)
        self.open_tasks.clear()
        # The status it cut, so a restart continues only a turn (restarts.py).
        self.last_exit = {"code": code, "at": time.time(), "status": self.status}
        self._record(
            {
                "kind": "exit",
                "code": code,
                "stderr_tail": stderr_tail,
                "harness": self.harness.label,
                "status": self.last_exit["status"],
            }
        )
        self._set(status="asleep", asleep_why="exited")
        self._host.exited(self, code, stderr_tail)

    def _on_error(self, text: str, idle: bool, line: str | None = None) -> None:
        if self._stopping:
            return
        rec: dict = {"kind": "harness_error", "text": text}
        if line:
            rec["line"] = line
        self._record(rec)
        if idle and self.status == "working":
            self._set(status="idle")
            self._host.turn_ended(self)


def _cascade_of(raw: Any) -> tuple[str, ...]:
    """A recorded cascade: the spawn record's list, or the meta's
    ``{"models", "at"}``."""
    if isinstance(raw, dict):
        raw = raw.get("models")
    return tuple(str(m) for m in raw) if isinstance(raw, list) else ()


def _inbox_text(batch: list[dict]) -> str:
    """Held messages as one prompt, in arrival order, each under its header."""
    return "\n\n".join(f"{m['header']}\n{m['body']}".rstrip() for m in batch)


def _rss(pid: int | None) -> int:
    """Resident bytes of ``pid``, 0 when it cannot be read."""
    try:
        for line in Path(f"/proc/{pid}/status").read_text().splitlines():
            if line.startswith("VmRSS:"):
                return int(line.split()[1]) * 1024
    except (OSError, ValueError):
        pass
    return 0
