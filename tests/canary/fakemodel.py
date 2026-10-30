"""A fake model for the real harnesses: the Anthropic Messages API, the OpenAI
Responses API and OpenAI Chat Completions, answering from a script.

The canary tests run the published Claude Code, Codex and OpenCode binaries,
so the harness is real and only the model is not. The script is a list of
``Rule``s. Each request is answered from the rule whose ``when`` text appears
latest in the conversation's last person message, and the reply is the rule's
first step the conversation has not done yet: a step is done once an assistant
message after that person message called its tool. So the reply depends only
on the request, and a harness that retries, compacts or reorders is answered
the same way.

A request with no tools is a harness's side call (a title, a summary) and gets
``SIDE``. A tool the script calls is found by suffix in the request's tool
list, because each harness names aegis's tools its own way
(``mcp__aegis__turn_end``, ``aegis_turn_end``, ...). When it is not listed but
a tool-search tool is, the fake searches for it first, as a model would. When
neither is, the reply says so in text, and the log records it.

A request whose last person message carries ``LIMITED`` and that names
``MODEL`` (not ``MODEL-two``) is answered with an HTTP 429 in the API's own
error shape and ``x-should-retry: false``, as a provider out of quota answers:
the cascade scenario falls from one model to the other on it.

Every request and reply goes to ``log``, which the tests write next to their
failure: it is the first thing to read when a harness changed.
"""

from __future__ import annotations

import asyncio
import json
import re
import time
import uuid
from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import Any

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, Response, StreamingResponse
from starlette.routing import Route

SIDE = "ok"
MODEL = "canary-model"
LIMITED = "CANARY-429"
# The 429 bodies: Anthropic's error shape, then OpenAI's (Responses and Chat).
LIMIT_ANTHROPIC = {
    "type": "error",
    "error": {"type": "rate_limit_error", "message": "canary: rate limit reached"},
}
LIMIT_OPENAI = {
    "error": {
        "message": "canary: rate limit reached",
        "type": "rate_limit_exceeded",
        "code": "rate_limit_exceeded",
    }
}


@dataclass(frozen=True)
class Call:
    """Call the harness's tool whose name ends with ``tool``."""

    tool: str
    args: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class Say:
    """End the turn with this text, after ``delay`` seconds. With ``recall``,
    the text is ``recall`` when it appears anywhere earlier in the
    conversation, and ``FORGOT`` when it does not."""

    text: str
    delay: float = 0.0
    recall: str | None = None


@dataclass(frozen=True)
class Shell:
    """Run ``cmd`` with the harness's own shell tool, whatever it is called.
    ``{path:NAME}`` in it becomes the path ending in NAME that the person
    message carries, as an attachment's does."""

    cmd: str


Step = Call | Say | Shell

# A harness's shell tool, and its arguments for one command line.
SHELLS = {
    "Bash": lambda cmd: {"command": cmd, "description": "canary"},
    "exec_command": lambda cmd: {"cmd": cmd},
    "shell_command": lambda cmd: {"command": cmd},
    "shell": lambda cmd: {"command": cmd, "description": "canary"},
    "bash": lambda cmd: {"command": cmd, "description": "canary"},
}


@dataclass
class Rule:
    when: str
    steps: list[Step]


# -- one conversation, read the same way from every wire format ----------------


@dataclass
class Convo:
    last_person: str  # every text of the last person message, joined
    called: list[str]  # tool names the assistant called after it, in order
    tools: list[str]  # tool names the request offers
    tool_results: list[str]  # results after the last person message
    history: str = ""  # every text before the last person message


def _texts(content: Any) -> Iterator[str]:
    if isinstance(content, str):
        yield content
    elif isinstance(content, list):
        for b in content:
            if isinstance(b, dict):
                if isinstance(b.get("text"), str):
                    yield b["text"]
                elif b.get("type") == "tool_result":
                    pass


