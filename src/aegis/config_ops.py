"""The configuration's operations: read, write, detect, doctor, propose.

The Settings page uses all five; ``config.doctor`` is open to agents too,
because it only reads. Writing is a person's: an agent that could write
.aegis.yaml could raise its own permission. A write goes to the file only; the
running server picks it up the way it picks up an editor's (config.py).
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING

from pydantic import BaseModel

from .config import ConfigDoc, write
from .doctor import Found, detect, doctor, propose

if TYPE_CHECKING:
    from .app import App

DETECT_TTL_S = 60.0


class WriteParams(BaseModel):
    model_config = {"extra": "forbid"}
    doc: ConfigDoc
    stamp: tuple[int, int, int] | None = None


def register_config_ops(app: App) -> None:
    r = app.registry
    cache: dict[str, tuple[float, list[Found]]] = {}

    def bins() -> dict[str, str]:
        return {"claude-code": app.claude_bin, "opencode": app.opencode_bin}

    async def detected() -> list[Found]:
        hit = cache.get("found")
        if hit is not None and time.monotonic() - hit[0] < DETECT_TTL_S:
            return hit[1]
        found = await detect(app.roots.config_root, bins())
        cache["found"] = (time.monotonic(), found)
        return found

    @r.op("config.read")
    async def config_read(_, caller):
        """.aegis.yaml as aegis holds it: the form's document, its stamp, and
        the error when the file on disk does not parse."""
        return app.config.current().wire()

    @r.op("config.write", WriteParams)
    async def config_write(p: WriteParams, caller):
        """Write the Settings form to .aegis.yaml. Refused when the file changed
        since `stamp`; nothing is written when the document has a problem."""
        problems = write(app.config.path, p.doc, p.stamp)
        return {
            "saved": not problems,
            "problems": [f.wire() for f in problems],
            "config": app.config.current().wire(),
        }

    @r.op("config.detect")
    async def config_detect(_, caller):
        """The harnesses installed on this machine: binary, version, models."""
        return [f.wire() for f in await detected()]

    @r.op("config.doctor", agent=True)
    async def config_doctor(_, caller):
        """Check .aegis.yaml, the harnesses it names and the state directory.
        Each finding has a level (ok, warn, error), where it is, and what is
        wrong."""
        return [f.wire() for f in await doctor(app.roots, bins())]

    @r.op("config.propose")
    async def config_propose(_, caller):
        """The first configuration `aegis init` would write here."""
        return propose(await detected()).model_dump()
