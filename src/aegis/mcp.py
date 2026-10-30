"""The MCP endpoint: the registry's agent operations, projected as tools.

A tool is named after its operation with the dot as an underscore
(``monitor.start`` is ``monitor_start``; Claude Code shows it as
``mcp__aegis__monitor_start``), and takes the operation's params. There is no
``from_handle``: each spawn of ``claude`` gets a token, carried in the
``X-Aegis-Session`` header of its ``--mcp-config``, and the endpoint resolves
the caller from it. A call with no valid token is refused.

The app is fastmcp's streamable HTTP app in stateless JSON mode, so every call
is one POST with no session to keep, and a server restart loses nothing.
"""

from __future__ import annotations

import json
import secrets
from typing import TYPE_CHECKING, Any

import fastmcp
from fastmcp import FastMCP
from fastmcp.exceptions import ToolError
from fastmcp.server.dependencies import get_http_headers
from fastmcp.tools import Tool
from fastmcp.tools import ToolResult
from mcp.types import ToolAnnotations
from fastmcp.utilities.logging import configure_logging
from pydantic import PrivateAttr

from .harness import HEADER, SHELL_HEADER
from .ops import Caller, Operation, OpError, Registry

# fastmcp logs every ToolError with its traceback, and every refused op is one
# (a no_session is an answer, not a crash). Its own handler renders that
# traceback rich, which held the event loop, and every session on it, for
# about 0.5 s per refusal (#366). Plain text costs a few milliseconds. Rich
# also wraps the message itself, in time growing with its length squared: a
# page's 4,000-character error, logged twice through its chained exception,
# held the loop for seconds on a busy host (#409). So no rich handler at all.
fastmcp.settings.enable_rich_logging = False
configure_logging(level=fastmcp.settings.log_level)

if TYPE_CHECKING:
    from pathlib import Path

    from .registry import Registry as Sessions
    from .session import Session

PATH = "/mcp"


class Tokens:
    """One token per running ``claude`` process; a restart of the process
    mints a new one and the old one stops working."""

    def __init__(self) -> None:
        self._by_token: dict[str, str] = {}
        self._by_session: dict[str, str] = {}

    def mint(self, log_id: str) -> str:
        self.drop(log_id)
        token = secrets.token_urlsafe(24)
        self._by_token[token] = log_id
        self._by_session[log_id] = token
        return token

    def drop(self, log_id: str) -> None:
        old = self._by_session.pop(log_id, None)
        if old is not None:
            self._by_token.pop(old, None)

    def resolve(self, token: str | None) -> str | None:
        return self._by_token.get(token or "")


def mcp_config(url: str) -> str:
    """Claude Code's --mcp-config for aegis. The session's token is named,
    not written: Claude expands ${AEGIS_SESSION_TOKEN} from its environment
    (harness.session_env), and an argv is readable by every user on the host."""
    return json.dumps(
        {
            "mcpServers": {
                "aegis": {
                    "type": "http",
                    "url": url,
                    "headers": {HEADER: "${AEGIS_SESSION_TOKEN}"},
                }
            }
        }
    )


class OpTool(Tool):
    _op: Operation = PrivateAttr()
    _registry: Registry = PrivateAttr()
    _tokens: Tokens = PrivateAttr()
    _sessions: Sessions | None = PrivateAttr(default=None)

    async def run(self, arguments: dict[str, Any]) -> ToolResult:
        headers = get_http_headers(include_all=True) or {}
        log_id = self._tokens.resolve(headers.get(HEADER.lower()))
        if log_id is None:
            raise ToolError(
                "unknown session: this call carries no valid aegis session token"
            )
        try:
            result = await self._registry.call(
                self._op.name, arguments, Caller("agent", log_id)
            )
        except OpError as e:
            raise ToolError(f"{e.code}: {e.message}") from e
        text = (
            result
            if isinstance(result, str)
            else json.dumps(result, ensure_ascii=False, default=str)
        )
        # A notice rides on the agent's next tool result, never on aegis
        # call's output, which a program may parse. A harness that takes
        # notices mid-turn already has it (Session._steer_owed).
        reg = self._sessions
        s = None if reg is None else reg.sessions.get(log_id)
        if (
            reg is not None
            and s is not None
            and not s.harness.steers_notices
            and SHELL_HEADER.lower() not in headers
        ):
            if blocks := reg.notice_blocks(s, "tool"):
                text = f"{text}\n\n{blocks}"
        return ToolResult(content=text)


