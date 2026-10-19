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

Each subscription has its own lane: its pushes go out in order, one at a
time, while the lanes run side by side, at most CONCURRENCY at once, so a
device whose push service never answers holds up nobody else. A push waits
at most MAX_AGE_S for its turn (a "needs you" two hours late is noise), and
the queue keeps the newest MAX_QUEUE. A row goes when its push service says it
is gone or refuses it (401, 403, 404, 410), and after MAX_FAILING failures in
a row.
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
from collections import deque
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
SHORT_MAX = 256  # bytes of UTF-8 in a title or a tag
ELLIPSIS = "…".encode()
JWT_FOR_S = 3600  # a VAPID token is reused this long (it is valid 12 hours)
CONCURRENCY = 8  # lanes posting at once
MAX_AGE_S = 300.0  # a queued push older than this when its turn comes is dropped
MAX_QUEUE = 1000  # queued pushes; beyond it the oldest go
MAX_FAILING = 5  # failures in a row that delete a subscription
GONE = (401, 403, 404, 410)  # the push service no longer takes this subscription
LABELS = {
    "needs_you": "needs you",
    "error": "error",
    "review": "to read",
    "done": "done",
}
URGENT = {"needs_you", "error"}
MAX_DEVICES = 10  # rows per owner
# The push services browsers subscribe at. An endpoint anywhere else is
# refused, so a token cannot make the server post to a host of its choosing
# (localhost, the private network, a cloud's metadata address) and read the
# status back from push.test. AEGIS_PUSH_HOSTS adds exact hosts, comma-separated:
# a self-hosted push service, or a test's fake one.
PUSH_HOSTS = (
    "fcm.googleapis.com",
    "jmt17.google.com",
    "updates.push.services.mozilla.com",
)
PUSH_SUFFIXES = (".push.apple.com", ".notify.windows.com")


def known_service(endpoint: str) -> bool:
    host = urlsplit(endpoint).hostname or ""
    extra = os.environ.get("AEGIS_PUSH_HOSTS", "").lower().split(",")
    return (
        host in PUSH_HOSTS
        or host.endswith(PUSH_SUFFIXES)
        or host in {h.strip() for h in extra if h.strip()}
    )


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
        rotated its subscription and the page subscribed again. Otherwise a
        row with the same endpoint is taken over, whoever owned it: one
        browser is one row, whichever token signed in on it last."""
        row: dict | None = self.get(id) if id else None
        same = [r for r in self._rows if r["endpoint"] == sub["endpoint"]]
        if row is None and same:
            row = same[0]
            row["owner"] = owner
        self._rows = [r for r in self._rows if r is row or r not in same]
        if row is None:
            row = {
                "id": "p_" + secrets.token_hex(4),
                "owner": owner,
                "created_at": int(time.time()),
                "last_ok_at": None,
                "failures": 0,
                "failing": 0,
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
        counts, and Settings shows both. MAX_FAILING failures in a row (a
        success starts ``failing`` again) delete the row."""
        row = self.get(id)
        if row is None:
            return
        if ok:
            row["last_ok_at"] = int(time.time())
            row["failing"] = 0
        else:
            row["failures"] = row.get("failures", 0) + 1
            row["failing"] = row.get("failing", 0) + 1
            if row["failing"] >= MAX_FAILING:
                log.warning(
                    "aegis: %d pushes in a row to %s failed; removing it",
                    row["failing"],
                    row.get("device"),
                )
                self.remove(id)
                return
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


def encode(payload: dict) -> bytes:
    """The JSON a device decrypts: UTF-8 as it is, so its size is the sum of
    the strings' UTF-8 sizes and the few bytes around them."""
    return json.dumps(payload, ensure_ascii=False).encode()


def cut(s: str, n: int) -> str:
    """``s`` in at most ``n`` bytes of UTF-8, cut on a character and ended
    with "…" when cut; empty when "…" alone does not fit."""
    b = s.encode()
    if len(b) <= n:
        return s
    if n < len(ELLIPSIS):
        return ""
    return b[: n - len(ELLIPSIS)].decode(errors="ignore") + "…"


