# Review: *One boot path: retiring the web UI, and aegis as a library*

*Design review — 2026-09-08. Target:
`docs/superpowers/specs/2026-09-07-retire-web-ui-tui-over-web-design.md` (at
`5da76c3`). Reviewed against the tree at that commit, textual 8.2.6.*

The case for the change is sound and unusually well-evidenced: line references
that check out, a spike that ran, an honest "what we lose", and a sequencing
that puts the irreversible step last. Three claims I re-verified independently
are correct — the `/ws` 403 diagnosis, the two unfalsifiable tests, and the
self-containment of `src/aegis/web/`.

Two things in it are wrong, and both are fixable before planning. **The
reattach mechanism as described cannot work**, and **the cwd coupling is
roughly eight times larger than the spec states, with the sharpest instances
somewhere the spec never looks.** Everything else below is a gap rather than a
defect.

---

## Blocking

### 1. Ring-buffer replay cannot reconstruct a screen

The spec's one genuinely new component is detached lifecycle, and it specifies
it as:

> maintain a bounded ring buffer of recent output packets … on (re)connect,
> replay the buffer so the client repaints, then stream live.

That does not repaint. Textual's web driver writes **incremental,
cursor-addressed** updates — `WebDriver.write(data: str)` is fed from the
compositor's diff, not from a full frame. A bounded ring buffer holds a
*suffix* of that stream. Replaying a suffix into a fresh xterm.js produces a
screen with holes: every cell whose last paint fell outside the window is
simply absent, and there is no way to tell from inside the buffer which those
are.

tmux reattaches correctly precisely **because** it runs a terminal emulator and
holds screen state. The spec rejects emulation explicitly — *"No pty. No
terminal emulation on our side"* — while promising tmux semantics. Those two
cannot both hold.

The obvious repair also fails silently. `WebDriver.on_meta` accepts a `resize`
packet (`textual/drivers/web_driver.py:238-241`) which posts `events.Resize`,
and a resize does force a full re-render — but only if the size *changed*:

```python
# textual/app.py, App._on_resize
if self._size == event.size:
    return
```

Sending the current geometry on reattach is a no-op. To force a repaint you
must send a *different* size and then the real one, which means a visible
reflow and a full re-layout of a heavy TUI on every reconnect — on the flaky
mobile link this design is aimed at.

Two honest options, and the spec should pick one:

- **(a) Size-toggle repaint.** Cheap, ~10 lines, but it makes the ring buffer
  decoration rather than mechanism — say so, and delete the buffer from the
  design (or keep it only to cover the sub-second gap, which is a different and
  much weaker claim than "so the client repaints").
- **(b) Emulate.** Run `pyte` (or equivalent) in the supervisor, keep a screen
  model, dump it on attach. This is correct and it *is* terminal emulation —
  and it takes the ~300-line budget with it.

Note what the 2026-09-07 spike actually covered: rendering, `Ctrl+T`
round-trip, 393 px reflow. It did not cover reattach. The one piece with no
spike is the one piece that is new.

### 2. The cwd coupling is 66 sites in 21 files, not 8 in `cli.py`

The spec lists eight `Path.cwd()` calls in `_serve` and calls stage 1 a *"pure
refactor, no behaviour change."* Measured across the tree:

```
grep -rn "Path.cwd()\|os.getcwd()" src/   →  66 hits, 21 files
grep -rn "find_project_root" src/         →  61 call sites
                                             26 of them in mcp/server.py
```

`find_project_root()` is itself cwd-rooted (`config/__init__.py:141`,
`config/yaml_loader.py:408`: `cur = (start or Path.cwd()).resolve()`), so those
61 sites are cwd sites too.

**The 26 in `mcp/server.py` are the serious ones**, and they are exactly the
sindri case. They sit inside the config MCP tools — `aegis_config_add_agent`,
`add_queue`, `remove_agent`, `config_show`, the `schedule_*` family. Each
resolves the project root *at call time* from the process cwd. With N aegis
instances embedded in one sindri process, an agent working in worktree B that
calls `aegis_config_add_agent` edits whichever `.aegis.yaml` the **process**
cwd walks up to — not instance B's. This is the same footgun already known
from the Workspace checkout, promoted from annoyance to correctness bug the
moment two instances share a process.

Others outside `cli.py` that the list misses, each structural rather than
cosmetic:

| Site | What it roots |
|---|---|
| `core/session.py:85,91` | `project_root or Path.cwd()` — the **harness subprocess's** working directory, i.e. where `claude` actually runs |
| `core/manager.py:97,117,167` | `root_fn=lambda: self.state_root or Path.cwd()` — a *lazy* closure, resolved per call, not at boot |
| `terminal/manager.py:156` | `cwd or os.getcwd()` — every shared terminal |
| `workflow/engine.py:199` | workflow execution root |
| `usage/env.py:14` | usage/budget state |
| `tui/app.py` | ×9 |

Two consequences for the spec:

**Stage 1 is not a pure refactor.** `root_fn` closures and `x or Path.cwd()`
defaults are late-bound process globals; threading a root converts them to
early-bound per-instance values. That is a semantic change, and every one needs
a decision about *which* root it means.

**There is not one root — there are at least four, and the code already knows
it.** `_serve` takes `local_root`; `SessionManager` takes both `state_root` and
`local_root`; `HostRegistry` takes `state_dir` plus `local_root`; and
`find_project_root()` is a fourth, implicit one. `cli.py:196` already
distinguishes `root` from `effective_cwd`. Collapsing all of that to "a
threaded `root`" hides the actual work. The spec should name the roots — config
root, state root, harness cwd — and say which is threaded where.

---

## Serious gaps

### 3. A capability loss that isn't in "What we lose": independent per-client views

Today each WebSocket connection owns its own subscription set
(`web/subscriptions.py` — per-sink `_globals`, `_handles`, `_queue_subs`). A
phone can watch agent A while the laptop watches agent B; each browser tab is
an independent view.

After: one screen, mirrored to every client. The spec names the shared cursor
and the min-geometry negotiation, but not that **two people can no longer look
at two different agents** — which is the normal case on a machine running a
fleet, and the VPS is exactly that machine. Also unlisted and in the same
family: no per-session deep link, no browser back/forward, no useful second
tab.

This belongs in "What we lose" as a decision, which is what that section is
for.

### 4. Afterwards there is no terminal access to the VPS brain at all

`--remote` dies, correctly — it is broken. But the replacement is browser-only.
`ssh vps && aegis` boots a *second* brain against the same root; it does not
attach to the running one. So the supervisor's subprocess becomes reachable
exclusively through browser → Caddy → basicauth → token.

That is a regression in the failure mode that matters most: if Caddy, the
token, or the supervisor is wedged, there is no path to the live fleet.

Cheap mitigation worth specifying, since it reuses the machinery finding 1
forces you to build: have the supervisor also expose a **local unix-socket
attach**, so `ssh vps -t aegis attach` bridges a real terminal onto the same
stream. That is genuinely tmux-shaped, and it is the same buffer/repaint path.

### 5. "Detached lifecycle" is detached from sockets, not from the supervisor

The spec promises survival across *disconnects*. It is silent on
`systemctl restart aegis-web`, on a supervisor crash, and on a supervisor
deploy. As written the subprocess is an ordinary child, so all three kill the
fleet — under a heading that invokes tmux, where the server outlives every
client *and* survives the client binary being replaced.

Either say plainly "restarting the unit restarts the brain, as today" — which
is fine, but then stop calling it tmux — or specify re-adoption (pidfile,
orphan reattach on supervisor start), which is a materially bigger supervisor.

### 6. The ~300-line estimate contradicts the spec's own framing

`textual-serve` is 1,224 lines and, by the spec's own account, does **less**:
no auth, no detached lifecycle, no replay. The supervisor is budgeted at ~300
while doing strictly more, plus everything not counted:

- the HTML page and xterm.js wiring;
- resize negotiation across N clients (the min-geometry rule);
- a **soft-keyboard shim** for mobile — the thing that makes a TUI usable on a
  phone at all, and the piece the headless-Chrome spike could not test;
- the `deliver_chunk_request` file-download path
  (`web_driver.py:250-265`), which today is a real route with a real test
  (`web/server.py:143`, `tests/test_web_download.py`);
- reconnect and backoff.

Unbudgeted too: vendoring xterm.js (~250 KB) into a repo whose static dir is
being deleted to save 2,124 lines.

The ledger currently reads 3,570-deleted for 300-added. Realistically it is
3,570 for 600–900 plus a vendored dependency. **This does not weaken the
case** — deleting a parallel implementation is worth doing at 1:1 — but the
number is what carries the argument, so it should be one that survives
contact.

### 7. Stages 2 and 4 contradict each other

The command-surface table has `aegis serve` losing the web frontend. Stage 4
says *"Both UIs still exist at this point, so it can be exercised against the
VPS before anything is deleted."*