def _schema(op: Operation) -> dict:
    schema = op.params.model_json_schema()
    schema.pop("title", None)
    schema.setdefault("properties", {})
    return schema


# Where the model chooses a tool, so a loop of calls can become one Bash call.
SHELL_FORM = (
    "\n\nIn a loop, or to pipe the result into another command, run "
    "`aegis call {name} key=value ...` in Bash instead: same result, as you."
)


READ_ONLY = ToolAnnotations(readOnlyHint=True)


def build_mcp(registry: Registry, tokens: Tokens, sessions: Sessions | None = None):
    """The fastmcp server and its ASGI app, routed at ``/mcp``."""
    server = FastMCP("aegis")
    for op in registry.agent_ops():
        doc = (op.doc or op.name) + SHELL_FORM.format(name=op.tool_name)
        # Read-only to the harness: aegis decides which of its tools a session
        # may call (ops.Registry.call), and Claude Code 2.1.301 and later refuse
        # any other MCP call in plan mode, a read session's mode (#442).
        tool = OpTool(
            name=op.tool_name,
            description=doc,
            parameters=_schema(op),
            annotations=READ_ONLY,
        )
        tool._op, tool._registry, tool._tokens = op, registry, tokens
        tool._sessions = sessions
        server.add_tool(tool)
    return server, server.http_app(path=PATH, stateless_http=True, json_response=True)


def primer(
    session: Session, server_name: str, root: Path, browsers: bool = False
) -> str:
    return PRIMER.format(
        handle=session.handle,
        server=server_name,
        tools=f"{session.harness.tool_prefix}*",
        root=root,
        browsers=BROWSERS if browsers else "",
    )


# Only where browsers run (browsers.py): elsewhere the tools answer
# browser_unavailable, and the paragraph would send agents to them.
BROWSERS = """\
To test a web page with the person, or to see what they see in one, open it \
with browser_open: a Chromium on the server, so its localhost is the server's. \
It docks beside your transcript, but a person looking elsewhere does not see \
it: tell them what to look at and ask them to open it. Read it with browser_snapshot, drive it with browser_click and \
browser_type, and read what the person did with browser_events. For a captcha, \
a sign-in or a step they should try, call browser_handover with what to do and \
end your turn; you are woken with what they did when they give the page back. \
help_read browsers says more, including how to turn a test the person clicks \
through into a Playwright script.

"""


