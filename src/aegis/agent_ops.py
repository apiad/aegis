"""The operations open to agents, which the MCP endpoint projects as tools.

Agents can read and message anything they can see, and change only what they
created: their own monitors, their own session's names, the tasks they
enqueued (the vision's security model). People can do anything.

Closing is the one change an agent may make to what it did not create, after a
second thought (#278). A session it spawned, or the worker of a task it
enqueued, closes on the first call once its attention is done. Any other close,
its own session included, is refused with a one-time token (``confirm.py``)
and a refusal that says what the session is doing and who started it, because
a person may be reading that tab; the same call with the token closes it.
"""

from __future__ import annotations

import re
import time
from pathlib import Path
from typing import TYPE_CHECKING, Annotated, Literal

from pydantic import BaseModel, Field, model_validator
from pydantic.json_schema import SkipJsonSchema

from . import files
from . import layout as canvas
from .confirm import TTL_S
from .monitors import find_source, iso_now
from .names import SPAWN_HEAD, SPAWN_TASK, valid_handle
from .ops import Caller, OpError
from .session import ASLEEP_WHY, Archived, asleep_from
from .transcript.entries import CONTENT_KINDS

if TYPE_CHECKING:
    from .app import App


# An agent's toasts: at most this many in a sliding window (spec #329).
TOASTS_PER_WINDOW = 6
TOAST_WINDOW_S = 60.0


# What turn_end answers a session that still has the handle or the title aegis
# gave it (#586): agents often set the handle and keep the drawn title.
RENAME_LINE = (
    "This session still has the handle or title aegis gave it. Once the "
    "task is clear, call session_rename with both a handle and a title that say "
    "what this session works on."
)


class _Strict(BaseModel):
    model_config = {"extra": "forbid"}


class MonitorStart(_Strict):
    description: str = Field(
        min_length=1,
        description="What is being waited on, shown on the card and in the wake.",
    )
    done: str = Field(min_length=1, description="Bash; exit 0 means finished.")
    fail: str | None = Field(None, description="Bash; exit 0 means failed.")
    # Required but nullable: an optional field was left out by 44 of 51 monitors
    # (#165), most of them CI waits whose checks could be counted.
    progress: str | None = Field(
        description="Bash; prints 0 to 100 for the bar on your card. Always give one: "
        "count what is finished over the total (CI checks or jobs done, files "
        "written, test lines passed), or as a last resort elapsed seconds over the "
        "expected duration, capped at 99. Pass null only when nothing about the "
        "process can be counted or estimated."
    )
    # Required but nullable, as progress is: agents leave optional fields out.
    source: str | None = Field(
        min_length=1,
        description="Text from your tool call that started what you wait on, such "
        "as the comment line that opens its Bash command: clicking the monitor goes "
        "to the latest call holding it. Pass null when no call of yours started it.",
    )
    interval_s: float = Field(10, ge=1, le=3600)
    timeout_s: float = Field(3600, gt=0, le=7 * 86400)
    cwd: str | None = Field(
        None, description="Where conditions run; relative to your session's cwd."
    )


class MonitorSessions(_Strict):
    description: str = Field(
        min_length=1,
        description="What is being waited on, shown on the card and in the wake.",
    )
    sessions: list[str] = Field(
        min_length=1,
        max_length=20,
        description="Handles of open sessions from session_list: on this "
        "server, or handle@server on one linked with trust.",
    )
    interval_s: float = Field(10, ge=1, le=3600)
    # Other sessions' work runs longer than a build.
    timeout_s: float = Field(4 * 3600, gt=0, le=7 * 86400)


class MonitorId(_Strict):
    monitor_id: str


class FarAgent(_Strict):
    """An agent on a server linked with trust, as that server's link names it."""

    handle: str
    server: str


class Enqueue(_Strict):
    queue: str
    payload: str = Field(
        min_length=1, description="The worker's first prompt; self-contained."
    )
    callback: bool = Field(
        True, description="Deliver the worker's result to your inbox."
    )
    # Set only by a trusted link, for the agent there that enqueued: the
    # result goes back to it over the link (queue.deliver).
    from_agent: SkipJsonSchema[FarAgent | None] = Field(None, exclude=True)


class QueueDeliver(_Strict):
    target: str
    queue: str
    task_id: str
    outcome: Literal["ok", "error"]
    body: str


class PeerClose(_Strict):
    target: str
    token: str | None = None
    sender: FarAgent


class TaskId(_Strict):
    task_id: str


URGENT_AND_INTERRUPT = (
    "urgent and interrupt together: interrupt ends the turn, so there is no "
    "turn to steer into. Pick one."
)


def handoff_reply(did: str, to: str, cannot_steer: bool) -> str:
    """What peer_handoff tells its sender (Session.deliver's outcome)."""
    if did == "steered":
        return f"steered into {to}"
    if did == "held":
        why = " (its harness cannot take a message mid-turn)" if cannot_steer else ""
        return f"held for {to}{why}"
    return f"landed at {to}"


class Handoff(_Strict):
    target: str = Field(description="The receiving session's handle.")
    context: str = Field(min_length=1)
    interrupt: bool = Field(False, description="Cut the target's current turn first.")
    urgent: bool = Field(
        False,
        description="Reach a working target inside its current turn, at its next "
        "tool step, instead of when the turn ends. For a correction that changes "
        "its work now: stop, a cut in scope, don't do X.",
    )


class Sender(_Strict):
    handle: str
    server: str
    user: str


class Deliver(_Strict):
    target: str
    context: str = Field(min_length=1)
    interrupt: bool = False
    urgent: bool = False
    sender: Sender


class Read(_Strict):
    target: str = Field(description="A session's handle.")
    last: int = Field(30, ge=1, le=200, description="How many entries back.")
    tools: bool = Field(False, description="Include tool calls.")


class Close(_Strict):
    handle: str | None = Field(None, description="The session's handle.")
    token: str | None = Field(
        None,
        description="The one-time token a refused session_close gave you, to "
        "close it after all.",
    )
    # A person's client names a tab by its log id; an agent names it by handle.
    log_id: SkipJsonSchema[str | None] = None


class Quiet(_Strict):
    handle: str | None = Field(None, description="The session's handle.")
    # A person's client names a tab by its log id; an agent names it by handle.
    log_id: SkipJsonSchema[str | None] = None


class FileSend(_Strict):
    paths: list[Annotated[str, Field(min_length=1)]] = Field(
        min_length=1,
        max_length=20,
        description="The files' absolute paths, in the order to show them: one "
        "card pages through them. A relative path resolves against your "
        "session's working directory, not your shell's last cd.",
    )
    caption: str | None = Field(
        None, description="A line of Markdown shown above the files."
    )
    dock: str | None = Field(
        None,
        description=canvas.DOCK_DOC
        + " Several files stack in one column, the first at dock.",
    )


class PlanItem(_Strict):
    text: str = Field(min_length=1)
    state: Literal["pending", "doing", "done"]


class PlanUpdate(_Strict):
    items: list[PlanItem] = Field(
        description="The whole plan, every time, in order. Mark one item `doing` "
        "while you work on it and `done` when it is finished."
    )

    @model_validator(mode="after")
    def _one_doing(self) -> "PlanUpdate":
        if sum(1 for i in self.items if i.state == "doing") > 1:
            raise ValueError("mark one item `doing` at a time")
        return self


Reply = Annotated[str, Field(min_length=1, max_length=80, pattern=r"^[^\n]+$")]


