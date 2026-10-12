"""Web Push: the devices subscribed on this server, and the ops the bell uses.

A device subscribes from the bell (``client/js/pushpanel.js``) with the sources
it wants to hear about. Its subscription is kept in
``<state>/push/subscriptions.json``, mode 0600 like the VAPID key beside it:
an endpoint and its two keys are all anyone needs to push to that device. Each
row belongs to the principal whose token made it; a member changes only their
own, an admin any, and revoking a token deletes its rows (``token.revoke`` in
``app.py``), so a lost phone stops hearing about the work.

The sender listens to the ``sessions`` channel inside the server, as a browser
does, and to ``App.toast``, and applies ping.js's rule: an event is a session
entering an attention state it was not in, or an agent's toast. The first
snapshot only records where each session stands, so a restart with sessions
already waiting sends nothing. A channel callback only puts work on a queue;
one task encrypts and posts it, so a slow or dead push service never delays
the channel, whose other subscribers are the browsers.
"""

from __future__ import annotations

import asyncio
import base64
import contextlib
import hashlib
import json
import logging
import os
import secrets
import time
from collections.abc import Sequence
from pathlib import Path
from typing import TYPE_CHECKING, Literal
from urllib.parse import urlsplit

import httpx
from pydantic import BaseModel, Field, field_validator

from . import webpush
from .ops import Caller, OpError

if TYPE_CHECKING:
    from .app import App

Source = Literal["needs_you", "error", "review", "done", "toast"]
SOURCES: tuple[str, ...] = ("needs_you", "error", "review", "done", "toast")
DEFAULT_SOURCES: list[Source] = ["needs_you", "error", "toast"]
# Subscriptions.list shadows the builtin inside its class.
Rows = list[dict]
Names = Sequence[str]
log = logging.getLogger("aegis.push")

TTL_S = 86400  # how long a push service keeps a push for a device that is off
TIMEOUT_S = 10.0  # per request, connection to answer
BACKOFF_S = (1.0, 4.0)  # before the second and the third try
MAX_PAYLOAD = 3072  # bytes of JSON; push services take about 4 KB encrypted
JWT_FOR_S = 3600  # a VAPID token is reused this long (it is valid 12 hours)
LABELS = {
    "needs_you": "needs you",
    "error": "error",
    "review": "to read",
    "done": "done",
}
URGENT = {"needs_you", "error"}


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

    def list(self) -> Rows:
        return [dict(r) for r in self._rows]

    def get(self, id: str) -> dict | None:
        return next((r for r in self._rows if r["id"] == id), None)

    def add(
        self,
        sub: dict,
        owner: str,
        device: str,
        sources: Names,
        id: str | None = None,
    ) -> str:
        """Store a subscription and return its id. With the ``id`` of an
        existing row, that row takes the new endpoint and keys: the browser
        rotated its subscription and the page subscribed again."""
        row: dict | None = self.get(id) if id else None
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
        self, id: str, sources: Names | None = None, device: str | None = None
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
    """What ``App.push`` holds: the subscriptions, this server's VAPID key,
    made on first use (``webpush.load_key``), the public origins aegis is
    served at (``web.build_web`` sets them) and the sender."""

    def __init__(self, app: App) -> None:
        self.dir = app.roots.state_root / "push"
        self.subscriptions = Subscriptions(self.dir / "subscriptions.json")
        self.origins: list[str] = []
        self._key = None
        self.sender = Sender(app, self)

    @property
    def key(self):
        if self._key is None:
            self._key = webpush.load_key(self.dir / "vapid.pem")
        return self._key


