"""Every session on the server: the open ones as Sessions, the archived ones as
their meta.

Boot reads only meta files. A missing or damaged one is rebuilt from its store
and the session lands in the archive; a store nothing can be rebuilt from is
skipped with a log line. Boot writes nothing to any store, with one exception:
a session whose meta says it was ``working`` gets one ``server_stopped`` record,
and its meta is rewritten so the next boot does not add another.

Handles are unique across every session, archived ones included, so a handle
names one conversation for as long as the server keeps it.
"""

from __future__ import annotations

import logging
import secrets
import time
from collections.abc import Callable
from pathlib import Path

from .archive import Position, page as archive_page
from . import attention
from .harness import harness_for
from .meta import MetaStore, rebuild
from .names import TITLE_MAX, mint_handle, valid_handle
from .ops import OpError
from .roots import Roots
from .session import Host, Session, SpawnSpec
from .transcript.entries import Fold, fold_records, tool_output
from .transcript.store import Store, read_store
from .transcript.wire import withheld_text

log = logging.getLogger("aegis.registry")
Publish = Callable[[str, list[dict]], None]


def mint_log_id() -> str:
    return f"{time.strftime('%Y%m%d-%H%M%S')}-{secrets.token_hex(3)}"


def _public(meta: dict) -> dict:
    """A stored meta as a browser may see it: the agent's priming and its raw
    standing stay on the server, like ``Session.wire`` keeps them. It carries
    the harness's label as the wire does, for the side panel's model line."""
    out = {k: v for k, v in meta.items() if k not in ("priming", "standing")}
    harness = harness_for(
        meta.get("harness") or "claude-code", "claude", "opencode", "codex"
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
    ) -> None:
        self.roots = roots
        self._publish = publish
        self._claude_bin = claude_bin
        self._opencode_bin = opencode_bin
        self._codex_bin = codex_bin
        self._interrupt_timeout = interrupt_timeout
        self.metas = MetaStore(roots.state_root / "sessions")
        self.sessions: dict[str, Session] = {}
        self.archived: dict[str, dict] = {}
        # Set by the app when it wires the MCP endpoint, monitors and queues.
        self.mcp_url: str | None = None
        self.tokens = None
        self.server_name = "aegis"
        self.monitors = None
        self.queues = None
        self.quota = None
        self.catalogs = None

    # -- the Host a session asks ----------------------------------------------
    def spawn_args(self, session: Session) -> tuple[tuple[str, str] | None, str | None]:
        priming = session.spec.priming
        if self.mcp_url is None or self.tokens is None:
            return None, priming
        from .mcp import primer

        prompt = primer(session, self.server_name)
        if priming:
            prompt += "\n\n" + priming
        return (self.mcp_url, self.tokens.mint(session.log_id)), prompt

    def turn_ended(self, session: Session) -> None:
        if self.queues is not None:
            self.queues.turn_ended(session)
        if self.quota is not None:
            self.quota.turn_ended()

    def exited(self, session: Session, code: int, stderr_tail: list[str]) -> None:
        if self.tokens is not None:
            self.tokens.drop(session.log_id)
        if self.queues is not None:
            self.queues.exited(session, code, stderr_tail)

    def card(self, session: Session) -> dict:
        return {
            "monitors": self.monitors.card(session.log_id)
            if self.monitors is not None
            else [],
            **self._attention(session),
            **self._worker(session),
        }

    def _worker(self, s: Session) -> dict:
        """A worker's wire field with its task's status, so the Fleet can set a
        finished worker (one reopened from the archive, say) apart from a live one."""
        if not s.worker:
            return {}
        t = self.queues.tasks.get(s.worker["task_id"]) if self.queues else None
        return {"worker": {**s.worker, "status": t.status if t else "gone"}}

    def _attention(self, s: Session) -> dict:
        last = s.standing.get("last_message")
        return attention.card(
            s.standing,
            working=s.in_turn,
            worker=bool(s.worker),
            waits=self._waits(s),
            last_unread=bool(last) and last in s.unread,
        )

    def _waits(self, s: Session) -> list[str]:
        """What a session waits on that is not a person, one phrase each."""
        n_mon = len(self.monitors.of(s.log_id)) if self.monitors is not None else 0
        n_task = (
            sum(
                1
                for t in self.queues.tasks.values()
                if t.enqueuer == s.log_id
                and t.callback
                and t.status in ("pending", "running")
            )
            if self.queues is not None
            else 0
        )
        n_kid = sum(
            1
            for o in self.sessions.values()
            # transitive: a child waiting on its own monitor, task or child counts
            if o.spec.spawned_by == s.log_id
            and self._attention(o)["attention"] in ("working", "waiting")
        )
        counts = (
            (n_mon, "monitor"),
            (len(s.open_tasks), "background task"),
            (len(s.held), "held message"),
            (n_task, "queue task"),
            (n_kid, "session"),
        )
        return [f"{n} {noun}{'' if n == 1 else 's'}" for n, noun in counts if n]

    def status_changed(self, session: Session) -> None:
        parent = session.spec.spawned_by
        if parent and parent in self.sessions:
            self.refresh_card(parent)

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

    def _handles(self, *, except_id: str | None = None) -> set[str]:
        hs = {s.handle for i, s in self.sessions.items() if i != except_id}
        hs |= {
            m["handle"]
            for i, m in self.archived.items()
            if i != except_id and m.get("handle")
        }
        return hs

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
            publish=self._publish,
            metas=self.metas,
            title=meta.get("title") or "",
            title_set=bool(meta.get("title_set")),
            resume_id=meta.get("resume_id") or meta.get("claude_session_id"),
            created_at=meta.get("created_at"),
            last_activity=meta.get("last_activity"),
            last_status=meta.get("last_status") or "stopped",
            cost_usd=meta.get("cost_usd"),
            context_tokens=meta.get("context_tokens"),
            context_window=meta.get("context_window"),
            activity=meta.get("activity") or "",
            model_id=meta.get("model_id"),
            interrupt_timeout=self._interrupt_timeout,
            host=self,
            held=meta.get("held"),
            worker=meta.get("worker"),
            standing=meta.get("standing"),
            unread=meta.get("unread"),
            last_read_at=meta.get("last_read_at"),
            recap_cost_usd=meta.get("recap_cost_usd") or 0.0,
        )

    def boot(self) -> None:
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
        if not meta.get("handle") or meta["handle"] in self._handles():
            meta["handle"] = mint_handle(self._handles())
        if meta.get("archived"):
            self.archived[meta["log_id"]] = meta
            return
        s = self._session(meta)
        self.sessions[s.log_id] = s
        if meta.get("last_status") == "working":
            s._record({"kind": "server_stopped"})
            s.last_status = "stopped"
            self.metas.write(s.meta())

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

    def _archived_fold(self, log_id: str) -> Fold:
        path = self.store_path(log_id)
        return fold_records(read_store(path)[0]) if path.exists() else Fold()

    async def spawn(
        self, spec: SpawnSpec, worker: dict | None = None, title: str = ""
    ) -> Session:
        log_id = mint_log_id()
        s = self._session(
            {"log_id": log_id, "handle": mint_handle(self._handles())}
            | spec.record()
            | {"created_at": time.time(), "worker": worker, "title": title}
        )
        self.sessions[log_id] = s
        self._publish("sessions", [{"upsert": s.wire()}])
        try:
            await s.start()
        except (OSError, TimeoutError):
            del self.sessions[log_id]
            s.store.close()
            self.store_path(log_id).unlink(missing_ok=True)
            self.metas.path(log_id).unlink(missing_ok=True)
            self._publish("sessions", [{"remove": log_id}])
            raise
        return s

    async def close(self, log_id: str) -> None:
        s = self.open(log_id)
        if self.monitors is not None:
            self.monitors.drop_owner(log_id)
        await s.stop()
        del self.sessions[log_id]
        s.archived = True
        s.artifacts.flush_all()  # a page's last coalesced write, before the store shuts
        s.store.close()
        meta = s.meta()
        self.metas.write(meta)
        self.archived[log_id] = meta
        self._publish("sessions", [{"remove": log_id}])

    def reopen(self, log_id: str) -> Session:
        if log_id in self.sessions:
            raise OpError("not_archived", f"{log_id} is open")
        meta = self.archived.pop(log_id, None)
        if meta is None:
            raise OpError("no_session", f"no session {log_id!r}")
        meta = {**meta, "archived": False, "last_status": "stopped"}
        s = self._session(meta)
        self.sessions[log_id] = s
        self.metas.write(s.meta())
        self._publish("sessions", [{"upsert": s.wire()}])
        return s

    def rename(self, log_id: str, handle: str | None, title: str | None) -> dict:
        if log_id not in self.sessions and log_id not in self.archived:
            raise OpError("no_session", f"no session {log_id!r}")
        if handle is not None:
            if not valid_handle(handle):
                raise OpError(
                    "bad_handle",
                    "a handle is 2 or 3 lowercase segments joined by hyphens",
                )
            if handle in self._handles(except_id=log_id):
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
            s._set(**changes)
            self.metas.write(s.meta())
            return s.wire()
        meta = self.archived[log_id]
        if handle is not None:
            meta["handle"] = handle
        if title is not None:
            meta["title"] = title
            meta["title_set"] = True
        self.metas.write(meta)
        return _public(meta)

    def archive(
        self, query: str | None, limit: int, after: Position | None
    ) -> tuple[list[dict], int, Position | None]:
        """A page of the archive, newest first, after ``after`` (archive.py)."""
        items, total, last = archive_page(self.archived.values(), query, limit, after)
        return [_public(m) for m in items], total, last

    async def shutdown(self) -> None:
        for s in list(self.sessions.values()):
            await s.shutdown()
        self.metas.flush()
