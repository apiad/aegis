"""The secret operations: two for agents, two for the person's cards.

``secret.send`` reads a file into a pad and a ciphertext (secret_store.py) and
records the ciphertext on a card; ``secret.reveal`` hands the pad to the
browser once, which undoes the XOR. ``secret.request`` puts a card with a
password field in the transcript; ``secret.provide`` writes what the person
typed to the agent's path and wakes the agent. No plaintext is ever recorded,
returned to an agent or logged (web.py masks a ``secret`` param).
"""

from __future__ import annotations

import asyncio
import base64
import logging
import time
from pathlib import Path
from typing import TYPE_CHECKING

from pydantic import BaseModel, Field

from . import secret_store as ss
from .monitors import iso_now
from .ops import Caller, OpError

if TYPE_CHECKING:
    from .app import App

log = logging.getLogger(__name__)


class _Strict(BaseModel):
    model_config = {"extra": "forbid"}


_PATH_DOC = (
    "A relative path resolves against your session's working directory, "
    "not your shell's last cd."
)


class SecretSend(_Strict):
    path: str = Field(
        min_length=1,
        description="The file that holds the secret, written without printing "
        "it. " + _PATH_DOC,
    )
    ttl: str = Field("10m", description="How long it can be revealed: 30s to 24h.")
    caption: str = Field(
        "", max_length=200, description="What the secret is, shown on the card."
    )
    delete_source: bool = Field(
        False,
        description="Remove the file once read. Use it when the file was made "
        "only to carry the secret.",
    )


class SecretRequest(_Strict):
    path: str = Field(
        min_length=1,
        description="Where to write what the person types. It must not exist "
        "yet, and its directory must. " + _PATH_DOC,
    )
    prompt: str = Field(
        min_length=1,
        max_length=500,
        description="What you need and why, shown above the field.",
    )
    ttl: str = Field("30m", description="How long the card waits: 30s to 24h.")


class SecretRef(_Strict):
    log_id: str
    id: str


class SecretProvide(SecretRef):
    secret: str = Field(min_length=1, max_length=ss.MAX_BYTES)


def _err(e: ss.SecretError) -> OpError:
    return OpError(e.code, e.message)


def _iso(ts: float) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(ts))


def header(id: str, kind: str) -> str:
    return f"> from secret:{id} · {kind} · {iso_now()}"


