"""Secrets between an agent and the person that never enter a transcript.

``secret_send``: the server reads a file, keeps a random pad of the same length
in memory only, and records ``content XOR pad`` on the session's card. The
first reveal hands the pad to the browser and forgets it; so does expiry, and
a restart forgets every pad. Without its pad a ciphertext is noise, so every
copy of the card (the log, the archive, a backup) is safe once the pad is gone.

``secret_request``: the person types a secret into a card and the server writes
it to a path the agent named, which must not exist yet. The file is removed
when the session closes, unless the agent moved it first.

Spec: docs/superpowers/specs/2026-10-22-secret-send-design.html.
"""

from __future__ import annotations

import asyncio
import os
import secrets
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from .workflow_lang import duration

MAX_BYTES = 16 * 1024
TTL_MIN_S, TTL_MAX_S = 30.0, 24 * 3600.0


class SecretError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code, self.message = code, message


def ttl_seconds(text: str) -> float:
    secs = duration(text)
    if secs is None or not TTL_MIN_S <= secs <= TTL_MAX_S:
        raise SecretError("bad_params", f"ttl {text!r} is not between 30s and 24h")
    return secs


def read_secret(path: Path) -> bytearray:
    """The file's bytes, one trailing newline stripped. Errors name the path
    and the size, never a byte of the content."""
    try:
        size = path.stat().st_size
    except FileNotFoundError:
        raise SecretError("not_found", f"{path} does not exist") from None
    except OSError as e:
        raise SecretError("unreadable", f"cannot stat {path}: {e.strerror}") from None
    if not path.is_file():
        raise SecretError("not_a_file", f"{path} is not a regular file")
    if size > MAX_BYTES:
        raise SecretError(
            "too_large", f"{path} is {size} bytes; the limit is {MAX_BYTES}"
        )
    try:
        data = bytearray(path.read_bytes())
    except OSError as e:
        raise SecretError("unreadable", f"cannot read {path}: {e.strerror}") from None
    if data.endswith(b"\r\n"):
        del data[-2:]
    elif data.endswith(b"\n"):
        del data[-1:]
    if not data:
        raise SecretError("empty", f"{path} is empty")
    try:
        data.decode("utf-8")
    except UnicodeDecodeError:
        wipe(data)
        raise SecretError("not_text", f"{path} is not UTF-8 text") from None
    return data


def wipe(buf: bytearray) -> None:
    """Hygiene, not a guarantee: Python may hold other copies."""
    buf[:] = bytes(len(buf))


def xor(a: bytes, b: bytes) -> bytes:
    return bytes(x ^ y for x, y in zip(a, b, strict=True))


def write_new(path: Path, data: bytes) -> tuple[int, int]:
    """Write ``data`` to a path that must not exist, mode 0600. Returns the
    file's (device, inode), so cleanup removes only this file."""
    if not path.parent.is_dir():
        raise SecretError("not_found", f"{path.parent} is not a directory")
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        raise SecretError("exists", f"{path} already exists") from None
    except OSError as e:
        raise SecretError("unwritable", f"cannot create {path}: {e.strerror}") from None
    try:
        os.write(fd, data)
        st = os.fstat(fd)
    finally:
        os.close(fd)
    return st.st_dev, st.st_ino


def remove_provided(path: str, dev: int, ino: int) -> bool:
    """Remove a provided file if it is still the one written there."""
    try:
        st = os.stat(path)
        if (st.st_dev, st.st_ino) != (dev, ino):
            return False
        os.unlink(path)
        return True
    except OSError:
        return False


def remove_all_provided(entries: list[dict]) -> int:
    """Remove every file a closing session's requests wrote, if still there."""
    n = 0
    for e in entries:
        d = e.get("detail") or {}
        if e.get("kind") == "secret_request" and e.get("status") == "provided":
            n += remove_provided(
                str(d.get("path")), int(d.get("dev", -1)), int(d.get("ino", -1))
            )
    return n


@dataclass
class _Held:
    log_id: str
    expires_at: float
    timer: asyncio.TimerHandle | None = field(default=None, repr=False)
    pad: bytes = b""  # a sent secret's
    path: str = ""  # a requested secret's


class SecretStore:
    """Pads of sent secrets and open requests, in this process's memory only.
    ``on_expired(log_id, id, kind)`` runs when a ttl runs out, kind being
    ``send`` or ``request``."""

    def __init__(self, on_expired: Callable[[str, str, str], None]):
        self._on_expired = on_expired
        self._sent: dict[str, _Held] = {}
        self._asked: dict[str, _Held] = {}

    def _arm(self, table: dict, id: str, held: _Held, kind: str, ttl: float) -> None:
        def fire() -> None:
            if table.pop(id, None) is not None:
                self._on_expired(held.log_id, id, kind)

        held.timer = asyncio.get_running_loop().call_later(ttl, fire)
        table[id] = held

    def put(
        self, log_id: str, data: bytes, ttl: float, now: float
    ) -> tuple[str, bytes]:
        """A new sent secret: its id and its ciphertext."""
        id = f"s-{secrets.token_hex(4)}"
        pad = secrets.token_bytes(len(data))
        self._arm(self._sent, id, _Held(log_id, now + ttl, pad=pad), "send", ttl)
        return id, xor(data, pad)

    def take(self, log_id: str, id: str) -> bytes | None:
        """The pad, once; None when it is gone or not this session's."""
        held = self._sent.get(id)
        if held is None or held.log_id != log_id:
            return None
        del self._sent[id]
        if held.timer:
            held.timer.cancel()
        return held.pad

    def ask(self, log_id: str, path: str, ttl: float, now: float) -> str:
        id = f"r-{secrets.token_hex(4)}"
        self._arm(self._asked, id, _Held(log_id, now + ttl, path=path), "request", ttl)
        return id

    def asked(self, log_id: str, id: str) -> str | None:
        """The open request's path; None when it is gone or not this session's."""
        held = self._asked.get(id)
        return held.path if held is not None and held.log_id == log_id else None

    def answered(self, id: str) -> None:
        held = self._asked.pop(id, None)
        if held is not None and held.timer:
            held.timer.cancel()

    def drop(self, log_id: str) -> None:
        """Forget a closing session's pads and open requests."""
        for table in (self._sent, self._asked):
            for id in [i for i, h in table.items() if h.log_id == log_id]:
                held = table.pop(id)
                if held.timer:
                    held.timer.cancel()

    def pending(self, id: str) -> bool:
        return id in self._sent or id in self._asked