def _anthropic(body: dict) -> Convo:
    msgs = body.get("messages") or []
    idx, person = -1, ""
    for i, m in enumerate(msgs):
        if m.get("role") != "user":
            continue
        c = m.get("content")
        blocks = c if isinstance(c, list) else [{"type": "text", "text": c}]
        if any(isinstance(b, dict) and b.get("type") == "text" for b in blocks):
            if not any(
                isinstance(b, dict) and b.get("type") == "tool_result" for b in blocks
            ):
                idx, person = i, "\n".join(_texts(c))
    called, results = [], []
    for m in msgs[idx + 1 :]:
        for b in m.get("content") if isinstance(m.get("content"), list) else []:
            if not isinstance(b, dict):
                continue
            if b.get("type") == "tool_use":
                called.append(b.get("name", ""))
            elif b.get("type") == "tool_result":
                results.append(json.dumps(b.get("content")))
    tools = [t.get("name", "") for t in body.get("tools") or [] if isinstance(t, dict)]
    return Convo(person, called, tools, results, json.dumps(msgs[:idx]))


def _responses(body: dict) -> Convo:
    items = body.get("input") or []
    if isinstance(items, str):
        items = [{"type": "message", "role": "user", "content": items}]
    idx, person = -1, ""
    for i, it in enumerate(items):
        if it.get("role") == "user" and it.get("type", "message") == "message":
            idx, person = i, "\n".join(_texts(it.get("content")))
    called, results, found = [], [], []
    for it in items[idx + 1 :]:
        if it.get("type") in ("function_call", "custom_tool_call"):
            called.append(_qualified(it))
        elif it.get("type") == "tool_search_call":
            called.append("tool_search")
        elif it.get("type") in ("function_call_output", "custom_tool_call_output"):
            results.append(json.dumps(it.get("output")))
        elif it.get("type") in ("tool_search_output", "additional_tools"):
            # Codex defers MCP tools on its own models: what a search found
            # is callable from the next request on.
            found += it.get("tools") or []
    for it in items[: idx + 1]:
        if it.get("type") == "additional_tools":
            found += it.get("tools") or []
    tools = _response_tools([*(body.get("tools") or []), *found])
    return Convo(person, called, tools, results, json.dumps(items[:idx]))


def _qualified(it: dict) -> str:
    ns = it.get("namespace")
    name = it.get("name", "")
    return f"{ns}::{name}" if isinstance(ns, str) and ns else name


def _response_tools(tools: list) -> list[str]:
    """Responses tools: functions, and namespaces of functions."""
    out = []
    for t in tools:
        if not isinstance(t, dict):
            continue
        if t.get("type") == "namespace":
            ns = t.get("name", "")
            for sub in t.get("tools") or []:
                if isinstance(sub, dict):
                    out.append(f"{ns}::{sub.get('name', '')}")
        elif t.get("type") == "tool_search":
            out.append("tool_search")
        elif t.get("name"):
            out.append(t["name"])
    return out


def _chat(body: dict) -> Convo:
    msgs = body.get("messages") or []
    idx, person = -1, ""
    for i, m in enumerate(msgs):
        if m.get("role") == "user":
            idx, person = i, "\n".join(_texts(m.get("content")))
    called, results = [], []
    for m in msgs[idx + 1 :]:
        for tc in m.get("tool_calls") or []:
            called.append((tc.get("function") or {}).get("name", ""))
        if m.get("role") == "tool":
            results.append(json.dumps(m.get("content")))
    tools = [
        (t.get("function") or {}).get("name", "")
        for t in body.get("tools") or []
        if isinstance(t, dict)
    ]
    return Convo(person, called, tools, results, json.dumps(msgs[:idx]))


# -- deciding the reply --------------------------------------------------------


@dataclass(frozen=True)
class Reply:
    text: str | None = None
    tool: str | None = None  # the harness's name for it
    args: dict[str, Any] | None = None
    # A namespaced Responses tool travels as name + namespace.
    namespace: str | None = None
    delay: float = 0.0


SEARCH_TOOLS = ("ToolSearch", "tool_search", "search_tools")


