"""The server's terminals (#362): spawn, close, restart, boot and shutdown.

A terminal belongs to the server, not to an agent. It has an owner, the person
who opened it or the owner of the agent that did, and outlives whoever asked
for it. Everything on disk is under ``<state>/terminals/<id>/``, keyed by the
id, never the name, because names are reused: ``meta.json`` and the store.

A terminal is ``running``, ``exited`` with its code, or ``ended`` with a
reason, ``closed`` or ``server_stopped``. A restart of aegis ends every
terminal's process, as it ends every harness. At boot a service (a profile
whose ``restart`` is not ``no``) starts again in the same terminal; any other
stays ended, with its output, until a person restarts it. Closing sends
SIGHUP, then SIGKILL after CLOSE_GRACE_S, and archives the terminal; one
still inside that grace when aegis stops is stopped with the open ones.

The card rides the ``sessions`` channel with ``kind: "term"`` and ``log_id``
set to the id, because the channel's throttle and the client key every card
by ``log_id``. Output rides ``term:<id>``.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import pwd
import secrets
import time
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING

from . import launch
from . import fs_paths
from .ops import OpError
from .term_profiles import argv, login_shell, with_shell
from .term_store import CastStore
from .terminal import Terminal

if TYPE_CHECKING:
    from .cgroups import SessionCgroups

log = logging.getLogger("aegis.terminals")

CLOSE_GRACE_S = 5.0
STOP_GRACE_S = 1.0  # at shutdown, well inside systemd's own stop timeout
COLS, ROWS = 120, 40
# What boot needs in a meta.json before it trusts it; any other is skipped.
META_KEYS = {"id", "name", "profile", "spec", "title", "cwd", "owner",
             "started_by", "cols", "rows", "state", "code", "reason"}  # fmt: skip


def _passwd(user: str) -> pwd.struct_passwd:
    try:
        return pwd.getpwnam(user)
    except KeyError:
        raise PermissionError(f"Linux user {user} does not exist") from None


def mint_term_id() -> str:
    return f"t-{time.strftime('%Y%m%d-%H%M%S')}-{secrets.token_hex(3)}"


class Terminals:
    def __init__(
        self,
        state_root: Path,
        config_root: Path,
        profiles: Callable[[], dict[str, dict]],
        publish: Callable[[str, list[dict]], None],
        cgroups: Callable[[], SessionCgroups | None] = lambda: None,
        acts_for: Callable[[str], str | None] = lambda owner: None,
        role_of: Callable[[str], str | None] = lambda owner: None,
        multi_user: bool = False,
    ) -> None:
        self.dir = state_root / "terminals"
        self.config_root = config_root
        self._profiles = profiles
        self._publish = publish
        self._cgroups = cgroups
        # Who a terminal runs as, in multi-user mode (users.py).
        self._acts_for = acts_for
        self._role_of = role_of
        self._multi_user = multi_user
        self.open: dict[str, Terminal] = {}
        self.archived: dict[str, dict] = {}
        self.closing: dict[str, Terminal] = {}  # closed, their program not yet ended
        self._background: set[asyncio.Task] = set()

    def profiles(self) -> dict[str, dict]:
        return with_shell(self._profiles())

    async def spawn(
        self,
        profile: str,
        owner: str,
        started_by: str | None = None,
        cwd: str | None = None,
    ) -> Terminal:
        """Start a terminal from ``profile``, in the profile's folder or in
        ``cwd``, a folder under the root that Files may open (Terminal here)."""
        p = self.profiles().get(profile)
        if p is None:
            raise OpError("no_profile", f"no terminal profile named {profile!r}")
        if "error" in p:
            raise OpError("bad_profile", f"terminal profile {profile}: {p['error']}")
        try:
            user = self._acts_for(owner)
            if user and cwd is None and self._role_of(owner) == "member":
                # The aegis root is the server's: a member's terminal starts home.
                where = Path(_passwd(user).pw_dir)
            else:
                where = None
        except PermissionError as e:
            raise OpError("no_linux_user", str(e)) from None
        if where is None:
            where = self._cwd(
                p["cwd"] if cwd is None else cwd, under_root=cwd is not None
            )
        tid = mint_term_id()
        meta = {
            "id": tid,
            "name": self._name(profile),
            "profile": profile,
            "spec": p,
            "title": p["cmd"] or login_shell(),
            "cwd": str(where),
            "owner": owner,
            "linux_user": user,
            "started_by": started_by,
            "cols": COLS,
            "rows": ROWS,
            "state": "running",
            "code": None,
            "reason": None,
            "created": time.time(),
            "archived": False,
        }
        t = self._terminal(meta)
        self.open[tid] = t
        try:
            await self._start(t)
        except OpError:
            del self.open[tid]
            raise
        return t

    def _user(self, t: Terminal) -> str | None:
        """The Linux user a terminal runs as: the one recorded when it was
        opened, in multi-user mode. One opened before the mode for an owner
        who now has a user may not start as the server."""
        if not self._multi_user:
            return None
        user = t.meta.get("linux_user")
        if user is None and self._acts_for(t.meta["owner"]) is not None:
            raise PermissionError(
                "this terminal was opened before multi-user mode; open a new one"
            )
        return user

    def _cwd(self, rel: str, under_root: bool) -> Path:
        if under_root:
            try:
                path = fs_paths.resolve(self.config_root, self.dir.parent, rel)
            except OpError as e:
                raise OpError("bad_cwd", str(e)) from None
        else:
            path = (self.config_root / rel).resolve()
        if not path.is_dir():
            raise OpError("bad_cwd", f"{rel} is not a directory")
        return path

    def get(self, tid: str) -> Terminal:
        t = self.open.get(tid)
        if t is None:
            raise OpError("no_terminal", f"no open terminal {tid}")
        return t

    async def close(self, tid: str) -> None:
        t = self.get(tid)
        if t.starting:  # close what the start leaves, never under it
            await t.started()
            t = self.get(tid)
        t.meta.update(
            state="ended", reason="closed", archived=True, closed_at=time.time()
        )
        del self.open[tid]
        self.archived[tid] = t.meta
        self._save(t)
        self._publish("sessions", [{"remove": tid}])
        if t.running:
            self.closing[tid] = t
            task = asyncio.create_task(t.stop(CLOSE_GRACE_S))
            self._background.add(task)
            task.add_done_callback(self._background.discard)
            task.add_done_callback(lambda _: self.closing.pop(tid, None))
        else:
            t.store.close()

    async def restart(self, tid: str) -> None:
        t = self.get(tid)
        if t.running or t.starting:
            raise OpError("running", f"terminal {t.meta['name']} is running")
        await self._start(t)

    def owner_of(self, tid: str) -> str | None:
        meta = t.meta if (t := self.open.get(tid)) else self.archived.get(tid)
        return meta["owner"] if meta else None

    def card(self, t: Terminal) -> dict:
        m = t.meta
        failed = m["state"] == "exited" and m["code"] not in (0, None)
        return {
            "log_id": m["id"],
            "kind": "term",
            "id": m["id"],
            "name": m["name"],
            "handle": m["name"],
            "title": m["title"],
            "model": None,
            "state": m["state"],
            "code": m["code"],
            "reason": m["reason"],
            "mark": "running" if t.running else "failed" if failed else "ended",
            "owner": m["owner"],
            "started_by": m["started_by"],
            "cwd": m["cwd"],
            "pid": t.pid,
            "started_at": m.get("started_at"),
            "cols": m["cols"],
            "rows": m["rows"],
            "restart": m["spec"]["restart"],
            "last_line": t.last_line(),
        }

    def cards(self) -> list[dict]:
        return [self.card(t) for t in self.open.values()]

    def closed(self) -> list[dict]:
        """Closed terminals, newest first, for the archive."""
        keys = ("id", "name", "title", "cwd", "owner", "code", "closed_at")
        rows = [{k: m.get(k) for k in keys} for m in self.archived.values()]
        return sorted(rows, key=lambda r: r["closed_at"] or 0, reverse=True)

    def channel(self, tid: str, since: int | None) -> Callable[[], dict] | None:
        if tid not in self.open and tid not in self.archived:
            return None

        def snapshot() -> dict:
            t = self.open.get(tid) or self.closing.get(tid) or self._archived(tid)
            t.store.load()
            m = t.meta
            data = {"id": tid, "cols": m["cols"], "rows": m["rows"],
                    "rev": t.store.rev, "state": m["state"], "code": m["code"]}  # fmt: skip
            delta = t.store.since(since) if since is not None else None
            if delta is not None:
                return data | {"delta": [_op(e) for e in delta if e[1] != "m"]}
            return data | {"tail": t.store.tail()}

        return snapshot

    async def boot(self) -> None:
        """Load every terminal's meta. The last run's end killed every process,
        so a terminal it left running is ended by the restart, and a service
        starts again in the same terminal."""
        for path in sorted(self.dir.glob("*/meta.json")):
            try:
                await self._boot_one(path)
            except Exception as e:
                log.warning("aegis: skipping terminal %s: %r", path.parent.name, e)

    async def _boot_one(self, path: Path) -> None:
        meta = json.loads(path.read_text())
        if missing := META_KEYS - set(meta):
            raise ValueError(f"meta.json lacks {sorted(missing)}")
        if meta.get("archived"):
            self.archived[meta["id"]] = meta
            return
        service = meta["spec"]["restart"] != "no"
        if meta["state"] == "running":
            meta.update(state="ended", reason="server_stopped")
        t = self._terminal(meta)
        self.open[meta["id"]] = t
        self._save(t)
        if service:
            try:
                await self._start(t)
            except OpError as e:
                log.warning("aegis: service %s did not start: %s", meta["name"], e)

    async def shutdown(self) -> None:
        for task in list(self._background):
            task.cancel()
        running = [t for t in self.open.values() if t.running]
        for t in running:
            t.meta.update(state="ended", reason="server_stopped")
        closing = list(self.closing.values())
        await asyncio.gather(*(t.stop(STOP_GRACE_S) for t in running + closing))
        for t in self.open.values():
            t.store.close()
            self._save(t)

    def _terminal(self, meta: dict) -> Terminal:
        return Terminal(
            meta, CastStore(self.dir / meta["id"]), self._on_output, self._on_exit
        )

    def _archived(self, tid: str) -> Terminal:
        """A read-only terminal over an archived one's files, built for one
        snapshot so it reads what is there now and nothing stays in memory."""
        return self._terminal(self.archived[tid])

    async def _start(self, t: Terminal) -> None:
        spec = t.meta["spec"]
        env = {**os.environ, **spec["env"],
               "TERM": "xterm-256color", "AEGIS_TERMINAL": t.meta["name"]}  # fmt: skip
        cg = self._cgroups()
        cgroup = cg.create(t.id) if cg is not None else None
        try:
            user = self._user(t)
            cmd = argv(spec)
            if user and spec["cmd"] is None:
                cmd = [_passwd(user).pw_shell or "/bin/sh", "-l"]
            # A profile that names a proxy of its own keeps it.
            own = {k.lower() for k in spec["env"]} & {"https_proxy", "http_proxy"}
            via = None if own else launch.resolve_via()
            await t.start(cmd, Path(t.meta["cwd"]), env, cgroup, user=user, via=via)
        except OSError as e:
            if cg is not None:
                cg.release(t.id, user=t.meta.get("linux_user"))
            raise OpError("spawn_failed", f"terminal {t.meta['name']}: {e}") from e
        t.meta.update(state="running", code=None, reason=None, started_at=time.time())
        self._save(t)
        self._card(t)

    def _on_output(self, t: Terminal, ops: list[dict]) -> None:
        self._publish(f"term:{t.id}", ops)
        if not t.meta["archived"]:
            self._card(t)

    def _on_exit(self, t: Terminal) -> None:
        if t.meta["state"] == "running":
            t.meta["state"] = "exited"
        if (cg := self._cgroups()) is not None:
            cg.release(t.id, user=t.meta.get("linux_user"))
        t.store.close()
        self._save(t)
        if not t.meta["archived"]:
            self._card(t)

    def _card(self, t: Terminal) -> None:
        self._publish("sessions", [{"upsert": self.card(t)}])

    def _name(self, base: str) -> str:
        taken = {t.meta["name"] for t in self.open.values()}
        name, n = base, 2
        while name in taken:
            name, n = f"{base}-{n}", n + 1
        return name

    def _save(self, t: Terminal) -> None:
        d = self.dir / t.id
        d.mkdir(parents=True, exist_ok=True)
        tmp = d / "meta.json.tmp"
        tmp.write_text(json.dumps(t.meta))
        os.replace(tmp, d / "meta.json")


def _op(e: tuple[int, str, str]) -> dict:
    rev, code, data = e
    if code == "o":
        return {"rev": rev, "o": data}
    cols, rows = data.split("x")
    return {"rev": rev, "r": [int(cols), int(rows)]}