class Toast(_Strict):
    text: str = Field(
        min_length=1,
        max_length=200,
        pattern=r"^[^\n]+$",
        description="One line the person should see now.",
    )
    kind: Literal["info", "error"] = Field(
        "info", description="error draws it in the error colour; info otherwise."
    )


class TurnEnd(_Strict):
    attention: Literal["needs_you", "review", "done"] = Field(
        description="needs_you: your message asks the person something. review: it "
        "presents something for them to read, or reports a step while work or waiting "
        "remains. done: the whole task they gave you is over; it puts an archive "
        "button on the card, and is refused while your plan has unfinished items "
        "or a monitor, task or workflow will still wake you."
    )
    line: str = Field(
        min_length=1,
        max_length=140,
        pattern=r"^[^\n]+$",
        description="One sentence: the question they must answer, what to read, or what got done.",
    )
    replies: list[Reply] = Field(
        default_factory=list,
        max_length=3,
        description="Up to three messages the person might send next, written as they "
        "would type them: their language, lowercase, no final period. Only when you "
        "laid out options or wait for a go-ahead; empty otherwise.",
    )
    handoff: str | None = Field(
        None,
        min_length=1,
        max_length=4000,
        description="Only when turn_end answered with your context size and the task "
        "is at a natural break: a brief for a fresh session that continues this work. "
        "What is done, what is next, the files, the decisions, what not to retry.",
    )
    close: bool = Field(
        False,
        description="With done only: archive your session, and every session you "
        "or they started, once this turn ends and all of them are finished. Only "
        "when the person told you in this conversation to close when you finish, "
        "or the plan or workflow they approved says so, or your session is in "
        "autoclose; never on your own judgment. Refused while you or they still "
        "wait on anything.",
    )


# What turn_end answers past the context block's handoff_at (#354). A tool
# result, never the system prompt: it is the newest message, so it cannot
# break the prompt cache. A turn reported done or review is at a break by
# definition, so the line asks for the brief outright; a real Sonnet ignored
# the conditional wording on a finished task in 2 of 3 runs.
# What turn_end tells an agent about the person's view of this turn (#679). A
# model that worked its answer out in its thinking believes it was shown, so a
# reminder to "write your final message" is not enough: the reply states what
# the transcript holds, counted from the stream, where text arrives before the
# call (Claude Code writes its own log only after the tool returns).
SEEN_NOTHING = (
    "The person has seen nothing from you this turn: what you worked out in your "
    "thinking is not shown to them. Write your full final message now, complete "
    "on its own: what you found or did, and what you need from them."
)
SEEN_SOME = (
    "This turn the person has seen {k} from you, {n} characters in all; the last "
    'begins "{head}". If your answer is already there, close with one or two '
    "lines and do not repeat it. If not, write your full final message now, "
    "complete on its own."
)


def seen_line(shown: list[str]) -> str:
    """What turn_end tells the agent the person has seen. The last message's
    opening, not a verdict: a status line before a tool call is prose too, and
    only the agent can tell it from an answer."""
    if not shown:
        return SEEN_NOTHING
    head = " ".join(shown[-1].split())
    head = head if len(head) <= 80 else head[:80].rstrip() + "…"
    k = f"{len(shown)} message" + ("s" if len(shown) != 1 else "")
    return SEEN_SOME.format(k=k, n=sum(map(len, shown)), head=head)


CONTEXT_LINE = (
    "noted. Your context is {k} tokens, and every turn from here rereads all of it. "
)
BRIEF_NOW = (
    "This turn is a natural break, so call turn_end again now, before your final "
    "message, with the same arguments plus handoff: a brief for a fresh session "
    "that continues this work (what is done, what is next, the files, the "
    "decisions, what not to retry)."
)
BRIEF_IF = (
    "If a fresh session could carry on from here, call turn_end again now with the "
    "same arguments plus handoff: a brief for it (what is done, what is next, the "
    "files, the decisions, what not to retry)."
)


class NoArgs(_Strict):
    pass


def _render(e: dict, tools: bool) -> str | None:
    kind = e["kind"]
    md = (e.get("md") or "").strip()
    if kind == "user":
        return f"user: {md}"
    if kind == "prose":
        return f"agent: {md}"
    if kind == "inbox":
        return f"from {e['title']}: {md.split(chr(10), 1)[1] if chr(10) in md else ''}".rstrip()
    if kind == "tool":
        if not tools:
            return None
        result = e["detail"].get("result") or e["status"]
        return f"tool {e['title']} {e['summary']} -> {result}"
    if kind in ("thinking", "recap", "btw"):
        # A recap is aegis talking to the person, and a side note is the
        # person's own aside; another agent never reads either.
        return None
    if kind == "file":
        return "\n".join(
            f"file: {f['name']} ({f['summary'].split(' · ')[0]}) {f['url']}"
            for f in e["detail"]["files"]
        )
    if kind == "artifact":
        return f"artifact: {e['title']} ({e['status']})"
    return f"· {e['summary']}"


# What a /spawn opening carries of the tab it was typed in: the last few turns,
# the person's words whole, the agent's prose trimmed to fit. The legacy tree
# measured why tools stay out: a tail filled from the newest event was tool
# calls, and 42% of its windows held none of the person's words.
SPAWN_TAIL_TURNS = 3
SPAWN_TAIL_CHARS = 8000


HANDOFF_HEAD = (
    "You continue the work of session {handle}. "
    "It is archived once you finish your first turn; peer_read {handle} still "
    "reads it after that, if you need more than what follows."
)
# On a linked server: nothing tells the old server when the first turn ends, and
# peer_read reaches an archived session only from its own server (#874).
HANDOFF_HEAD_FAR = (
    "You continue the work of session {handle}, on another server. It is archived "
    "as you start, so what follows is all you have of it."
)


HANDOFF_KIDS = (
    "{handle} started these sessions, which are still open: {kids}. Once {handle} "
    "is archived they are yours, as if you had started them. "
    "Tell each one with peer_handoff that you continue {handle}'s work, so they "
    "report to you from now on, and coordinate them as {handle} did."
)
HANDOFF_LEADS = " {handle} headed their group of tabs; you head it now."


def handoff_opening(
    handle: str,
    brief: str | None,
    tail: str,
    kids: tuple[str, ...] = (),
    leads: bool = False,
    instruction: str | None = None,
    far: bool = False,
) -> str:
    """A handoff's first message: who it continues, the sessions it started
    that the new one adopts (#641), the brief when the agent wrote one, the
    last turns as /spawn carries them (#354), and last, so it is what the agent
    reads last, the instruction it was handed off with (#874)."""
    parts = [(HANDOFF_HEAD_FAR if far else HANDOFF_HEAD).format(handle=handle)]
    if kids:
        line = HANDOFF_KIDS.format(handle=handle, kids=", ".join(kids))
        parts.append(line + (HANDOFF_LEADS.format(handle=handle) if leads else ""))
    if brief:
        parts.append("Its brief for you:\n\n" + brief)
    if tail:
        parts.append("Its last turns:\n\n" + tail)
    if instruction:
        parts.append("What to do now:\n\n" + instruction)
    return "\n\n".join(parts)


