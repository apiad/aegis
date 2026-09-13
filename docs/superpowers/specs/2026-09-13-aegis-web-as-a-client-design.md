# `aegis web` as a client, not a second daemon

**Status: proposed.** Written 2026-09-13 against `f8ab7e5`, after stage 5a
shipped. Decides where the WebSocket and the token live, which is a
question stage 5b answers differently and cannot leave open.

**Proposed by Alex**, in these words: *"`aegis server` debe levantar un
daemon con un unix socket, `aegis web` es el que levanta un puerto y sirve
por ahí, es el dueño de la app web con sus múltiples clientes."*

## The problem this starts from

`aegis` in a terminal starts a web server, and nobody asked it to.

That is not a slip. `_serve()` binds the web port whenever the config
carries a `web:` block, and `aegis web` writes that block into
`.aegis.yaml` permanently the first time it runs. From then on every
daemon for that root binds that port, including the one a bare `aegis`
autostarts in a terminal.

It cost a morning on 2026-09-13. `aegis` hung for 20s and never came up,
which is `ensure_daemon`'s timeout waiting for a socket that never
appeared. Found running: a daemon holding port 8899, absent from the
registry, with no socket file. Four daemons had started in eighty seconds,
each one spawned by the previous client's timeout, each unlinking the
previous socket and colliding on a port pinned in the config.

Two defects made that possible and both are worth naming, because this
design removes one of them by construction and leaves the other to be
fixed on its own:

- Nothing stops two daemons existing for one root. `ensure_daemon` probes
  the socket and then spawns, with no lock between.
- A daemon opens a port that the invocation did not ask for.

A lock fixes the first. Only moving the port out of the daemon fixes the
second.

## Where we are

One process does everything. `_serve()` takes the roots, the agent roster,
queues, schedules, hosts and the MCP plane, plus two switches:

| switch | what it builds |
|---|---|
| `views=True` | `ViewRegistry` + `UnixSocketServer` on `.aegis/state/daemon.sock` |
| `web=<cfg>` | `WebFrontend`, uvicorn, a TCP port |

Three commands reach it, and two of them are the same command:

- `aegis serve` → `_run_serve` → `_serve(views=True, web=boot.web)`
- `aegis web` → resolve a port, **write it to `.aegis.yaml`**, print the
  URL, schedule `webbrowser.open`, → `_run_serve` → the same thing
- `aegis` → spawns `python -m aegis serve --cwd <root> --autostarted`,
  then attaches to the socket

So `aegis web` serves nothing today. It is `aegis serve` with a browser
opened in front of it.

### Why the web cannot simply move out

`WebFrontend` is handed the **manager object**, in memory:

```
WebFrontend(manager, web_cfg, state_dir=…)
  └─ build_web_app(manager, …)
       └─ WSSession(transport, manager, …)
```

and the web layer calls eleven manager methods directly: `list_sessions`,
`get`, `spawn`, `close`, `interrupt`, `handoff`, `rename_handle`,
`set_title`, `register_queue`, `register_agent`, `list_agents`. It is not
a client of the brain. It is code living inside the brain, reaching into
it by attribute.

That is 1,098 lines of Python plus a 2,071-line browser client across 14
static files.

### Two protocols that do not speak the same language

The unix socket carries **Textual frames**: `b"D"` or `b"M"`, a 4-byte
length, a payload. It knows nothing of sessions or handles, and that
ignorance is the point. The stage-5a plan states it: the client parses no
aegis concepts, and the moment it needs its own encoding it has become
`RemoteSessionManager` again.

The web WebSocket carries **aegis concepts**: sessions, handles,
transcripts, the compact protocol, a token in the first frame.

Any design that moves the web out of the daemon has to say which of these
two the new seam speaks. That is the whole decision.

## The design

Three processes with three jobs, and only one of them opens a TCP port.

```
                    ┌───────────────────────────────┐
  terminal ────────►│  aegis server                 │
  (aegis, unix      │    brain: sessions, queues,   │
   socket)          │    schedules, hosts, MCP      │
                    │    ViewRegistry               │
  aegis web ───────►│    unix socket ONLY           │
  (unix socket)     └───────────────────────────────┘
        ▲
        │ HTTPS / WSS, token, many browsers
        │
    browsers
```

**`aegis server`** holds the brain and publishes a unix socket. It binds
no TCP port, ever. Renamed from `aegis serve`, because the name should say
what it is now that it is not the thing that serves the web.

**`aegis web`** is a separate process and the owner of the web
application. It binds the port, terminates TLS behind Caddy, authenticates
browsers, and holds their sessions. To the daemon it is one more client on
the unix socket.

**`aegis`** is unchanged: a terminal on the unix socket.

### What `aegis web` speaks to the daemon

**One view per browser client**, over the same `serve_view` a terminal
uses. A browser gets a `View` exactly as a tty does, and `aegis web`
relays that view's frames to an xterm.js in the page.

The alternative is to keep the web's aegis-level protocol and carry it
over the socket, which means teaching the socket `spawn`, `close`,
`interrupt` and the rest. That rebuilds the second protocol this whole
programme exists to delete, and it is rejected for that reason and not
because it is hard.

The consequence is that `aegis web` stops knowing what a session is. It
becomes a terminal multiplexer over HTTP: auth, one view per client,
frames in both directions. Those eleven manager calls have no successor
because they have no caller.