def fit(payload: dict) -> dict:
    """``payload`` with its body cut until its JSON is at most MAX_PAYLOAD bytes."""
    out = dict(payload)
    while len(json.dumps(out).encode()) > MAX_PAYLOAD and out["body"]:
        excess = len(json.dumps(out).encode()) - MAX_PAYLOAD
        body = out["body"].removesuffix("…")
        out["body"] = body[: max(0, len(body) - max(excess // 2, 1))] + "…"
    return out


def topic(key: str) -> str:
    """The Topic header for a session: a newer push for it replaces an older
    one the service still holds. The header allows 32 URL-safe characters."""
    digest = hashlib.sha256(key.encode()).digest()
    return base64.urlsafe_b64encode(digest).decode().rstrip("=")[:32]


class Sender:
    """Turns transitions and agents' toasts into encrypted pushes (module doc)."""

    # An httpx transport in place of the network: tests set it on the class,
    # so it holds for the sender of an app that restarts.
    transport = None

    def __init__(self, app: App, push: Push, transport=None) -> None:
        self.app = app
        self.push = push
        if transport is not None:
            self.transport = transport
        self._seen: dict[str, str | None] = {}  # log id -> attention
        self._subs: list = []
        self._queue: asyncio.Queue = asyncio.Queue()
        self._task: asyncio.Task | None = None
        self._client: httpx.AsyncClient | None = None
        self._jwts: dict[str, tuple[str, float]] = {}  # origin -> (token, made)

    def start(self) -> None:
        """Called at the end of ``App.boot``, when every session is loaded, so
        the first snapshot holds them all."""
        self._task = asyncio.create_task(self._drain())
        self._subs = [self.app.channels.subscribe("sessions", self._on_sessions)]

    async def stop(self) -> None:
        for sub in self._subs:
            self.app.channels.unsubscribe(sub)
        self._subs = []
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            self._task = None
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    def _on_sessions(self, frame: dict) -> None:
        if frame["t"] == "snapshot":
            self._seen = {m["log_id"]: m.get("attention") for m in frame["data"]}
            return
        for op in frame.get("ops", ()):
            if "remove" in op:
                self._seen.pop(op["remove"], None)
                continue
            m = op.get("upsert")
            if m is None:
                continue
            was = self._seen.get(m["log_id"])
            now = self._seen[m["log_id"]] = m.get("attention")
            if now == was or now not in LABELS:
                continue
            # A fresh session idles as done, and a reply to one that needs
            # you passes through done on its way to working: only a turn
            # that just ended is finished work, or something to read.
            if now in ("review", "done") and was != "working":
                continue
            self._queue_for(
                now,
                {
                    "title": f"{m.get('title') or m['handle']}: {LABELS[now]}",
                    "body": m.get("attention_line") or "",
                    "key": m["log_id"],
                    "kind": now,
                    "tag": f"{m['log_id']}:{now}",
                },
            )

    def on_toast(self, t: dict) -> None:
        """A toast as ``App.toast`` publishes it. Called by it rather than
        subscribed to the ``toasts`` channel, whose subscribers it counts as
        the browsers that saw the toast."""
        if not t.get("from"):
            return  # aegis's own toasts answer something done on a page
        handle, sid = t["from"]["handle"], t["from"]["id"]
        self._queue_for(
            "toast",
            {
                "title": f"{handle}: error" if t.get("kind") == "error" else handle,
                "body": t["text"],
                "key": sid,
                "kind": "toast",
                "tag": f"toast:{sid}:{t['text']}",
            },
        )

    def _queue_for(self, source: str, payload: dict) -> None:
        if self._task is None:
            return  # not started, or stopped
        for row in self.push.subscriptions.list():
            if source in row.get("sources", ()):
                self._queue.put_nowait((row["id"], payload))

    async def _drain(self) -> None:
        while True:
            sid, payload = await self._queue.get()
            row = self.push.subscriptions.get(sid)
            if row is None:
                continue  # removed since
            try:
                await self.send_to(row, payload)
            except Exception:
                log.exception("aegis: a push to %s failed", row.get("device"))

    def _jwt(self, origin: str) -> str:
        cached = self._jwts.get(origin)
        if cached and time.time() - cached[1] < JWT_FOR_S:
            return cached[0]
        https = [o for o in self.push.origins if o.startswith("https://")]
        sub = https[0] if https else "mailto:aegis@localhost"
        token = webpush.vapid_jwt(self.push.key, origin, sub)
        self._jwts[origin] = (token, time.time())
        return token

    async def send_to(self, row: dict, payload: dict) -> int | None:
        """Encrypt ``payload`` for one subscription and post it, trying a 429,
        a 5xx or no answer three times in all. A 201 stamps ``last_ok_at``; a
        404 or 410 means the browser dropped the subscription, which goes; any
        other end counts a failure. Returns the last status, None for none."""
        payload = fit(payload)
        keys = row["keys"]
        body = webpush.encrypt(
            json.dumps(payload).encode(),
            webpush.b64d(keys["p256dh"]),
            webpush.b64d(keys["auth"]),
        )
        u = urlsplit(row["endpoint"])
        public = webpush.b64e(webpush.raw_public(self.push.key))
        headers = {
            "TTL": str(TTL_S),
            "Urgency": "high" if payload["kind"] in URGENT else "normal",
            "Content-Encoding": "aes128gcm",
            "Content-Type": "application/octet-stream",
            "Authorization": f"vapid t={self._jwt(f'{u.scheme}://{u.netloc}')}, k={public}",
        }
        if payload["kind"] in LABELS and payload["key"]:
            headers["Topic"] = topic(payload["key"])
        if self._client is None:
            self._client = httpx.AsyncClient(
                timeout=TIMEOUT_S, transport=self.transport
            )
        status = None
        for attempt in range(1 + len(BACKOFF_S)):
            if attempt:
                await asyncio.sleep(BACKOFF_S[attempt - 1])
            try:
                async with asyncio.timeout(TIMEOUT_S):
                    r = await self._client.post(
                        row["endpoint"], content=body, headers=headers
                    )
                status = r.status_code
            except (httpx.HTTPError, TimeoutError) as e:
                status = None
                log.info("aegis: push to %s: %r", u.netloc, e)
            if status is not None and 200 <= status < 300:
                self.push.subscriptions.record(row["id"], ok=True)
                return status
            if status in (404, 410):
                self.push.subscriptions.remove(row["id"])
                return status
            if status is not None and status != 429 and status < 500:
                break
        log.warning("aegis: push to %s failed: %s", u.netloc, status)
        self.push.subscriptions.record(row["id"], ok=False)
        return status


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
    sources: list[Source] = list(DEFAULT_SOURCES)  # pydantic copies it per call
    id: str | None = None


class Update(_Strict):
    id: str
    sources: list[Source] | None = None
    device: str | None = Field(None, min_length=1, max_length=80, pattern=r"^[^\n]+$")


class SubId(_Strict):
    id: str


def register_push_ops(app: App) -> Push:
    """The ops of section 10 of the push spec; returns what ``App.push`` holds."""
    push = Push(app)
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
        """Send a test push to one subscription; answers the push service's
        status, null when it never answered."""
        row = mine(p.id, caller)
        status = await push.sender.send_to(
            row,
            {
                "title": "aegis",
                "body": "Push reaches this device.",
                "key": "",
                "kind": "test",
                "tag": "test",
            },
        )
        return {"status": status}

    return push
