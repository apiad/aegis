"""/setup: the turn that hands the onboarding to the agent, and its record.

The steps (what to read, what each habit becomes, how to apply it) live in the
help page ``setup``, written for an agent: agents read it for the version they
run. This module builds the turn, adding what the server knows (its version,
the harnesses installed, the agents and queues configured) and the caller's
last run, so a second run proposes only what is new.

The record is one JSON file per principal under the state directory, written
through the ``setup_record`` tool: in multi-user mode a member's agent runs as
the member's Linux user and cannot write the server's state directory itself.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import TYPE_CHECKING

from pydantic import BaseModel, Field

from .monitors import iso_now
from .ops import OpError

if TYPE_CHECKING:
    from .app import App


class Records:
    def __init__(self, state_root: Path):
        self.dir = state_root / "setup"

    def _path(self, principal: str) -> Path:
        if not principal or "/" in principal or principal.startswith("."):
            raise OpError("bad_params", f"no record for {principal!r}")
        return self.dir / f"{principal}.json"

    def get(self, principal: str) -> dict | None:
        try:
            return json.loads(self._path(principal).read_text())
        except (OSError, ValueError):
            return None

    def put(self, principal: str, rec: dict) -> None:
        self.dir.mkdir(parents=True, exist_ok=True)
        path = self._path(principal)
        tmp = path.with_name(f".{path.name}.tmp")
        tmp.write_text(json.dumps({**rec, "at": iso_now()}, indent=2))
        os.replace(tmp, path)


def facts(version: str, found: list[dict], agents: list[str], queues: list[str]) -> str:
    have = [
        f"{f['harness']} {f.get('version') or ''}".strip()
        for f in found
        if f.get("bin")
    ]
    return " · ".join(
        [
            f"aegis {version}",
            f"harnesses: {', '.join(have) or 'none found'}",
            f"agents: {', '.join(agents) or 'none'}",
            f"queues: {', '.join(queues) or 'none'}",
        ]
    )


def turn(typed: str, facts: str, record: dict | None, now: str) -> tuple[str, str]:
    said = (
        f"The person typed: /setup {typed}"
        if typed
        else "The person typed /setup with no text."
    )
    scratch = typed.strip().lower().startswith("from scratch")
    if scratch:
        last = "They asked to start from scratch: ignore the record of earlier runs.\n"
    elif record is None:
        last = "There is no earlier run: everything you find is new.\n"
    else:
        declined = ", ".join(
            f"{d.get('key')} ({d.get('evidence')})" for d in record.get("declined", [])
        )
        last = (
            f"Their last run {record.get('at')} read sources up to "
            f"{record.get('sources_until')}. Applied then: "
            f"{', '.join(record.get('applied', [])) or 'nothing'}. Declined, with "
            f"the evidence count: {declined or 'nothing'}. Propose what is new "
            "since, and a declined key again only if its evidence has doubled "
            "(help_read setup record).\n"
        )
    how = (
        "They want aegis to take over how they already work. Read the help page\n"
        "in full first (help_read setup) and follow it with them:\n"
        "- look at the machine and change nothing while you look (look, sources);\n"
        "  ask before reading transcripts, saying how many files and how much text;\n"
        "- say in a few lines what you found (summarise);\n"
        "- send one artifact with a row per proposal, its evidence and a\n"
        "  checkbox, and wait for their submit (propose);\n"
        "- apply the ticked rows: config_suggest for .aegis.yaml, schedule_create,\n"
        "  workflow_save, and ordinary edits to their own files (apply);\n"
        "- check with config_doctor, list where each change now lives, and call\n"
        "  setup_record with what was applied and declined (record, check).\n"
    )
    return f"> from setup · {now}", f"{said}\n\n{how}\n{last}\n{facts}\n"


class Declined(BaseModel):
    model_config = {"extra": "forbid"}
    key: str = Field(min_length=1, description="<kind>:<name>, as in applied.")
    evidence: int = Field(ge=0, description="How many times the habit was seen.")


class RecordParams(BaseModel):
    model_config = {"extra": "forbid"}
    applied: list[str] = Field(
        description="Keys of what was applied, each <kind>:<name>, such as "
        "queue:tests or schedule:weekly-prs."
    )
    declined: list[Declined]
    sources_until: str = Field(
        min_length=1, description="The newest transcript time you read, ISO 8601."
    )


def register_setup(app: App) -> Records:
    r = app.registry
    records = Records(app.roots.state_root)

    @r.op("setup.record", RecordParams, agent=True)
    async def record(p: RecordParams, caller):
        """Save what this /setup run applied and declined, for the person
        whose session this is, so their next run proposes only what is new.
        Call it once, at the end of a run (help_read setup record)."""
        records.put(app.who(caller).name, p.model_dump())
        return "ok"

    return records
