"""Every session on the server: the open ones as Sessions, the archived ones as
their meta.

Boot reads only meta files. A missing or damaged one is rebuilt from its store
and the session lands in the archive; a store nothing can be rebuilt from is
skipped with a log line. Boot writes nothing to any store, with one exception:
a session whose turn the last run's end cut gets one ``server_stopped`` record,
and its meta is rewritten so the next boot does not add another. Which of those
turns continue is restarts.py's to say; continue_after_boot sends.

Handles are unique across every session, archived ones included, so a handle
names one conversation for as long as the server keeps it.
"""

from __future__ import annotations

import asyncio
import dataclasses
import logging
import re
import secrets
import time
from collections.abc import Callable
from pathlib import Path

from .as_user import Runners

from .archive import Position, count_workers, page as archive_page
from . import attention, cache, restarts, secret_store, users
from .cgroups import SessionCgroups
from .config import CONTEXT_DEFAULTS
from .harness import harness_for
from .meta import MetaStore, rebuild
from .names import TITLE_MAX, free_handle, mint_handle, valid_handle
from . import notice_sources
from .knowledge import hint
from .notice_sources import Item
from .notices import TEXT_MAX
from .ops import OpError
from .roots import Roots
from .session import Host, Session, SpawnSpec
from .transcript.entries import Fold, fold_records, tool_output
from .transcript.store import Store, read_store
from .transcript.wire import withheld_text

log = logging.getLogger("aegis.registry")
Publish = Callable[[str, list[dict]], None]

# A busy session is told, on its next tool result, that inbox messages wait
# for its turn to end (Session.deliver), so it can finish early when they may
# change its work. The message itself stays a user turn.
HELD_SOURCE = "held"
notice_sources.declare(HELD_SOURCE, {})
HELD_TAIL = " End your turn early if it changes your work."


def held_items(held: list[dict]) -> list[Item]:
    """One warning per message held mid-turn: its sender and as much of its
    first line as a notice's one line leaves room for. Keyed by its arrival,
    so each new message is told once. Messages the quota gate holds are left
    out: their session is not in a turn."""
    items = []
    for m in held:
        if "held_for" in m:
            continue
        head = f"{held_label(m['header'])} waits for your turn to end: "
        first = next((ln.strip() for ln in m["body"].splitlines() if ln.strip()), "")
        room = TEXT_MAX - len(head) - len(HELD_TAIL) - 2
        if len(first) > room:
            first = first[: max(room - 1, 0)] + "…"
        key = f"m{int(m['ts'] * 1000)}"
        items.append(Item(key, "warn", f'{head}"{first}"{HELD_TAIL}'))
    return items


def held_label(header: str) -> str:
    """``> from monitor:m-1 · fail · 2026-…Z`` → ``monitor:m-1 · fail``."""
    parts = header.removeprefix("> from ").split(" · ")
    if len(parts) > 1 and re.match(r"\d{4}-\d{2}-\d{2}T", parts[-1]):
        parts.pop()
    return " · ".join(parts)


def mint_log_id() -> str:
    return f"{time.strftime('%Y%m%d-%H%M%S')}-{secrets.token_hex(3)}"


def _public(meta: dict) -> dict:
    """A stored meta as a browser may see it: the agent's priming and its raw
    standing stay on the server, like ``Session.wire`` keeps them. It carries
    the harness's label as the wire does, for the side panel's model line."""
    out = {k: v for k, v in meta.items() if k not in ("priming", "standing")}
    harness = harness_for(
        meta.get("harness") or "claude-code",
        "claude",
        "opencode",
        "codex",
        "cursor-agent",
    )
    out["harness_label"] = harness.label
    return out


