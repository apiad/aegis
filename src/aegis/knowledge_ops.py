"""The knowledge operations: memory and skill tools for agents, and stats.

Every op works on the caller's own principal (``App.who``): an agent's is its
session's owner, so a member's agent never reaches another member's items, and
a name that belongs to someone else answers ``not_found`` as a name that does
not exist. Reads are open to read-permission sessions, as help_read is; saves
and deletes are not. A save is one commit in the principal's store
(knowledge/store.py). In this slice every save is approved; proposals for
memories an agent saves on its own initiative come with the approval card.

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
import io
import tarfile
import time
from datetime import date, datetime, timezone
from typing import TYPE_CHECKING, Literal

from pydantic import BaseModel, Field

from . import launch
from .skill_mcp import SkillToolError, stop_group
from .knowledge.items import Item, check_name, dump
from .ops import Caller, OpError

if TYPE_CHECKING:
    from .app import App


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
        "(/remember, 'remember that ...').",
    )


class DeleteMemory(_Strict):
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
# What a run keeps of each stream: enough for OUTPUT_CHARS of any UTF-8.
KEEP_BYTES = 4 * OUTPUT_CHARS
# Unpack the tar on stdin into a fresh directory of the user's, run the
# script there with its arguments, and remove the directory however it ends.
RUN_SH = (
    "d=$(mktemp -d) || exit 125; trap 'rm -rf -- \"$d\"' EXIT; "
    "trap 'exit 143' TERM; "
    'tar -x -C "$d" || exit 125; cd -- "$d" && s=$1 && shift && python3 "./$s" "$@"'
)


def _cut(data: bytes | bytearray) -> str:
    text = bytes(data).decode("utf-8", "replace")
    if len(text) <= OUTPUT_CHARS and len(data) <= KEEP_BYTES:
        return text
    return text[:OUTPUT_CHARS] + f"\n[cut at {OUTPUT_CHARS} characters]"


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


class Stats(_Strict):
    unused_days: int | None = Field(
        None,
        ge=1,
        description="Only items not read, invoked or run in this many days.",
    )
    kind: Literal["skill", "memory"] | None = None


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


def skill_turn(name: str, body: str, args: str, now: str) -> tuple[str, str]:
    """The turn ``/<skill> args`` delivers: the skill's text and the
    person's arguments."""
    said = f"the person's arguments: {args}" if args else "no arguments were given"
    return (
        f"> from skill:{name} · {now}",
        f'Run the skill "{name}". Its text follows; {said}.\n\n{body}',
    )


