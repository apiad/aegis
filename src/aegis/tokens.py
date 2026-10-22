"""Who may use this server: one token per principal.

The token in ``<state>/token`` is the admin's, named after the server's owner
(the Unix user ``aegis serve`` runs as), so a server with one token behaves as
it always has. Every other token lives in ``<state>/tokens.json``, mode 0600,
as a SHA-256 digest and never in clear. A principal is a name and a role; the
role decides what it may change (ops.py), and a session's owner is a
principal's name. Revoking the admin token writes a new one, so a server is
never left without an admin.

The file is read again whenever its mtime changes, so a hand edit or
``aegis token`` applies at the next call. An edit that does not parse is
logged and the last good state kept. Keys this module does not know survive
every write. A policy (quota_gate.py) is set through ``set_policy`` or
``set_default_policy``, which refuse one ``quota_gate.validate`` rejects; a
hand-edited one that does not validate closes the pools it names wrongly.

In multi-user mode (users.py) an entry also names the Linux user its sessions
run as, ``linux_user``, checked when the token is made and again at each
sign-in: a member whose account is gone, or that never had one, is refused
with a log line rather than run as the server. Outside the mode the field is
ignored.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
import pwd
import re
import secrets
import time
from pathlib import Path

from . import quota_gate, users
from .ops import OpError, Principal

log = logging.getLogger("aegis.tokens")

ROLES = ("admin", "member")
NAME = re.compile(r"^[a-z][a-z0-9-]{0,31}$")
FILE = "tokens.json"


def load_or_create_token(state_root: Path) -> str:
    """The admin's token, kept in ``<state>/token`` with mode 0600 and reused
    across restarts so an open tab survives one."""
    path = state_root / "token"
    if path.exists():
        return path.read_text().strip()
    state_root.mkdir(parents=True, exist_ok=True)
    token = secrets.token_urlsafe(32)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(token + "\n")
    return token


def _digest(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def _write_private(path: Path, text: str) -> None:
    tmp = path.with_name(path.name + ".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(text)
    tmp.replace(path)


class TokenStore:
    def __init__(
        self,
        state_root: Path,
        owner: str,
        *,
        multi_user: bool = False,
        server_uid: int | None = None,
    ) -> None:
        self.root = state_root
        self.owner = owner
        self.multi_user = multi_user
        self.server_uid = os.getuid() if server_uid is None else server_uid
        # The member tokens already logged as refused, so a browser retrying
        # does not fill the log.
        self._told: set[str] = set()
        self.path = state_root / FILE
        self._admin = load_or_create_token(state_root)
        self._admin_mtime = self._mtime(state_root / "token")
        self._doc: dict = {"tokens": []}
        self._mtime_seen: int | None = None
        # When each principal last authenticated, since this server started.
        self._used: dict[str, float] = {}
        self._fresh()

    @staticmethod
    def _mtime(p: Path) -> int | None:
        try:
            return p.stat().st_mtime_ns
        except OSError:
            return None

    def _fresh(self) -> None:
        m = self._mtime(self.root / "token")
        if m != self._admin_mtime:
            self._admin = load_or_create_token(self.root)
            self._admin_mtime = m
        m = self._mtime(self.path)
        if m == self._mtime_seen:
            return
        self._mtime_seen = m
        if m is None:
            self._doc = {"tokens": []}
            return
        try:
            doc = json.loads(self.path.read_text())
            if not isinstance(doc, dict) or not isinstance(doc.get("tokens", []), list):
                raise ValueError("not an object with a tokens list")
        except (OSError, ValueError) as e:
            log.warning(
                "aegis: %s does not parse (%s); keeping the last good tokens",
                self.path,
                e,
            )
            return
        doc.setdefault("tokens", [])
        self._doc = doc

    def _entries(self) -> list[dict]:
        return [e for e in self._doc["tokens"] if isinstance(e, dict) and e.get("name")]

    def _save(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        _write_private(self.path, json.dumps(self._doc, indent=2) + "\n")
        self._mtime_seen = self._mtime(self.path)

    # -- reading --------------------------------------------------------------
    def resolve(self, token: object) -> Principal | None:
        """The principal ``token`` is, or None."""
        if not isinstance(token, str) or not token:
            return None
        self._fresh()
        if hmac.compare_digest(token, self._admin):
            who = Principal(self.owner, "admin")
        else:
            d = _digest(token)
            e = next(
                (
                    e
                    for e in self._entries()
                    if hmac.compare_digest(d, str(e.get("sha256", "")))
                ),
                None,
            )
            if e is None:
                return None
            role = str(e.get("role")) if e.get("role") in ROLES else "member"
            if self.multi_user and role == "member" and not self._user_ok(e):
                return None
            who = Principal(str(e["name"]), role)
        self._used[who.name] = time.time()
        return who

    def role_of(self, name: str) -> str | None:
        """A principal's role by name; None for one that no longer exists."""
        if name == self.owner:
            return "admin"
        self._fresh()
        e = next((e for e in self._entries() if e["name"] == name), None)
        if e is None:
            return None
        return e.get("role") if e.get("role") in ROLES else "member"

    def _user_ok(self, e: dict) -> bool:
        """Whether a member entry names a Linux user that exists; logs once
        per token when not."""
        name, user = str(e["name"]), e.get("linux_user")
        if not user:
            why = "has no Linux user; revoke it and add it again with --linux-user"
        else:
            try:
                pwd.getpwnam(str(user))
                return True
            except KeyError:
                why = f"names Linux user {user}, which does not exist; refusing it"
        if name not in self._told:
            self._told.add(name)
            log.warning("aegis: token %s %s", name, why)
        return False

    def linux_user_of(self, name: str | None) -> str | None:
        """The Linux user ``name``'s sessions run as; None outside the mode,
        for the server's owner and for an admin made without one."""
        if not self.multi_user or name is None or name == self.owner:
            return None
        self._fresh()
        e = self._entry(name)
        user = e.get("linux_user") if e else None
        return str(user) if user else None

    def missing_users(self) -> list[tuple[str, str]]:
        """(token, Linux user) for each member token whose user does not
        exist, in the mode."""
        if not self.multi_user:
            return []
        self._fresh()
        out = []
        for e in self._entries():
            user = e.get("linux_user")
            if e.get("role", "member") != "admin" and user:
                try:
                    pwd.getpwnam(str(user))
                except KeyError:
                    out.append((str(e["name"]), str(user)))
        return out

    def _entry(self, name: str) -> dict | None:
        return next((e for e in self._entries() if e["name"] == name), None)

    def default_policy(self) -> dict | None:
        self._fresh()
        p = self._doc.get("default_policy")
        return p if isinstance(p, dict) else None

    def policy_of(self, name: str) -> dict | None:
        """The policy that gates ``name`` (quota_gate.py): its own, else the
        default. None for an admin, who is never gated. A member with
        neither, and a principal whose token was revoked, get ``{}``: every
        pool closed."""
        if self.role_of(name) == "admin":
            return None
        e = self._entry(name)
        if e is None:
            return {}
        own = e.get("policy")
        if isinstance(own, dict):
            return own
        return self.default_policy() or {}

    def _check_policy(self, policy: object) -> None:
        if errs := quota_gate.validate(policy):
            raise OpError("bad_policy", "; ".join(errs))

    def set_policy(self, name: str, policy: dict | None) -> None:
        """Give ``name`` its own policy; None makes it use the default."""
        self._fresh()
        if self.role_of(name) == "admin":
            raise OpError(
                "never_gated", f"{name} is an admin, and admins are never gated"
            )
        e = self._entry(name)
        if e is None:
            raise OpError("unknown_token", f"no token named {name}")
        if policy is None:
            e.pop("policy", None)
        else:
            self._check_policy(policy)
            e["policy"] = policy
        self._save()

    def set_default_policy(self, policy: dict | None) -> None:
        """The policy of every member without its own; None removes it."""
        self._fresh()
        if policy is None:
            self._doc.pop("default_policy", None)
        else:
            self._check_policy(policy)
            self._doc["default_policy"] = policy
        self._save()

    def admin_token(self) -> str:
        self._fresh()
        return self._admin

    def use_admin(self, token: str) -> None:
        """Make ``token`` the admin's: what the web layer was built with. A
        no-op when it already is, which is every real start."""
        self._fresh()
        if token and token != self._admin:
            _write_private(self.root / "token", token + "\n")
            self._admin, self._admin_mtime = token, self._mtime(self.root / "token")

    def rows(self) -> list[dict]:
        self._fresh()
        rows = [
            {
                "name": self.owner,
                "role": "admin",
                "owner": True,
                "created_at": None,
                "policy": None,
                "linux_user": None,
            }
        ]
        rows += [
            {
                "name": e["name"],
                "role": e.get("role", "member"),
                "owner": False,
                "created_at": e.get("created_at"),
                "policy": e["policy"] if isinstance(e.get("policy"), dict) else None,
                "linux_user": self.linux_user_of(e["name"]),
            }
            for e in self._entries()
        ]
        for r in rows:
            r["used_at"] = self._used.get(str(r["name"]))
        return rows

    # -- changing -------------------------------------------------------------
    def add(
        self,
        name: str,
        role: str,
        linux_user: str | None = None,
        no_linux_user: bool = False,
    ) -> str:
        """Mint a token for ``name``; returns it, the one time it is shown. In
        multi-user mode it runs as ``linux_user`` (default: ``name``), or, for
        an admin with ``no_linux_user``, as the server's own user."""
        self._fresh()
        if not NAME.match(name):
            raise OpError(
                "bad_name",
                "a name is lowercase letters, digits and hyphens, starting with a letter, at most 32",
            )
        if role not in ROLES:
            raise OpError("bad_role", f"a role is one of {', '.join(ROLES)}")
        if name == self.owner or any(e["name"] == name for e in self._entries()):
            raise OpError("name_taken", f"{name} already has a token")
        entry: dict = {"name": name, "role": role}
        if not self.multi_user:
            if linux_user or no_linux_user:
                raise OpError(
                    "needs_multi_user", "--linux-user needs multi_user in .aegis.yaml"
                )
        elif no_linux_user:
            if role != "admin":
                raise OpError("member_needs_user", "a member token needs a Linux user")
        else:
            pw = users.allowed(linux_user or name, server_uid=self.server_uid)
            entry["linux_user"] = pw.pw_name
        token = secrets.token_urlsafe(32)
        self._doc["tokens"].append(
            entry | {"sha256": _digest(token), "created_at": time.time()}
        )
        self._save()
        return token

    def revoke(self, name: str) -> str | None:
        """End ``name``'s token. The admin's is replaced, never removed: the new
        one is returned so whoever revoked it can sign in with it."""
        self._fresh()
        if name == self.owner:
            token = secrets.token_urlsafe(32)
            _write_private(self.root / "token", token + "\n")
            self._admin, self._admin_mtime = token, self._mtime(self.root / "token")
            self._used.pop(name, None)
            return token
        before = len(self._doc["tokens"])
        self._doc["tokens"] = [
            e
            for e in self._doc["tokens"]
            if not (isinstance(e, dict) and e.get("name") == name)
        ]
        if len(self._doc["tokens"]) == before:
            raise OpError("unknown_token", f"no token named {name}")
        self._save()
        self._used.pop(name, None)
        return None
