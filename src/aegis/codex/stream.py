"""Codex's app-server notifications, one stdout line at a time, as typed events.

The events are the ones ``claude/stream.py`` defines, so the fold keeps one set
of rules for every harness. Measured on codex-cli 0.162.1 (spec
``2026-10-09-aegis-2-codex-harness-design.md``; fixtures in
``tests/fixtures/codex/``, recorded by ``scripts/record_codex.py``):

- A turn is ``turn/started`` … ``turn/completed``; every item is
  ``item/started`` then ``item/completed``, the latter carrying the whole item.
- Text streams as ``item/agentMessage/delta`` and reasoning as
  ``item/reasoning/textDelta`` (raw content) or ``summaryTextDelta``; the item
  id is the entry id, so the opening item, the deltas and the closing item
  touch one entry.
- ``thread/tokenUsage/updated`` follows each model request; its ``last``
  breakdown is that request, and ``outputTokens`` already holds the reasoning.
- A subagent is a ``collabAgentToolCall`` whose ``receiverThreadIds`` name a
  child thread; the child's own events arrive on the same stream and are steps
  of that call.
- No line names the model of a turn or the Codex version, so CodexProcess
  writes its own ``aegis/*`` lines for them (``OWN``).

The parser keeps what one line cannot carry alone (the root thread, the child
threads, the model, item kinds, open calls, the running cost), so it must see
every stored line in order, which is why the fold owns it. Every value comes
from the lines and none from the clock, so a fold of the store equals the live
fold.
"""

from __future__ import annotations

import json
import re
from typing import Any

from ..claude.stream import (
    Compact,
    Delta,
    Echo,
    Event,
    Garbled,
    Ignored,
    Init,
    Result,
    Step,
    Text,
    Thinking,
    Title,
    ToolCall,
    ToolOutput,
    Usage,
)
from ..usage.prices import codex_prices_for
from .config import SERVER

LABEL = "Codex"
OWN = "aegis/"
STORED = frozenset(
    {
        "turn/started",
        "turn/completed",
        "item/started",
        "item/completed",
        "thread/tokenUsage/updated",
        "thread/name/updated",
        "turn/plan/updated",
    }
)
DELTAS = frozenset(
    {
        "item/agentMessage/delta",
        "item/reasoning/textDelta",
        "item/reasoning/summaryTextDelta",
    }
)

_VERSION = re.compile(r"/(\d+\.\d+\.\d+)")
_STATUS = {"inProgress": "in_progress"}


def _dict(v: Any) -> dict:
    return v if isinstance(v, dict) else {}


def _str(v: Any) -> str | None:
    return v if isinstance(v, str) else None


def diff_pair(diff: str) -> tuple[str, str]:
    """A unified diff's (old, new) text, so an Edit row draws its diff window."""
    old: list[str] = []
    new: list[str] = []
    for ln in diff.splitlines():
        if ln.startswith(("@@", "---", "+++")):
            continue
        if ln.startswith("-"):
            old.append(ln[1:])
        elif ln.startswith("+"):
            new.append(ln[1:])
        else:
            body = ln[1:] if ln.startswith(" ") else ln
            old.append(body)
            new.append(body)
    return "\n".join(old), "\n".join(new)


def _usage(b: Any) -> Usage | None:
    """One request's tokens. Codex counts cached and written input inside
    ``inputTokens``; aegis counts them apart, so ``context`` is ``totalTokens``."""
    if not isinstance(b, dict):
        return None
    cached = int(b.get("cachedInputTokens") or 0)
    write = int(b.get("cacheWriteInputTokens") or 0)
    return Usage(
        input=max(0, int(b.get("inputTokens") or 0) - cached - write),
        cache_creation=write,
        cache_read=cached,
        output=int(b.get("outputTokens") or 0),
    )


def _user_text(item: dict) -> str:
    return "".join(
        str(c.get("text") or "")
        for c in item.get("content") or []
        if isinstance(c, dict) and c.get("type") == "text"
    )


