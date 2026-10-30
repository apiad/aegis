"""The knowledge operations: memory and skill tools for agents, and stats.

Every op works on the caller's own principal (``App.who``): an agent's is its
session's owner, so a member's agent never reaches another member's items, and
a name that belongs to someone else answers ``not_found`` as a name that does
not exist. Reads are open to read-permission sessions, as help_read is; saves
and deletes are not. A save is one commit in the principal's store
(knowledge/store.py).

An item an agent saves on its own initiative (``asked`` false) is written
``status: proposed`` and a card goes in the session's transcript; a proposal
is never hinted, and a proposed skill is not offered as a command, but
skill_read loads it by name. ``asked`` counts only in a turn a person's
message began (Session.person_turn); an item it saves approved still gets a
card that offers Undo. An agent's update or delete of an approved item
applies at once, asked or not, and its card offers Undo, which reverts that
commit. Approve,
Discard and Undo are the session owner's ops on the owner's store, refused
to anyone else, an admin included. The card is a transcript record, so it
works after a restart.

Reading a skill's SKILL.md loads it in the calling session: the name joins
``Session.skills``, which the card shows and which ``skill_run`` checks.

``skill_run`` runs a script the loaded skill declares under ``aegis.scripts``.
The store is out of a member's reach, so the server packs the skill's folder
as a tar and pipes it to one process started through the launcher as the
session's Linux user: a shell that unpacks it into a fresh ``mktemp -d``
directory, which is therefore the member's, runs ``python3`` on the script
there and removes the directory when it ends.
"""

from __future__ import annotations

import asyncio
import base64
import binascii
import hashlib
import io
import json
import logging
import secrets
import tarfile
import time
from contextlib import contextmanager
from datetime import date, datetime, timezone
from typing import TYPE_CHECKING, Literal

from pydantic import BaseModel, Field

from pathlib import Path

from . import launch
from .skill_mcp import SkillToolError, stop_group
from .knowledge import checks, history, importer
from .knowledge.items import Item, check_name, dump, parse_memory, parse_skill
from .monitors import iso_now
from .ops import Caller, OpError

if TYPE_CHECKING:
    from .app import App

log = logging.getLogger("aegis.knowledge")

# Items one knowledge.import call takes: a library of 70 is two calls.
MAX_IMPORT_ITEMS = 50
# knowledge_stats marks an item never_recalled once it was hinted this often
# with no read or invocation: the hint keeps offering it and nobody takes it.
NEVER_RECALLED_HINTS = 10


class _Strict(BaseModel):
    model_config = {"extra": "forbid"}


class Search(_Strict):
    query: str = Field(
        min_length=1,
        description="What you need to know, in your own words, such as "
        "'which host runs the bot'.",
    )
    limit: int = Field(5, ge=1, le=20, description="How many memories to return.")


class ReadMemories(_Strict):
    names: list[str] = Field(
        min_length=1,
        max_length=10,
        description="Memory names, from the hint or from memory_search.",
    )


class SaveMemory(_Strict):
    name: str = Field(
        description="Lowercase letters, digits, - and _, such as 'deploy-host'. "
        "Saving an existing memory's name replaces it."
    )
    description: str = Field(
        min_length=1,
        max_length=200,
        description="One line that says what the memory is about; the hint and "
        "search show it.",
    )
    body: str = Field(
        min_length=1,
        description="The fact in Markdown. For feedback or a project, add "
        "**Why:** and **How to apply:** lines. Link another item as [[name]].",
    )
    type: Literal["user", "feedback", "project", "reference"] = Field(
        "reference",
        description="user: who the person is; feedback: how they want you to "
        "work; project: ongoing work or a constraint; reference: a pointer.",
    )
    lifetime: Literal["permanent", "temporary"] = Field(
        "permanent", description="temporary needs expires."
    )
    expires: date | None = Field(
        None, description="The day a temporary memory stops being true."
    )
    paths: list[str] = Field(
        default_factory=list,
        description="Directory globs where the memory matters most; its hint "
        "ranks higher in sessions working there.",
    )
    asked: bool = Field(
        False,
        description="true when the person asked you to remember this "
        "(/remember, 'remember that ...'). It counts only in a turn that began "
        "with the person's own message.",
    )


class DeleteMemory(_Strict):
    name: str


class DeleteSkill(_Strict):
    name: str


class SearchSkills(_Strict):
    query: str = Field(
        min_length=1,
        description="What you are about to do, in your own words, such as "
        "'cut a release' or 'rotate the bot token'.",
    )
    limit: int = Field(5, ge=1, le=20, description="How many skills to return.")


class ReadSkill(_Strict):
    name: str = Field(description="A skill's name, from the hint or skill_search.")
    file: str | None = Field(
        None,
        description="One file of the skill's folder, as skill_read lists it; "
        "omit it for SKILL.md, which loads the skill.",
    )


class SaveSkill(_Strict):
    name: str = Field(
        description="Lowercase letters, digits, - and _, such as 'release'. "
        "Saving an existing skill's name replaces it, files included."
    )
    description: str = Field(
        min_length=1,
        max_length=300,
        description="When to use it, in one line, such as 'Use when cutting a "
        "release'; the hint and search show it.",
    )
    body: str = Field(min_length=1, description="The procedure in Markdown.")
    files: dict[str, str] = Field(
        default_factory=dict,
        description="Other files of the skill's folder: relative path to the "
        "file's bytes in base64.",
    )
    aegis: dict | None = Field(
        None,
        description="The SKILL.md's aegis block: command (offer it as "
        "/<name>, default true), scripts (files skill_run may run).",
    )
    asked: bool = Field(
        False, description="true when the person asked you to save this skill."
    )


class RunSkill(_Strict):
    skill: str = Field(description="A skill you loaded with skill_read.")
    script: str = Field(
        description="One of the files its aegis.scripts declares, such as 'bump.py'."
    )
    args: list[str] = Field(default_factory=list, description="The script's arguments.")
    timeout_s: int = Field(
        120, ge=1, le=600, description="Seconds before it is killed."
    )


OUTPUT_CHARS = 20000
# The largest file skill_read returns, and the most a skill_save's files hold.
READ_FILE_BYTES = 2**20
SAVE_FILES_BYTES = 20 * 2**20
# How long a timed-out run waits for its pipes after the kill, for a child
# that left the group and still holds them.
PIPES_AFTER_KILL_S = 5.0
# What a run keeps of each stream: enough for OUTPUT_CHARS of any UTF-8.
KEEP_BYTES = 4 * OUTPUT_CHARS
# Unpack the tar on stdin into a fresh directory of the user's, run the
# script there with its arguments, and remove the directory however it ends.
# The script runs in the background and the shell waits for it, so a TERM to
# the shell alone (the launcher's die-with-parent signal) reaches the trap at
# once, and the trap passes it to the whole group.
RUN_SH = (
    "d=$(mktemp -d) || exit 125; trap 'rm -rf -- \"$d\"' EXIT; "
    "trap 'trap \"\" TERM; kill -TERM 0; exit 143' TERM; "
    'tar -x -C "$d" || exit 125; cd -- "$d" && s=$1 && shift || exit 125; '
    'python3 "./$s" "$@" & wait $!'
)