class Registry(Host):
    def __init__(
        self,
        roots: Roots,
        publish: Publish,
        claude_bin: str = "claude",
        interrupt_timeout: float = 10.0,
        opencode_bin: str = "opencode",
        codex_bin: str = "codex",
        cursor_bin: str = "cursor-agent",
    ) -> None:
        self.roots = roots
        self._publish = publish
        self._claude_bin = claude_bin
        self._opencode_bin = opencode_bin
        self._codex_bin = codex_bin
        self._cursor_bin = cursor_bin
        self._interrupt_timeout = interrupt_timeout
        self.metas = MetaStore(roots.state_root / "sessions")
        self.sessions: dict[str, Session] = {}
        self.archived: dict[str, dict] = {}
        # Set by the app when it wires the MCP endpoint, monitors and queues.
        self.mcp_url: str | None = None
        self.tokens = None
        self.server_name = "aegis"
        self.monitors = None
        self.side_notes = None
        self.secrets = None  # secret_store.SecretStore, set by the app
        self.queues = None
        self.schedules = None
        self.workflows = None
        self.browsers = None  # browsers.py, set by the app: docked browser panes
        self.quota = None
        self.notices = None  # notices.Notices, set by App
        self.sign_in = None  # claude.login.SignIn, set by App
        self.load = None  # load.LoadSampler, set by App
        # knowledge.Knowledge, and whose knowledge a session's sends are
        # hinted from (its owner, else the admin); both set by App.
        self.knowledge = None
        self.know_how = None  # knowledge.know_how.KnowHow, set by App
        self.skill_servers = None  # skill_mcp.SkillServers, set by App
        self._hint_failures: set[str] = set()  # causes already logged
        self.principal_of: Callable[[Session], str] = lambda s: s.spec.owner or ""
        # Whether a session's owner turned the harness's own knowledge off;
        # set by App from the owner's setup record.
        self.native_of: Callable[[str | None], bool] = lambda owner: False
        self.nudges = None  # nudges.Nudges, set by App
        # The live config's context block (config.py); the app points it at
        # Config.current().
        self.context: Callable[[], dict] = lambda: dict(CONTEXT_DEFAULTS)
        # The live config's harness_versions block; None skips the version
        # check (harness_versions.py), as a registry with no app does. The
        # app points it at Config.current().
        self.version_policy: Callable[[], dict | None] = lambda: None
        # The app's quota gate: (owner, harness, model) -> quota_gate.Gate.
        self.gate = None
        # The person's pause of every session (pause.py): session -> why.
        self.pause_hold = None
        # The app's start_spec: a spec -> the spec to start (its cascade's
        # first model free now) and the gate it passes.
        self.start_spec = None
        # The app's cascade_next, which cools and picks (Host.cascade_next).
        self.cascade = None
        # Starts the handoff an agent asked for in the turn that just ended
        # (app.start_pending_handoff).
        self.start_pending_handoff = None
        self.journal = None
        self.catalogs = None
        # One cgroup per session, when the unit is delegated (cgroups.py); the
        # app sets it at boot.
        self.cgroups: SessionCgroups | None = None
        # The multi-user mode (users.py), and who a principal is; the app sets
        # them at boot from its tokens.
        self.multi_user = False
        # tokens.TokenStore.acts_for: the user a principal's processes run
        # as, raising PermissionError when nothing may run for it.
        self.acts_for: Callable[[str | None], str | None] = lambda name: None
        self.role_of: Callable[[str | None], str | None] = lambda name: None
        # Threads that open files as a session's user (as_user.py).
        self.runners = Runners()
        # What boot decided about the turns the last run's end cut (restarts.py):
        # each continued session, and whether that end was a crash; the ones a
        # person must continue.
        self.continuing: dict[str, bool] = {}
        self.declined: set[str] = set()
        self._prev = restarts.CLEAN

    # -- the Host a session asks ----------------------------------------------
    def spawn_args(self, session: Session) -> tuple[tuple[str, str] | None, str | None]:
        priming = session.spec.priming
        if self.mcp_url is None or self.tokens is None:
            return None, priming
        from .browsers import available
        from .mcp import primer

        prompt = primer(
            session,
            self.server_name,
            self.roots.config_root,
            browsers=not self.multi_user and available(),
        )
        if priming:
            prompt += "\n\n" + priming
        return (self.mcp_url, self.tokens.mint(session.log_id)), prompt

    def user_for(self, spec: SpawnSpec) -> str | None:
        """The Linux user a session's process runs as. In multi-user mode a
        member's session made before the mode has none, and its transcript
        lives in the server's home: it may not start as the server instead."""
        if not self.multi_user:
            return None
        if spec.linux_user is not None:
            return spec.linux_user
        # An owner that would run as a user now, or none at all (revoked):
        # its session from before the mode may not start as the server.
        if self.acts_for(spec.owner) is not None:
            raise PermissionError(
                "this session was created before multi-user mode; start a new one"
            )
        return None

    def cgroup(self, session: Session) -> Path | None:
        return self.cgroups.create(session.log_id) if self.cgroups else None

    def released(self, session: Session) -> None:
        if self.cgroups is not None:
            self.cgroups.release(session.log_id, user=session.spec.linux_user)

    def load_changed(self, log_id: str) -> None:
        """A session's shown load changed: republish its card."""
        if (s := self.sessions.get(log_id)) is not None:
            s._publish_now()

    def cgroup_dir(self, session: Session) -> Path | None:
        """The session's cgroup, or None when the unit is not delegated."""
        return self.cgroups.path(session.log_id) if self.cgroups is not None else None

    def turn_ended(self, session: Session) -> None:
        if self.queues is not None:
            self.queues.turn_ended(session)
        if self.quota is not None:
            self.quota.turn_ended()
        self._retire_predecessor(session)
        self._pending_handoff(session)
        self._close_when_finished(session)
        self._maybe_unquiet(session)
        if self.schedules is not None:
            self.schedules.changed()
        if self.workflows is not None:
            self.workflows.turn_ended(session)
        if self.notices is not None:
            self.notices.turn_ended(session)
        nudges = self.nudges
        if nudges is not None and (silent_s := nudges.timings()[0]) is not None:
            # The silent-turn nudge comes silent_s after the turn's end, not on
            # the next tick; the grace lets a held flush start and the card settle.
            loop = asyncio.get_running_loop()
            loop.call_later(silent_s, lambda: loop.create_task(nudges.check(session)))

    def flush_ended(self, session: Session) -> None:
        """What turn_ended does that reads finished(), which was False then:
        the flush was still in flight (#536)."""
        if self.queues is not None:
            self.queues.turn_ended(session)
        self._close_when_finished(session)
        self._maybe_unquiet(session)
        if self.schedules is not None:
            self.schedules.changed()
        if self.workflows is not None:
            self.workflows.changed()

    def _retire_predecessor(self, new: Session) -> None:
        """After a handoff, archive the old session once the new one has
        answered (#354). Stateless, so a restart in between changes nothing:
        the new session's spec names the old one, the old one's meta names the
        new. Called off, with continued_by cleared, when the new turn failed.
        Nothing else can have used the old session: it refuses messages meanwhile
        (Session.check_handed_off, #874)."""
        old = self.sessions.get(new.spec.continues or "")
        if old is None or old.continued_by != new.log_id:
            return
        # Only a turn that ended with the model's answer counts: a send that
        # failed reaches here with no Result, so no "ended" at all.
        ended = new.standing.get("ended") or {}
        if ended.get("how") not in ("reported", "silent", "command") or old.in_turn:
            self._call_off_handoff(old, new, "its first turn did not finish")
            return
        # The sessions the old one started report to the new one now (#641).
        for kid in self.sessions.values():
            if kid.spec.spawned_by == old.log_id:
                kid.spec = dataclasses.replace(kid.spec, spawned_by=new.log_id)
                self.metas.write(kid.meta())
        self.refresh_card(new.log_id)
        asyncio.get_running_loop().create_task(self._archive(old))

    def _pending_handoff(self, s: Session) -> None:
        """The turn that asked for a handoff ended: start it, or give the
        session back when the turn failed."""
        if s.continued_by != "pending":
            return
        if s.standing.get("turn_error"):
            self.call_off_pending(s, "the turn that asked for it failed")
        elif self.start_pending_handoff is not None:
            asyncio.get_running_loop().create_task(self.start_pending_handoff(s))

    def call_off_pending(self, s: Session, why: str) -> None:
        """A handoff an agent asked for will not start."""
        s.mark_continued(None)
        s.report({"kind": "handoff_off", "handle": "the new session", "why": why})

    def _call_off_handoff(self, old: Session, new: Session, why: str) -> None:
        """The new session will not take over: the old one opens again and its
        transcript says so. Safe to call twice for one failure: the first call
        clears the name the guard reads."""
        if old.continued_by == new.log_id:
            old.mark_continued(None)
            old.report({"kind": "handoff_off", "handle": new.handle, "why": why})

    async def _archive(self, s: Session) -> None:
        try:
            await self.close(s.log_id)
        except Exception:
            log.exception("aegis: could not archive %s after its handoff", s.handle)

    def descendants(self, log_id: str) -> list[Session]:
        """The open sessions ``log_id`` started, transitively, deepest first."""
        out: list[Session] = []
        frontier = {log_id}
        while frontier:
            kids = [s for s in self.sessions.values() if s.spec.spawned_by in frontier]
            out += kids
            frontier = {s.log_id for s in kids}
        return out[::-1]

    def wakers(self, s: Session, *, run: bool = True) -> list[str]:
        """What will still wake ``s`` with a new turn, one phrase each: its
        approved run, monitors, background tasks and callback queue tasks.
        turn_end refuses done while any is live (#608): the task is not over.
        Its children are not here; they do not wake it, and one spawned for the
        person may outlive the task that started it. ``run=False`` leaves the
        run out, for a handoff: a run follows its owner's successor."""
        n_mon = len(self.monitors.of(s.log_id)) if self.monitors is not None else 0
        n_tasks = sum(1 for w in self._waiting_for(s) if w["kind"] == "task")
        counts = (
            (n_mon, "monitor"),
            (len(s.open_tasks), "background task"),
            (n_tasks, "queue task"),
        )
        live = self._running(s) if run else None
        out = [f"workflow {live.run}"] if live is not None else []
        out += [f"{n} {noun}{'' if n == 1 else 's'}" for n, noun in counts if n]
        return out

    def close_blockers(self, s: Session) -> list[str]:
        """What keeps ``s`` and the sessions it started from closing once its
        turn ends done (turn_end's close), one phrase each: its wakers, and
        each descendant still working, waiting, or whose last turn did not end
        done. Held messages are not here: they start a new turn, whose own
        turn_end decides."""
        out = self.wakers(s)
        for kid in self.descendants(s.log_id):
            mark = self._attention(kid)["attention"]
            flow = kid.workflow["run"] if kid.workflow else None
            if (
                flow is not None
                and self.workflows is not None
                and self.workflows.live(flow)
            ):
                out.append(f"{kid.handle} (workflow {flow})")
            elif mark != "done" or not self.finished(kid):
                out.append(f"{kid.handle} ({mark})")
        return out

    def _close_when_finished(self, s: Session) -> None:
        """Archive a session whose turn ended done with close, and the
        sessions it started, deepest first. Stateless, like the handoff: the
        fold drops a report made in an earlier turn, so a later turn without
        close never closes. Anything still running keeps the whole tree open."""
        report = s.standing.get("report") or {}
        if not report.get("close") or report.get("attention") != "done":
            return
        if s.continued_by == "pending":
            return  # its handoff retires it, and its children go to the new one
        if s.standing.get("turn_error") or not self.finished(s):
            return
        if self.close_blockers(s):
            return
        tree = [*self.descendants(s.log_id), s]
        asyncio.get_running_loop().create_task(self._archive_tree(tree))

    async def _archive_tree(self, tree: list[Session]) -> None:
        for s in tree:
            if s.log_id not in self.sessions:
                continue
            try:
                await self.close(s.log_id)
            except Exception:
                log.exception("aegis: could not archive %s on its close", s.handle)

    def recorded(self, session: Session, record: dict, events: list | None) -> None:
        if record.get("kind") == "plan" and self.nudges is not None:
            # Before the plan_update's result reads its notices, so it never
            # answers with the stale-plan warning it just ended.
            self.nudges.refresh(session)
        if self.journal is not None:
            self.journal.recorded(
                session.log_id,
                session.handle,
                self.store_path(session.log_id),
                record,
                events,
            )

    def exited(self, session: Session, code: int, stderr_tail: list[str]) -> None:
        if self.tokens is not None:
            self.tokens.drop(session.log_id)
        if self.queues is not None:
            self.queues.exited(session, code, stderr_tail)
        # A handoff whose new session died before answering: the old one stays.
        # One that answered has already archived the old one, so this is a no-op.
        old = self.sessions.get(session.spec.continues or "")
        if old is not None:
            self._call_off_handoff(old, session, "its process ended")
        # The turn that asked for a handoff will never end now.
        if session.continued_by == "pending":
            self.call_off_pending(session, "the session's process ended")
        # A process that ends on its own failed: whatever its card says, the
        # person should see it (#386). A SIGTERM is most likely the server's
        # own stop under systemd, and the next boot continues the session.
        if session.quiet and code not in restarts.SIGTERM_CODES:
            self.set_quiet(session.log_id, False)
        if self.workflows is not None:
            self.workflows.exited(session, code)

    def successor(self, old: Session) -> Session | str | None:
        """The live session continuing ``old`` after a handoff: "starting"
        while it spawns, the open session it names, or None when the name is
        stale (the new session was closed, or a crash left it behind);
        "pending" while the agent that asked ends its turn; ``handle@server``
        for the moment before a handoff across a link archives it."""
        c = old.continued_by
        if c in ("starting", "pending") or (c and "@" in c):
            return c
        return self.sessions.get(c) if c else None

    def successor_name(self, session: Session) -> str | None:
        """The handle continuing ``session`` after a handoff, or None."""
        next_ = self.successor(session)
        if next_ is None:
            return None
        if isinstance(next_, str):
            return next_ if "@" in next_ else "a new session"
        return next_.handle

    def workflow_lock(self, session: Session) -> str | None:
        """A node's session is the workflow's while its run is live: nobody
        types into it, interrupts it or hands it off but the engine (spec §05).
        The reason comes from the run (workflows.Workflows.lock)."""
        return None if self.workflows is None else self.workflows.lock(session)

    def cascade_next(
        self, session: Session, text: str
    ) -> tuple[str | None, float] | None:
        return None if self.cascade is None else self.cascade(session, text)

    def notice_blocks(self, session: Session, at: str) -> str:
        blocks = "" if self.notices is None else self.notices.pending(session, at)
        menus = "" if self.know_how is None else self.know_how.pending(session)
        return "\n\n".join(b for b in (blocks, menus) if b)

    def know_how_touched(self, session: Session, tool_input: dict) -> None:
        if self.know_how is not None:
            self.know_how.touched(session, tool_input)

    async def know_how_ready(self, session: Session) -> None:
        # An extra, as the hint is: whatever breaks in it, the send still goes.
        if self.know_how is not None:
            try:
                await self.know_how.ready(session)
            except Exception:
                log.exception("know-how failed for %s", session.handle)

    def knowledge_native(self, owner: str | None) -> bool:
        return self.native_of(owner)

    def knowledge_hint(self, session: Session, text: str) -> str:
        if self.knowledge is None or not hint.wanted(text):
            return ""
        # A hint is an extra: whatever breaks in it, the send still goes out.
        try:
            return self.knowledge.hint_for(
                self.principal_of(session), session.log_id, text, str(session.spec.cwd)
            )
        except Exception as e:
            cause = f"{type(e).__name__}: {e}"
            if cause not in self._hint_failures:
                self._hint_failures.add(cause)
                log.exception("knowledge hint failed, sending without it: %s", cause)
            return ""

    def compacted(self, session: Session) -> None:
        if self.notices is not None:
            self.notices.forget(session.log_id)
        if self.know_how is not None:
            self.know_how.reset(session)

    def sign_in_failed(self, session: Session) -> None:
        if self.sign_in is not None:
            self.sign_in.failed(session)

    def rate_limit(self, session: Session, windows: dict) -> None:
        # A member's stream reports the member's account, not the server's.
        if self.quota is not None and session.spec.linux_user is None:
            self.quota.observe(windows)

    def quota_hold(self, session: Session) -> str | None:
        """A paused session waits for its gate to open; any other may start a
        turn unless its owner is stopped, or closed and the harness would
        have to start (quota_gate.py), or every session is paused (pause.py)."""
        if self.pause_hold is not None and (why := self.pause_hold(session)):
            return why
        if session.quota_paused:
            return session.quota_paused
        if self.gate is None:
            return None
        s = session.spec
        g = self.gate(s.owner, s.harness, s.model)
        if g.state == "stopped" or (g.state == "closed" and not session.running):
            return g.why or "the quota gate is closed"
        return None

    def card(self, session: Session) -> dict:
        return {
            "monitors": self.monitors.card(session.log_id)
            if self.monitors is not None
            else [],
            **self._attention(session),
            "waiting_for": self._waiting_for(session),
            **self._worker(session),
            "layout": session.standing.get("layout"),
            # CPU in cores over the last minute and RSS, with what it started.
            "load": self.load.of(session.log_id) if self.load is not None else None,
            "cache_cold": cache.cold_policy(session.spec.harness, self.context()),
            # Only a live successor counts; an archived session keeps its own.
            "continued_by": session.continued_by
            if session.archived or self.successor(session) is not None
            else None,
        }

    def _worker(self, s: Session) -> dict:
        """A worker's wire field with its task's status, so the Fleet can set a
        finished worker (one reopened from the archive, say) apart from a live one."""
        if not s.worker:
            return {}
        t = self.queues.tasks.get(s.worker["task_id"]) if self.queues else None
        return {"worker": {**s.worker, "status": t.status if t else "gone"}}

    # The attentions that end a quiet session's quiet (#386). A worker's
    # result goes to its enqueuer and the worker is archived, so only its
    # error is the person's.
    BACK = frozenset({"done", "review", "needs_you", "error"})

    def _maybe_unquiet(self, s: Session) -> None:
        if not s.quiet:
            return
        kind = self._attention(s)["attention"]
        # A worker done with its task is archived at once: its error goes to
        # the enqueuer, and a tab would only flash while it stops.
        if s.worker and self.queues is not None and self.queues.finished(s):
            return
        # A workflow node is the engine's until its run ends (workflows.py).
        held = bool(s.workflow) and (
            self.workflows is not None and self.workflows.live(s.workflow["run"])
        )
        if kind == "error" or (not s.worker and not held and kind in self.BACK):
            self.set_quiet(s.log_id, False)

    def _running(self, s: Session):
        """The approved run ``s`` is the main agent of, until it ends."""
        st = self.workflows.of_owner(s.log_id) if self.workflows is not None else None
        return st if st is not None and st.status == "running" else None

    def attention_of(self, s: Session) -> str:
        """What the session's card says it waits on: working, needs_you, ..."""
        return self._attention(s)["attention"]

    def _attention(self, s: Session) -> dict:
        last = s.standing.get("last_message")
        run = self._running(s)
        return attention.card(
            s.standing,
            working=s.in_turn,
            worker=bool(s.worker),
            waits=self._waits(s),
            last_unread=bool(last) and last in s.unread,
            # Approving the run answered the ask that armed it.
            answered_at=run.started if run is not None else None,
        )

    def _waiting_for(self, s: Session) -> list[dict]:
        """The sessions ``s`` waits on, by name: the worker of each callback
        task it enqueued (none yet while the task is pending), and each child
        it spawned that is still working or waiting."""
        tasks = [
            {
                "kind": "task",
                "task_id": t.id,
                "queue": t.queue,
                "status": t.status,
                "key": t.worker if t.worker in self.sessions else None,
                "handle": self.sessions[t.worker].handle
                if t.worker in self.sessions
                else None,
            }
            for t in (self.queues.tasks.values() if self.queues is not None else ())
            if t.enqueuer == s.log_id
            and t.callback
            and t.status in ("pending", "running")
        ]
        kids = [
            {"kind": "session", "key": o.log_id, "handle": o.handle, "status": a}
            for o in self.sessions.values()
            # transitive: a child waiting on its own monitor, task or child counts
            if o.spec.spawned_by == s.log_id
            and (a := self._attention(o)["attention"]) in ("working", "waiting")
        ]
        return tasks + kids

    def _waits(self, s: Session) -> list[str]:
        """What a session waits on that is not a person, one phrase each: the
        approved run it is the main agent of, by name, and the sessions it
        waits on by handle, the first two and then a count."""
        n_mon = len(self.monitors.of(s.log_id)) if self.monitors is not None else 0
        quota = s.quota_paused or (
            any(m.get("held_for") == "quota" for m in s.held) and self.quota_hold(s)
        )
        whom = self._waiting_for(s)
        names = [w["handle"] for w in whom if w["handle"]]
        if len(names) > 2:
            names = [*names[:2], f"+{len(names) - 2}"]
        n_pending = sum(1 for w in whom if not w["handle"])
        counts = (
            (n_mon, "monitor"),
            (len(s.open_tasks), "background task"),
            (len(s.held), "held message"),
            (n_pending, "pending queue task"),
        )
        waits = [f"{n} {noun}{'' if n == 1 else 's'}" for n, noun in counts if n]
        # A run waits on its own clock and commands too, not only on the
        # node sessions named above; the engine wakes its main agent.
        run = self._running(s)
        flow = [f"workflow {run.run}"] if run is not None else []
        return flow + names + waits + ([f"quota ({quota})"] if quota else [])

    def status_changed(self, session: Session) -> None:
        parent = session.spec.spawned_by
        if parent and parent in self.sessions:
            self.refresh_card(parent)
        # The enqueuer's card names its worker (_waiting_for).
        t = self.queues.task_of_worker(session.log_id) if self.queues else None
        if t is not None and t.enqueuer in self.sessions:
            self.refresh_card(t.enqueuer)
        if self.schedules is not None:
            self.schedules.changed()
        if self.workflows is not None:
            self.workflows.changed()
        if self.notices is not None:
            self.notices.set(session.log_id, HELD_SOURCE, held_items(session.held))

    def finished(self, s: Session) -> bool:
        """Done working, which a turn ending is not: not busy (held messages
        included), not paused by the quota gate, no live monitor, no open
        Claude task, and no child session still working or waiting. A queue's
        task and a schedule's run both end on it."""
        return (
            not s.busy
            and not s.quota_paused
            and not (self.monitors is not None and self.monitors.of(s.log_id))
            and not s.open_tasks
            and not any(
                o.spec.spawned_by == s.log_id
                and self._attention(o)["attention"] in ("working", "waiting")
                for o in self.sessions.values()
            )
        )

    def harness_versions(self) -> dict | None:
        return self.version_policy()

    def catalog_ready(self, session: Session, catalog) -> None:
        if self.catalogs is not None:
            self.catalogs.put(session.spec.harness, session.spec.cwd, catalog)

    def refresh_card(self, log_id: str) -> None:
        s = self.sessions.get(log_id)
        if s is not None:
            s._publish_now()
            self.status_changed(s)

    def by_handle(self, handle: str) -> Session | None:
        return next((s for s in self.sessions.values() if s.handle == handle), None)

    # -- paths ---------------------------------------------------------------
    def store_path(self, log_id: str) -> Path:
        return self.roots.state_root / "transcripts" / f"{log_id}.jsonl"

    def _stderr_path(self, log_id: str) -> Path:
        return self.roots.state_root / "stderr" / f"{log_id}.log"

    def _open_handles(self, *, except_id: str | None = None) -> set[str]:
        """The handles a session may not take: an archived session's is free
        to rename to (#673)."""
        return {s.handle for i, s in self.sessions.items() if i != except_id}

    def _handles(self) -> set[str]:
        """Every handle, archived ones included, for minting a fresh one."""
        return self._open_handles() | {
            m["handle"] for m in self.archived.values() if m.get("handle")
        }

    # -- building sessions -------------------------------------------------------
    def _session(self, meta: dict) -> Session:
        return Session(
            log_id=meta["log_id"],
            spec=SpawnSpec.from_record(meta, self.roots.harness_cwd),
            handle=meta["handle"],
            store=Store(self.store_path(meta["log_id"])),
            stderr_path=self._stderr_path(meta["log_id"]),
            claude_bin=self._claude_bin,
            opencode_bin=self._opencode_bin,
            codex_bin=self._codex_bin,
            cursor_bin=self._cursor_bin,
            publish=self._publish,
            metas=self.metas,
            title=meta.get("title") or "",
            title_set=bool(meta.get("title_set")),
            handle_set=bool(meta.get("handle_set", True)),
            resume_id=meta.get("resume_id") or meta.get("claude_session_id"),
            created_at=meta.get("created_at"),
            last_activity=meta.get("last_activity"),
            last_status=meta.get("last_status") or "asleep",
            asleep_why=meta.get("asleep_why"),
            parked_rss=meta.get("parked_rss") or 0,
            parked_idle_s=meta.get("parked_idle_s") or 0,
            cost_usd=meta.get("cost_usd"),
            context_tokens=meta.get("context_tokens"),
            context_window=meta.get("context_window"),
            activity=meta.get("activity") or "",
            model_id=meta.get("model_id"),
            interrupt_timeout=self._interrupt_timeout,
            host=self,
            held=meta.get("held"),
            worker=meta.get("worker"),
            schedule=meta.get("schedule"),
            workflow=meta.get("workflow"),
            standing=meta.get("standing"),
            unread=meta.get("unread"),
            last_read_at=meta.get("last_read_at"),
            recap_cost_usd=meta.get("recap_cost_usd") or 0.0,
            btw_cost_usd=meta.get("btw_cost_usd") or 0.0,
            last_exit=meta.get("last_exit"),
            restart_continues=meta.get("restart_continues") or 0,
            quota_paused=meta.get("quota_paused"),
            notices_sent=meta.get("notices_sent"),
            know_how_sent=meta.get("know_how_sent"),
            items_sent=meta.get("items_sent"),
            skills=meta.get("skills"),
            skill_runs=meta.get("skill_runs"),
            tool_calls=meta.get("tool_calls") or 0,
            cache_recent=meta.get("cache_recent"),
            compactions=meta.get("compactions") or 0,
            # "starting" names no session: a crash mid-handoff left it behind.
            continued_by=None
            if meta.get("continued_by") == "starting"
            else meta.get("continued_by"),
            handoff_pending=meta.get("handoff_pending"),
            quiet=bool(meta.get("quiet")),
            autoclose=bool(meta.get("autoclose")),
            checked_version=meta.get("checked_version"),
            # The meta's {"models", "at", "exhausted"}; a spawn record's list
            # has no exhausted.
            cascade_exhausted=isinstance(c := meta.get("cascade"), dict)
            and bool(c.get("exhausted")),
        )

    def boot(self, prev: restarts.Previous = restarts.CLEAN) -> None:
        self._prev = prev
        metas, broken = self.metas.read_all()
        known = {m["log_id"] for m in metas}
        to_rebuild = [self.store_path(p.stem) for p in broken]
        tdir = self.roots.state_root / "transcripts"
        if tdir.is_dir():
            to_rebuild += [
                p
                for p in sorted(tdir.glob("*.jsonl"))
                if p.stem not in known and p not in to_rebuild
            ]
        for m in sorted(metas, key=lambda m: m.get("created_at") or 0):
            self._admit(m)
        for store in to_rebuild:
            m = rebuild(store)
            if m is None:
                log.warning(
                    "aegis: skipping %s: no meta and nothing to rebuild it from", store
                )
                continue
            m["handle"] = mint_handle(self._handles())
            self.metas.write(m)
            self._admit(m)

    def _admit(self, meta: dict) -> None:
        if not meta.get("handle") or (
            not meta.get("archived") and meta["handle"] in self._open_handles()
        ):
            meta["handle"] = mint_handle(self._handles())
        if meta.get("archived"):
            self.archived[meta["log_id"]] = meta
            return
        if not self._prev.clean and restarts.end_leftover(
            meta.get("pid"), meta.get("pid_started"), user=meta.get("linux_user")
        ):
            log.warning(
                "aegis: ended the harness a crash left behind for %s", meta["handle"]
            )
        s = self._session(meta)
        self.sessions[s.log_id] = s
        v = restarts.verdict(meta, self._prev)
        if v is None:
            return
        if meta.get("last_status") == "working" or not v.go:
            rec: dict = {"kind": "server_stopped"}
            if v.crash:
                rec["crash"] = True
            if not v.go:
                rec["declined"] = restarts.DECLINED
            s._record(rec)
        s.last_status = "asleep"
        if v.go:
            self.continuing[s.log_id] = v.crash
        else:
            self.declined.add(s.log_id)
        self.metas.write(s.meta())

    async def continue_after_boot(self) -> None:
        """Give back what the last run's end cut. A continued session gets one
        message saying so, ahead of what it was holding, in one turn; a session
        that was only holding messages sends them. A declined one keeps its
        messages for a person: sending them would start the turn the guard
        refused. A session that cannot start keeps its messages too, and the
        others still go."""
        for log_id, s in list(self.sessions.items()):
            crash = self.continuing.get(log_id)
            if crash is None and (not s.held or log_id in self.declined):
                continue
            msgs = list(s.held)
            if crash is not None:
                body = restarts.CRASHED if crash else restarts.RESTARTED
                msgs.insert(
                    0,
                    {"header": restarts.header(crash), "body": body, "ts": time.time()},
                )
                s.restart_continues += 1
            s.held = msgs
            if s.in_turn:
                # A turn started first (a monitor's wake): the messages go out
                # when it ends, like any held message, never into it.
                self.metas.write(s.meta())
                continue
            if self.quota_hold(s) is not None:
                # Through the gate: they wait for the owner's quota to open
                # (app.on_quota), and a paused session stays paused.
                s.held = [m | {"held_for": "quota"} for m in msgs]
                self.metas.write(s.meta())
                continue
            try:
                await s._flush_held()
            except Exception:
                # _flush_held empties held before it sends: put the batch back.
                log.exception(
                    "aegis: could not continue %s after the restart", s.handle
                )
                s.held = msgs
                self.metas.write(s.meta())
                # Not continued after all: a worker's queue tries, and fails
                # its task when it cannot either.
                self.continuing.pop(log_id, None)

    # -- operations ----------------------------------------------------------------
    def open_sessions(self) -> list[Session]:
        return sorted(self.sessions.values(), key=lambda s: s.created_at)

    def open(self, log_id: str) -> Session:
        s = self.sessions.get(log_id)
        if s is not None:
            return s
        if log_id in self.archived:
            raise OpError("archived", f"{log_id} is archived; reopen it first")
        raise OpError("no_session", f"no session {log_id!r}")

    def transcript(self, log_id: str, since: int | None = None):
        """A snapshot function for any session's transcript, archived included."""
        s = self.sessions.get(log_id)
        if s is not None:
            return lambda: s.snapshot(since)
        if log_id in self.archived:
            return lambda: self._archived_fold(log_id).snapshot(since)
        return None

    def detail(self, log_id: str, ids: list[str]) -> list[dict]:
        """The whole entries ``ids`` name, for rows the wire sent without
        their detail (transcript/wire.py); unknown ids are left out."""
        fold = self._fold(log_id)
        return [e for e in map(fold.entry, ids) if e is not None]

    def search(self, log_id: str, q: str) -> list[str]:
        """The ids of the entries whose withheld detail holds ``q``, ignoring
        case: the part of a transcript the browser cannot search itself."""
        q = q.lower()
        return [
            e["id"]
            for e in self._fold(log_id).entries()
            if q in withheld_text(e).lower()
        ]

    def _fold(self, log_id: str) -> Fold:
        s = self.sessions.get(log_id)
        if s is not None:
            return s.fold()
        if log_id in self.archived:
            return self._archived_fold(log_id)
        raise OpError("no_session", f"no session {log_id!r}")

    def output(self, log_id: str, entry_id: str) -> str | None:
        """A tool row's whole output, which its entry holds only the tail of;
        None when the store has no output for it."""
        if log_id not in self.sessions and log_id not in self.archived:
            raise OpError("no_session", f"no session {log_id!r}")
        path = self.store_path(log_id)
        return tool_output(read_store(path)[0], entry_id) if path.exists() else None

    def archived_entries(self, log_id: str) -> list[dict]:
        """An archived session's transcript entries, read from its store."""
        return self._archived_fold(log_id).entries()

    def _archived_fold(self, log_id: str) -> Fold:
        path = self.store_path(log_id)
        return fold_records(read_store(path)[0]) if path.exists() else Fold()

    async def spawn(
        self,
        spec: SpawnSpec,
        worker: dict | None = None,
        title: str = "",
        quiet: bool = False,
        schedule: dict | None = None,
        workflow: dict | None = None,
        launch_by: str = "agent",
    ) -> Session:
        log_id = mint_log_id()
        if self.multi_user:
            try:
                spec = users.place(
                    spec,
                    root=self.roots.harness_cwd,
                    role=self.role_of(spec.owner),
                    linux_user=self.acts_for(spec.owner),
                )
            except PermissionError as e:
                raise OpError("no_linux_user", str(e)) from None
        if self.knowledge_native(spec.owner):
            spec = dataclasses.replace(spec, knowledge_native=True)
        s = self._session(
            {
                "log_id": log_id,
                "handle": mint_handle(self._handles()),
                "handle_set": False,
            }
            | spec.record()
            | {
                "created_at": time.time(),
                "worker": worker,
                "title": title,
                "quiet": quiet,
                "schedule": schedule,
                "workflow": workflow,
            }
        )
        self.sessions[log_id] = s
        self._publish("sessions", [{"upsert": s.wire()}])
        s.launch_by = launch_by
        try:
            await s.start()
        except (OSError, TimeoutError):
            del self.sessions[log_id]
            s.store.close()
            self.store_path(log_id).unlink(missing_ok=True)
            self.metas.path(log_id).unlink(missing_ok=True)
            self._publish("sessions", [{"remove": log_id}])
            raise
        finally:
            s.launch_by = "agent"
        return s

    async def close(self, log_id: str) -> None:
        s = self.open(log_id)
        if self.monitors is not None:
            self.monitors.drop_owner(log_id)
        if self.side_notes is not None:
            self.side_notes.drop(log_id)
        if self.knowledge is not None:
            self.knowledge.forget(log_id)
        if self.know_how is not None:
            self.know_how.drop(log_id)
        if self.skill_servers is not None:
            try:
                await self.skill_servers.stop_session(log_id)
            except Exception:
                log.exception("aegis: %s's skill servers did not stop", log_id)
        await s.stop()
        if (old := self.sessions.get(s.spec.continues or "")) is not None:
            self._call_off_handoff(old, s, "it was closed")
        if self.secrets is not None:
            # Pads and open requests die with the session, and so do the
            # files people provided to it, unless the agent moved them.
            self.secrets.drop(log_id)
            secret_store.remove_all_provided(s.entries())
        s.report({"kind": "close"})
        del self.sessions[log_id]
        s.archived = True
        s.artifacts.flush_all()  # a page's last coalesced write, before the store shuts
        s.store.close()
        meta = s.meta()
        self.metas.write(meta)
        self.archived[log_id] = meta
        # The row the archive gains rides along, so a browser adds it to the
        # archive it shows instead of reading a page again (#525).
        self._publish("sessions", [{"remove": log_id, "archived": _public(meta)}])
        if self.schedules is not None:
            self.schedules.changed()
        if self.workflows is not None:
            self.workflows.changed()

    def reopen(self, log_id: str) -> Session:
        if log_id in self.sessions:
            raise OpError("not_archived", f"{log_id} is open")
        meta = self.archived.get(log_id)
        if meta is None:
            raise OpError("no_session", f"no session {log_id!r}")
        if (c := meta.get("continued_by")) is not None:
            # Its work moved on for good; the transcript stays readable (#874).
            live = self.sessions.get(c)
            who = live.handle if live else self.archived.get(c, {}).get("handle", c)
            raise OpError(
                "handed_off", f"{meta['handle']} was handed off to {who}; open that one"
            )
        del self.archived[log_id]
        meta = {
            **meta,
            "archived": False,
            "last_status": "asleep",
            "asleep_why": "slept",
            "quiet": False,  # a person reopening it wants to see it (#386)
        }
        # An open session may have taken its handle meanwhile (#673).
        handle = free_handle(str(meta["handle"]), self._open_handles())
        renamed = handle != meta["handle"]
        s = self._session({**meta, "handle": handle})
        self.sessions[log_id] = s
        if renamed:
            s.report({"kind": "name", "handle": s.handle, "title": s.title})
        self.metas.write(s.meta())
        self._publish("sessions", [{"upsert": s.wire()}])
        return s

    def rename(self, log_id: str, handle: str | None, title: str | None) -> dict:
        """Set a session's handle and/or title. An open session records a
        ``name`` record in its store when it gets a handle; an archived session
        has no open store, so its rename stays meta-only and the journal does
        not see it."""
        if log_id not in self.sessions and log_id not in self.archived:
            raise OpError("no_session", f"no session {log_id!r}")
        if handle is not None:
            if not valid_handle(handle):
                raise OpError(
                    "bad_handle",
                    "a handle is 2 or 3 lowercase segments joined by hyphens",
                )
            if handle in self._open_handles(except_id=log_id):
                raise OpError("handle_taken", f"{handle} is already a session's handle")
        if title is not None and (len(title) > TITLE_MAX or "\n" in title):
            raise OpError(
                "bad_title", f"a title is one line of at most {TITLE_MAX} characters"
            )
        s = self.sessions.get(log_id)
        if s is not None:
            changes = {
                k: v for k, v in (("handle", handle), ("title", title)) if v is not None
            }
            if title is not None:
                s.title_set = True
            if handle is not None:
                s.handle_set = True
            renamed = handle is not None and handle != s.handle
            s._set(**changes)
            if renamed:
                s.report({"kind": "name", "handle": s.handle, "title": s.title})
                self.status_changed(s)  # the cards that name it by handle
            self.metas.write(s.meta())
            return s.wire()
        meta = self.archived[log_id]
        if handle is not None:
            meta["handle"] = handle
            meta["handle_set"] = True
        if title is not None:
            meta["title"] = title
            meta["title_set"] = True
        self.metas.write(meta)
        return _public(meta)

    def set_quiet(self, log_id: str, quiet: bool) -> dict:
        """Hide a session's tab until its turn ends, or show it again (#386)."""
        s = self.open(log_id)
        attention = s.wire()["attention"]
        if quiet and attention not in ("working", "waiting"):
            raise OpError(
                "not_running",
                f"{s.handle} is {attention}; only a working or waiting session "
                "can be quiet, since only its turn's end brings it back",
            )
        s._set(quiet=quiet)
        self.metas.write(s.meta())
        return s.wire()

    def set_autoclose(self, log_id: str, on: bool) -> dict:
        """Turn a session's autoclose on or off (#691): while on, its agent
        may end only done with close, review or needs_you."""
        s = self.open(log_id)
        s._set(autoclose=on)
        self.metas.write(s.meta())
        return s.wire()

    def archive(
        self,
        query: str | None,
        limit: int,
        after: Position | None,
        workers: bool = True,
        keep: Callable[[dict], bool] | None = None,
    ) -> tuple[list[dict], int, Position | None]:
        """A page of the archive, newest first, after ``after`` (archive.py),
        of the metas ``keep`` passes."""
        metas = [m for m in self.archived.values() if keep is None or keep(m)]
        items, total, last = archive_page(metas, query, limit, after, workers)
        return [_public(m) for m in items], total, last

    def archived_workers(
        self, query: str | None, keep: Callable[[dict], bool] | None = None
    ) -> int:
        """How many archived queue workers match ``query``, of those ``keep``
        passes."""
        metas = [m for m in self.archived.values() if keep is None or keep(m)]
        return count_workers(metas, query)

    async def shutdown(self) -> None:
        for s in list(self.sessions.values()):
            await s.shutdown()
        self.metas.flush()