def spawn_tail(entries: list[dict]) -> str:
    """The last ``SPAWN_TAIL_TURNS`` turns as ``peer_read`` renders them, without
    tools or status notes, at most ``SPAWN_TAIL_CHARS``: the person's lines first, then the
    agent's newest-first, so the line with the referent is the last to go."""
    starts = [i for i, e in enumerate(entries) if e["kind"] == "user"]
    first = starts[-SPAWN_TAIL_TURNS] if len(starts) >= SPAWN_TAIL_TURNS else 0
    lines = [
        (e["kind"], x)
        for e in entries[first:]
        if e["kind"] in CONTENT_KINDS and (x := _render(e, False))
    ]
    keep = [False] * len(lines)
    left = SPAWN_TAIL_CHARS
    order = [i for i, (k, _) in enumerate(lines) if k == "user"][::-1]
    order += [i for i, (k, _) in enumerate(lines) if k != "user"][::-1]
    for i in order:
        if len(lines[i][1]) + 1 <= left:
            keep[i] = True
            left -= len(lines[i][1]) + 1
    return "\n".join(x for (_, x), k in zip(lines, keep) if k)


def spawn_opening(source, prompt: str, home: str | None) -> str:
    """The first message of a session a person ``/spawn``\\ s from tab ``source``:
    where they typed it, the tail of that tab, how to read more and how to
    answer, then their words. ``home`` is this server's name when the new
    session is on a linked server, which cannot read ``source``: it gets the
    tail and an address to hand off to. A tab with nothing in it yet gives the
    bare prompt, since a pointer at an empty transcript buys a wasted call.

    The wording is the legacy tree's (spec 2026-08-10, spawn with provenance):
    the person started it while standing in another tab, rather than that tab's
    agent delegating, so the new agent works for the person."""
    tail = spawn_tail(source.entries())
    if not tail:
        return prompt
    fence = "`" * max(3, max(map(len, re.findall("`+", tail)), default=0) + 1)
    to = f"{source.handle}@{home}" if home else source.handle
    read = (
        ""
        if home
        else f'Read more of it with peer_read with target "{source.handle}" '
        "before you start, unless the task is plainly self-contained. "
    )
    return (
        f"{SPAWN_HEAD}`{source.handle}`, and this probably refers to what is happening "
        f"there. Below is its recent tail.\n\n{fence}text\n{tail}\n{fence}\n\n"
        f"{read}The tail is a snapshot taken when you were spawned; "
        f"`{source.handle}` has kept going since.\n\n"
        "Then do the work: you are an agent with your own tab, not a question "
        f'being answered. When you are done, hand the result back with peer_handoff to "{to}" '
        f"if it matters there.{SPAWN_TASK}{prompt}"
    )


ACROSS = (
    "an agent on this server reaches a linked server by spawning there "
    "(session_spawn with agent@server, gated by that server's quota policy for "
    "the link) and by handing off to a session there (peer_handoff to "
    "handle@server); reading, sending to and enqueueing on another server are "
    "for people, unless the link is trusted (help: links#trusted-links)"
)


# What an agent here may learn of a linked server, all of it composed here from
# values checked here: the far server may be hostile, so none of its text is
# passed on (DESIGN.md, "A link is a client").
# A server older than #479 says stopped or parked for asleep.
FAR_STATES = frozenset({"idle", "working", "error", "asleep", "stopped", "parked"})
FAR_ERRORS = {
    "no_session": "no open session {handle}@{server}",
    "archived": "{handle}@{server} is archived",
    "server_offline": "{server} is not linked right now",
    "timeout": "{server} did not answer in time",
    "not_owner": "{handle}@{server} is not this server's link's to prompt there",
    "quota_closed": "the quota gate on {server} for this server's link is closed: "
    "nothing was started; a person here can see why with /spawn",
    "not_for_members": "{server} lets this server's link do that only as an admin",
    "not_allowed": "{server} refused the permission asked for",
    "unknown_agent": "{server} has no such agent",
    "trust_ended": "the trusted link with {server} no longer holds; a person "
    "can see why under Settings, Servers",
}


def far_entry(e: dict, server: str) -> dict | None:
    """One linked server's session as an agent here may see it: a valid handle
    and a known state, or nothing. An older server's stopped and parked read as
    asleep with their reason; a reason that is not one of ours is dropped."""
    if not isinstance(e, dict):
        return None
    handle, state = e.get("handle"), e.get("state")
    if (
        not (isinstance(handle, str) and valid_handle(handle))
        or state not in FAR_STATES
    ):
        return None
    if state not in ("stopped", "parked", "asleep"):
        return {"handle": handle, "server": server, "state": state}
    why = e.get("asleep_why")
    if state != "asleep":
        why = asleep_from(state, None)[1]
    elif why not in ASLEEP_WHY:
        why = None
    return {"handle": handle, "server": server, "state": "asleep", "asleep_why": why}


def far_error(
    e: OpError, server: str, handle: str = "", trusted: bool = False
) -> OpError:
    """A linked server's refusal as this server words it: the code if it is
    on the allowlist, never the far server's text. Over a trusted link the
    far server is this person's own, and its words pass through."""
    if trusted:
        return OpError(e.code, f"{server}: {e.message}")
    if e.code in FAR_ERRORS:
        return OpError(e.code, FAR_ERRORS[e.code].format(handle=handle, server=server))
    return OpError("far_error", f"{server} refused the call")


USER = re.compile(r"^[A-Za-z0-9._-]{1,64}$")
QUEUE_NAME = re.compile(r"^[A-Za-z0-9._-]{1,64}$")
TASK_ID = re.compile(r"^task-[0-9a-f]{1,32}$")

# What session_list adds from a session's card, so one agent can follow
# another's plan and state. replies, mark and blink describe the person's tab,
# not the session, and stay out.
LISTED = (
    "model",
    "model_id",
    "cost_usd",
    "context_tokens",
    "context_window",
    "last_activity",
    "attention",
    "attention_line",
    "waiting_on",
    "plan",
    "plan_now",
    "plan_did",
    "plan_done",
    "plan_total",
    "plan_clock",
    "load",
)


def ago(seconds: float) -> str:
    n = int(max(0, seconds))
    for unit, size in (("day", 86400), ("hour", 3600), ("minute", 60)):
        if n >= size * (2 if unit == "day" else 1):
            n //= size
            return f"{n} {unit}{'' if n == 1 else 's'} ago"
    return f"{n} second{'' if n == 1 else 's'} ago"


# What a session that is not done is doing, by its attention (attention.py),
# and what closing it costs.
DOING = {
    "working": ("is working right now{now}", "closing it stops that work mid-turn"),
    "waiting": (
        "is waiting on {waits}{now}",
        "closing it stops that work and cancels its monitors",
    ),
    "needs_you": (
        'is waiting for the person to answer it: "{line}"',
        "closing it throws that question away unanswered",
    ),
    "review": (
        'is waiting for the person to read what it showed them: "{line}"',
        "closing it takes that away before they have read it",
    ),
    "error": (
        'stopped on an error: "{line}"',
        "closing it takes that error out of the person's sight",
    ),
}