def _find(tools: list[str], suffix: str) -> str | None:
    hits = [t for t in tools if _named(t, suffix)]
    hits.sort(key=lambda t: ("aegis" not in t, len(t)))
    return hits[0] if hits else None


def _done(called: list[str], suffix: str) -> int:
    return sum(1 for c in called if _named(c, suffix))


def _named(name: str, suffix: str) -> bool:
    return name == suffix or name.endswith(("_" + suffix, "::" + suffix))


class FakeModel:
    def __init__(self) -> None:
        self.rules: list[Rule] = []
        self.log: list[dict] = []
        self.systems: list[str] = []  # each request's system text (_system)

    def script(self, *rules: Rule) -> None:
        self.rules = list(rules)

    def decide(self, c: Convo) -> Reply:
        r = self._decide(c)
        return r

    def _decide(self, c: Convo) -> Reply:
        if not c.tools:
            return Reply(text=SIDE)
        # The marker that comes last wins: a harness may fold an interrupted
        # message and the next one into a single person message.
        hits = [(c.last_person.rfind(r.when), r) for r in self.rules]
        at, rule = max(hits, key=lambda h: h[0], default=(-1, None))
        if at < 0 or rule is None:
            return Reply(text=SIDE)
        seen: dict[str, int] = {}
        for step in rule.steps:
            if isinstance(step, Say):
                text = step.text
                if step.recall is not None:
                    text = step.recall if step.recall in c.history else "FORGOT"
                return Reply(text=text, delay=step.delay)
            if isinstance(step, Shell):
                seen["shell"] = seen.get("shell", 0) + 1
                if sum(1 for x in c.called if x in SHELLS) >= seen["shell"]:
                    continue
                name = next((t for t in SHELLS if t in c.tools), None)
                if name is None:
                    continue  # a session without a shell: nothing to run
                return Reply(tool=name, args=SHELLS[name](_fill(step.cmd, c)))
            seen[step.tool] = seen.get(step.tool, 0) + 1
            if _done(c.called, step.tool) >= seen[step.tool]:
                continue
            name = _find(c.tools, step.tool)
            if name is not None:
                return Reply(tool=name, args=step.args)
            search = next((t for t in SEARCH_TOOLS if t in c.tools), None)
            searched = sum(1 for x in c.called if x in SEARCH_TOOLS)
            if search and searched < 3:
                return Reply(tool=search, args=_search_args(search, step.tool))
            return Reply(text=f"CANARY: no tool ending in {step.tool!r} was offered")
        return Reply(text=SIDE)

    # -- the app --

    def app(self) -> Starlette:
        return Starlette(
            routes=[
                Route("/v1/messages", self._messages, methods=["POST"]),
                Route("/v1/messages/count_tokens", self._count, methods=["POST"]),
                Route("/v1/responses", self._responses, methods=["POST"]),
                Route("/v1/chat/completions", self._chat, methods=["POST"]),
                Route("/v1/models", self._models, methods=["GET"]),
                Route("/{path:path}", self._other, methods=["GET", "POST", "HEAD"]),
            ]
        )

    def _system(self, body: dict) -> int:
        """Anthropic's system, the Responses API's instructions or a system
        message, kept in ``systems``; its index there."""
        self.systems.append(
            "\n".join(
                [
                    *_texts(body.get("system")),
                    *_texts(body.get("instructions")),
                    *(
                        t
                        for m in body.get("messages") or []
                        if isinstance(m, dict) and m.get("role") == "system"
                        for t in _texts(m.get("content"))
                    ),
                ]
            )
        )
        return len(self.systems) - 1

    def _record(self, api: str, body: dict, c: Convo, r: Reply) -> None:
        self.log.append(
            {
                "t": time.time(),
                "api": api,
                "model": body.get("model"),
                # Where systems[] keeps what the harness put before the
                # conversation, kept out of the log a failed run reports.
                "system": self._system(body),
                "stream": body.get("stream"),
                "last_person": c.last_person[-400:],
                # A steered notice, in whichever message the harness put it.
                "whole": " ".join(
                    re.findall(
                        r"> from aegis:notice[^\"]{0,200}",
                        json.dumps(body.get("messages") or body.get("input") or []),
                    )
                ),
                "called": c.called,
                "tools": c.tools,
                "tool_types": sorted(
                    {
                        t.get("type", "?")
                        for t in body.get("tools") or []
                        if isinstance(t, dict)
                    }
                ),
                "tool_results": [x[:400] for x in c.tool_results],
                "special_tools": [
                    t
                    for t in body.get("tools") or []
                    if isinstance(t, dict)
                    and t.get("type") not in ("function", "custom")
                ],
                "input_types": [
                    i.get("type")
                    for i in body.get("input") or []
                    if isinstance(i, dict)
                ],
                "reply": {k: v for k, v in r.__dict__.items() if v is not None},
            }
        )

    def _limited(self, api: str, body: dict, c: Convo) -> Response | None:
        """The 429 a LIMITED request on MODEL gets, or None."""
        model = str(body.get("model") or "")
        if not c.tools or LIMITED not in c.last_person or not model.endswith(MODEL):
            return None
        self.log.append({"t": time.time(), "api": api, "model": model, "reply": 429})
        shape = LIMIT_ANTHROPIC if api == "anthropic" else LIMIT_OPENAI
        return JSONResponse(shape, status_code=429, headers={"x-should-retry": "false"})

    async def _other(self, request: Request) -> Response:
        self.log.append({"t": time.time(), "api": "unknown", "path": request.url.path})
        return JSONResponse({"error": {"message": "not faked"}}, status_code=404)

    async def _models(self, request: Request) -> Response:
        return JSONResponse(
            {
                "object": "list",
                "data": [
                    {"id": m, "object": "model", "owned_by": "canary"}
                    for m in (MODEL, MODEL + "-two")
                ],
            }
        )

    async def _count(self, request: Request) -> Response:
        return JSONResponse({"input_tokens": 100})

    # Anthropic Messages
    async def _messages(self, request: Request) -> Response:
        body = await request.json()
        c = _anthropic(body)
        if limited := self._limited("anthropic", body, c):
            return limited
        r = self.decide(c)
        self._record("anthropic", body, c, r)
        if r.delay:
            await asyncio.sleep(r.delay)
        mid = "msg_" + uuid.uuid4().hex[:20]
        if r.tool:
            block = {
                "type": "tool_use",
                "id": "toolu_" + uuid.uuid4().hex[:20],
                "name": r.tool,
                "input": r.args or {},
            }
            stop = "tool_use"
        else:
            block = {"type": "text", "text": r.text or ""}
            stop = "end_turn"
        usage = {"input_tokens": 100, "output_tokens": 10}
        msg = {
            "id": mid,
            "type": "message",
            "role": "assistant",
            "model": body.get("model", MODEL),
            "content": [block],
            "stop_reason": stop,
            "stop_sequence": None,
            "usage": usage,
        }
        if not body.get("stream"):
            return JSONResponse(msg)

        def events() -> Iterator[str]:
            start = {**msg, "content": [], "stop_reason": None}
            yield _sse("message_start", {"type": "message_start", "message": start})
            empty = (
                {**block, "input": {}} if block["type"] == "tool_use" else
                {"type": "text", "text": ""}
            )  # fmt: skip
            yield _sse(
                "content_block_start",
                {"type": "content_block_start", "index": 0, "content_block": empty},
            )
            delta = (
                {"type": "input_json_delta", "partial_json": json.dumps(block["input"])}
                if block["type"] == "tool_use"
                else {"type": "text_delta", "text": block["text"]}
            )
            yield _sse(
                "content_block_delta",
                {"type": "content_block_delta", "index": 0, "delta": delta},
            )
            yield _sse("content_block_stop", {"type": "content_block_stop", "index": 0})
            yield _sse(
                "message_delta",
                {
                    "type": "message_delta",
                    "delta": {"stop_reason": stop, "stop_sequence": None},
                    "usage": {"output_tokens": 10},
                },
            )
            yield _sse("message_stop", {"type": "message_stop"})

        return StreamingResponse(events(), media_type="text/event-stream")

    # OpenAI Responses
    async def _responses(self, request: Request) -> Response:
        body = await request.json()
        c = _responses(body)
        if limited := self._limited("responses", body, c):
            return limited
        items = body.get("input") or []
        compact = (
            isinstance(items, list)
            and bool(items)
            and isinstance(items[-1], dict)
            and items[-1].get("type") == "compaction_trigger"
        )
        # Codex compacts on the server when a turn changes model: the reply is
        # one opaque compaction item.
        r = Reply(text="(compaction)") if compact else self.decide(c)
        if r.tool and r.namespace is None:
            r = _split_namespace(r)
        self._record("responses", body, c, r)
        if r.delay:
            await asyncio.sleep(r.delay)
        rid = "resp_" + uuid.uuid4().hex[:20]
        if compact:
            item: dict[str, Any] = {
                "type": "compaction",
                "id": "cmp_" + uuid.uuid4().hex[:20],
                "encrypted_content": "canary-compacted",
            }
        elif r.tool == "tool_search":
            item = {
                "type": "tool_search_call",
                "id": "ts_" + uuid.uuid4().hex[:20],
                "call_id": "call_" + uuid.uuid4().hex[:20],
                "status": "completed",
                "execution": "client",
                "arguments": {"query": (r.args or {}).get("query", ""), "limit": 8},
            }
        elif r.tool:
            item = {
                "type": "function_call",
                "id": "fc_" + uuid.uuid4().hex[:20],
                "call_id": "call_" + uuid.uuid4().hex[:20],
                "name": r.tool,
                "arguments": json.dumps(r.args or {}),
                "status": "completed",
            }
            if r.namespace:
                item["namespace"] = r.namespace
        else:
            item = {
                "type": "message",
                "id": "msg_" + uuid.uuid4().hex[:20],
                "role": "assistant",
                "status": "completed",
                "content": [
                    {"type": "output_text", "text": r.text or "", "annotations": []}
                ],
            }
        usage = {
            "input_tokens": 100,
            "input_tokens_details": {"cached_tokens": 0},
            "output_tokens": 10,
            "output_tokens_details": {"reasoning_tokens": 0},
            "total_tokens": 110,
        }
        resp = {
            "id": rid,
            "object": "response",
            "created_at": int(time.time()),
            "model": body.get("model", MODEL),
            "status": "completed",
            "output": [item],
            "usage": usage,
        }
        if not body.get("stream"):
            return JSONResponse(resp)

        def events() -> Iterator[str]:
            n = 0

            def ev(kind: str, data: dict) -> str:
                nonlocal n
                n += 1
                return _sse(kind, {"type": kind, "sequence_number": n, **data})

            pending = {**resp, "status": "in_progress", "output": []}
            yield ev("response.created", {"response": pending})
            yield ev("response.in_progress", {"response": pending})
            if item["type"] == "message":
                opening = {**item, "status": "in_progress", "content": []}
                yield ev(
                    "response.output_item.added", {"output_index": 0, "item": opening}
                )
                part = {"type": "output_text", "text": "", "annotations": []}
                yield ev(
                    "response.content_part.added",
                    {"item_id": item["id"], "output_index": 0, "content_index": 0,
                     "part": part},
                )  # fmt: skip
                yield ev(
                    "response.output_text.delta",
                    {"item_id": item["id"], "output_index": 0, "content_index": 0,
                     "delta": r.text or ""},
                )  # fmt: skip
                yield ev(
                    "response.output_text.done",
                    {"item_id": item["id"], "output_index": 0, "content_index": 0,
                     "text": r.text or ""},
                )  # fmt: skip
                yield ev(
                    "response.content_part.done",
                    {"item_id": item["id"], "output_index": 0, "content_index": 0,
                     "part": item["content"][0]},
                )  # fmt: skip
            elif item["type"] in ("tool_search_call", "compaction"):
                yield ev(
                    "response.output_item.added",
                    {"output_index": 0, "item": {**item, "status": "in_progress"}},
                )
            else:
                opening = {**item, "arguments": "", "status": "in_progress"}
                yield ev(
                    "response.output_item.added", {"output_index": 0, "item": opening}
                )
                yield ev(
                    "response.function_call_arguments.delta",
                    {"item_id": item["id"], "output_index": 0,
                     "delta": item["arguments"]},
                )  # fmt: skip
                yield ev(
                    "response.function_call_arguments.done",
                    {"item_id": item["id"], "output_index": 0,
                     "arguments": item["arguments"]},
                )  # fmt: skip
            yield ev("response.output_item.done", {"output_index": 0, "item": item})
            yield ev("response.completed", {"response": resp})

        return StreamingResponse(events(), media_type="text/event-stream")

    # OpenAI Chat Completions
    async def _chat(self, request: Request) -> Response:
        body = await request.json()
        c = _chat(body)
        if limited := self._limited("chat", body, c):
            return limited
        r = self.decide(c)
        self._record("chat", body, c, r)
        if r.delay:
            await asyncio.sleep(r.delay)
        cid = "chatcmpl-" + uuid.uuid4().hex[:20]
        created = int(time.time())
        model = body.get("model", MODEL)
        usage = {"prompt_tokens": 100, "completion_tokens": 10, "total_tokens": 110}
        if r.tool:
            tc = {
                "id": "call_" + uuid.uuid4().hex[:20],
                "type": "function",
                "function": {"name": r.tool, "arguments": json.dumps(r.args or {})},
            }
            message: dict[str, Any] = {
                "role": "assistant",
                "content": None,
                "tool_calls": [tc],
            }
            finish = "tool_calls"
        else:
            message = {"role": "assistant", "content": r.text or ""}
            finish = "stop"
        if not body.get("stream"):
            return JSONResponse(
                {
                    "id": cid,
                    "object": "chat.completion",
                    "created": created,
                    "model": model,
                    "choices": [
                        {"index": 0, "message": message, "finish_reason": finish}
                    ],
                    "usage": usage,
                }
            )

        def chunk(delta: dict, finish: str | None = None, **extra) -> str:
            return (
                "data: "
                + json.dumps(
                    {
                        "id": cid,
                        "object": "chat.completion.chunk",
                        "created": created,
                        "model": model,
                        "choices": [
                            {"index": 0, "delta": delta, "finish_reason": finish}
                        ],
                        **extra,
                    }
                )
                + "\n\n"
            )

        def events() -> Iterator[str]:
            yield chunk({"role": "assistant", "content": ""})
            if r.tool:
                yield chunk({"tool_calls": [{"index": 0, **tc}]})
            else:
                yield chunk({"content": r.text or ""})
            yield chunk({}, finish)
            yield (
                "data: "
                + json.dumps(
                    {
                        "id": cid,
                        "object": "chat.completion.chunk",
                        "created": created,
                        "model": model,
                        "choices": [],
                        "usage": usage,
                    }
                )
                + "\n\n"
            )
            yield "data: [DONE]\n\n"

        return StreamingResponse(events(), media_type="text/event-stream")


def _fill(cmd: str, c: Convo) -> str:
    def path(m: re.Match) -> str:
        found = re.findall(r"(/[^\s`'\"]*" + re.escape(m.group(1)) + ")", c.last_person)
        return found[-1] if found else "/no/such/" + m.group(1)

    return re.sub(r"\{path:([^}]+)\}", path, cmd)


def _sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data)}\n\n"


def _search_args(tool: str, wanted: str) -> dict:
    if tool == "ToolSearch":
        return {"query": wanted, "max_results": 5}
    return {"query": wanted}


def _split_namespace(r: Reply) -> Reply:
    """A Responses tool inside a namespace is called by its own name with the
    namespace beside it."""
    if r.tool and "::" in r.tool:
        ns, name = r.tool.split("::", 1)
        return Reply(tool=name, args=r.args, namespace=ns, delay=r.delay)
    return r