def offered(it: Item) -> bool:
    """Whether a skill is offered as a slash command: approved, and its
    ``aegis.command`` not false."""
    block = it.meta.get("aegis")
    command = block.get("command", True) if isinstance(block, dict) else True
    return it.kind == "skill" and it.status == "approved" and command is not False


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
        hits = (await kn.index(principal(caller))).rank(p.query, "memory", p.limit)
        return {
            "hits": [
                {
                    "name": h.name,
                    "description": h.description,
                    "score": round(h.score, 3),
                    "status": h.status,
                    "flags": h.flags,
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
        when the person asked you to remember it."""
        owner = principal(caller)
        check_name(p.name)
        if p.lifetime == "temporary" and p.expires is None:
            raise OpError("bad_params", "a temporary memory needs expires")
        held = kn.store.get(owner, p.name)
        created = (
            held.meta.get("created")
            if held is not None and held.kind == "memory"
            else None
        ) or datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        meta = {
            "type": p.type,
            "lifetime": p.lifetime,
            "expires": p.expires if p.lifetime == "temporary" else None,
            "status": "approved",
            "paths": p.paths,
            "created": created,
            "source": author(caller),
        }
        meta = {k: v for k, v in meta.items() if v is not None}
        it = Item("memory", p.name, p.description.strip(), p.body.rstrip() + "\n", meta)
        await kn.store.save(owner, it, author(caller))
        await kn.refresh(owner)  # the next send's hint sees it
        kn.usage.add(owner, caller.log_id or "", "memory", p.name, "saved")
        return {"saved": p.name, "status": "approved"}

    @r.op("memory.delete", DeleteMemory, agent=True)
    async def memory_delete(p: DeleteMemory, caller):
        """Delete a memory that is no longer true. The deletion is kept in
        the store's history; the Knowledge view, in a later release, will
        restore it."""
        owner = principal(caller)
        memory(owner, p.name)
        sha = await kn.store.delete(owner, p.name, author(caller))
        await kn.refresh(owner)
        kn.usage.add(owner, caller.log_id or "", "memory", p.name, "deleted")
        return {"deleted": p.name, "sha": sha}

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

    @r.op("skill.search", SearchSkills, agent=True, read=True)
    async def skill_search(p: SearchSkills, caller):
        """Search your skills (procedures for a kind of task, such as cutting
        a release) with your own words, before you start a task. Returns the
        best names with their description, score and status; load one with
        skill_read."""
        hits = (await kn.index(principal(caller))).rank(p.query, "skill", p.limit)
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

    async def server_of(owner: str, it: Item, s):
        """The session's running server for the skill, started if need be."""
        cmd = mcp_command(it)
        if cmd is None:
            raise OpError("no_mcp", f"the skill {it.name!r} brings no MCP server")

        def pack() -> bytes:
            names = kn.store.files(owner, it.name)
            return _tar({n: kn.store.read_file(owner, it.name, n) for n in names})

        tar = await asyncio.to_thread(pack)
        try:
            return await app.skill_servers.ensure(
                s.log_id, it.name, cmd, tar, s.spec.linux_user
            )
        except SkillToolError as e:
            raise OpError("mcp_failed", f"the skill's server: {e}") from e

    @r.op("skill.read", ReadSkill, agent=True, read=True)
    async def skill_read(p: ReadSkill, caller):
        """Load a skill: returns its SKILL.md, which you then follow, and the
        files of its folder. With file, returns that one file instead."""
        owner = principal(caller)
        it = skill(owner, p.name)
        files = kn.store.files(owner, p.name)
        if p.file is not None and p.file != "SKILL.md":
            data = kn.store.read_file(owner, p.name, p.file)
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
        kind of task, with any files it needs. Saving an existing name
        replaces it. Set asked: true when the person asked for it."""
        owner = principal(caller)
        check_name(p.name)
        try:
            extra = {k: base64.b64decode(v, validate=True) for k, v in p.files.items()}
        except (binascii.Error, ValueError):
            raise OpError("bad_params", "files must hold base64") from None
        meta = {"aegis": p.aegis} if p.aegis else {}
        it = Item("skill", p.name, p.description.strip(), p.body.rstrip() + "\n", meta)
        await kn.store.save(owner, it, author(caller), extra=extra)
        await kn.refresh(owner)
        kn.usage.add(owner, caller.log_id or "", "skill", p.name, "saved")
        return {"saved": p.name, "status": "approved"}

    @r.op("skill.run", RunSkill, agent=True)
    async def skill_run(p: RunSkill, caller):
        """Run a script a skill you loaded declares under aegis.scripts, with
        arguments, in a fresh copy of the skill's folder. Returns its exit
        code, stdout and stderr, each cut at 20,000 characters. Needs a
        session with full permission."""
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
                cwd="/",
                stdin=launch.PIPE,
                stdout=launch.PIPE,
                stderr=launch.PIPE,
                new_session=True,
            )
        except OSError as e:
            raise OpError("run_failed", f"the script could not start: {e}") from e
        out, err = bytearray(), bytearray()
        io_done = asyncio.gather(
            _feed(proc.stdin, tar), _drain(proc.stdout, out), _drain(proc.stderr, err)
        )
        code: int | None = None
        try:
            await asyncio.wait_for(asyncio.shield(io_done), p.timeout_s)
            code = await proc.wait()
        except TimeoutError:
            # The whole group: python3 under the shell holds the pipes too.
            await stop_group(proc, s.spec.linux_user)
            await io_done
            err += f"\n[killed after {p.timeout_s}s]".encode()
        s.ran_skill(it.name)
        kn.usage.add(owner, s.log_id, "skill", it.name, "ran", p.script)
        return {"exit": code, "stdout": _cut(out), "stderr": _cut(err)}

    @r.op("skill.tool", CallSkillTool, agent=True)
    async def skill_tool(p: CallSkillTool, caller):
        """Call a tool of the MCP server a skill you loaded brings; skill_read
        lists those tools and their arguments. Only this session reaches the
        server, and it stops when the session closes. Needs a session with
        full permission."""
        owner = principal(caller)
        it = skill(owner, p.skill)
        s = app.sessions.sessions.get(caller.log_id or "")
        need_full(s, "a skill's MCP server")
        if s is None or it.name not in s.skills:
            raise OpError(
                "not_loaded", f"load the skill {it.name!r} with skill_read first"
            )
        server = await server_of(owner, it, s)
        if p.tool not in {t["name"] for t in server.tools}:
            raise OpError(
                "no_such_tool",
                f"{it.name!r} has no tool {p.tool!r}; it has "
                + ", ".join(t["name"] for t in server.tools),
            )
        try:
            got = await app.skill_servers.call(
                s.log_id, it.name, p.tool, p.args, p.timeout_s
            )
        except SkillToolError as e:
            raise OpError("mcp_failed", f"{it.name}/{p.tool}: {e}") from e
        s.ran_skill(it.name)
        kn.usage.add(owner, s.log_id, "skill", it.name, "ran", f"tool={p.tool}")
        return {
            "is_error": bool(got.get("isError")),
            "content": got.get("content") or [],
        }

    @r.op("knowledge.stats", Stats, agent=True, read=True)
    async def knowledge_stats(p: Stats, caller):
        """How your skills and memories are used: per item, how often it was
        hinted, read, invoked or run, and when it was last used. unused_days
        keeps only the items not used in that many days."""
        owner = principal(caller)
        used = await asyncio.to_thread(kn.usage.summary, owner)
        cutoff = time.time() - p.unused_days * 86400 if p.unused_days else None
        out = []
        for it in await kn.items(owner):
            if p.kind and it.kind != p.kind:
                continue
            u = used.get(it.name, {})
            last = u.get("last_used")
            if cutoff is not None and last is not None and last >= cutoff:
                continue
            out.append(
                {
                    "kind": it.kind,
                    "name": it.name,
                    "description": it.description,
                    "status": it.status,
                    "flags": ["expired"] if it.expired() else [],
                    **{k: u.get(k, 0) for k in ("hinted", "read", "invoked", "ran")},
                    "last_used": last,
                }
            )
        return {"items": out}