def _cut(data: bytes | bytearray) -> str:
    text = bytes(data).decode("utf-8", "replace")
    if len(text) <= OUTPUT_CHARS and len(data) <= KEEP_BYTES:
        return text
    return text[:OUTPUT_CHARS] + f"\n[cut at {OUTPUT_CHARS} characters]"


def _cut_content(content: list) -> list:
    """An MCP tool's content cut at ``OUTPUT_CHARS``: text counts its
    characters, any other item its JSON."""
    out, left = [], OUTPUT_CHARS
    mark = f"\n[cut at {OUTPUT_CHARS} characters]"
    for c in content:
        text = c.get("text") if isinstance(c, dict) else None
        if isinstance(text, str) and c.get("type") == "text":
            if len(text) > left:
                return [*out, {**c, "text": text[:left] + mark}]
            left -= len(text)
        else:
            size = len(json.dumps(c))
            if size > left:
                return [*out, {"type": "text", "text": mark.lstrip()}]
            left -= size
        out.append(c)
    return out


async def _feed(stream, data: bytes) -> None:
    try:
        stream.write(data)
        await stream.drain()
        stream.close()
    except (BrokenPipeError, ConnectionResetError):
        pass  # it stopped reading; what it wrote says why


async def _drain(stream, buf: bytearray) -> None:
    """Read ``stream`` to its end into ``buf``, so a killed run keeps what it
    printed before."""
    while chunk := await stream.read(65536):
        if len(buf) <= KEEP_BYTES:  # beyond, read on and keep nothing
            buf += chunk[: KEEP_BYTES + 1 - len(buf)]