### What owns the token

`aegis web` does, and this is the clearest gain over the alternative.

The daemon has no port, so it has nothing to authenticate. The token
guards the only door that faces a network, and that door belongs to the
process whose entire job is facing the network. `serve_view` stays
unauthenticated because its transport is a unix socket, guarded by
filesystem permissions, which is what stage 5a already assumes.

### Deployment

Two units instead of one. Today `aegis-web.service` runs
`aegis serve` and Caddy reverse-proxies `127.0.0.1:8899` with
`basicauth` in front.

After: `aegis-server.service` runs `aegis server` with no port at all, and
`aegis-web.service` runs `aegis web`, which is what Caddy proxies. Caddy's
`basicauth` drops in favour of the token, as stage 5b already planned, and
the reasoning does not change: one secret, presented in a handshake,
rather than two.

The VPS gains something it does not have today. The brain survives a web
restart, because they are no longer the same process.

## What changes

| | work |
|---|---|
| Rename `serve` → `server`, drop its `web=` | mechanical: `_spawn_detached`, the systemd unit, docs, tests |
| `aegis web` becomes a socket client | new: an HTTP/WS server that opens a view per browser and relays frames |
| Browser terminal view | vendored xterm.js, the soft-keyboard shim; stage 5b was going to build this anyway |
| Token handshake | moves to `aegis web`'s front door instead of the daemon's |
| Delete the aegis-aware web layer | 1,098 lines of Python and 2,071 of JS, once the view path serves the browser |
| `.aegis.yaml` `web:` block | belongs to `aegis web` now, and stops being something the daemon reads |

`aegis.embed()` is untouched: it already builds `_serve` with neither
`views` nor `web`. The socket, its codec and `serve_view` are stage 5a's
and serve both designs unchanged.

## Compared with stage 5b as it stands

5b puts the WebSocket **in the daemon**, as a second transport under the
same `serve_view`, and the daemon keeps binding a port. This design puts
it in a separate process and the daemon binds nothing.

They agree on more than they differ: both serve the browser a terminal
view over `serve_view`, both delete the aegis-aware web layer in stage 6,
both replace Caddy's `basicauth` with one token in a handshake, and both
need the same vendored xterm.js.

| | 5b as written | this variant |
|---|---|---|
| Daemon binds a TCP port | yes | **no** |
| `aegis` in a terminal starts a web server | yes, if `web:` is configured | no, never |
| Processes on the VPS | 1 | 2 |
| What the token guards | the daemon's own handshake | `aegis web`'s front door |
| Between the internet and `permission: full` | the daemon | `aegis web`, then a unix socket |
| Hops for a browser keystroke | browser → daemon | browser → `aegis web` → daemon |
| Blast radius of a web restart | the brain goes too | the brain survives |
| `aegis attach wss://…` | the daemon's WS | `aegis web` proxies it, or it is dropped |

### What this variant buys

The failure of 2026-09-13 stops being possible rather than being guarded
against. A terminal client never opens a port, so two of them cannot
collide on one, and the pinned-port trap disappears with the block that
pinned it.

The process that faces the internet is not the process running
`permission: full` agents. Today they are the same, and the spec says so
plainly: after `basicauth` goes, aegis's own handshake is the only thing
between the open internet and full code execution on the VPS. Here a
compromise of the web process yields a unix socket, which is still
everything, but the surface being attacked is a relay that knows no aegis
concepts rather than the brain itself.

Restarting the web stops restarting the brain.

### What it costs

One more hop and one more process to supervise and to get right on boot
order. `aegis web` must handle the daemon being down or restarting, which
today is not a state that can exist.

`aegis attach wss://…` loses its obvious home. Either `aegis web` grows a
pass-through for terminal clients, or the remote-terminal case is served
by ssh plus the unix socket and the `wss://` form is dropped. That is a
real scope question this design has to answer and 5b does not.

It is scope beyond what was planned. 5b's plan does not exist yet, so
nothing is thrown away, but the estimate grows by a process boundary.

## Open questions

1. **Does `aegis attach wss://…` survive?** If yes, `aegis web` proxies
   raw view frames for terminal clients alongside its browser clients. If
   no, remote terminals go over ssh and the unix socket, which works today
   and needs no token at all.
2. **How does `aegis web` find its daemon?** The same `ensure_daemon`
   autostart a terminal uses, or a refusal when none is running. On the
   VPS, systemd ordering answers it; on a laptop it is a real choice.
3. **Does `aegis web` survive the daemon restarting?** Reconnect and
   repaint every view, or drop its browsers. The `repaint()` that stage 5a
   removed from `serve_view` comes back here, for exactly the case it was
   removed for not having yet.
4. **One view per browser, or one per tab?** View ids are client-minted
   and client-persisted, which is settled; what is not settled is whether
   two tabs of one browser share a view or get their own.

## Recommendation

Take this variant, and decide it now rather than after 5b.

The reason is not the failure it prevents, though that is real. It is that
5b is the stage that chooses where the WebSocket lives and where the token
is checked, and both choices are expensive to move afterwards. Picking the
shape first costs a spec. Picking it second costs the transport.

The one thing to settle before writing a plan is open question 1, because
the answer decides whether `aegis web` is a browser server or a general
relay, and that is a different program.