def close_refusal(
    *,
    handle: str,
    title: str,
    status: str,
    card: dict,
    origin: str,
    starter: str | None,
    mine: bool,
    yourself: bool,
    idle_s: float,
    token: str,
    why: str | None = None,
) -> str:
    """The prose an agent gets when session_close wants a second thought: why,
    what the session is doing as of now, the better moves, and only then the
    token. ``origin`` says who started it, as a clause; ``starter`` is that
    session's handle when it is another open one, to suggest as a peer."""
    a = card["attention"]
    line, now = card["attention_line"], card["plan_now"]
    paras = [f"The token you passed did not count: {why}."] if why else []
    if yourself:
        paras.append(
            f"Not closed: {handle} is your own session. Closing it ends this "
            "conversation where it stands, and takes away the tab a person may "
            "be reading you in."
        )
    else:
        if a == "done":
            lead = f"Not closed: you did not start {handle}; {origin}."
        else:
            doing, cost = DOING.get(a, ("is {a}", "closing it stops that"))
            what = doing.format(
                now=f' on "{now}"' if now else "",
                waits=card["waiting_on"] or "something",
                line=line,
                a=a,
            )
            lead = f"Not closed: {handle} {what}, and {cost}. " + (
                "You started it, but it is not done."
                if mine
                else f"It is not done, and it is not yours either: {origin}."
            )
        paras.append(
            lead + " A session closes on the first call only when you started it "
            "(or enqueued its task) and it is done."
        )
    # The lead already named the plan step or the line of a session not done.
    told = a != "done" and not yourself
    named = f'{handle} ("{title}")' if title else handle
    facts = [f"its state is {status} and its attention {a}"]
    if line and not told:
        facts[0] += f', with the line "{line}"'
    if not (told and a in ("working", "waiting")):
        facts.append(f'its plan step is "{now}"' if now else "it shows no plan step")
    facts.append(f"its last activity was {ago(idle_s)}")
    paras.append(f"What {named} looks like now: {'; '.join(facts)}.")
    if yourself:
        paras.append(
            "If your work is finished, end your turn with turn_end(done) instead: "
            "the person closes the tab once they have read it. If they told you "
            "to close when you finish, pass close=true to that turn_end."
        )
    else:
        peer = f" (or {starter}, which started it)" if starter else ""
        paras.append(
            "A person may be reading that tab right now, and closing it takes it "
            f"away from them. Better moves come first: ask the person whether "
            f"{handle} can go, or peer_handoff {handle}{peer} to ask whether it "
            "is finished."
        )
    paras.append(
        f"If you still have a reason to close it, call session_close again with "
        f'handle "{handle}" and token "{token}". The token works once, only for '
        f"you and only for {handle}, for the next {TTL_S / 60:.0f} minutes."
    )
    return "\n\n".join(paras)


def split_address(target: str, own: str) -> tuple[str, str | None]:
    """``knuth@vps`` as ``("knuth", "vps")``; a bare handle, or this server's
    own name, as ``(handle, None)``."""
    handle, at, server = target.partition("@")
    return (handle, None) if not at or server == own else (handle, server)


def across(app: App, server: str, caller: Caller):
    """The link for an agent's call to ``server``, which only a link both
    servers trust carries (links.py, #684); the refusal otherwise."""
    if caller.link is not None:
        raise OpError("not_relayed", "a link is not relayed to another server")
    if not app.links.trusted(server):
        raise OpError("not_across_links", f"{server} is another server; {ACROSS}")
    if not app.who(caller).is_admin:
        raise OpError(
            "not_for_members", "acting across a link is for an admin's agents"
        )
    link = app.links.get(server)
    assert link is not None
    return link


def from_trusted(app: App, caller: Caller, sender: FarAgent) -> str:
    """A trusted link speaking for one of its own agents: its address."""
    if (
        caller.link is None
        or sender.server != caller.link
        or not app.links.trusted(caller.link)
    ):
        raise OpError(
            "not_a_link", "only a trusted link, for its own server, says this"
        )
    if not valid_handle(sender.handle):
        raise OpError("bad_sender", "a sender is a handle")
    return f"{sender.handle}@{sender.server}"


