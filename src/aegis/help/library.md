---
title: aegis as a library
kind: howto
when: building your own program on aegis, starting an aegis server from Python, or driving sessions from code instead of a browser
hints:
  - Run aegis inside your own Python program and drive its sessions from code. #start-a-server
  - session.ask() sends a message and returns the turn it started, typed. #drive-a-session
---

A Python program can run an aegis server inside its own event loop and drive
it through typed objects: a `Server`, its sessions as `Session` handles, and
every other operation as a typed method on `srv.ops`. The names exported by
`aegis` itself (`Server`, `Session`, `Turn`, `StartError`, `OpError`,
`SessionNotFound`) and by `aegis.api` are the stable surface. Every other
module is internal and may change in any release.

## Start a server

```python
import asyncio
from aegis import Server

async def main():
    async with Server("./myproj", name="lab") as srv:
        print(srv.url)   # the web client, signed in
        ...

asyncio.run(main())
```

`Server` takes the options of `aegis serve`: the root, the folder holding
`.aegis.yaml` (required, since a library never guesses from the working
directory), `port` (0, the default, takes a free one),
`host`, `name`, and the harness binaries (`claude`, `opencode`, `codex`,
`cursor`). Entering the block runs the startup checks, boots aegis and
listens. Leaving it shuts aegis down as a stopped `aegis serve` does.

- It always listens, on loopback unless you pass `host`. The agents it
  starts reach aegis's tools at the server's MCP endpoint, under
  `srv.base_url`, so a server without a listener would start agents with no
  aegis tools. The web
  client at `srv.url` is there if you want to watch.
- A failed startup check raises `StartError`. Its `lines` hold what
  `aegis serve` would print.
- It installs no signal handlers. Your program decides when aegis stops, by
  leaving the block.
- One Server runs per process at a time. A second one raises `RuntimeError`
  until the first has exited.
- Every call runs as the server's admin, as a browser with the owner's token
  does.

## Drive a session

```python
from aegis.api import Prose, ToolCall, TurnEnded

s = await srv.sessions.spawn(agent="opus", cwd="repo")
turn = await s.ask("summarize the README", timeout=600)
print(turn.text, turn.attention, turn.cost_usd)

await s.send("now write tests for it")
async for ev in s.events():
    match ev:
        case Prose(md=md):
            print(md)
        case ToolCall(title=title, status=status):
            print("tool", title, status)
        case TurnEnded():
            break
await s.close()
```

`srv.sessions` has `spawn(prompt=None, *, agent, harness, model, effort,
permission, cwd, quiet)`, `list()` and `get(ref)`, where `ref` is a log id or
a handle. A `Session` is a live handle known by its log id. Its methods act
on the real session, so a Session kept in a variable never goes stale:

- `info()` returns a `SessionInfo` snapshot of the session's card: handle,
  title, agent, model, state (working, idle, asleep, error), attention, plan,
  cost.
- `send(text)` delivers a message and returns once it is accepted.
- `ask(text)` sends and waits for the turn to end, and `wait()` waits for the
  running one. Both return a `Turn`: its attention and line, the turn's text,
  its entries and, for `ask`, what it cost.
- `events(history=False)` yields each transcript entry once it settles
  (`UserMessage`, `Prose`, `Thinking`, `ToolCall`, `SystemNote`, `ErrorNote`,
  or `OtherEntry` for a kind the library does not model yet), and
  `TurnEnded` when a turn ends. Leaving the loop unsubscribes.
- `entries()`, `interrupt()`, `stop()`, `close()` and `rename(handle, title)`
  do what their names say. A closed session raises `SessionNotFound`.

## Call any operation

Everything aegis can do is an operation, and `srv.ops` has each one as a
method with keyword arguments: `srv.ops.queue.enqueue(queue="review",
prompt="...")`, `srv.ops.browser.goto(browser="b1", url="...")`. In a name
with more than one dot, the dots after the first become underscores, so
`workflow.edit.apply` is `srv.ops.workflow.edit_apply()`. An operation with
no dot is a method of `srv.ops` itself, such as `srv.ops.meta()`. The
arguments are checked by the operation's own model, and a wrong one raises
`OpError` with the code bad_params. The results are what the browser and the
agents get: dicts, lists and strings.

The signatures are in `src/aegis/api/ops.pyi`, so an editor completes them
and a type checker checks them. After changing an operation's parameters,
run `make ops-stubs` and commit the file. A test fails while it is stale.