def register_secret_ops(app: App) -> ss.SecretStore:
    r, reg = app.registry, app.sessions

    def own(caller: Caller):
        if not caller.is_agent or caller.log_id not in reg.sessions:
            raise OpError("agents_only", "only an agent's session can do this")
        return reg.sessions[caller.log_id]

    def resolve(s, path: str) -> Path:
        p = Path(path).expanduser()
        return p if p.is_absolute() else s.spec.cwd / p

    def by(caller: Caller) -> str | None:
        return caller.principal.name if caller.principal else None

    def expired(log_id: str, id: str, kind: str) -> None:
        s = reg.sessions.get(log_id)
        if s is None:
            return
        s.record_secret({"kind": "secret_gone", "secret_id": id, "why": "expired"})
        if kind == "request":

            async def wake() -> None:
                try:
                    await s.deliver(
                        header(id, "expired"),
                        "The person did not provide it in time; nothing was "
                        "written. Ask again if you still need it.",
                    )
                except Exception:  # an archived or locked session: the card says it
                    log.info("secret %s expired with no session to wake", id)

            asyncio.get_running_loop().create_task(wake())

    store = ss.SecretStore(expired)

    # -- agents ------------------------------------------------------------------
    @r.op("secret.send", SecretSend, agent=True)
    async def send(p: SecretSend, caller):
        """Hand the person a secret (a password, a token, a key) that only
        they can read, once. The server reads the file at path and you get
        back an id, never the content. The person sees a card with a Reveal
        button until ttl runs out, and the first reveal burns it. Write the
        secret into the file without printing it (umask 077; openssl rand
        -base64 18 > /tmp/pw), and pass delete_source when the file was made
        only to carry it."""
        s = own(caller)
        path = resolve(s, p.path)
        try:
            ttl = ss.ttl_seconds(p.ttl)
            data = await asyncio.to_thread(ss.read_secret, path)
        except ss.SecretError as e:
            raise _err(e) from e
        now = time.time()
        id, ct = store.put(s.log_id, bytes(data), ttl, now)
        size = len(data)
        ss.wipe(data)
        if p.delete_source:
            path.unlink(missing_ok=True)
        expires = _iso(now + ttl)
        s.record_secret(
            {
                "kind": "secret",
                "secret_id": id,
                "ct": base64.b64encode(ct).decode(),
                "bytes": size,
                "caption": p.caption,
                "expires_at": expires,
            }
        )
        return {"id": id, "bytes": size, "expires_at": expires}

    @r.op("secret.request", SecretRequest, agent=True)
    async def request(p: SecretRequest, caller):
        """Ask the person for a secret only they have (an API token, a
        password) without it entering the chat. They type it into a card;
        the server writes it to path (mode 0600) and wakes you with
        `> from secret:<id> · provided`. End your turn needs_you after
        calling this. Use the file by reference (--password-file,
        curl -H @file, VAR=$(< f) cmd) and never print it. aegis removes the
        file when your session closes; move it where it belongs if it must
        stay."""
        s = own(caller)
        path = resolve(s, p.path)
        if path.exists() or path.is_symlink():
            raise OpError("exists", f"{path} already exists; name a new path")
        if not path.parent.is_dir():
            raise OpError("not_found", f"{path.parent} is not a directory")
        try:
            ttl = ss.ttl_seconds(p.ttl)
        except ss.SecretError as e:
            raise _err(e) from e
        now = time.time()
        id = store.ask(s.log_id, str(path), ttl, now)
        expires = _iso(now + ttl)
        s.record_secret(
            {
                "kind": "secret_request",
                "secret_id": id,
                "prompt": p.prompt,
                "path": str(path),
                "expires_at": expires,
            }
        )
        return {"id": id, "expires_at": expires}

    # -- the person, from the cards ------------------------------------------------
    def gone_why(s, id: str) -> str:
        e = next((x for x in s.entries() if x["id"] == id), None)
        if e is None:
            raise OpError("no_secret", f"no secret {id!r} in this session")
        return (
            e["status"]
            if e["status"] in ("revealed", "expired", "provided")
            else "restarted"
        )

    @r.op("secret.reveal", SecretRef, owned="log_id")
    async def reveal(p: SecretRef, caller):
        s = reg.open(p.log_id)
        pad = store.take(s.log_id, p.id)
        if pad is None:
            return {"gone": gone_why(s, p.id)}
        s.record_secret(
            {
                "kind": "secret_gone",
                "secret_id": p.id,
                "why": "revealed",
                "by": by(caller),
            }
        )
        return {"pad": base64.b64encode(pad).decode()}

    @r.op("secret.provide", SecretProvide, owned="log_id")
    async def provide(p: SecretProvide, caller):
        s = reg.open(p.log_id)
        s.check_lock()  # a person does not steer a workflow's node
        path = store.asked(s.log_id, p.id)
        if path is None:
            raise OpError("gone", f"the request {p.id} is {gone_why(s, p.id)}")
        data = p.secret.encode("utf-8")
        try:
            dev, ino = await asyncio.to_thread(ss.write_new, Path(path), data)
        except ss.SecretError as e:
            raise _err(e) from e
        store.answered(p.id)
        s.record_secret(
            {
                "kind": "secret_provided",
                "secret_id": p.id,
                "bytes": len(data),
                "by": by(caller),
                "dev": dev,
                "ino": ino,
            }
        )
        await s.deliver(
            header(p.id, "provided"),
            f"path={path} · {len(data)} B, mode 0600. Use it by reference and "
            "never print it. aegis removes it when this session closes unless "
            "you move it.",
        )
        return "ok"

    @r.op("claude.token", SecretProvide, owned="log_id")
    async def claude_token(p: SecretProvide, caller):
        """The sign-in card's field (claude/login.py): check a setup-token,
        keep it, restart the sessions that failed to sign in."""
        s = reg.open(p.log_id)
        if reg.sign_in is None:
            raise OpError("off", "this server keeps no Claude token")
        try:
            return await reg.sign_in.provide(s, p.id, p.secret)
        except ValueError as e:
            raise OpError("refused", str(e)) from None

    return store
