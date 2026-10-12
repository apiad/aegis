"""What each person has been offered by the help system, kept on the server.

One small JSON per principal (tokens.py) at ``<state>/help/<principal>.json``,
the name percent-quoted so that one read from a hand-edited tokens.json, such as
``../x``, stays a file in that directory::

    {"tutorial": true, "toured": "2.6.0"}

``tutorial`` turns true when the person starts any tutorial or dismisses the
tutorial hint. ``toured`` is the newest release whose notes they toured
or dismissed. The record is the server's, not the browser's, so a person sees the
same new-tab hints on every device.

The first read of a principal with no record decides who is new. A principal
who owns no session, live or archived, is new: ``toured`` is set to the
installed release, because release notes mean nothing before a first use. One
who owns sessions was here before the help system shipped: ``toured`` stays
empty, so the installed release is offered. The decision is written at once,
so the principal's own first session does not change it.

Writes go through write-then-rename, like the session metas (meta.py), so a
crash leaves the old record or the new one. A record that does not parse, or
does not have this shape, is read as absent and decided again: the worst that
costs is a hint shown once more, where an error would break the new tab.
"""

from __future__ import annotations

import json
import logging
import os
import re
from importlib.metadata import PackageNotFoundError, version as dist_version
from pathlib import Path
from urllib.parse import quote

from .version import DIST

log = logging.getLogger("aegis.helpstate")


def installed() -> str:
    """The release this server runs, from its metadata. A build from git
    reports its last release, which is as far as its notes go."""
    try:
        return dist_version(DIST)
    except PackageNotFoundError:
        return "0.0.0"


def _release(v: str) -> tuple[int, ...]:
    return tuple(int(n) for n in re.findall(r"\d+", v)[:3])


def newer(installed: str, toured: str | None) -> bool:
    """Whether ``installed`` has notes the person has not toured."""
    return toured is None or _release(installed) > _release(toured)


class HelpState:
    def __init__(self, dir: Path) -> None:
        self.dir = dir

    def path(self, principal: str) -> Path:
        return self.dir / f"{quote(principal, safe='')}.json"

    def get(self, principal: str, *, owns_sessions: bool, installed: str) -> dict:
        """The principal's record, decided and written on its first read."""
        rec = self._read(principal)
        if rec is None:
            rec = {"tutorial": False, "toured": None if owns_sessions else installed}
            self._write(principal, rec)
        return rec

    def mark_tutorial(self, principal: str) -> None:
        self._update(principal, tutorial=True)

    def mark_toured(self, principal: str, version: str) -> None:
        """Never backwards: after a downgrade and back, the releases the
        person toured on the newer install are not offered again."""
        rec = self._read(principal)
        if rec and rec["toured"] and newer(rec["toured"], version):
            return
        self._update(principal, toured=version)

    def _update(self, principal: str, **changes: object) -> None:
        rec = self._read(principal) or {"tutorial": False, "toured": None}
        self._write(principal, rec | changes)

    def _read(self, principal: str) -> dict | None:
        p = self.path(principal)
        try:
            rec = json.loads(p.read_text())
        except FileNotFoundError:
            return None
        except (OSError, ValueError) as e:
            log.warning("aegis: %s is unreadable, deciding it again: %s", p, e)
            return None
        toured = rec.get("toured") if isinstance(rec, dict) else None
        if not (
            isinstance(rec, dict)
            and isinstance(rec.get("tutorial"), bool)
            and (toured is None or isinstance(toured, str))
        ):
            log.warning("aegis: %s is not a help record, deciding it again", p)
            return None
        return {"tutorial": rec["tutorial"], "toured": toured}

    def _write(self, principal: str, rec: dict) -> None:
        self.dir.mkdir(parents=True, exist_ok=True)
        target = self.path(principal)
        tmp = target.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(rec))
        os.replace(tmp, target)
