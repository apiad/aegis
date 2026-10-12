"""Web Push: the devices subscribed on this server, and the ops the bell uses.

A device subscribes from the bell (``client/js/pushpanel.js``) with the sources
it wants to hear about. Its subscription is kept in
``<state>/push/subscriptions.json``, mode 0600 like the VAPID key beside it:
an endpoint and its two keys are all anyone needs to push to that device. Each
row belongs to the principal whose token made it; a member changes only their
own, an admin any, and revoking a token deletes its rows (``token.revoke`` in
``app.py``), so a lost phone stops hearing about the work.
"""

from __future__ import annotations

import json
import os
import secrets
import time
from pathlib import Path
from typing import TYPE_CHECKING, Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, Field, field_validator

from . import webpush
from .ops import Caller, OpError

if TYPE_CHECKING:
    from .app import App

Source = Literal["needs_you", "error", "review", "done", "toast"]
SOURCES: tuple[str, ...] = ("needs_you", "error", "review", "done", "toast")
DEFAULT_SOURCES = ["needs_you", "error", "toast"]


def _write_private(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(text)
    os.replace(tmp, path)


class Subscriptions:
    """Every device's subscription, read once and written whole on each change."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._rows: list[dict] = []
        if path.exists():
            try:
                rows = json.loads(path.read_text())
                self._rows = [r for r in rows if isinstance(r, dict) and "id" in r]
            except (OSError, ValueError):
                self._rows = []

    def _save(self) -> None:
        _write_private(self.path, json.dumps(self._rows, indent=1))

    def list(self) -> list[dict]:
        return [dict(r) for r in self._rows]

    def get(self, id: str) -> dict | None:
        return next((r for r in self._rows if r["id"] == id), None)

    def add(
        self,
        sub: dict,
        owner: str,
        device: str,
        sources: list[str],
        id: str | None = None,
    ) -> str:
        """Store a subscription and return its id. With the ``id`` of an
        existing row, that row takes the new endpoint and keys: the browser
        rotated its subscription and the page subscribed again."""
        row = self.get(id) if id else None
        if row is None:
            row = {
                "id": "p_" + secrets.token_hex(4),
                "owner": owner,
                "created_at": int(time.time()),
                "last_ok_at": None,
                "failures": 0,
            }
            self._rows.append(row)
        row.update(
            device=device,
            endpoint=sub["endpoint"],
            keys=dict(sub["keys"]),
            sources=list(sources),
        )
        self._save()
        return row["id"]

    def update(
        self, id: str, sources: list[str] | None = None, device: str | None = None
    ) -> None:
        row = self.get(id)
        if row is None:
            return
        if sources is not None:
            row["sources"] = list(sources)
        if device is not None:
            row["device"] = device
        self._save()

    def remove(self, id: str) -> None:
        self._rows = [r for r in self._rows if r["id"] != id]
        self._save()

    def remove_owner(self, name: str) -> None:
        if any(r["owner"] == name for r in self._rows):
            self._rows = [r for r in self._rows if r["owner"] != name]
            self._save()

    def record(self, id: str, ok: bool) -> None:
        """A delivery's outcome: a success stamps ``last_ok_at``, a failure
        counts, and Settings shows both."""
        row = self.get(id)
        if row is None:
            return
        if ok:
            row["last_ok_at"] = int(time.time())
        else:
            row["failures"] = row.get("failures", 0) + 1
        self._save()


class Push:
    """What ``App.push`` holds: the subscriptions and this server's VAPID key,
    made on first use (``webpush.load_key``)."""

    def __init__(self, state_root: Path) -> None:
        self.dir = state_root / "push"
        self.subscriptions = Subscriptions(self.dir / "subscriptions.json")
        self._key = None

    @property
    def key(self):
        if self._key is None:
            self._key = webpush.load_key(self.dir / "vapid.pem")
        return self._key


class _Strict(BaseModel):
    model_config = {"extra": "forbid"}


class Keys(_Strict):
    p256dh: str
    auth: str

    @field_validator("p256dh")
    @classmethod
    def _point(cls, v: str) -> str:
        raw = _decode(v)
        if len(raw) != 65 or raw[0] != 4:
            raise ValueError("p256dh is a 65-byte uncompressed P-256 point")
        return v

    @field_validator("auth")
    @classmethod
    def _secret(cls, v: str) -> str:
        if len(_decode(v)) != 16:
            raise ValueError("auth is a 16-byte secret")
        return v


def _decode(v: str) -> bytes:
    try:
        return webpush.b64d(v)
    except ValueError as e:
        raise ValueError("not base64url") from e


class Subscription(_Strict):
    endpoint: str = Field(max_length=2048)
    keys: Keys
    # What a browser's toJSON() adds; not kept.
    expirationTime: float | None = None

    @field_validator("endpoint")
    @classmethod
    def _https(cls, v: str) -> str:
        u = urlsplit(v)
        if u.scheme != "https" or not u.netloc:
            raise ValueError("a push endpoint is an https URL")
        return v


Device = Field(min_length=1, max_length=80, pattern=r"^[^\n]+$")


class Subscribe(_Strict):
    subscription: Subscription
    device: str = Device
    sources: list[Source] = Field(default_factory=lambda: list(DEFAULT_SOURCES))
    id: str | None = None


class Update(_Strict):
    id: str
    sources: list[Source] | None = None
    device: str | None = Field(None, min_length=1, max_length=80, pattern=r"^[^\n]+$")


class SubId(_Strict):
    id: str


def register_push_ops(app: App) -> Push:
    """The ops of section 10 of the push spec; returns what ``App.push`` holds."""
    push = Push(app.roots.state_root)
    r = app.registry

    def mine(id: str, caller: Caller) -> dict:
        row = push.subscriptions.get(id)
        if row is None:
            raise OpError("unknown_subscription", f"no push subscription {id}")
        who = app.who(caller)
        if not who.is_admin and row["owner"] != who.name:
            raise OpError("not_owner", f"{id} belongs to {row['owner']}")
        return row

    @r.op("push.key")
    async def push_key(_, caller):
        """This server's VAPID public key, base64url: a browser subscribes with it."""
        return {"key": webpush.b64e(webpush.raw_public(push.key))}

    @r.op("push.subscribe", Subscribe)
    async def push_subscribe(p: Subscribe, caller):
        """Store this device's subscription and the sources it wants; an ``id``
        given replaces that subscription (a browser that rotated its own)."""
        if p.id is not None and push.subscriptions.get(p.id) is not None:
            mine(p.id, caller)
        sid = push.subscriptions.add(
            p.subscription.model_dump(exclude={"expirationTime"}),
            app.who(caller).name,
            p.device,
            list(dict.fromkeys(p.sources)),
            p.id,
        )
        return {"id": sid}

    @r.op("push.update", Update)
    async def push_update(p: Update, caller):
        """Change a subscription's sources or device name."""
        mine(p.id, caller)
        sources = list(dict.fromkeys(p.sources)) if p.sources is not None else None
        push.subscriptions.update(p.id, sources=sources, device=p.device)

    @r.op("push.remove", SubId)
    async def push_remove(p: SubId, caller):
        """Delete a subscription: that device hears nothing more."""
        mine(p.id, caller)
        push.subscriptions.remove(p.id)

    @r.op("push.list")
    async def push_list(_, caller):
        """The caller's subscriptions, every one for an admin, without keys."""
        who = app.who(caller)
        rows = [
            {k: v for k, v in row.items() if k != "keys"}
            for row in push.subscriptions.list()
            if who.is_admin or row["owner"] == who.name
        ]
        return {"subscriptions": rows}

    @r.op("push.test", SubId)
    async def push_test(p: SubId, caller):
        """Send a test push to one subscription."""
        mine(p.id, caller)
        raise OpError("not_ready", "the sender is not built yet")

    return push