def fit(payload: dict) -> dict:
    """``payload`` with its title and tag cut to SHORT_MAX bytes, then its
    body, title and tag, in that order, cut until its JSON is at most
    MAX_PAYLOAD bytes. Each pass of the loops makes one string strictly
    shorter (``cut`` to fewer bytes than it has), so they end."""
    out = dict(payload)
    for k in ("title", "tag"):
        out[k] = cut(out[k], SHORT_MAX)
    for k in ("body", "title", "tag"):
        while (excess := len(encode(out)) - MAX_PAYLOAD) > 0 and out[k]:
            out[k] = cut(out[k], len(out[k].encode()) - excess)
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
        self._running = False
        # subscription id -> its pushes waiting, each with when it was queued
        self._pending: dict[str, deque[tuple[dict, float]]] = {}
        self._lanes: dict[str, asyncio.Task] = {}
        self._slots = asyncio.Semaphore(CONCURRENCY)
        self._client: httpx.AsyncClient | None = None
        self._jwts: dict[str, tuple[str, float]] = {}  # origin -> (token, made)

    def start(self) -> None:
        """Called at the end of ``App.boot``, when every session is loaded, so
        the first snapshot holds them all."""
        self._running = True
        self._subs = [self.app.channels.subscribe("sessions", self._on_sessions)]

    async def stop(self) -> None:
        for sub in self._subs:
            self.app.channels.unsubscribe(sub)
        self._subs = []
        self._running = False
        self._pending.clear()
        lanes = list(self._lanes.values())
        for task in lanes:
            task.cancel()
        for task in lanes:
            with contextlib.suppress(asyncio.CancelledError):
                await task
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
        if not self._running:
            return  # not started, or stopped
        now = time.monotonic()
        for row in self.push.subscriptions.list():
            if source not in row.get("sources", ()):
                continue
            sid = row["id"]
            self._pending.setdefault(sid, deque()).append((payload, now))
            if sid not in self._lanes:
                self._lanes[sid] = asyncio.create_task(self._lane(sid))
        while sum(map(len, self._pending.values())) > MAX_QUEUE:
            oldest = min(
                (q for q in self._pending.values() if q), key=lambda q: q[0][1]
            )
            oldest.popleft()

    async def _lane(self, sid: str) -> None:
        """One subscription's pushes, in order. It ends when none wait; the
        next one queued starts it again. No await separates the last look at
        the queue from leaving ``_lanes``, so no push is left behind."""
        try:
            while q := self._pending.get(sid):
                payload, queued = q.popleft()
                row = self.push.subscriptions.get(sid)
                if row is None:
                    break  # removed since
                async with self._slots:
                    if time.monotonic() - queued > MAX_AGE_S:
                        continue
                    try:
                        await self.send_to(row, payload)
                    except Exception:
                        log.exception("aegis: a push to %s failed", row.get("device"))
        finally:
            self._lanes.pop(sid, None)
            self._pending.pop(sid, None)

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
        status in GONE means the browser dropped the subscription or the push
        service no longer takes it, and it goes; any other end counts a
        failure. Returns the last status, None for none."""
        payload = fit(payload)
        keys = row["keys"]
        body = webpush.encrypt(
            encode(payload),
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
            if status in GONE:
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
        given replaces that subscription (a browser that rotated its own), as
        does one with the same endpoint. Only a known push service's endpoint
        is taken (``known_service``), and at most MAX_DEVICES per owner."""
        endpoint = p.subscription.endpoint
        if not known_service(endpoint):
            raise OpError(
                "bad_endpoint",
                f"{urlsplit(endpoint).hostname} is not a known push service",
            )
        if p.id is not None and push.subscriptions.get(p.id) is not None:
            mine(p.id, caller)
        name = app.who(caller).name
        others = [
            r
            for r in push.subscriptions.list()
            if r["owner"] == name and r["id"] != p.id and r["endpoint"] != endpoint
        ]
        if len(others) >= MAX_DEVICES:
            raise OpError(
                "too_many_devices",
                f"{name} has {len(others)} devices with push; remove one first",
            )
        sid = push.subscriptions.add(
            p.subscription.model_dump(exclude={"expirationTime"}),
            name,
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
