"""config_suggest: an agent proposes .aegis.yaml, a person applies it.

Writing the file is a person's (config_ops.py): an agent that wrote it could
raise its own permission. So the agent sends the whole new file as text, and
the transcript records a card with that text, its diff against the file and
the file's stamp. Apply writes the recorded text through the stamp check, so
what is written is byte for byte what the card showed, and a file that moved
since makes the card stale instead of being overwritten. Only an admin
applies, as only an admin writes Settings. Either outcome wakes the agent.

The card lives in the transcript, not in memory, so it applies after a
restart.
"""

from __future__ import annotations

import difflib
import secrets
from typing import TYPE_CHECKING

from pydantic import BaseModel, Field

from .config import stamp_of, stamp_from, text_problems, write_text
from .monitors import iso_now
from .ops import Caller, OpError

if TYPE_CHECKING:
    from .app import App


class _Strict(BaseModel):
    model_config = {"extra": "forbid"}


class Suggest(_Strict):
    yaml: str = Field(
        min_length=1,
        description="The whole new .aegis.yaml, as it should be written: read "
        "the file first and keep what you do not mean to change, comments "
        "included.",
    )
    why: str = Field(
        min_length=1,
        max_length=500,
        description="What the change is for, shown on the card.",
    )


class SuggestionRef(_Strict):
    log_id: str
    id: str


def header(id: str, outcome: str) -> str:
    return f"> from config:{id} · {outcome} · {iso_now()}"


def _stamp(path) -> str | None:
    st = stamp_of(path)
    return None if st is None else ":".join(str(x) for x in st)


def register_config_suggest(app: App) -> None:
    r, reg = app.registry, app.sessions

    def by(caller: Caller) -> str | None:
        return caller.principal.name if caller.principal else None

    @r.op("config.suggest", Suggest, agent=True)
    async def suggest(p: Suggest, caller):
        """Propose a change to .aegis.yaml (agents, queues, any key). You
        cannot write the file; this puts a card with the diff in your
        transcript, and a person applies or declines it. You are woken with
        `> from config:<id> · applied|declined|stale`. End your turn after
        calling this. Refused when the text does not parse or would not
        validate."""
        if not caller.is_agent or caller.log_id not in reg.sessions:
            raise OpError("agents_only", "only an agent's session can do this")
        s = reg.sessions[caller.log_id]
        if problems := text_problems(p.yaml):
            raise OpError(
                "bad_config",
                "; ".join(f"{f.where}: {f.message}" for f in problems),
            )
        path = app.config.path
        old = path.read_text() if path.exists() else ""
        diff = "".join(
            difflib.unified_diff(
                old.splitlines(keepends=True),
                p.yaml.splitlines(keepends=True),
                ".aegis.yaml",
                ".aegis.yaml (suggested)",
            )
        )
        if not diff:
            raise OpError("no_change", "the text is the file as it is now")
        id = "cfg-" + secrets.token_hex(4)
        s.record_config(
            {
                "kind": "config_suggest",
                "suggest_id": id,
                "why": p.why,
                "yaml": p.yaml,
                "diff": diff,
                "stamp": _stamp(path),
            }
        )
        return {"id": id, "diff": diff}

    def pending(p: SuggestionRef):
        s = reg.open(p.log_id)
        e = next(
            (x for x in s.entries() if x["id"] == p.id and x["kind"] == "config"),
            None,
        )
        if e is None:
            raise OpError("not_found", f"no config suggestion {p.id!r} in this session")
        if e["status"] != "pending":
            raise OpError("gone", f"the suggestion {p.id} is {e['status']}")
        return s, e

    async def done(s, id: str, outcome: str, caller: Caller, body: str) -> None:
        s.record_config(
            {
                "kind": "config_suggest_done",
                "suggest_id": id,
                "outcome": outcome,
                "by": by(caller),
            }
        )
        await s.deliver(header(id, outcome), body)

    @r.op("config.suggestion.apply", SuggestionRef, owned="log_id")
    async def apply(p: SuggestionRef, caller):
        if not app.who(caller).is_admin:
            raise OpError("not_for_members", "an admin applies a config change")
        s, e = pending(p)
        det = e["detail"]
        try:
            problems = write_text(
                app.config.path, det["yaml"], stamp_from(det["stamp"])
            )
        except OpError as err:
            if err.code == "stale":
                await done(
                    s,
                    p.id,
                    "stale",
                    caller,
                    ".aegis.yaml changed since you suggested this; nothing was "
                    "written. Read the file again and suggest anew if it still "
                    "applies.",
                )
            raise
        if problems:
            raise OpError(
                "bad_config", "; ".join(f"{f.where}: {f.message}" for f in problems)
            )
        await done(
            s, p.id, "applied", caller, ".aegis.yaml now holds the text you suggested."
        )
        return "ok"

    @r.op("config.suggestion.decline", SuggestionRef, owned="log_id")
    async def decline(p: SuggestionRef, caller):
        s, _ = pending(p)
        await done(
            s, p.id, "declined", caller, "The person declined it; nothing was written."
        )
        return "ok"