def _shell(item: dict) -> str:
    """The command Codex ran, without its ``/bin/bash -lc`` wrapper when the
    item says what it was."""
    for a in item.get("commandActions") or []:
        if isinstance(a, dict) and isinstance(a.get("command"), str):
            return a["command"]
    return str(item.get("command") or "")


def _calls(item: dict) -> list[tuple[str, str, dict]]:
    """The tool rows one item makes: (call id, Claude's tool name, input)."""
    iid, kind = str(item.get("id") or ""), item.get("type")
    if kind == "commandExecution":
        return [(iid, "Bash", {"command": _shell(item)})]
    if kind == "mcpToolCall":
        server = "aegis" if item.get("server") == SERVER else item.get("server")
        name = f"mcp__{server}__{item.get('tool')}"
        return [(iid, name, _dict(item.get("arguments")))]
    if kind == "webSearch":
        return [(iid, "WebSearch", {"query": str(item.get("query") or "")})]
    if kind == "collabAgentToolCall":
        prompt = str(item.get("prompt") or "")
        if item.get("tool") == "spawnAgent":
            return [(iid, "Task", {"description": prompt[:60], "prompt": prompt})]
        return [
            (iid, f"agent.{item.get('tool')}", {"description": str(item.get("tool"))})
        ]
    if kind == "fileChange":
        changes = [c for c in item.get("changes") or [] if isinstance(c, dict)]
        out = []
        for n, ch in enumerate(changes):
            cid = iid if len(changes) == 1 else f"{iid}:{n}"
            path = str(ch.get("path") or "")
            old, new = diff_pair(str(ch.get("diff") or ""))
            how = _dict(ch.get("kind")).get("type")
            if how == "add":
                out.append((cid, "Write", {"file_path": path, "content": new}))
            elif how == "delete":
                out.append((cid, "Delete", {"file_path": path}))
            else:
                out.append(
                    (
                        cid,
                        "Edit",
                        {"file_path": path, "old_string": old, "new_string": new},
                    )
                )
        return out
    return []


def _result(item: dict) -> tuple[str, bool]:
    """(output text, is_error) of a finished tool item."""
    kind, status = item.get("type"), item.get("status")
    if kind == "commandExecution":
        code = item.get("exitCode")
        failed = status != "completed" or (isinstance(code, int) and code != 0)
        return str(item.get("aggregatedOutput") or ""), failed
    if kind == "mcpToolCall":
        if status == "failed":
            return str(_dict(item.get("error")).get("message") or "failed"), True
        res = _dict(item.get("result"))
        text = "\n".join(
            str(c.get("text") or "")
            for c in res.get("content") or []
            if isinstance(c, dict) and c.get("type") == "text"
        )
        return text, False
    return str(status or ""), status == "failed"