def _tar(files: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w") as tar:
        for rel, data in files.items():
            info = tarfile.TarInfo(rel)
            info.size, info.mode = len(data), 0o700
            tar.addfile(info, io.BytesIO(data))
    return buf.getvalue()


class CallSkillTool(_Strict):
    skill: str = Field(description="A skill you loaded whose server skill_read listed.")
    tool: str = Field(description="One of the tools skill_read listed for it.")
    args: dict = Field(default_factory=dict, description="The tool's arguments.")
    timeout_s: int = Field(120, ge=1, le=600, description="Seconds to wait for it.")


class CardRef(_Strict):
    log_id: str
    id: str


class CardItem(_Strict):
    log_id: str
    id: str
    name: str


class ImportItem(_Strict):
    source: str = Field(
        min_length=1,
        description="The absolute path of a skill's folder, a memory file or a "
        "procedure doc, as the person ticked it.",
    )
    kind: Literal["skill", "memory", "doc"] = Field(
        description="skill: a folder with SKILL.md, copied whole; memory: one "
        "fact's file, such as Claude Code's auto-memory; doc: a procedure doc "
        "whose when: line becomes the skill's description."
    )
    name: str | None = Field(
        None,
        description="Its name in aegis; the folder's or file's own name when omitted.",
    )
    expires: date | None = Field(
        None,
        description="For a memory that names a date or an event: the day it "
        "stops being true. It is then temporary; otherwise permanent.",
    )


class Setting(_Strict):
    native: bool | None = Field(
        None, description="Set the switch; omit it to read the setting."
    )


class ImportItems(_Strict):
    items: list[ImportItem] = Field(
        min_length=1, description=f"At most {MAX_IMPORT_ITEMS}; split a longer list."
    )
    log_id: str | None = Field(
        None,
        description="A person's call only: the session of theirs whose "
        "transcript gets the card. An agent's card goes in its own session.",
    )


# What a card keeps of a body: enough to read, not a whole skill.
CARD_BODY_CHARS = 4000


def card_header(id: str, outcome: str) -> str:
    return f"> from knowledge:{id} · {outcome} · {iso_now()}"


def digest(it: Item, files: dict[str, bytes] | None = None) -> str:
    """What a proposal's card was drawn from, so Approve never approves a
    later text the person was not shown. A skill's covers ``files``, the
    other files of its folder, so a changed script makes the card stale."""
    h = hashlib.sha256(dump(it).encode())
    for name in sorted(files or {}):
        data = (files or {})[name]
        h.update(f"\0{name}\0{len(data)}\0".encode())
        h.update(data)
    return h.hexdigest()


class Stats(_Strict):
    unused_days: int | None = Field(
        None,
        ge=1,
        description="Only items not read, invoked or run in this many days.",
    )
    kind: Literal["skill", "memory"] | None = None
    flag: Literal["expired", "stale", "never_recalled"] | None = Field(
        None,
        description="Only items with this flag: expired (a temporary memory "
        "past its day), stale (it quotes a path that is gone) or "
        "never_recalled (hinted at least 10 times, never read or invoked).",
    )
    duplicates: bool = Field(
        False,
        description="Also list pairs of items of one kind that are likely "
        "duplicates, with their similarity score.",
    )


def remember_turn(text: str, now: str) -> tuple[str, str]:
    """The turn /remember delivers: the agent saves the text as a memory."""
    return (
        f"> from remember · {now}",
        f"The person typed: /remember {text}\n\n"
        "Save this as one memory with memory_save and asked: true. Choose a "
        "short name, a one-line description and the type; make it temporary "
        "with expires when it names a date or an event that will pass. Then "
        "say in one line what you saved.",
    )


def consolidate_turn(text: str, now: str) -> tuple[str, str]:
    """The turn /consolidate delivers: the agent reviews the person's skills
    and memories by the help page consolidate."""
    said = (
        f"The person typed: /consolidate {text}"
        if text
        else "The person typed /consolidate with no text."
    )
    return (
        f"> from consolidate · {now}",
        f"{said}\n\n"
        "They want their skills and memories reviewed. Read the help page in "
        "full first (help_read consolidate) and follow it:\n"
        "- gather with knowledge_stats (duplicates=true), changing nothing;\n"
        "- send one artifact with a row per proposal, its evidence and a "
        "checkbox, keyed merge:<a>+<b>, renew:<name>, drop:<name>, "
        "fix:<name>, retire:<name> or link:<name>, and wait for their submit;\n"
        "- apply only the ticked rows, each through its tool as one commit, "
        "and say what changed.\n",
    )


def escape_headers(body: str) -> str:
    """A stored body with a backslash before every line that starts like a
    message header (``> from ``), so the transcript's fold never reads part
    of it as a notice or a nudge aegis sent."""
    return "\n".join(
        "\\" + ln if ln.startswith("> from ") else ln for ln in body.split("\n")
    )


def skill_turn(name: str, body: str, args: str, now: str) -> tuple[str, str]:
    """The turn ``/<skill> args`` delivers: the skill's text and the
    person's arguments."""
    said = f"the person's arguments: {args}" if args else "no arguments were given"
    return (
        f"> from skill:{name} · {now}",
        f'Run the skill "{name}". Its text follows; {said}.\n\n' + escape_headers(body),
    )


def offered(it: Item) -> bool:
    """Whether a skill is offered as a slash command: approved, and its
    ``aegis.command`` not false."""
    block = it.meta.get("aegis")
    command = block.get("command", True) if isinstance(block, dict) else True
    return it.kind == "skill" and it.status == "approved" and command is not False


def saved_answer(
    name: str, status: str, card: str | None, unheard: bool = False
) -> dict:
    out: dict = {"saved": name, "status": status}
    if status == "proposed":
        out["note"] = (
            "proposed: a card in your transcript asks the person to approve it;"
            " until then it is not hinted"
        )
        if unheard:
            out["note"] += (
                ". asked counts only in a turn that began with the person's own message"
            )
    if card:
        out["card"] = card
    return out


class ListItems(_Strict):
    query: str = ""
    kind: Literal["all", "skill", "memory", "flagged"] = "all"


class ItemRef(_Strict):
    name: str


class PutItem(_Strict):
    name: str
    text: str = Field(min_length=1)
    stamp: str = Field(min_length=1)


class RestoreItem(_Strict):
    name: str
    sha: str = Field(pattern=r"^[0-9a-f]{7,40}$")


def stamp_of(it: Item) -> str:
    """What the view's edit was opened on: the item's own file, as bytes.
    Blocking: reads it."""
    assert it.path is not None
    return hashlib.sha256(it.path.read_bytes()).hexdigest()


def register_knowledge_ops(app: App) -> None:
    r = app.registry
    kn = app.knowledge

    def principal(caller: Caller) -> str:
        return app.who(caller).name

    def author(caller: Caller) -> str:
        if caller.is_agent:
            s = app.sessions.sessions.get(caller.log_id or "")
            return f"session {s.handle if s else caller.log_id}"
        return principal(caller)

    def agent_session(caller: Caller):
        return (
            app.sessions.sessions.get(caller.log_id or "") if caller.is_agent else None
        )

    def honoured(caller: Caller, asked: bool) -> bool:
        """``asked`` counts only in a turn a person's message began
        (Session.person_turn): an agent woken by a handoff, a queue task or
        a monitor cannot speak for the person."""
        if not asked or not caller.is_agent:
            return asked
        s = agent_session(caller)
        return s is not None and s.person_turn

    def status_for(caller: Caller, asked: bool, held: Item | None) -> str:
        """approved when the person asked, or for a change to an approved
        item; else proposed."""
        if not caller.is_agent or asked:
            return "approved"
        return (
            "approved" if held is not None and held.status == "approved" else "proposed"
        )

    def folder(owner: str, it: Item) -> dict[str, bytes]:
        """A skill's files other than SKILL.md, by relative path; a memory
        has none. Reads files, so never on the loop."""
        if it.kind != "skill":
            return {}
        return {
            n: kn.store.read_file(owner, it.name, n)
            for n in kn.store.files(owner, it.name)
            if n != "SKILL.md"
        }

    def record_card(
        s, op: str, it: Item, sha: str, files: dict[str, bytes] | None = None
    ) -> str:
        id = "kn-" + secrets.token_hex(4)
        expires = it.expires
        files = files or {}
        listed = (
            [{"name": "SKILL.md", "size": len(dump(it).encode())}]
            if it.kind == "skill"
            else []
        )
        listed += [{"name": n, "size": len(files[n])} for n in sorted(files)]
        s.record_knowledge(
            {
                "kind": "knowledge_card",
                "card_id": id,
                "op": op,
                "item_kind": it.kind,
                "name": it.name,
                "description": it.description,
                "body": it.body[:CARD_BODY_CHARS],
                "partial": len(it.body) > CARD_BODY_CHARS,
                "files": listed,
                "type": it.meta.get("type"),
                "lifetime": it.lifetime if it.kind == "memory" else None,
                "expires": expires.isoformat() if expires else None,
                "sha": sha,
                "digest": digest(it, files),
            }
        )
        return id

    async def after_save(
        caller: Caller, owner: str, asked: bool, held, name: str, sha: str
    ):
        """The card a save by an agent leaves: a proposal's; Undo for a change
        to an approved item, asked or not; Undo for an item saved approved
        because the person asked."""
        s = agent_session(caller)
        it = kn.store.get(owner, name)
        if s is None or it is None or not sha:
            return None  # nothing changed: the card already drawn stands
        files = await asyncio.to_thread(folder, owner, it)
        if it.status == "proposed":
            return record_card(s, "new", it, sha, files)
        if held is not None and held.status == "approved":
            return record_card(s, "changed", it, sha, files)
        if asked:
            return record_card(s, "saved", it, sha, files)
        return None

    def memory(owner: str, name: str) -> Item:
        check_name(name)
        it = kn.store.get(owner, name)
        if it is None or it.kind != "memory":
            raise OpError("not_found", f"no memory named {name!r}")
        return it

    @r.op("memory.search", Search, agent=True, read=True)
    async def memory_search(p: Search, caller):
        """Search your memories (facts about the person, their projects and how
        they want you to work) with your own words. Returns the best names with
        their description, score, status and flags; read one with memory_read.
        Proposed and expired memories are included and marked."""
        owner = principal(caller)
        ix = await kn.index(owner)
        qv = await kn.query_vector(p.query) if ix.vectors is not None else None
        hits = ix.rank(p.query, "memory", p.limit, query_vector=qv)
        return {
            "hits": [
                {
                    "name": h.name,
                    "description": h.description,
                    "score": round(h.score, 3),
                    "status": h.status,
                    "flags": kn.flags_of(owner, h.name, "expired" in h.flags),
                }
                for h in hits
            ]
        }

    @r.op("memory.read", ReadMemories, agent=True, read=True)
    async def memory_read(p: ReadMemories, caller):
        """Read one or more memories in full, by name: the ones the hint on
        your message names, or memory_search's hits."""
        owner = principal(caller)
        items = [memory(owner, n) for n in p.names]
        session = caller.log_id or ""
        for it in items:
            rank = kn.read_rank(session, it.name)
            kn.usage.add(
                owner,
                session,
                "memory",
                it.name,
                "read",
                f"rank={rank}" if rank else None,
            )
        return {"memories": [{"name": it.name, "text": dump(it)} for it in items]}

    @r.op("memory.save", SaveMemory, agent=True)
    async def memory_save(p: SaveMemory, caller):
        """Save a memory: one fact that later sessions should know, such as
        who the person is, how they want you to work, or where something
        runs. Saving an existing name replaces that memory. Set asked: true
        when the person's message that began this turn asked you to remember
        it; it is then saved at once, with a card that lets them undo it.
        Otherwise a new memory is
        proposed: a card in your transcript asks the person to approve it,
        and it is not hinted until then; a change to an approved one applies
        at once, and its card lets the person undo it."""
        owner = principal(caller)
        check_name(p.name)
        if p.lifetime == "temporary" and p.expires is None:
            raise OpError("bad_params", "a temporary memory needs expires")
        held = kn.store.get(owner, p.name)
        if held is not None and held.kind != "memory":
            held = None  # the store refuses it as name_taken
        created = (held.meta.get("created") if held is not None else None) or (
            datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        )
        asked = honoured(caller, p.asked)
        status = status_for(caller, asked, held)
        meta = {
            "type": p.type,
            "lifetime": p.lifetime,
            "expires": p.expires if p.lifetime == "temporary" else None,
            "status": status,
            "paths": p.paths,
            # Where its relative paths were written (knowledge/checks.py).
            "cwd": str(s.spec.cwd)
            if (s := agent_session(caller)) and s.spec.cwd
            else None,
            "created": created,
            "source": author(caller),
        }
        meta = {k: v for k, v in meta.items() if v is not None}
        it = Item("memory", p.name, p.description.strip(), p.body.rstrip() + "\n", meta)
        sha = await kn.store.save(owner, it, author(caller))
        await kn.refresh(owner)  # the next send's hint sees it
        kn.usage.add(owner, caller.log_id or "", "memory", p.name, "saved")
        card = await after_save(caller, owner, asked, held, p.name, sha)
        out = saved_answer(p.name, status, card, p.asked and not asked)
        if (now := kn.store.get(owner, p.name)) is not None:
            # Its paths are looked for in the background: a hung or failing
            # read must not hold or fail a save that has committed.
            app.knowledge_check_soon(owner, [now])
            if now.expired():
                out["flags"] = ["expired"]
        return out

    @r.op("memory.delete", DeleteMemory, agent=True)
    async def memory_delete(p: DeleteMemory, caller):
        """Delete a memory that is no longer true. The deletion is kept in
        the store's history: deleting an approved memory puts a card in your
        transcript that lets the person undo it, and the Knowledge view
        restores it from its history."""
        owner = principal(caller)
        held = memory(owner, p.name)
        sha = await kn.store.delete(owner, p.name, author(caller))
        await kn.refresh(owner)
        kn.usage.add(owner, caller.log_id or "", "memory", p.name, "deleted")
        kn.drop_flags(owner, p.name)
        out: dict = {"deleted": p.name, "sha": sha}
        s = agent_session(caller)
        if s is not None and held.status == "approved" and sha:
            out["card"] = record_card(s, "deleted", held, sha)
        return out

    def need_full(s, what: str) -> None:
        """A skill's scripts and server run on the server without asking, so
        only a full-permission session reaches them, as with a workflow's
        shell steps."""
        if s is not None and s.spec.permission != "full":
            raise OpError(
                "not_allowed",
                f"{what} runs on the server without asking, so only a session "
                f"with full permission may; yours has {s.spec.permission}",
            )

    def skill(owner: str, name: str) -> Item:
        check_name(name)
        it = kn.store.get(owner, name)
        if it is None or it.kind != "skill":
            raise OpError("not_found", f"no skill named {name!r}")
        return it

    @r.op("skill.delete", DeleteSkill, agent=True)
    async def skill_delete(p: DeleteSkill, caller):
        """Delete a skill nobody uses any more, its whole folder. The deletion
        is kept in the store's history: deleting an approved skill puts a
        card in your transcript that lets the person undo it, and the
        Knowledge view restores it from its history."""
        owner = principal(caller)
        held = await asyncio.to_thread(skill, owner, p.name)
        sha = await kn.store.delete(owner, p.name, author(caller))
        await kn.refresh(owner)
        kn.usage.add(owner, caller.log_id or "", "skill", p.name, "deleted")
        out: dict = {"deleted": p.name, "sha": sha}
        s = agent_session(caller)
        if s is not None and held.status == "approved" and sha:
            out["card"] = record_card(s, "deleted", held, sha)
        return out

    @r.op("skill.search", SearchSkills, agent=True, read=True)
    async def skill_search(p: SearchSkills, caller):
        """Search your skills (procedures for a kind of task, such as cutting
        a release) with your own words, before you start a task. Returns the
        best names with their description, score and status; load one with
        skill_read."""
        ix = await kn.index(principal(caller))
        qv = await kn.query_vector(p.query) if ix.vectors is not None else None
        hits = ix.rank(p.query, "skill", p.limit, query_vector=qv)
        return {
            "hits": [
                {
                    "name": h.name,
                    "description": h.description,
                    "score": round(h.score, 3),
                    "status": h.status,
                }
                for h in hits
            ]
        }

    def mcp_command(it: Item) -> list[str] | None:
        block = it.meta.get("aegis")
        mcp = block.get("mcp") if isinstance(block, dict) else None
        cmd = mcp.get("command") if isinstance(mcp, dict) else None
        if isinstance(cmd, list) and cmd and all(isinstance(c, str) for c in cmd):
            return cmd
        return None

    # (principal, skill) -> (store version, its folder as a tar): what a
    # server's start sends, packed once per version of the skill.
    tars: dict[tuple[str, str], tuple[int, bytes]] = {}

    async def server_of(owner: str, it: Item, s):
        """The session's running server for the skill, started if need be."""
        cmd = mcp_command(it)
        if cmd is None:
            raise OpError("no_mcp", f"the skill {it.name!r} brings no MCP server")
        version = kn.store.item_version(owner, it.name)

        def pack() -> bytes:
            names = kn.store.files(owner, it.name)
            return _tar({n: kn.store.read_file(owner, it.name, n) for n in names})

        async def tar() -> bytes:
            held = tars.get((owner, it.name))
            if held is not None and held[0] == version:
                return held[1]
            data = await asyncio.to_thread(pack)
            tars[(owner, it.name)] = (version, data)
            return data

        try:
            return await app.skill_servers.ensure(
                s.log_id,
                it.name,
                cmd,
                tar,
                s.spec.linux_user,
                version,
            )
        except SkillToolError as e:
            raise OpError("mcp_failed", f"the skill's server: {e}") from e

    @r.op("skill.read", ReadSkill, agent=True, read=True)
    async def skill_read(p: ReadSkill, caller):
        """Load a skill: returns its SKILL.md, which you then follow, and the
        files of its folder. With file, returns that one file instead, up to
        1 MiB."""
        owner = principal(caller)
        it = skill(owner, p.name)
        files = kn.store.files(owner, p.name)
        if p.file is not None and p.file != "SKILL.md":
            data = kn.store.read_file(owner, p.name, p.file, READ_FILE_BYTES)
            try:
                return {"name": p.name, "file": p.file, "text": data.decode("utf-8")}
            except UnicodeDecodeError:
                return {
                    "name": p.name,
                    "file": p.file,
                    "base64": base64.b64encode(data).decode("ascii"),
                }
        session = caller.log_id or ""
        rank = kn.read_rank(session, it.name)
        kn.usage.add(
            owner, session, "skill", it.name, "read", f"rank={rank}" if rank else None
        )
        s = app.sessions.sessions.get(session)
        if s is not None:
            s.load_skill(it.name)
        out: dict = {"name": it.name, "text": dump(it), "files": files}
        # Its MCP server's tools, called with skill_tool. Only a full session
        # may call them, so any other starts nothing.
        if mcp_command(it) is not None and s is not None:
            if s.spec.permission != "full":
                out["tools_error"] = (
                    "this skill's MCP tools need a session with full "
                    f"permission; yours has {s.spec.permission}"
                )
            else:
                try:
                    out["tools"] = (await server_of(owner, it, s)).tools
                except OpError as e:
                    out["tools_error"] = e.message
        return out

    @r.op("skill.save", SaveSkill, agent=True)
    async def skill_save(p: SaveSkill, caller):
        """Save a skill: a procedure later sessions should follow for one
        kind of task, with any files it needs, 20 MiB at most in all.
        Saving an existing name replaces it. Set asked: true when the person
        asked for it. Otherwise a new skill is proposed: a card asks the
        person to approve it, and until then it is neither hinted nor a
        slash command, though skill_read loads it."""
        owner = principal(caller)
        check_name(p.name)
        try:
            extra = {k: base64.b64decode(v, validate=True) for k, v in p.files.items()}
        except (binascii.Error, ValueError):
            raise OpError("bad_params", "files must hold base64") from None
        if (total := sum(map(len, extra.values()))) > SAVE_FILES_BYTES:
            raise OpError(
                "too_large",
                f"the files hold {total} bytes; a skill holds at most "
                f"{SAVE_FILES_BYTES}",
            )
        held = kn.store.get(owner, p.name)
        if held is not None and held.kind != "skill":
            held = None  # the store refuses it as name_taken
        asked = honoured(caller, p.asked)
        status = status_for(caller, asked, held)
        meta: dict = {"aegis": p.aegis} if p.aegis else {}
        if status == "proposed":
            meta["status"] = status
        it = Item("skill", p.name, p.description.strip(), p.body.rstrip() + "\n", meta)
        sha = await kn.store.save(owner, it, author(caller), extra=extra)
        await kn.refresh(owner)
        kn.usage.add(owner, caller.log_id or "", "skill", p.name, "saved")
        card = await after_save(caller, owner, asked, held, p.name, sha)
        return saved_answer(p.name, status, card, p.asked and not asked)

    @r.op("skill.run", RunSkill, agent=True)
    async def skill_run(p: RunSkill, caller):
        """Run a script a skill you loaded declares under aegis.scripts, with
        arguments, in a fresh copy of the skill's folder. Returns its exit
        code, stdout and stderr, each cut at 20,000 characters, and whether
        it timed out. Needs a session with full permission."""
        owner = principal(caller)
        it = skill(owner, p.skill)
        s = app.sessions.sessions.get(caller.log_id or "")
        need_full(s, "a skill's script")
        if s is None or it.name not in s.skills:
            raise OpError(
                "not_loaded", f"load the skill {it.name!r} with skill_read first"
            )
        block = it.meta.get("aegis")
        declared = block.get("scripts") if isinstance(block, dict) else None
        if not isinstance(declared, list) or p.script not in declared:
            raise OpError(
                "not_declared",
                f"{p.script!r} is not among the scripts {it.name!r} declares",
            )

        def pack() -> bytes:
            names = kn.store.files(owner, it.name)
            if p.script not in names:
                raise OpError("not_found", f"the skill has no file {p.script!r}")
            return _tar({n: kn.store.read_file(owner, it.name, n) for n in names})

        tar = await asyncio.to_thread(pack)
        try:
            proc = await launch.child(
                ["bash", "-c", RUN_SH, "skill-run", p.script, *p.args],
                user=s.spec.linux_user,
                via=launch.resolve_via(s.spec.agent),
                cwd="/",
                stdin=launch.PIPE,
                stdout=launch.PIPE,
                stderr=launch.PIPE,
                new_session=True,
                die_with_parent=True,
            )
        except OSError as e:
            raise OpError("run_failed", f"the script could not start: {e}") from e
        out, err = bytearray(), bytearray()
        io_done = asyncio.gather(
            _feed(proc.stdin, tar), _drain(proc.stdout, out), _drain(proc.stderr, err)
        )
        code: int | None = None
        timed_out = False
        with app.skill_servers.tracking(s.log_id, proc, s.spec.linux_user):
            try:
                await asyncio.wait_for(asyncio.shield(io_done), p.timeout_s)
                code = await proc.wait()
            except TimeoutError:
                timed_out = True
                # The whole group: python3 under the shell holds the pipes too.
                await stop_group(proc, s.spec.linux_user)
                try:
                    # A child that left the group may still hold them.
                    await asyncio.wait_for(io_done, PIPES_AFTER_KILL_S)
                except TimeoutError:
                    transport = getattr(proc, "_transport", None)
                    if transport is not None:
                        transport.close()
                err += f"\n[killed after {p.timeout_s}s]".encode()
        s.ran_skill(it.name)
        kn.usage.add(owner, s.log_id, "skill", it.name, "ran", p.script)
        return {
            "exit": code,
            "stdout": _cut(out),
            "stderr": _cut(err),
            "timed_out": timed_out,
        }

    @r.op("skill.tool", CallSkillTool, agent=True)
    async def skill_tool(p: CallSkillTool, caller):
        """Call a tool of the MCP server a skill you loaded brings; skill_read
        lists those tools and their arguments. Only this session reaches the
        server, and it stops when the session closes. The answer is cut at
        20,000 characters. Needs a session with full permission."""
        owner = principal(caller)
        check_name(p.skill)
        s = app.sessions.sessions.get(caller.log_id or "")
        need_full(s, "a skill's MCP server")
        # A server running on the skill as it is now is called without
        # reading the store; any other goes through the store and starts.
        name = p.skill
        if s is None or name not in s.skills:
            skill(owner, name)  # not_found first, for a name nobody has
            raise OpError(
                "not_loaded", f"load the skill {name!r} with skill_read first"
            )
        version = kn.store.item_version(owner, name)
        server = app.skill_servers.current(s.log_id, name, version)
        if server is None:
            server = await server_of(owner, skill(owner, name), s)
        if p.tool not in {t["name"] for t in server.tools}:
            raise OpError(
                "no_such_tool",
                f"{name!r} has no tool {p.tool!r}; it has "
                + ", ".join(t["name"] for t in server.tools),
            )
        try:
            got = await app.skill_servers.call(
                s.log_id, name, p.tool, p.args, p.timeout_s
            )
        except SkillToolError as e:
            raise OpError("mcp_failed", f"{name}/{p.tool}: {e}") from e
        s.ran_skill(name)
        kn.usage.add(owner, s.log_id, "skill", name, "ran", f"tool={p.tool}")
        return {
            "is_error": bool(got.get("isError")),
            "content": _cut_content(got.get("content") or []),
        }

    def reader(caller: Caller):
        """The runner that reads an import's sources: as the session's
        Linux user for an agent, else as the person's."""
        try:
            if caller.is_agent and (caller.log_id or "") in app.sessions.sessions:
                return app.place_of_session(caller.log_id or "").run
            return app.place_of(caller).run
        except PermissionError as e:
            raise OpError("not_allowed", str(e)) from None

    @r.op("knowledge.import", ImportItems, agent=True)
    async def knowledge_import(p: ImportItems, caller):
        """Move the skills, memories and procedure docs the person ticked on
        the /setup page into aegis, one commit each. In the turn the
        person's submit began, the ticks are their approval and each item is
        saved approved, with one card that undoes each; in any other turn
        each is a proposal with its own card. Reads each source as the
        session's user; a skill's folder is copied whole, up to 20 MiB. A
        name aegis already holds is refused, never overwritten. Returns what
        came in and what was refused, and leaves every source where it is:
        remove each original with its own ticked row (help_read setup)."""
        owner = principal(caller)
        s = agent_session(caller)
        if not caller.is_agent:
            # A person's own call: their approval, carded in a session of theirs.
            if not p.log_id:
                raise OpError(
                    "bad_params", "name the session for the import's card: log_id"
                )
            s = app.sessions.open(p.log_id)
            if (s.spec.owner or app.user) != owner:
                raise OpError("not_owner", f"{p.log_id} is not one of your sessions")
        if len(p.items) > MAX_IMPORT_ITEMS:
            raise OpError(
                "too_many",
                f"{len(p.items)} items; one call imports at most "
                f"{MAX_IMPORT_ITEMS}: split them over several calls",
            )
        approved = honoured(caller, True)
        run = reader(caller)
        now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        done: list[dict] = []
        refused: list[dict] = []
        memories: list[Item] = []
        for x in p.items:
            try:
                it, extra = await run(
                    importer.load, x.kind, Path(x.source), x.name, x.expires, now
                )
                if not approved:
                    it = Item(
                        it.kind,
                        it.name,
                        it.description,
                        it.body,
                        {**it.meta, "status": "proposed"},
                    )
                sha = await kn.store.save(
                    owner, it, author(caller), extra=extra, verb="import", fresh=True
                )
            except OpError as e:
                refused.append(
                    {"source": x.source, "error": e.code, "message": e.message}
                )
                continue
            except OSError as e:
                refused.append(
                    {"source": x.source, "error": "unreadable", "message": str(e)}
                )
                continue
            except Exception as e:
                # One item's fault never strands the ones already committed.
                log.exception("aegis: importing %s failed", x.source)
                refused.append(
                    {
                        "source": x.source,
                        "error": "failed",
                        "message": f"{type(e).__name__}: {e}",
                    }
                )
                continue
            done.append(
                {"source": x.source, "kind": it.kind, "name": it.name, "sha": sha}
            )
            if it.kind == "memory":
                memories.append(it)
        out: dict = {
            "status": "approved" if approved else "proposed",
            "imported": [{k: d[k] for k in ("source", "kind", "name")} for d in done],
            "refused": refused,
        }
        if not done:
            return out
        # The card first: whatever fails after, each committed item has one.
        if s is not None and not approved:
            # Not the person's turn: each item waits for its own Approve.
            out["cards"] = []
            for d in done:
                try:
                    it = kn.store.get(owner, d["name"])
                    if it is not None:
                        files = await asyncio.to_thread(folder, owner, it)
                        out["cards"].append(record_card(s, "new", it, d["sha"], files))
                except Exception:
                    log.exception("aegis: the card of %s failed", d["name"])
        elif s is not None:
            id = "kn-" + secrets.token_hex(4)
            s.record_knowledge(
                {
                    "kind": "knowledge_card",
                    "card_id": id,
                    "op": "imported",
                    "item_kind": "import",
                    "name": f"{len(done)} item{'s' if len(done) != 1 else ''}",
                    "items": done,
                }
            )
            out["card"] = id
        errors: list[str] = []

        async def step(what: str, fn, *a) -> None:
            try:
                got = fn(*a)
                if asyncio.iscoroutine(got):
                    await got
            except Exception as e:
                log.exception("aegis: after an import, %s failed", what)
                errors.append(f"{what}: {e}")

        await step("refreshing the index", kn.refresh, owner)
        await step(
            "the usage log",
            lambda: [
                kn.usage.add(
                    owner, caller.log_id or "", d["kind"], d["name"], "saved", "import"
                )
                for d in done
            ],
        )
        await step(
            "writing the setup record",
            app.setup_records.add_imported,
            owner,
            [{k: d[k] for k in ("source", "kind", "name")} | {"at": now} for d in done],
        )
        if memories:
            await step(
                "checking the memories", app.knowledge_check_soon, owner, memories
            )
        if errors:
            out["error"] = (
                "the items listed under imported are in aegis, but " + "; ".join(errors)
            )
        return out

    # Cards being answered now: a second press while the first is at work is
    # refused, so a card is answered once whatever the timing.
    answering: set[tuple[str, str]] = set()

    def pending_card(p: CardRef, ops: tuple[str, ...], caller: Caller):
        s = app.sessions.open(p.log_id)
        owner = s.spec.owner or app.user
        if principal(caller) != owner:
            # An admin may read a member's transcript, but what the member's
            # agent believes is the member's to approve.
            raise OpError(
                "not_owner", f"only {owner}, who owns this session, answers its cards"
            )
        e = s.fold().entry(p.id)
        if e is None or e["kind"] != "knowledge" or e["detail"].get("op") not in ops:
            raise OpError(
                "not_found", f"no such knowledge card {p.id!r} in this session"
            )
        if e["status"] != "pending" or (s.log_id, p.id) in answering:
            raise OpError("gone", f"the card {p.id} was already answered")
        # The session owner's store: the card is about the agent's items.
        return s, e, owner

    @contextmanager
    def answer(s, id: str):
        """Hold the card while one press acts on it and records its outcome."""
        answering.add((s.log_id, id))
        try:
            yield
        finally:
            answering.discard((s.log_id, id))

    def unrecord(owner: str, kind: str, name: str) -> None:
        """Take an item out of the setup record's imports; a failed write is
        logged, never raised into a press that already changed the store."""
        try:
            app.setup_records.drop_imported(owner, kind, name)
        except Exception:
            log.exception("aegis: the setup record of %s could not be written", owner)

    async def reflag(owner: str, name: str) -> None:
        """An answered card's item flagged as it now is: checked again, or
        its flags dropped when it is gone."""
        it = kn.store.get(owner, name)
        if it is None or it.kind != "memory":
            kn.drop_flags(owner, name)
        else:
            await app.bounded_check(owner, [it])

    async def card_done(s, id: str, outcome: str, caller: Caller, body: str) -> None:
        s.record_knowledge(
            {
                "kind": "knowledge_card_done",
                "card_id": id,
                "outcome": outcome,
                "by": principal(caller),
            }
        )
        await s.deliver(card_header(id, outcome), body)

    def as_shown(d: dict, owner: str):
        """The store's check, under its lock, that the proposal is still the
        text its card showed: else ``gone`` or ``stale``, and nothing moves."""
        what = f"the {d['item_kind']} {d['name']}"

        def expect(it: Item | None) -> None:
            if it is None or it.kind != d["item_kind"]:
                raise OpError("gone", f"{what} no longer exists")
            if it.status != "proposed" or digest(it, folder(owner, it)) != d.get(
                "digest"
            ):
                raise OpError("stale", f"{what} changed since this card was shown")

        return expect

    async def refused(s, id: str, err: OpError, caller: Caller, d: dict) -> None:
        """Tell the agent why a press did nothing, for the outcomes that end
        the card."""
        what = f"the {d['item_kind']} {d['name']}"
        if err.code == "gone":
            await card_done(
                s, id, "gone", caller, f"{what} no longer exists; nothing was done."
            )
        elif err.code == "stale":
            await card_done(
                s,
                id,
                "stale",
                caller,
                f"{what} changed since its card was shown; nothing was done.",
            )
        elif err.code == "conflict":
            await card_done(
                s,
                id,
                "conflict",
                caller,
                f"The person tried to undo your change to {what}, but it changed "
                "again since; nothing was undone.",
            )

    @r.op("knowledge.card.approve", CardRef, owned="log_id")
    async def card_approve(p: CardRef, caller):
        s, e, owner = pending_card(p, ("new",), caller)
        d = e["detail"]
        with answer(s, p.id):
            try:
                await kn.store.set_status(
                    owner,
                    d["name"],
                    "approved",
                    principal(caller),
                    "approve",
                    expect=as_shown(d, owner),
                )
            except OpError as err:
                await refused(s, p.id, err, caller, d)
                raise
            await kn.refresh(owner)
            await reflag(owner, d["name"])
            it = kn.store.get(owner, d["name"])
            kn.usage.add(owner, s.log_id, d["item_kind"], d["name"], "approved")
            offered_as = (
                f", and offered as /{d['name']}"
                if it is not None and offered(it)
                else ""
            )
            await card_done(
                s,
                p.id,
                "approved",
                caller,
                f"The person approved the {d['item_kind']} {d['name']}; it is hinted "
                f"from now on{offered_as}.",
            )
        return "ok"

    @r.op("knowledge.card.discard", CardRef, owned="log_id")
    async def card_discard(p: CardRef, caller):
        s, e, owner = pending_card(p, ("new",), caller)
        d = e["detail"]
        with answer(s, p.id):
            try:
                await kn.store.delete(
                    owner,
                    d["name"],
                    principal(caller),
                    "discard",
                    expect=as_shown(d, owner),
                )
            except OpError as err:
                await refused(s, p.id, err, caller, d)
                raise
            await kn.refresh(owner)
            await reflag(owner, d["name"])
            kn.usage.add(owner, s.log_id, d["item_kind"], d["name"], "discarded")
            unrecord(owner, d["item_kind"], d["name"])
            await card_done(
                s,
                p.id,
                "discarded",
                caller,
                f"The person discarded the proposed {d['item_kind']} {d['name']}; "
                "it was deleted.",
            )
        return "ok"

    @r.op("knowledge.card.undo", CardRef, owned="log_id")
    async def card_undo(p: CardRef, caller):
        s, e, owner = pending_card(p, ("changed", "deleted", "saved"), caller)
        d = e["detail"]
        what = f"the {d['item_kind']} {d['name']}"
        with answer(s, p.id):
            try:
                await kn.store.revert(
                    owner, d["sha"], d["item_kind"], d["name"], principal(caller)
                )
            except OpError as err:
                await refused(s, p.id, err, caller, d)
                raise
            await kn.refresh(owner)
            await reflag(owner, d["name"])
            done = {
                "deleted": "restored",
                "saved": "as it was before you saved it",
            }.get(d["op"], "back as it was before your change")
            await card_done(
                s,
                p.id,
                "undone",
                caller,
                f"The person undid your change: {what} is {done}.",
            )
        return "ok"

    @r.op("knowledge.card.unimport", CardItem, owned="log_id")
    async def card_unimport(p: CardItem, caller):
        """Undo one item of an import card: its import commit is reverted,
        so it leaves aegis. Its original is wherever it then was."""
        s, e, owner = pending_card(
            CardRef(log_id=p.log_id, id=p.id), ("imported",), caller
        )
        row = next(
            (x for x in e["detail"].get("items") or [] if x["name"] == p.name), None
        )
        if row is None:
            raise OpError("not_found", f"the card {p.id} imported no {p.name!r}")
        key = f"{p.id}/{p.name}"
        if row.get("outcome") or (s.log_id, key) in answering:
            raise OpError("gone", f"{p.name!r} was already undone")
        what = f"the {row['kind']} {p.name}"

        def item_done(outcome: str) -> None:
            s.record_knowledge(
                {
                    "kind": "knowledge_card_item_done",
                    "card_id": p.id,
                    "name": p.name,
                    "outcome": outcome,
                    "by": principal(caller),
                }
            )

        def nothing_added() -> None:
            """A skill's folder holds only what the import brought: a revert
            would leave a file added since behind, in a folder with no
            SKILL.md."""
            if row["kind"] != "skill":
                return
            rel = f"skills/{p.name}"
            top = kn.store.dir(owner) / rel
            now = {q.relative_to(top).as_posix() for q in top.rglob("*") if q.is_file()}
            added = sorted(
                now - set(history.files_at(kn.store.dir(owner), row["sha"], rel))
            )
            if added:
                raise OpError(
                    "conflict",
                    f"{what} gained files since the import ({', '.join(added)}); "
                    "undoing it would leave them behind, so nothing was undone",
                )

        with answer(s, key):
            try:
                await kn.store.revert(
                    owner,
                    row["sha"],
                    row["kind"],
                    p.name,
                    principal(caller),
                    expect=nothing_added,
                )
            except OpError as err:
                if err.code == "conflict":
                    item_done("conflict")
                    await s.deliver(
                        card_header(p.id, "conflict"),
                        f"The person tried to undo the import of {what}, but it "
                        f"changed since; nothing was undone. {err.message}",
                    )
                raise
            await kn.refresh(owner)
            await reflag(owner, p.name)
            kn.usage.add(owner, s.log_id, row["kind"], p.name, "deleted", "by=person")
            item_done("undone")
            unrecord(owner, row["kind"], p.name)
            await s.deliver(
                card_header(p.id, "undone"),
                f"The person undid the import of {what}: it is no longer in "
                "aegis. Its original is wherever it was; if you removed it, "
                "tell them where it was.",
            )
        return "ok"

    @r.op("knowledge.stats", Stats, agent=True, read=True)
    async def knowledge_stats(p: Stats, caller):
        """How your skills and memories are used: per item, how often it was
        hinted, read, invoked or run, when it was last used, and its flags
        (expired, or stale:<path> for a quoted path that is gone). unused_days
        keeps only the items not used in that many days, flag only the items
        with that flag. never_recalled marks an item hinted at least 10 times
        and never read or invoked. duplicates adds the pairs of likely
        duplicates (checks.duplicates), each pair once."""
        owner = principal(caller)
        used = await asyncio.to_thread(kn.usage.summary, owner)
        cutoff = time.time() - p.unused_days * 86400 if p.unused_days else None
        out = []
        items = await kn.items(owner)
        for it in items:
            if p.kind and it.kind != p.kind:
                continue
            u = used.get(it.name, {})
            last = u.get("last_used")
            if cutoff is not None and last is not None and last >= cutoff:
                continue
            flags = kn.flags_of(owner, it.name, it.expired())
            never = (
                u.get("hinted", 0) >= NEVER_RECALLED_HINTS
                and not u.get("read")
                and not u.get("invoked")
            )
            if p.flag == "never_recalled":
                if not never:
                    continue
            elif p.flag and not any(f.split(":", 1)[0] == p.flag for f in flags):
                continue
            out.append(
                {
                    "kind": it.kind,
                    "name": it.name,
                    "description": it.description,
                    "status": it.status,
                    "flags": flags,
                    **{k: u.get(k, 0) for k in ("hinted", "read", "invoked", "ran")},
                    "last_used": last,
                    "never_recalled": never,
                }
            )
        if not p.duplicates:
            return {"items": out}
        ix = await kn.index(owner)
        pairs = await asyncio.to_thread(
            checks.duplicates, ix, items, checks.DUPLICATE_SCORE
        )
        dups = [{"a": a, "b": b, "score": score} for a, b, score in pairs]
        return {"items": out, "duplicates": dups}

    # The Knowledge view's ops: a person's, never an agent's, on the caller's
    # own store, an admin's included.

    def row(owner: str, it: Item, used: dict) -> dict:
        u = used.get(it.name, {})
        return {
            "kind": it.kind,
            "name": it.name,
            "description": it.description,
            "status": it.status,
            "flags": kn.flags_of(owner, it.name, it.expired()),
            "last_used": u.get("last_used"),
            "reads": u.get("read", 0) + u.get("invoked", 0),
        }

    @r.op("knowledge.setting", Setting)
    async def knowledge_setting(p: Setting, caller):
        """The caller's own knowledge switch, read or set: native starts
        their sessions without the harness's own skills and memory. A
        session keeps the setting it was started with; the switch reaches the
        sessions started after it."""
        owner = principal(caller)
        if p.native is not None:
            app.setup_records.merge(
                owner, {"knowledge": "native" if p.native else "side-by-side"}
            )
        return {"native": app.knowledge_native(owner)}

    @r.op("knowledge.list", ListItems)
    async def knowledge_list(p: ListItems, caller):
        """The caller's own skills and memories for the Knowledge view:
        proposals first, then skills and memories by name, or by score for a
        query."""
        owner = principal(caller)
        items = await kn.items(owner)
        used = await asyncio.to_thread(kn.usage.summary, owner)
        q = p.query.strip()
        if q:
            # BM25 alone, even with embeddings: the view's search is a filter,
            # and a cosine rank would keep every item.
            ix = await kn.index(owner)
            n = max(len(items), 1)
            hits = ix.rank(q, "skill", n) + ix.rank(q, "memory", n)
            score = {h.name: h.score for h in hits}
            items = sorted(
                (it for it in items if it.name in score), key=lambda it: -score[it.name]
            )
        else:
            items = sorted(items, key=lambda it: (it.kind != "skill", it.name))
        rows = [row(owner, it, used) for it in items]
        if p.kind == "flagged":
            rows = [x for x in rows if x["flags"] or x["status"] == "proposed"]
        elif p.kind != "all":
            rows = [x for x in rows if x["kind"] == p.kind]
        rows.sort(key=lambda x: x["status"] != "proposed")  # stable
        return {"items": rows}

    def held_item(owner: str, name: str) -> Item:
        """Blocking: reads the file."""
        it = kn.store.get(owner, check_name(name))
        if it is None:
            raise OpError("not_found", f"no item named {name!r}")
        return it

    @r.op("knowledge.get", ItemRef)
    async def knowledge_get(p: ItemRef, caller):
        """One item for the view: its file's whole text, the stamp an edit
        sends back, and a skill's files."""
        owner = principal(caller)

        def read():
            it = held_item(owner, p.name)
            data = it.path.read_bytes() if it.path else b""
            files = kn.store.files(owner, it.name) if it.kind == "skill" else []
            return it, data, files

        it, data, files = await asyncio.to_thread(read)
        used = await asyncio.to_thread(kn.usage.summary, owner)
        return {
            **row(owner, it, used),
            "text": data.decode("utf-8"),
            "stamp": hashlib.sha256(data).hexdigest(),
            "files": files,
        }

    @r.op("knowledge.put", PutItem)
    async def knowledge_put(p: PutItem, caller):
        """The person's edit of an item's own file. It is saved approved, as
        one commit. Refused as stale when the file changed since ``stamp``
        (knowledge.get's), so an edit never overwrites a change it did not
        see."""
        owner = principal(caller)
        held = await asyncio.to_thread(held_item, owner, p.name)
        parse = parse_memory if held.kind == "memory" else parse_skill
        try:
            got = parse(p.text, p.name)
        except ValueError as e:
            raise OpError("bad_params", str(e)) from None
        new = Item(
            got.kind,
            got.name,
            got.description,
            got.body,
            {**got.meta, "status": "approved"},
        )
        if new.kind == "memory" and new.lifetime == "temporary" and not new.expires:
            raise OpError("bad_params", "a temporary memory needs expires")

        def expect(it: Item | None) -> None:
            if it is None or it.kind != held.kind:
                raise OpError("not_found", f"no {held.kind} named {p.name!r}")
            if stamp_of(it) != p.stamp:
                raise OpError(
                    "stale",
                    f"{p.name!r} changed since you opened it; reload it and edit again",
                )

        who = principal(caller)
        sha = await kn.store.replace(owner, new, who, "edit", expect=expect)
        await kn.refresh(owner)
        kn.usage.add(owner, "", new.kind, new.name, "saved", "by=person")
        now = await asyncio.to_thread(held_item, owner, p.name)
        if now.kind == "memory":
            app.knowledge_check_soon(owner, [now])
        return {
            "saved": p.name,
            "sha": sha,
            "status": now.status,
            "stamp": await asyncio.to_thread(stamp_of, now),
        }

    @r.op("knowledge.history", ItemRef)
    async def knowledge_history(p: ItemRef, caller):
        """The commits that changed an item, newest first, a deleted one's
        included."""
        owner = principal(caller)
        return {"history": await asyncio.to_thread(kn.store.history, owner, p.name)}

    @r.op("knowledge.restore", RestoreItem)
    async def knowledge_restore(p: RestoreItem, caller):
        """Put an item back as it was at one of its commits, as a new commit."""
        owner = principal(caller)
        sha = await kn.store.restore(owner, p.name, p.sha, principal(caller))
        await kn.refresh(owner)
        it = await asyncio.to_thread(kn.store.get, owner, p.name)
        if it is not None:
            kn.usage.add(owner, "", it.kind, it.name, "saved", "by=person")
        await reflag(owner, p.name)
        return {"restored": p.name, "sha": sha}

    @r.op("knowledge.remove", ItemRef)
    async def knowledge_remove(p: ItemRef, caller):
        """Delete an item, as a commit: its history keeps it, and Restore
        brings it back."""
        owner = principal(caller)
        held = await asyncio.to_thread(held_item, owner, p.name)
        sha = await kn.store.delete(owner, p.name, principal(caller))
        await kn.refresh(owner)
        kn.usage.add(owner, "", held.kind, p.name, "deleted", "by=person")
        kn.drop_flags(owner, p.name)
        return {"removed": p.name, "sha": sha}
