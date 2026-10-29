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
from typing import TYPE_CHECKING, Literal

from pydantic import BaseModel, Field

from .monitors import iso_now
from .ops import OpError

if TYPE_CHECKING:
    from .app import App


class Records:
    """One record per principal. Reads come from memory after the first, so
    the knowledge setting can be asked at every spawn; each write replaces
    the file whole, merged over what it held."""

    def __init__(self, state_root: Path):
        self.dir = state_root / "setup"
        self._held: dict[str, dict | None] = {}

    def _path(self, principal: str) -> Path:
        if not principal or "/" in principal or principal.startswith("."):
            raise OpError("bad_params", f"no record for {principal!r}")
        return self.dir / f"{principal}.json"

    def get(self, principal: str) -> dict | None:
        if principal not in self._held:
            try:
                got = json.loads(self._path(principal).read_text())
            except (OSError, ValueError):
                got = None
            self._held[principal] = got if isinstance(got, dict) else None
        held = self._held[principal]
        return None if held is None else dict(held)

    def put(self, principal: str, rec: dict) -> None:
        self.dir.mkdir(parents=True, exist_ok=True)
        path = self._path(principal)
        tmp = path.with_name(f".{path.name}.tmp")
        whole = {**rec, "at": iso_now()}
        tmp.write_text(json.dumps(whole, indent=2))
        os.replace(tmp, path)
        self._held[principal] = whole

    def merge(self, principal: str, part: dict) -> None:
        """``part``'s keys written over the record's, the rest kept: a run's
        record keeps what the import and the Settings switch wrote."""
        self.put(principal, {**(self.get(principal) or {}), **part})

    def native(self, principal: str) -> bool:
        try:
            return (self.get(principal) or {}).get("knowledge") == "native"
        except OpError:
            return False  # no such principal: nobody's setting

    def imported(self, principal: str) -> list[dict]:
        got = (self.get(principal) or {}).get("imported")
        return got if isinstance(got, list) else []

    def add_imported(self, principal: str, rows: list[dict]) -> None:
        """Each item an import brought in: its source, kind and name, so the
        next /setup proposes only what is new."""
        self.merge(principal, {"imported": [*self.imported(principal), *rows]})

    def drop_imported(self, principal: str, kind: str, name: str) -> None:
        """An import undone or discarded leaves the list, so the next /setup
        proposes its source again."""
        held = self.imported(principal)
        kept = [r for r in held if (r.get("kind"), r.get("name")) != (kind, name)]
        if len(kept) != len(held):
            self.merge(principal, {"imported": kept})


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
    elif record is None or "sources_until" not in record:
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
    if not scratch and record is not None and record.get("imported"):
        # Sources only: a second run proposes a source not listed here.
        last += (
            "Already moved into aegis (knowledge_import), by source: "
            + ", ".join(
                f"{r.get('source')} as {r.get('kind')} {r.get('name')}"
                for r in record["imported"]
            )
            + ".\n"
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
        "  workflow_save, knowledge_import for skills and memories, then each\n"
        "  removal row, and ordinary edits to their own files (apply, knowledge);\n"
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
    knowledge: Literal["native", "side-by-side"] | None = Field(
        None,
        description="native when the person ticked knowledge:native: their "
        "sessions then start without the harness's own skills and memory. "
        "Only in the turn their submit began (else not_person_turn). Omit it "
        "otherwise; the person changes it back in Settings.",
    )


def register_setup(app: App) -> Records:
    r = app.registry
    records = Records(app.roots.state_root)

    @r.op("setup.record", RecordParams, agent=True)
    async def record(p: RecordParams, caller):
        """Save what this /setup run applied and declined, for the person
        whose session this is, so their next run proposes only what is new.
        Call it once, at the end of a run (help_read setup record)."""
        if p.knowledge is not None and caller.is_agent:
            # The switch changes how every later session of theirs starts: only
            # the person's own turn speaks for them (Session.person_turn).
            s = app.sessions.sessions.get(caller.log_id or "")
            if s is None or not s.person_turn:
                raise OpError(
                    "not_person_turn",
                    "knowledge is set only in a turn the person's own message "
                    "began, such as the submit of the /setup page; they can "
                    "set it in Settings",
                )
        records.merge(app.who(caller).name, p.model_dump(exclude_none=True))
        return "ok"

    return records