class Parser:
    def __init__(self) -> None:
        self.root: str | None = None
        self.children: dict[str, str] = {}  # child thread -> its Task call
        self.task_call: str | None = None
        self.version: str | None = None
        self.model: str | None = None
        self.title: str | None = None
        self.kinds: dict[str, str] = {}  # item id -> "prose" | "thinking"
        self.open_calls: set[str] = set()
        self.closed_calls: set[str] = set()
        self.cost: float | None = None
        self.window: int | None = None
        self.skip_echo = False
        self.plans = 0
        self.turn_open = False

    def end_turn(self) -> None:
        """The turn ended (its ``turn/completed``, or for the fold an exit, a
        stop or a server restart): nothing of it may reach the next."""
        self.closed_calls |= self.open_calls
        self.open_calls.clear()
        self.turn_open = False
        self.skip_echo = False

    def feed(self, line: str) -> list[Event]:
        try:
            obj: Any = json.loads(line)
        except ValueError:
            return [Garbled(raw=line)]
        if not isinstance(obj, dict):
            return [Garbled(raw=line)]
        method = str(obj.get("method"))
        handler = getattr(self, HANDLERS.get(method, ""), None)
        if handler is None:
            return [Ignored(type=method)]
        return handler(_dict(obj.get("params")))

    # -- whose line ----------------------------------------------------
    def _whose(self, tid: Any) -> tuple[bool, str | None]:
        """Whether the line is this conversation's, and the Task call it is a
        step of (None for the conversation itself). A thread nobody named is a
        subagent of the last spawn, or nobody's."""
        if not isinstance(tid, str):
            return False, None
        if self.root is None:
            self.root = tid
        if tid == self.root:
            return True, None
        if tid not in self.children:
            if self.task_call is None:
                return False, None
            self.children[tid] = self.task_call
        return True, self.children[tid]

    # -- aegis's own lines ---------------------------------------------
    def _initialize(self, p: dict) -> list[Event]:
        m = _VERSION.search(str(p.get("userAgent") or ""))
        self.version = m.group(1) if m else None
        return []

    def _thread(self, p: dict) -> list[Event]:
        th = _dict(p.get("thread"))
        tid = _str(th.get("id"))
        provider, model = _str(th.get("modelProvider")), _str(th.get("model"))
        self.root, self.children, self.task_call = tid, {}, None
        self.model = f"{provider}/{model}" if provider and model else None
        return [
            Init(
                session_id=tid,
                model=self.model,
                version=self.version,
                harness=LABEL,
                cwd=_str(th.get("cwd")),
            )
        ]

    def _turn_settings(self, p: dict) -> list[Event]:
        model = _str(p.get("model"))
        if not model or model == self.model:
            return []
        self.model = model
        return [
            Init(session_id=self.root, model=model, version=self.version, harness=LABEL)
        ]

    def _command(self, p: dict) -> list[Event]:
        """A slash command, shown as typed. Its turn's user message, if any,
        is the expanded input and is not echoed again."""
        self.skip_echo = True
        return [Echo(text=str(p.get("line") or ""))]

    # -- turns -----------------------------------------------------------
    def _turn_started(self, p: dict) -> list[Event]:
        ok, parent = self._whose(p.get("threadId"))
        if ok and parent is None:
            self.turn_open = True
        return []

    def _turn_completed(self, p: dict) -> list[Event]:
        ok, parent = self._whose(p.get("threadId"))
        if not ok or parent is not None or not self.turn_open:
            return []
        turn = _dict(p.get("turn"))
        status = turn.get("status")
        if status == "completed":
            subtype = "success"
        elif status == "interrupted":
            subtype = "interrupted"
        else:
            subtype = str(_dict(turn.get("error")).get("message") or status or "error")[
                :120
            ]
        span = turn.get("durationMs")
        result = Result(
            is_error=status != "completed",
            subtype=subtype,
            duration_ms=span if isinstance(span, int) else None,
            cost_usd=self.cost,
            stop_reason=None,
            context_window=self.window,
        )
        self.end_turn()
        return [result]

    # -- items -----------------------------------------------------------
    def _item_started(self, p: dict) -> list[Event]:
        return self._item(p, done=False)

    def _item_completed(self, p: dict) -> list[Event]:
        return self._item(p, done=True)

    def _item(self, p: dict, *, done: bool) -> list[Event]:
        ok, parent = self._whose(p.get("threadId"))
        item = _dict(p.get("item"))
        if not ok:
            return []
        kind, iid = item.get("type"), str(item.get("id") or "")
        if kind == "userMessage":
            if not done or parent is not None:
                return []
            if self.skip_echo:
                self.skip_echo = False
                return []
            text = _user_text(item)
            return [Echo(text=text)] if text.strip() else []
        if kind == "agentMessage":
            self.kinds[iid] = "prose"
            return [
                Text(
                    text=str(item.get("text") or ""), parent=parent, usage=None, key=iid
                )
            ]
        if kind == "reasoning":
            self.kinds[iid] = "thinking"
            text = "\n\n".join(
                str(c) for c in item.get("content") or []
            ) or "\n\n".join(str(s) for s in item.get("summary") or [])
            return [Thinking(text=text, parent=parent, usage=None, key=iid)]
        if kind == "contextCompaction":
            return (
                [Compact(pre_tokens=0, post_tokens=0)]
                if done and parent is None
                else []
            )
        return self._tool(item, parent, done)

    def _tool(self, item: dict, parent: str | None, done: bool) -> list[Event]:
        out: list[Event] = []
        for cid, name, inp in _calls(item):
            if cid in self.closed_calls:
                continue  # its turn already ended: an interrupt's late item
            if cid not in self.open_calls:
                self.open_calls.add(cid)
                out.append(
                    ToolCall(id=cid, name=name, input=inp, parent=parent, usage=None)
                )
                if name == "Task" and parent is None:
                    self.task_call = cid
            if done:
                self.open_calls.discard(cid)
                self.closed_calls.add(cid)
                text, err = _result(item)
                out.append(ToolOutput(id=cid, text=text, is_error=err, parent=parent))
        if (
            item.get("type") == "collabAgentToolCall"
            and item.get("tool") == "spawnAgent"
        ):
            for t in item.get("receiverThreadIds") or []:
                if isinstance(t, str) and t != self.root:
                    self.children.setdefault(t, str(item.get("id")))
        return out

    def _plan(self, p: dict) -> list[Event]:
        ok, parent = self._whose(p.get("threadId"))
        if not ok:
            return []
        self.plans += 1
        cid = f"plan:{p.get('turnId')}:{self.plans}"
        todos = [
            {
                "content": str(s.get("step") or ""),
                "status": _STATUS.get(str(s.get("status")), str(s.get("status"))),
                "activeForm": str(s.get("step") or ""),
            }
            for s in p.get("plan") or []
            if isinstance(s, dict)
        ]
        return [
            ToolCall(
                id=cid,
                name="TodoWrite",
                input={"todos": todos},
                parent=parent,
                usage=None,
            ),
            ToolOutput(id=cid, text="", is_error=False, parent=parent),
        ]

    # -- usage, names, deltas ----------------------------------------
    def _price(self, u: Usage) -> float | None:
        """What one request cost at the turn's model's rates, or None."""
        prices = codex_prices_for(self.model)
        if prices is None:
            return None
        return float(
            prices.cost(
                inp=u.input,
                out=u.output,
                cc5=u.cache_creation,
                cc1=0,
                cache_read=u.cache_read,
            )
        )

    def _token_usage(self, p: dict) -> list[Event]:
        ok, parent = self._whose(p.get("threadId"))
        tu = _dict(p.get("tokenUsage"))
        u = _usage(tu.get("last"))
        if not ok or u is None:
            return []
        if parent is None and isinstance(tu.get("modelContextWindow"), int):
            self.window = tu["modelContextWindow"]
        cost = self._price(u)
        if cost is not None:
            self.cost = (self.cost or 0.0) + cost
        return [Step(usage=u, parent=parent)]

    def _name(self, p: dict) -> list[Event]:
        ok, parent = self._whose(p.get("threadId"))
        name = _str(p.get("threadName"))
        if not ok or parent is not None or not name or name == self.title:
            return []
        self.title = name
        return [Title(text=name)]

    def _delta(self, p: dict) -> list[Event]:
        """Always one Delta, so the session never stores a delta line; one
        with no key (a child's, or an item not seen opening) draws nothing."""
        ok, parent = self._whose(p.get("threadId"))
        iid = str(p.get("itemId") or "")
        kind = self.kinds.get(iid)
        if not ok or parent is not None or kind is None:
            return [Delta(key="", kind="", text="")]
        return [Delta(key=iid, kind=kind, text=str(p.get("delta") or ""))]


HANDLERS = {
    "aegis/initialize": "_initialize",
    "aegis/thread": "_thread",
    "aegis/turn": "_turn_settings",
    "aegis/command": "_command",
    "turn/started": "_turn_started",
    "turn/completed": "_turn_completed",
    "item/started": "_item_started",
    "item/completed": "_item_completed",
    "turn/plan/updated": "_plan",
    "thread/tokenUsage/updated": "_token_usage",
    "thread/name/updated": "_name",
    "item/agentMessage/delta": "_delta",
    "item/reasoning/textDelta": "_delta",
    "item/reasoning/summaryTextDelta": "_delta",
}
