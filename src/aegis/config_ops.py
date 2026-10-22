"""The configuration's operations: read, write, detect, doctor, propose, and
``models.list``.

The Settings page uses all six; ``config.doctor`` and ``models.list`` are
open to agents too, because they only read. Writing is a person's: an agent that could write
.aegis.yaml could raise its own permission. A write goes to the file only; the
running server picks it up the way it picks up an editor's (config.py).
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING

from pydantic import BaseModel

from .config import ConfigDoc, stamp_from, write
from . import service
from .doctor import Found, detect, doctor, propose
from .doctor_watch import DoctorWatch
from .ranking import ranking

if TYPE_CHECKING:
    from .app import App

DETECT_TTL_S = 60.0


class WriteParams(BaseModel):
    model_config = {"extra": "forbid"}
    doc: ConfigDoc
    stamp: str | None = None


def register_config_ops(app: App) -> None:
    r = app.registry
    cache: dict[str, tuple[float, list[Found]]] = {}

    def bins() -> dict[str, str]:
        return {
            "claude-code": app.claude_bin,
            "opencode": app.opencode_bin,
            "codex": app.codex_bin,
            "cursor": app.cursor_bin,
        }

    async def checked():
        # What `aegis doctor` prints: the config, the harnesses, the service.
        return await doctor(app.roots, bins()) + service.check()

    app.doctor_watch = DoctorWatch(checked)

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

    @r.op("config.write", WriteParams, admin=True)
    async def config_write(p: WriteParams, caller):
        """Write the Settings form to .aegis.yaml. Refused when the file changed
        since `stamp`; nothing is written when the document has a problem."""
        problems = write(app.config.path, p.doc, stamp_from(p.stamp))
        return {
            "saved": not problems,
            "problems": [f.wire() for f in problems],
            "config": app.config.current().wire(),
        }

    @r.op("config.detect")
    async def config_detect(_, caller):
        """The harnesses installed on this machine: binary, version, models."""
        return [f.wire() for f in await detected()]

    @r.op("config.doctor", agent=True, read=True)
    async def config_doctor(_, caller):
        """Check .aegis.yaml, the harnesses it names and the state directory.
        Each finding has a level (ok, warn, error), where it is, and what is
        wrong."""
        return [f.wire() for f in await doctor(app.roots, bins())]

    @r.op("config.propose", admin=True)
    async def config_propose(_, caller):
        """The first configuration `aegis init` would write here."""
        return propose(await detected()).model_dump()

    @r.op("models.list", agent=True, read=True)
    async def models_list(_, caller):
        """Every model the installed harnesses can run, with its rank among
        its harness's models, Artificial Analysis's Coding and Intelligence
        Indexes, its price in and out per million tokens, the agents that
        name it (at: its index in the agent's models, 0 first) and the epoch
        it stops cooling, or null. Models in use come first, then by
        Intelligence Index, the Coding Index breaking ties; those with no
        Intelligence Index last."""
        agents = app.config.current().agents
        now = time.time()
        rows = []
        for f in await detected():
            for m in f.models:
                used = [
                    {"agent": a.name, "at": a.models.index(v)}
                    for a in agents
                    if a.harness == f.harness
                    for v in (m.value, m.resolved)
                    if v in a.models
                ]
                rows.append(
                    {
                        "harness": f.harness,
                        **m.wire(),
                        "used_by": list({u["agent"]: u for u in used}.values()),
                        "cooling_until": app.cooling.until(f.harness, m.value, now),
                    }
                )
        rows.sort(
            key=lambda x: (
                not x["used_by"],
                x["intelligence"] is None,
                -(x["intelligence"] or 0),
                -(x["coding"] or 0) if x["intelligence"] is not None else 0,
            )
        )
        rk = ranking()
        return {"refreshed": rk.refreshed, "source_url": rk.url, "models": rows}