def register_agent_ops(app: App) -> None:
    r = app.registry
    reg = app.sessions

    def own(caller: Caller):
        if not caller.is_agent or caller.log_id not in reg.sessions:
            raise OpError("agents_only", "only an agent's session can do this")
        return reg.sessions[caller.log_id]

    def here(target: str) -> str:
        """The handle in an address that must be on this server."""
        handle, server = split_address(target, app.server_name)
        if server is not None:
            raise OpError(
                "not_across_links", f"{target} is on another server; {ACROSS}"
            )
        return handle

    def target(handle: str):
        handle = here(handle)
        s = reg.by_handle(handle) or reg.sessions.get(handle)
        if s is None:
            if any(m.get("handle") == handle for m in reg.archived.values()):
                raise OpError("archived", f"{handle} is archived")
            raise OpError("no_session", f"no open session {handle!r}")
        return s

    def task(task_id: str):
        t = app.queues.tasks.get(task_id)
        if t is None:
            raise OpError("no_task", f"no task {task_id!r}")
        return t

    def mine(t, caller: Caller):
        if caller.is_agent and t.enqueuer != caller.log_id:
            raise OpError("not_yours", "agents can change only the tasks they enqueued")
        # A member changes only its own tasks; one from before owners is the admin's.
        who = app.who(caller)
        if not who.is_admin and (t.owner or app.user) != who.name:
            raise OpError(
                "not_owner", f"{t.id} belongs to {t.owner or app.user}; you can read it"
            )

    def roster(owner: str, but: str | None = None) -> list[dict]:
        return [
            {"monitor_id": m.id, "description": m.description}
            for m in app.monitors.of(owner)
            if m.id != but
        ]

    @r.op("monitor.start", MonitorStart, agent=True)
    async def monitor_start(p: MonitorStart, caller):
        """Watch a long-running process without polling: bash conditions run every
        interval_s, and you are woken through your inbox when `done` or `fail` exits 0
        or the timeout passes. `progress` feeds the bar a person watches: measure it
        whenever anything can be counted. Returns at once; end your turn after
        calling it. The reply lists your other live monitors: cancel any you no
        longer need."""
        s = own(caller)
        cwd = Path(p.cwd).expanduser() if p.cwd else s.spec.cwd
        if not cwd.is_absolute():
            cwd = s.spec.cwd / cwd
        source = find_source(s.entries(), p.source) if p.source else None
        m = app.monitors.start(
            s.log_id,
            str(cwd),
            description=p.description,
            done=p.done,
            fail=p.fail,
            progress=p.progress,
            source=source,
            interval_s=p.interval_s,
            timeout_s=p.timeout_s,
        )
        said = {
            "monitor_id": m.id,
            "source": source,
            "other_live_monitors": roster(s.log_id, m.id),
        }
        if p.source and not source:
            said["note"] = (
                "none of your tool calls holds that text, so the monitor links to none"
            )
        return said

    @r.op("monitor.sessions", MonitorSessions, agent=True, read=True)
    async def monitor_sessions(p: MonitorSessions, caller):
        """Wait for other sessions on this server to finish, without polling.
        You are woken `ok` when every one has finished (its last turn ended done
        or for review, or it was closed), `blocked` as soon as one needs the
        person or hit an error, naming it, and `timeout` otherwise. Pick the
        handles from session_list. Returns at once; end your turn after calling
        it. monitor_cancel stops it."""
        s = own(caller)
        picked: dict[str, dict] = {}
        for handle in p.sessions:
            h, server = split_address(handle, app.server_name)
            if server is not None:
                link = across(app, server, caller)
                try:
                    listed = await link.call("session.list")
                except OpError as e:
                    raise far_error(e, server, h, True) from None
                if not any(
                    isinstance(e, dict) and e.get("handle") == h for e in listed or []
                ):
                    raise OpError("no_session", f"no open session {h}@{server}")
                key = f"{h}@{server}"
                picked.setdefault(
                    key, {"log_id": key, "handle": key, "server": server, "far": h}
                )
                continue
            t = target(handle)
            if t.log_id == s.log_id:
                raise OpError(
                    "not_yourself",
                    "a session cannot wait for itself: its own wait keeps it waiting",
                )
            picked.setdefault(t.log_id, {"log_id": t.log_id, "handle": t.handle})
        m = app.monitors.start(
            s.log_id,
            str(s.spec.cwd),
            description=p.description,
            done="",
            sessions=list(picked.values()),
            interval_s=p.interval_s,
            timeout_s=p.timeout_s,
        )
        return {"monitor_id": m.id, "other_live_monitors": roster(s.log_id, m.id)}

    @r.op("monitor.cancel", MonitorId, agent=True, read=True)
    async def monitor_cancel(p: MonitorId, caller):
        """Stop one of your monitors. No wake is sent."""
        m = app.monitors.items.get(p.monitor_id)
        if m is None:
            raise OpError("no_monitor", f"no live monitor {p.monitor_id!r}")
        if caller.is_agent and m.owner != caller.log_id:
            raise OpError("not_yours", "agents can cancel only their own monitors")
        # A monitor wakes its session: a member stops only its own sessions' ones.
        who, owner = app.who(caller), app.owner_of(m.owner)
        if not who.is_admin and owner not in (None, who.name):
            raise OpError(
                "not_owner", f"{m.id} watches {owner}'s session; you can read it"
            )
        app.monitors.cancel(m.id)
        return {"cancelled": m.id, "remaining": roster(m.owner)}

    @r.op("monitor.list", NoArgs, agent=True, read=True)
    async def monitor_list(_, caller):
        """Your live monitors."""
        items = (
            app.monitors.of(caller.log_id)
            if caller.is_agent
            else list(app.monitors.items.values())
        )
        return [
            {
                "monitor_id": m.id,
                "description": m.description,
                "progress": m.last_progress,
            }
            for m in items
        ]

    @r.op("queue.enqueue", Enqueue, agent=True)
    async def queue_enqueue(p: Enqueue, caller):
        """Hand a self-contained task to a fresh worker on a queue. Returns at once
        with the task id; the worker's final message reaches your inbox when it
        finishes (with callback). Keep working meanwhile. `queue@server`
        enqueues on a server linked with trust; the result still reaches your
        inbox, and the task id comes back as `task-id@server`."""
        name, server = split_address(p.queue, app.server_name)
        if server is not None:
            link = across(app, server, caller)
            me = own(caller)
            try:
                r = await link.call(
                    "queue.enqueue",
                    {
                        "queue": name,
                        "payload": p.payload,
                        "callback": p.callback,
                        "from_agent": {"handle": me.handle, "server": app.server_name},
                    },
                )
            except OpError as e:
                raise far_error(e, server, trusted=True) from None
            return {**r, "task_id": f"{r['task_id']}@{server}"}
        far = None
        if p.from_agent is not None:
            from_trusted(app, caller, p.from_agent)
            far = p.from_agent.model_dump()
        if name not in app.queues.queues:
            known = ", ".join(sorted(app.queues.queues)) or "none configured"
            raise OpError("unknown_queue", f"no queue {name!r}; queues: {known}")
        if err := app.queues.queues[name].get("error"):
            raise OpError("bad_config", f"queue {name!r} in .aegis.yaml: {err}")
        enqueuer = own(caller) if caller.is_agent else None
        cwd = str(enqueuer.spec.cwd if enqueuer else app.roots.harness_cwd)
        t = await app.queues.enqueue(
            name,
            p.payload,
            p.callback,
            enqueuer.log_id if enqueuer else None,
            cwd,
            owner=app.who(caller).name,
            far_enqueuer=far,
        )
        return {"task_id": t.id, "status": t.status, "position": app.queues.position(t)}

    @r.op("queue.deliver", QueueDeliver)
    async def queue_deliver(p: QueueDeliver, caller):
        """A task's result from a server linked with trust, for the agent here
        that enqueued it. Only that link may call it; the header is composed
        here from values checked here."""
        if caller.link is None or not app.links.trusted(caller.link):
            raise OpError("not_a_link", "only a trusted link delivers a task's result")
        if not (QUEUE_NAME.match(p.queue) and TASK_ID.match(p.task_id)):
            raise OpError("bad_sender", "a queue and a task id, one word each")
        to = target(p.target)
        where = caller.link
        await to.deliver(
            f"> from queue:{p.queue}@{where} · task#{p.task_id}@{where} · "
            f"{p.outcome} · {iso_now()}",
            p.body,
            through_lock=True,
        )

    async def far_task(op: str, p: TaskId, caller):
        """A task on a server linked with trust, ``task-id@server``: the call
        goes there, or None when the task is here."""
        task_id, server = split_address(p.task_id, app.server_name)
        if server is None:
            return None
        link = across(app, server, caller)
        try:
            return {"result": await link.call(op, {"task_id": task_id})}
        except OpError as e:
            raise far_error(e, server, trusted=True) from None

    def far_mine(t, caller: Caller) -> None:
        """A trusted link changes only the tasks its own server enqueued."""
        if (
            caller.link is not None
            and (t.far_enqueuer or {}).get("server") != caller.link
        ):
            raise OpError("not_yours", f"{t.id} was not enqueued from {caller.link}")

    @r.op("task.status", TaskId, agent=True, read=True)
    async def task_status(p: TaskId, caller):
        """A task's status, its worker, and its result once finished.
        `task-id@server` reads one on a server linked with trust."""
        if (far := await far_task("task.status", p, caller)) is not None:
            return far["result"]
        return app.queues.status(task(p.task_id))

    @r.op("task.cancel", TaskId, agent=True)
    async def task_cancel(p: TaskId, caller):
        """Cancel a task you enqueued: drop it if pending, stop and archive its worker if running."""
        if (far := await far_task("task.cancel", p, caller)) is not None:
            return far["result"]
        t = task(p.task_id)
        far_mine(t, caller)
        mine(t, caller)
        await app.queues.cancel(t)
        return app.queues.status(t)

    @r.op("task.resume", TaskId, agent=True)
    async def task_resume(p: TaskId, caller):
        """Put a failed or cancelled task's worker back to work, with its conversation intact."""
        if (far := await far_task("task.resume", p, caller)) is not None:
            return far["result"]
        t = task(p.task_id)
        far_mine(t, caller)
        mine(t, caller)
        if t.status not in ("failed", "cancelled"):
            raise OpError("not_resumable", f"the task is {t.status}")
        try:
            await app.queues.resume(t)
        except LookupError as e:
            raise OpError("no_worker", str(e)) from e
        return app.queues.status(t)

    @r.op("peer.handoff", Handoff, agent=True)
    async def peer_handoff(p: Handoff, caller):
        """Give another session context or an instruction; it arrives as a user turn
        headed `> from agent:<you>`. A busy target gets it when its turn ends, and
        is told meanwhile who waits; urgent reaches it inside the turn instead, at
        its next tool step, and interrupt cuts that turn first. `handle@server`
        reaches a session on a server this one links; nothing there can reach you
        back, unless the link is trusted."""
        if p.urgent and p.interrupt:
            raise OpError("bad_params", URGENT_AND_INTERRUPT)
        handle, server = split_address(p.target, app.server_name)
        who = app.who(caller)
        if server is not None:
            if caller.link is not None:
                raise OpError("not_relayed", "a link is not relayed to another server")
            # The link delivers as its own token on the far server (web.py).
            if not who.is_admin:
                raise OpError(
                    "not_for_members",
                    f"handing off to {server} through this server's link is for "
                    "an admin here",
                )
            return await far_handoff(handle, server, p, caller)
        to = target(p.target)
        to.check_lock()  # before an interrupt reaches it
        # A prompt or an interrupt changes the session: a member's reaches only
        # its own (tokens.py).
        owner = app.owner_of(to.log_id)
        if not who.is_admin and owner != who.name:
            raise OpError(
                "not_owner",
                f"{to.handle} belongs to {owner}; you can read it, not prompt it",
            )
        sender = (
            f"agent:{reg.sessions[caller.log_id].handle}"
            if caller.is_agent and caller.log_id in reg.sessions
            else "user"
        )
        if p.interrupt and to.status == "working":
            await to.interrupt()
        try:
            did = await to.deliver(
                f"> from {sender} · {iso_now()}", p.context, urgent=p.urgent
            )
        except Archived as e:
            raise OpError("archived", f"{p.target} is archived") from e
        return handoff_reply(
            did, to.handle, p.urgent and not to.harness.steers_mid_turn
        )

    async def far_handoff(handle: str, server: str, p: Handoff, caller: Caller):
        link = app.links.get(server)
        if link is None:
            raise OpError(
                "unknown_server", f"this server links no server named {server}"
            )
        me = reg.sessions.get(caller.log_id) if caller.log_id else None
        sender = {
            "handle": me.handle if me else "user",
            "server": app.server_name,
            "user": app.user,
        }
        try:
            r = await link.call(
                "peer.deliver",
                {
                    "target": handle,
                    "context": p.context,
                    "interrupt": p.interrupt,
                    # Only when set: a linked server from before urgent refuses
                    # a field it does not know.
                    **({"urgent": True} if p.urgent else {}),
                    "sender": sender,
                },
            )
        except OpError as e:
            if e.code in FAR_ERRORS or app.links.trusted(server):
                raise far_error(e, server, handle, app.links.trusted(server)) from None
            raise OpError("far_error", f"{server} refused the handoff") from None
        r = r if isinstance(r, dict) else {}
        did = "steered" if r.get("steered") else "held" if r.get("held") else "sent"
        return handoff_reply(did, f"{handle}@{server}", r.get("cannot_steer") is True)

    @r.op("peer.deliver", Deliver)
    async def peer_deliver(p: Deliver, caller):
        """A handoff from an agent on a server that links this one. Only a link
        socket may call it, and only in the name of the server it linked as."""
        if caller.link is None or p.sender.server != caller.link:
            raise OpError(
                "not_a_link", "only a link, for its own server, delivers handoffs"
            )
        # The header is one line a person and an agent trust: nothing in it may
        # start another.
        sender_ok = valid_handle(p.sender.handle) or p.sender.handle == "user"
        if not (sender_ok and USER.match(p.sender.user)):
            raise OpError(
                "bad_sender", "a sender is a handle and a user name, one line each"
            )
        to = target(p.target)
        # The link is the token it presented: a member link prompts only the
        # sessions it owns here, like any member (tokens.py).
        principal, owner = app.who(caller), app.owner_of(to.log_id)
        if not principal.is_admin and owner != principal.name:
            raise OpError(
                "not_owner",
                f"{to.handle} belongs to {owner}; a handoff cannot reach it",
            )
        if p.urgent and p.interrupt:
            raise OpError("bad_params", URGENT_AND_INTERRUPT)
        if p.interrupt and to.status == "working":
            await to.interrupt()
        who = f"agent:{p.sender.handle}@{p.sender.server} ({p.sender.user})"
        try:
            did = await to.deliver(
                f"> from {who} · {iso_now()}", p.context, urgent=p.urgent
            )
        except Archived as e:
            raise OpError("archived", f"{p.target} is archived") from e
        return {
            "held": did == "held",
            "steered": did == "steered",
            "cannot_steer": p.urgent and not to.harness.steers_mid_turn,
        }

    def readable(handle: str, caller) -> list[dict]:
        """A transcript peer_read may read: an open session's, or the archived
        one the caller continues after a handoff (#354), and no other archived
        one."""
        h = here(handle)
        if reg.by_handle(h) is None and h not in reg.sessions:
            me = reg.sessions.get(caller.log_id or "")
            old = me.spec.continues if me else None
            if old in reg.archived and h in (old, reg.archived[old].get("handle")):
                return reg.archived_entries(old)
        return target(handle).entries()

    @r.op("peer.read", Read, agent=True, read=True)
    async def peer_read(p: Read, caller):
        """The last entries of another session's transcript, one line each.
        `handle@server` reads a session on a server this one links with
        trust."""
        handle, server = split_address(p.target, app.server_name)
        if server is not None and caller.link is None and app.links.trusted(server):
            link = app.links.get(server)
            assert link is not None  # trusted() found it
            try:
                return await link.call(
                    "peer.read", {"target": handle, "last": p.last, "tools": p.tools}
                )
            except OpError as e:
                raise far_error(e, server, handle, True) from None
        entries = readable(p.target, caller)
        lines = [x for e in entries[-p.last * 3 :] if (x := _render(e, p.tools))]
        return "\n".join(lines[-p.last :]) or "(nothing yet)"

    @r.op("session.list", NoArgs, agent=True, read=True)
    async def session_list(_, caller):
        """The open sessions on this server, each with its state, what it needs
        (attention and its line, what it waits on), its plan and what it is
        doing now, its model (`model` as configured, `model_id` as the harness
        resolved it, null until it has), spend, and `load`: the CPU in cores
        over the last minute and the RAM in GB of it and everything it started,
        null while it has no process; then each linked server's by
        handle and state alone, as `handle@server`: reach one with
        peer_handoff. A server linked with trust lists its sessions in full,
        with `server`."""
        out = []
        who = app.who(caller)
        member = app.multi_user is not None and not who.is_admin
        for s in reg.open_sessions():
            if not app.sees(who, app.owner_of(s.log_id)):
                continue
            w = s.wire()
            out.append(
                {
                    "handle": s.handle,
                    "title": s.title,
                    "state": s.status,
                    **({"asleep_why": s.asleep_why} if s.status == "asleep" else {}),
                    "cwd": str(s.spec.cwd),
                    "worker": s.worker,
                    "you": caller.log_id == s.log_id,
                    **{k: w.get(k) for k in LISTED},
                    # Whether its last turn ended with turn_end: a monitor on
                    # another server waits for that (monitors.py).
                    **(
                        {"reported": bool(s.standing.get("report"))}
                        if caller.link
                        else {}
                    ),
                }
            )
        # A link asking is not relayed further; links are the admin's.
        if caller.link is None and not member:
            out += await far_sessions()
        return out

    async def far_sessions() -> list[dict]:
        """Every linked server's open sessions, cut to handle and state: a title
        is text an agent over there wrote, and nothing written there reaches an
        agent here (links.py). A server linked with trust is this person's
        own, and its entries come whole."""
        out = []
        for link in app.links.up():
            try:
                listed = await link.call("session.list")
            except OpError:
                continue
            trusted = app.links.trusted(link.name)
            for e in listed if isinstance(listed, list) else []:
                if not isinstance(e, dict) or "server" in e:
                    continue
                if trusted:
                    e = {k: v for k, v in e.items() if k != "you"}
                    out.append({**e, "server": link.name})
                elif (entry := far_entry(e, link.name)) is not None:
                    out.append(entry)
        return out

    def origin(s, me) -> tuple[str, str | None]:
        """Who started ``s``, as a clause, and that session's handle when it is
        another open one."""
        if s.worker:
            t = app.queues.tasks.get(s.worker["task_id"])
            by = t.enqueuer if t else None
            who = "you" if by == me.log_id else name(by) or "a person"
            clause = f"it is queue {s.worker['queue']}'s worker for task#{s.worker['task_id']}, enqueued by {who}"
        else:
            by = s.spec.spawned_by
            who = "you" if by == me.log_id else name(by)
            clause = f"{who} started it" if who else "a person opened it"
        starter = reg.sessions.get(by) if by and by != me.log_id else None
        return clause, starter.handle if starter else None

    def name(log_id: str | None) -> str | None:
        if not log_id:
            return None
        if log_id in reg.sessions:
            return reg.sessions[log_id].handle
        gone = reg.archived.get(log_id, {}).get("handle")
        return f"{gone}, now archived," if gone else "a session now gone"

    def started_by(s, me) -> bool:
        if s.spec.spawned_by == me.log_id:
            return True
        t = app.queues.tasks.get(s.worker["task_id"]) if s.worker else None
        return t is not None and t.enqueuer == me.log_id

    @r.op("session.close", Close, agent=True, owned="log_id")
    async def session_close(p: Close, caller):
        """Close a session: its tab goes away and it stays in the archive. A
        session you spawned, or whose queue task you enqueued, closes at once
        when its attention is done; any other, your own included, is refused
        with a one-time token and what to weigh first, and closes on a second
        call that passes the token."""
        if not caller.is_agent:
            if p.log_id is None:
                raise OpError("bad_params", "log_id is required")
            if (s := reg.sessions.get(p.log_id)) is not None:
                s.check_lock()  # a live workflow's step closes with its run
            await reg.close(p.log_id)
            return None
        me = own(caller)
        if p.handle is None:
            raise OpError("bad_params", "handle is required")
        h, server = split_address(p.handle, app.server_name)
        if server is not None:
            link = across(app, server, caller)
            try:
                return await link.call(
                    "peer.close",
                    {
                        "target": h,
                        "token": p.token,
                        "sender": {"handle": me.handle, "server": app.server_name},
                    },
                )
            except OpError as e:
                raise far_error(e, server, h, True) from None
        s = target(p.handle)
        s.check_lock()
        card = s.wire()
        yourself = s.log_id == me.log_id
        mine = not yourself and started_by(s, me)
        why = None
        if not (mine and card["attention"] == "done"):
            if p.token is not None:
                why = app.confirmations.redeem(
                    p.token, "session.close", me.log_id, s.log_id
                )
            if p.token is None or why is not None:
                clause, starter = origin(s, me)
                raise OpError(
                    "second_thought",
                    close_refusal(
                        handle=s.handle,
                        title=s.title or "",
                        status=s.status,
                        card=card,
                        origin=clause,
                        starter=starter,
                        mine=mine,
                        yourself=yourself,
                        idle_s=time.time() - s.last_activity,
                        token=app.confirmations.issue(
                            "session.close", me.log_id, s.log_id
                        ),
                        why=why,
                    ),
                )
        handle = s.handle
        await reg.close(s.log_id)
        return f"closed {handle}: its tab is gone, and it stays in the archive"

    def far_origin(s, key: str) -> str:
        """Who started ``s``, as a clause, told to an agent on another server
        whose address is ``key``."""
        if s.worker:
            return f"it is queue {s.worker['queue']}'s worker for task#{s.worker['task_id']}"
        if s.spec.spawned_from:
            by = s.spec.spawned_from
            return "you started it" if by == key else f"{by} started it"
        who = name(s.spec.spawned_by)
        return f"{who} started it" if who else "a person opened it"

    @r.op("peer.close", PeerClose)
    async def peer_close(p: PeerClose, caller):
        """session_close from an agent on a server linked with trust, under
        the same rule as here: a session it started closes at once when done,
        any other on a second call with the token it was given."""
        key = from_trusted(app, caller, p.sender)
        s = target(p.target)
        s.check_lock()
        card = s.wire()
        t = app.queues.tasks.get(s.worker["task_id"]) if s.worker else None
        mine = s.spec.spawned_from == key or (
            t is not None and t.far_enqueuer == p.sender.model_dump()
        )
        if not (mine and card["attention"] == "done"):
            why = None
            if p.token is not None:
                why = app.confirmations.redeem(p.token, "session.close", key, s.log_id)
            if p.token is None or why is not None:
                raise OpError(
                    "second_thought",
                    close_refusal(
                        handle=f"{s.handle}@{app.server_name}",
                        title=s.title or "",
                        status=s.status,
                        card=card,
                        origin=far_origin(s, key),
                        starter=None,
                        mine=mine,
                        yourself=False,
                        idle_s=time.time() - s.last_activity,
                        token=app.confirmations.issue("session.close", key, s.log_id),
                        why=why,
                    ),
                )
        handle = s.handle
        await reg.close(s.log_id)
        return (
            f"closed {handle}@{app.server_name}: its tab is gone, and it stays in "
            "the archive"
        )

    @r.op("session.quiet", Quiet, agent=True, owned="log_id")
    async def session_quiet(p: Quiet, caller):
        """Hide a session's tab while it works: it stays in the Fleet, dimmed,
        and its tab comes back by itself when its turn ends done, review,
        needs_you or error. Only a working or waiting session can be quiet.
        Use it for a session doing background work the person need not
        watch, your own included."""
        if not caller.is_agent:
            if p.log_id is None:
                raise OpError("bad_params", "log_id is required")
            return reg.set_quiet(p.log_id, True)
        own(caller)
        if p.handle is None:
            raise OpError("bad_params", "handle is required")
        s = target(p.handle)
        reg.set_quiet(s.log_id, True)
        return f"{s.handle} is quiet: its tab is hidden until its turn ends"

    @r.op("file.send", FileSend, agent=True, read=True)
    async def file_send(p: FileSend, caller):
        """Hand files to the person: one card in your transcript, paging
        through them, with a preview when the browser can draw one, and Open
        and Download links. The files are copied, so later changes to them are
        not seen until you send them again: a file sent again from the same
        path is its next version, and its card and any pane it is docked in
        show the newest. With dock, they also open beside the transcript. If
        one cannot be sent, none is."""
        s = own(caller)
        paths = [Path(x).expanduser() for x in p.paths]
        relative = {s.spec.cwd / x for x in paths if not x.is_absolute()}
        paths = [s.spec.cwd / x if not x.is_absolute() else x for x in paths]
        # A file's key is its resolved path (layout.file_key): one path twice
        # in one send would be two versions on one card, so it is refused.
        run = app.place_of_session(s.log_id).run
        sources = await run(lambda: [x.resolve() for x in paths])
        twice = sorted({str(x) for x in sources if sources.count(x) > 1})
        if twice:
            raise OpError(
                "bad_params",
                f"{', '.join(twice)} is listed more than once; send it once",
            )
        # Each file's pane id (layout.file_key), so a bad dock is refused
        # before anything is stored: the first file at dock, each next one
        # below the one before.
        keys = [canvas.file_key(str(x)) for x in sources]

        def docked(lay: dict) -> dict:
            wheres = [str(p.dock)] + [f"below:{k}" for k in keys[:-1]]
            for k, where in zip(keys, wheres):
                lay = canvas.place(lay, k, where, lambda i: i in keys or s.dockable(i))
            return lay

        if p.dock is not None:
            try:
                docked(s.layout)
            except canvas.LayoutError as e:
                raise OpError("bad_params", str(e)) from e
        try:
            recs = await files.store_as(app.roots.state_root, paths, run)
        except files.FileError as e:
            # Agents write files after a cd in the shell, which does not carry
            # over, then send the bare name (seen in the first smoke test).
            hint = (
                f"; a relative path resolves against your session's working "
                f"directory, {s.spec.cwd}; pass the file's absolute path"
                if e.path in relative and e.code == "not_found"
                else ""
            )
            raise OpError(e.code, e.message + hint) from e
        # The source stays in the store: the fold keys by it and never copies
        # it into an entry.
        sent = [
            {**{k: v for k, v in r.items() if k != "kind"}, "source": str(src)}
            for r, src in zip(recs, sources)
        ]
        s.record_file({"kind": "file", "files": sent, "caption": p.caption})
        out = [
            {
                "url": files.url(r["file_id"], r["name"]),
                "name": r["name"],
                "size": r["size"],
                "mime": r["mime"],
            }
            for r in recs
        ]
        if p.dock is not None:
            # Against the layout as it is now, after the copy; the files are
            # sent, so a placement that no longer fits is reported, not raised.
            try:
                s.set_layout(docked(s.layout), "agent")
            except canvas.LayoutError as e:
                return {"files": out, "docked": False, "reason": str(e)}
        return out

    @r.op("plan.update", PlanUpdate, agent=True, read=True)
    async def plan_update(p: PlanUpdate, caller):
        """Keep your plan where the person can see it: on your tab's card and in
        its sidebar. Send the whole list each time."""
        s = own(caller)
        items = [{"text": i.text[:120], "state": i.state} for i in p.items[:30]]
        s.report({"kind": "plan", "items": items})
        done = sum(1 for i in items if i["state"] == "done")
        # The plan comes back as saved, so the agent sees what the card shows
        # and when to call again, instead of waiting for the stale-plan nudge
        # (#542).
        mark = {"done": "✓", "doing": "▶", "pending": "·"}
        return "\n".join(
            [f"plan saved: {done} of {len(items)} done. The current plan:"]
            + [f"  {mark[i['state']]} {i['text']}" for i in items]
            + [
                "Call plan_update again whenever an item starts, finishes, "
                "or the work changes."
            ]
        )

    @r.op("turn.end", TurnEnd, agent=True, read=True)
    async def turn_end(p: TurnEnd, caller):
        """Call this before the final message of every turn you hand back to the
        person, and not when you end a turn to wait on a monitor or a queue task.
        It sets the mark on your tab and the line on your card."""
        s = own(caller)
        left = [i for i in s.standing.get("plan") or [] if i["state"] != "done"]
        if p.attention == "done" and left:
            raise OpError(
                "plan_unfinished",
                f"done means the task is over, and your plan has {len(left)} "
                "unfinished items. If it is over, mark them done or drop them with "
                "plan_update, then call turn_end again; if not, call turn_end with "
                "review, or needs_you when you ask something.",
            )
        # The person turned autoclose on (#691): their word to close is
        # given ahead of time, so done without close is the one ending left out.
        if p.attention == "done" and not p.close and s.autoclose:
            raise OpError(
                "autoclose",
                "this session is in autoclose: the person asked for it to close "
                "when you finish. If the task is over, call turn_end with done and "
                "close true; if it is not, call turn_end with review, or needs_you "
                "when you ask something.",
            )
        if p.attention == "done" and not p.close and (live := reg.wakers(s)):
            raise OpError(
                "still_waiting",
                "done means the task is over, and this will still wake you: "
                + ", ".join(live)
                + ". If it is over, cancel or stop it (monitor_cancel, task_cancel) and "
                "call turn_end again; if you are waiting on it, end the turn "
                "without turn_end, or call turn_end with review.",
            )
        if p.close and p.attention != "done":
            raise OpError(
                "bad_params",
                "close goes with done only: a session that asks the person "
                "something or has something for them to read stays open.",
            )
        if p.close and (blockers := reg.close_blockers(s)):
            raise OpError(
                "close_refused",
                "Not closed, because this is still running: "
                + ", ".join(blockers)
                + ". Wait for it, or cancel or close it, then call turn_end again; "
                "or call turn_end without close and leave the tab open.",
            )
        rec: dict[str, object] = {
            "kind": "turn_end",
            "attention": p.attention,
            "line": p.line,
            "replies": p.replies,
        }
        if p.handoff and not s.worker:
            rec["handoff"] = p.handoff
        if p.close:
            rec["close"] = True
        s.report(rec)
        if p.close:
            n = len(reg.descendants(s.log_id))
            tree = (
                f" and the {n} session{'s' if n != 1 else ''} it started" if n else ""
            )
            return (
                f"noted. aegis archives this session{tree} when this turn ends; "
                "your final message is the last thing in its tab. "
                + seen_line(s.fold().shown())
            )
        at = app.config.current().context["handoff_at"]
        n = s.context_tokens or 0
        unnamed = not s.worker and not (s.handle_set and s.title_set)
        if s.worker:
            # A worker's final message is its task's result, read by the
            # enqueuer, not a reply to the person.
            return "noted"
        if p.handoff or not at or n < at:
            said = f"noted. {seen_line(s.fold().shown())}"
            return f"{said}\n{RENAME_LINE}" if unnamed else said
        then = BRIEF_IF if p.attention == "needs_you" else BRIEF_NOW
        line = CONTEXT_LINE.format(k=f"{round(n / 1000)}k") + then
        return f"{line}\n{RENAME_LINE}" if unnamed else line

    toasted: dict[str, list[float]] = {}

    @r.op("toast", Toast, agent=True, read=True)
    async def toast(p: Toast, caller):
        """Show a short message in every open browser, now, without ending your
        turn: a heads-up the person should see while you keep working. A browser
        whose page is hidden also shows it as a desktop notification. Not for
        reports; turn_end does that. Clicking it opens your tab. Returns how many
        browsers showed it; with 0, nobody saw it. At most 6 a minute."""
        s = own(caller)
        now = time.monotonic()
        recent = [t for t in toasted.get(s.log_id, ()) if now - t < TOAST_WINDOW_S]
        if len(recent) >= TOASTS_PER_WINDOW:
            wait = int(TOAST_WINDOW_S - (now - recent[0])) + 1
            raise OpError(
                "rate_limited",
                f"{TOASTS_PER_WINDOW} toasts a minute at most; the next in {wait}s",
            )
        toasted[s.log_id] = [*recent, now]
        return {"shown": app.toast(p.text, p.kind, s)}

    @r.op("meta", NoArgs, agent=True, read=True)
    async def meta(_, caller):
        """How aegis works for you, and its tools."""
        s = reg.sessions.get(caller.log_id or "")
        from .browsers import available
        from .mcp import primer

        tools = ", ".join(op.tool_name for op in r.agent_ops())
        browsers = not reg.multi_user and available()
        head = (
            primer(s, reg.server_name, reg.roots.config_root, browsers=browsers)
            if s
            else "aegis"
        )
        return f"{head}\n\nTools: {tools}. help_search finds how to use any of these."