Those reconcile only if `_serve` keeps its `web=` frontend wired through stages
2–4 and drops it in stage 5. The spec never says so — and `web=None` is
literally a parameter of `_serve` today (`cli.py:376`), so it is a live
question, not a pedantic one. As written, stage 2 can dark-land a VPS with no
UI.

Relatedly, *"every step is independently revertible"* is not true of stage 1.
Once 2–4 build on the threaded root, reverting 1 means reverting all of them.
Stage 1 is revertible only while it stands alone.

---

## Accuracy and smaller notes

**8. The static/ inventory undercounts.** Listed files sum to 2,071; the tree
is 2,124 — `manifest.webmanifest` (17) and `icons/` (36) are missing. Those two
*are* the PWA, so they belong in a ledger that lists the PWA as a loss.

**9. `frontend.py`: "keep port resolution, drop the rest."** `_resolve_port` is
imported at `cli.py:624` by the `web` command, which is being rewritten
wholesale. Say whether it moves into the supervisor and `frontend.py` dies
entirely. A 53-line module kept alive for one function is the residue this spec
is otherwise good at removing.

**10. The "20 `typer.Exit` calls" claim is true of the file, not the boot
path.** `grep -c` gives 20, spread across `token`, `web`, `--remote` and the
config paths. The library-relevant subset is much smaller and should be named —
"convert 20 exits to `ConfigError`" is a different task from "the three on the
config-load path."

**11. The multi-instance test asserts one step away from the thing.** "State
dirs, locks and canvases are disjoint" checks *paths*. Stronger, and it fails
today for a second and more interesting reason: boot two instances, spawn an
agent in each, have agent A call `aegis_config_add_agent`, and assert instance
B's `.aegis.yaml` is untouched. That gate catches the 26 `find_project_root()`
sites; the path-disjointness one does not.

**12. "Open questions: none blocking" is not right.** At least four:

- What forces the repaint on reattach (finding 1) — this one *is* blocking.
- What geometry does the subprocess render at with **zero** clients attached?
  Textual needs a size, and there is no viewport to take a minimum over.
- What happens when the subprocess exits — crash, `/quit` from the browser,
  `Ctrl+C`? Respawn? A restart affordance in the browser? Today `aegis serve`
  dying is a systemd concern; now there are two processes and systemd supervises
  only one of them.
- Buffer size is listed, but it is the wrong question if the buffer is not the
  reattach mechanism.

---

## Claims I checked that hold

Worth recording, so planning doesn't re-litigate them:

- **The `/ws` 403 diagnosis is right.** `web/server.py:148` is
  `WebSocketRoute("/ws", ws_endpoint)`; `/` is a plain `Route`. A WS upgrade to
  `/` cannot succeed.
- **The unfalsifiable-test critique is right.**
  `tests/tui/test_ws_client_reconnect.py` stands up bare `websockets.serve`
  handlers that accept any path; `tests/cli/test_remote_flag.py` fakes the
  client outright (`_FakeWsClient`, `_FakeManager`). Neither can fail on a
  wrong URL shape.
- **The deletion is clean.** Nothing outside `src/aegis/web/` and its tests
  imports `web.*`, apart from `cli.py:480,624` importing `WebFrontend` /
  `_resolve_port`. 18 `tests/test_web_*.py` files, all confined.
- **Multi-instance MCP is *not* a blocker** — one fewer than you would expect.
  There are no module-level mutable singletons in `mcp/server.py`, and
  `AegisMCP` already allocates its own free loopback port per instance
  (`mcp/runtime.py:12,46`). Ports are fine; it is the cwd that isn't.
- **The PWA loss is smaller than the spec implies in one respect**: there is no
  push-notification code in `service-worker.js`, so what is lost is the offline
  shell and installability, not notifications.

---

## Recommendation

Do not plan against this spec as written. Three edits first, all small:

1. **Replace the reattach section** with whichever of (a) size-toggle repaint
   or (b) server-side screen model you pick, and re-budget the supervisor
   accordingly. Spike the chosen one — it is the only unspiked piece.
2. **Rewrite stage 1** against the real number (66 sites / 21 files / 26 in the
   MCP tools), name the distinct roots, and drop "pure refactor."
3. **Fix the stage 2/4 contradiction** by stating that `web=` stays wired
   through stage 4, and add the losses in findings 3 and 4 to "What we lose."

Findings 6, 8, 9, 10, 12 are corrections to the ledger and the open-questions
list; they can be folded in during planning without another review pass.