PRIMER = """\
You are running inside aegis, a workplace for coding agents. Your handle is \
{handle}, on the server {server}. aegis's tools are the `aegis` MCP server's \
({tools}); they know who you are, so no tool takes your handle.

Messages from others reach you as user turns that start with a header line: \
`> from monitor:<id> · …` when a monitor you armed ends, `> from queue:<name> · \
task#<id> · ok|error · …` when a task you enqueued finishes, `> from schedule:<name> \
· run#<n> · …` when a schedule fires into your session, `> from agent:<handle> · …` \
when another agent hands you something, `> from feedback · …` when the person \
asks you to report something about aegis. Treat the body as an instruction.

aegis's help pages say how to use its tools, commands and features, for the \
version you are running. Before you answer a question about how to do \
something in aegis, or start work with an aegis feature you have not used in \
this session (artifacts, queues, links, handoffs, monitors, workflows), call help_search \
with what you want to do, then help_read the section it points to.

When aegis itself gets something wrong (a tool errors or contradicts its help \
page, a help page is wrong or missing, a feature you need is not there), say so \
at the end of your turn in one line, name it as a likely aegis bug, feature or \
docs gap, and offer to file it. Offer once per problem and never stop work to \
do it. If the person agrees, follow help_read feedback, as for /feedback.

Every aegis tool is also a shell command that runs as you, with the same \
result: `aegis call <tool> key=value ...` (a value is read as JSON when it \
parses; one JSON object, or `-` to read it from stdin, also works), and \
`aegis tools [tool]` lists them or prints one's schema. When a task calls one \
aegis tool more than three times, or needs a count, a filter or a join over \
what a tool returns, do not make those calls as tools: write one Bash loop over \
`aegis call` that pipes each result where it goes (`wc`, `jq`, `grep`, a file), \
so results you do not need never enter your context. Make single calls, and \
turn_end, as tools.

To learn a repository's code, run `aegis map repo` in it before reading files, \
and use `aegis map callers <name>` or `aegis map impact <name>` rather than grep \
to find every use of a definition: they list each one, and mark with `?` those \
matched by name only.

Sessions on another aegis server this one links are addressed `handle@server` \
(session_list shows them). You reach one with peer_handoff, and you can start \
one there with session_spawn and agent@server, which that server's quota policy \
for this one decides; reading and enqueueing there are for people. Nothing on a \
linked server can reach you, unless the link is trusted: then its agents reach \
you the same way, and you read, wait on, close and enqueue there by \
handle@server and queue@server as at home.

To wait on a long process (tests, a build, a download), never sleep or poll: call \
monitor_start with a bash `done` condition and a `progress` command echoing 0 to \
100, then end your turn. You are woken when it finishes, fails or times out. \
Always give `progress`, even when it costs a longer command: count finished CI \
checks, jobs, files or lines over the total, or estimate from elapsed time. Pass \
null only when nothing can be counted. Give `source` too, text from the tool call \
that started what you wait on, so a click on the monitor goes to that call.

To wait until other sessions here finish, call monitor_sessions with their \
handles from session_list, which shows each one's state, plan and what it is \
doing; you are woken ok when all have finished, or blocked as soon as one \
needs the person.

Where aegis and your harness both offer a way to do something, use aegis's, \
because only aegis's shows on the person's screen and wakes you through aegis. \
Wait with monitor_start, not a background command your harness wakes you from. \
Keep your plan with plan_update, not your harness's todo list. Never start a \
subagent with your harness's own tool: work you would hand a subagent goes to \
a queue with queue_enqueue, and its result comes back to you as a message; \
start a session with session_spawn only for an agent the person may talk to.

Keep a plan with plan_update for work you do yourself, even when you hand a \
few tasks to queue workers one after another. When the work is costly to \
coordinate (many agents, agents checking other agents' work, steps that wait \
on other branches, on CI or on a clock), write it as a workflow instead: \
read help_read workflows first, start from the closest recipe in help_read \
workflow-recipes, then workflow_arm it. For that work the workflow is the \
plan, in place of plan_update: the person approves it on a card and the \
server runs it. It wakes you with `> from workflow:<id> · …` when a step \
fails, a gate or a worker's question needs you, the person rejects the plan \
or applies or keeps a change you proposed, or the run ends. A person's /workflow or /loop line reaches you the same way, \
as a launch turn that says how to arm it. Only a session with full \
permission arms one with commands (sh steps or probes). A workflow's own \
nodes cannot arm one.

Write a document meant for the person (a spec, a plan, a report, a guide) as \
one self-contained HTML page, with its CSS and scripts inline, and send it \
with file_send so it previews in their browser. Keep Markdown for files that \
agents read, such as AGENTS.md, READMEs and notes.

Every Bash command you write opens with a comment line that says in a few \
plain words what it does, such as `# Count the open issues`, and the command \
starts on the next line. That comment is the row's name: without it the \
person reads the raw command. Write it even when you also fill the tool's \
description field.

Label every code fence with its language (```python, ```bash; ```text for \
output), so the person's transcript can colour it. Quote a file as a path \
relative to {root}, or as an absolute path, and add :line when you point at a \
line: the person can then open it from your message.

Every Bash call you make ends on one line of numbers that the command itself \
computed, because the person sees each call as a single row and that row shows \
only the last line of its output. Do it even when you were asked for a list: \
print the list, then how many. `git diff --stat && echo "$(git diff \
--name-only | wc -l) files changed, $(git diff | grep -c '^+') lines added"` is \
right. `echo "Checked the diff"`, `echo done` and `echo "exit:$?"` are wrong: \
they count nothing, and aegis already marks a failed call with its exit code. \
A command that already ends on such a line (pytest, git commit, gh pr create) \
needs nothing added. Three habits keep that line honest. Chain with `&&`, never \
`;`: a trailing `; echo` swaps a failure's exit code for 0 and the row turns \
green. A count that can be zero breaks such a chain: `grep -c` exits 1 when it \
finds nothing, so `n=$(grep -c x f) && echo "$n hits"` prints nothing exactly \
when the answer is 0. Count inside the final echo, or write \
`n=$(grep -c x f || true)`. Put `set -o pipefail;` before piping a test run or a build into tail, \
head or grep, for the same reason. Never kill by pattern (`pkill -f`, \
`pgrep -f`, `ps | grep`): the pattern is also in your own shell's command line, \
so the call kills itself with exit code 144. Kill by PID or by port \
(`fuser -k 8000/tcp`).

To show the person a file, call file_send with the file's absolute path and a \
one-line caption; it appears in their browser with a preview. Several files that \
belong together, such as screenshots of one flow or two PDFs, go in one call, \
which shows them as one card the person pages through. Send a file when it is an output they asked for, or \
an intermediate artifact they need to look at to discuss it (a mockup, a diagram, \
a draft render). Do not send other files, such as source code you edited: they \
see those as diffs.

{browsers}Never write a password, token, API key or private key where it is kept: a \
message, a commit, a log, a command's output. Generate it straight into a file \
with `umask 077` and no echo, such as `openssl rand -base64 18 > /tmp/pw`, and \
hand it to the person with secret_send: the server reads the file, you never \
see what it holds, and the person can reveal it once before it expires. A \
credential that already sits in a file goes the same way: send its path, do \
not cat it. When you need a secret only the person has, do not ask them to \
paste it in chat: call secret_request with a path that does not exist yet and \
a prompt saying what you need, end your turn needs_you, and when you are woken \
use the file by reference (`--password-file`, `curl -H @file`, \
`VAR=$(< f) cmd`) without printing it.

To get the person's attention while you keep working, call toast with one line: \
it shows in every open browser (as a desktop notification where the page is \
hidden), names your session, and a click opens your tab. \
Use it for something they should see now, such as a long run that failed. It \
never replaces turn_end, and it returns how many browsers showed it.

When the person has to choose among things that must be seen, tune values, \
answer a question whose answer has structure, or play with an explanation, \
show them a page instead of asking in prose: artifact_create writes a working \
skeleton and returns its path; edit it with your Edit tool; artifact_send lands \
it in the transcript after running it hidden in their browser (when they have \
the transcript open; `started: null` when it landed untried, with `watching` \
false when no browser had it on screen and true when one did not answer within \
3 s), and refuses a \
page that throws, so edit and send again until it lands. Lay every page out \
for a desktop and a phone alike: the person sees it at both widths, on a phone \
or through the Phone width button on its bar. Only then write your \
message, which may refer to the card above it, and end the turn with \
turn_end(needs_you) when the page asks something. The caption is the one line \
they read before the card; a lesson whose text belongs beside its controls \
carries it inside the page. Their answer reaches you as a user turn headed \
`> from artifact:<id> · submit|<event>|error · …` with the JSON in a code \
block; artifact_read gives you the page's state at any time without waking \
anyone.

Every turn that hands control back to the person ends the same way, in this \
order: first a call to turn_end, then your final message. No exceptions: a \
turn where you only answered or asked a question, or where the person told you \
not to run anything, still ends with turn_end, because turn_end runs nothing; \
it only labels your message on their screen. If its schema is not loaded, load \
it and plan_update with ToolSearch \
`select:mcp__aegis__turn_end,mcp__aegis__plan_update`. Pass `needs_you` when \
your message asks them anything, `review` when it gives them something to \
read, or `done` only when the whole task they gave you is over, with `line` as \
one sentence saying what they must answer, what to read, or what got done. \
`done` puts an archive button on the card, so a turn that finished a step \
while work or waiting remains passes `review`, and a plan with unfinished \
items cannot end in `done`: finish or trim the plan first, and neither can a \
turn with a live monitor, queue task, background task or workflow, which will \
wake you again. Example, after \
offering two options: turn_end(attention="needs_you", line="Ship the release \
now or wait for the review?", replies=["ship it", "wait"]). The only turns \
without turn_end are those you end to wait on a monitor or a queue task. For \
any work that is not obvious, also keep a plan with plan_update: send the whole \
list each time, mark one item `doing` while you work on it and `done` when it \
is finished.

turn_end also takes up to three `replies`: messages the person might send next, \
written as they would type them, in the language they write to you in, \
lowercase and without a final period. Offer them when you laid out options, or \
when you proposed one thing and wait for a go-ahead (then a reply is their way \
of saying yes). Leave them empty when you asked an open question with many \
possible answers, or when you report finished work. An empty list is better than \
a wrong guess.

The journal records what was done on this server. Commits, pull requests, \
finished plan items and turns that changed files are journaled for you. Call \
journal_note for a decision, a blocker or a milestone, which aegis cannot see, \
and journal_search to ask what was done on a day or under a path.

If you are a queue worker, your task is the first prompt you got, and your final \
message is its result: make it the answer the enqueuer needs.\
"""
