# A panel that knows the disk is 71% full should know the network is dead

**Status:** designed 2026-09-27, approved in conversation, **no code yet**.
Issues [#13](https://github.com/apiad/aegis/issues/13) (the network block) and
[#12](https://github.com/apiad/aegis/issues/12) (the tok/s regression). Both
land in `src/aegis/tui/sidebar.py`, so they ship in one cycle rather than two.
The plan is `docs/superpowers/plans/2026-09-27-f3-network-and-tps.md` (6 tasks, 59 steps).

F3's SYSTEM block samples CPU, RAM and disk every second and says nothing about
the network. The two questions it cannot answer are the two that decide what an
agent should do next: are packets leaving this machine, and is the link fast
enough to build here rather than shipping the work to the VPS.

Both were measured on zion while this design was being written, and both
readings were surprising, which is the argument for the feature:

```
$ curl -s https://www.cloudflare.com/cdn-cgi/trace
ip=2a0d:5600:6:202::15    colo=MIA    loc=US

$ curl -s -o /dev/null -w '%{size_download} %{time_total} %{speed_download}\n' \
    'https://speed.cloudflare.com/__down?bytes=1000000'
1000000  7.157571  139712
```

Egress is IPv6 through a tunnel landing in Miami, and the uplink is 139 KB/s —
a factor of forty under the 5 MB/s line at which work stays local instead of
going to the VPS. Neither fact had a surface anywhere in aegis.

## Why not ICMP

The obvious probe is `ping`, and it is the wrong one twice over. It needs
`CAP_NET_RAW` or a subprocess for every sample, and a TUI that forks `/bin/ping`
every twenty seconds is worse than one that holds a socket. More importantly a
captive portal — the failure this block exists to catch — answers ICMP and
refuses the connection. A completed TCP handshake on port 443 tests the thing
that actually breaks.

## Three facts, three cadences

The three readings differ by a factor of three thousand in cost, so they cannot
share a cadence. Each gets the one its cost and its volatility justify.

| fact | cadence | cost per sample |
|---|---|---|
| egress alive + RTT | 20s | one TCP handshake, no payload |
| exit IP + colo | 300s, **plus forced on any down→up transition** | ~300 B |
| throughput | `speed_interval`, default `0` = off | `speed_bytes`, default 1 MB |

The forced re-trace is the part that earns its complexity. An exit IP changes
almost never, except at exactly one moment: when egress has just come back,
because that is when you joined a different network. Polling for it is waste;
sampling it on the transition is free and catches every case that matters.

A 1 MB fetch occupies a 139 KB/s link for seven seconds. That is why throughput
cannot be ambient by default, and it is also why the figure is never rendered
without its age — a throughput number stops being true the moment the network
changes, and an undated one invites a decision based on a reading from another
building.

## `aegis/net/probe.py` — pure functions, no state

Three coroutines, each returning a frozen dataclass. None of them raises for a
network condition; a failure is a value.

- `reach(anchors, timeout) -> Reach` — `asyncio.open_connection` against each
  anchor in order, returning the first success and its handshake RTT in
  milliseconds. `Reach(ok=False, error="timeout")` when every anchor fails.
  Defaults `1.1.1.1:443` and `8.8.8.8:443`: two operators, so one provider
  being down does not read as "no egress".
- `trace(url, timeout) -> Trace` — httpx GET of `cdn-cgi/trace`, parsing `ip=`,
  `colo=` and `loc=` out of its `key=value` lines. `cdn-cgi/trace` rather than
  `ifconfig.me` because one ~300-byte response carries three facts where
  `ifconfig.me` carries one. `ifconfig.me/ip` stays as the fallback, used when
  `reach` succeeded but the trace host did not answer; it yields an address and
  no colo, and the renderer simply has no colo to draw.
- `throughput(url, nbytes, timeout) -> Throughput` — a timed httpx stream,
  returning bytes per second **and the byte count actually received**. Both,
  deliberately: a connection that drops at 200 KB of a 1 MB request computes a
  perfectly plausible rate, and only the short count reveals it. The renderer
  refuses a reading whose count falls short of what was asked for.

`httpx>=0.28` is already a dependency (`pyproject.toml:33`). This adds none.

## `aegis/net/service.py` — the sampler

`NetService`, shaped like `QuotaService` (`src/aegis/usage/quota.py:188`):
`start()`, `stop()`, a `state` property and `refresh(force_speed=False)`, over
one asyncio task. `NetState` is frozen and holds the last `Reach`, `Trace` and
`Throughput`, each with its own timestamp, so the renderer can age any of them
independently.

Two rules the service owes the UI:

- **Nothing propagates into the tick.** The 1s `_tick` in `tui/app.py:1650`
  already wraps its psutil sample in `contextlib.suppress(Exception)`, which
  hides a broken sampler rather than reporting it. `NetService` does not rely
  on that: every probe failure becomes a state with an `error` string, and the
  sidebar renders the error.
- **A failed reach does not erase what is known.** Losing egress tells you
  nothing about what the exit IP was, and blanking it costs the one row that
  helps you work out which network you fell onto. The IP persists with its age;
  only the reach row goes red.

## The rows

Inside SYSTEM rather than in a section of their own — the panel already answers
"what is this machine doing", and egress belongs with CPU and disk. Placed
below the meters and above the clock, which keeps `SECTIONS`' volatility
ordering: RTT moves every 20 seconds, the clock every minute, `cwd` and `build`
never.

```
── SYSTEM ──────────────────────────
CPU ▇▇▁▁▁▁  12%   RAM ▇▇▇▁▁  38%
DSK ▇▇▇▇▇▇▇▇▁▁  71%
NET  ✓ 18ms · MIA · ↓ 1.1 Mbps 4m
     2a0d:5600:6:202::15
17:42 · Sat 27 Sep
~/Workspace/repos/aegis
aegis 0.39.0 (c40f736)
```

Two `Segment`s handed to `fit_rows`, which is the sidebar's composer rather
than `fit`: rows do not share horizontal space, so **`priority` is never
consulted** (`tui/fit.py:110-113`) and each segment independently takes the
widest tier that fits. Both keep `priority=0`, like every other segment in this
file. The tiers are where the decision lives:

| segment | tiers, widest first |
|---|---|
| `net` | `✓ 18ms · MIA · ↓ 1.1 Mbps 4m` → `✓ 18ms · ↓ 1.1 Mbps` → `✓ 18ms` |
| `exit_ip` | `2a0d:5600:6:202::15` |

The reach state appears in every tier, because it is the one reading with no
alternative surface. Colo drops first: interesting rather than actionable.
Throughput drops second, since `/net` reprints it on demand.

The IP gets a **single** tier deliberately. `fit_rows` drops a segment whose
narrowest tier still overflows rather than truncating it (`fit.py:115-116`), so a
column too narrow for the address loses the row whole — which is what we want,
because `2a0d:5600:6:2…` reads as a different address rather than a clipped one.
That is the same failure `gauge` already hit when it truncated a RAM tail to
`9.8/1`, a plausible wrong ratio (`sidebar.py:463-465`). Giving the IP a second,
shorter tier would recreate it.

Four states for the reach segment:

| render | meaning |
|---|---|
| `✓ 18ms` | egress alive, healthy path |
| `✓ 340ms` | alive, amber past 200ms |
| `✗ no egress` | every anchor failed — red |
| `· ⋯` | not sampled yet |

`· ⋯` rather than `0ms` for the first twenty seconds. A zero claims a
measurement; the ellipsis admits there isn't one. This is the same rule
`_context()` already follows for its gauges — "no reading falls back to the
tier, never to a 0% bar", `sidebar.py:321-322`.

## `/net`

`src/aegis/commands/builtins/net.py`, registered like `builtins/usage.py`.
Forces a full sample including throughput regardless of `speed_interval`, and
prints a transcript block: the exit IP, the colo, RTT per anchor rather than
just the winner, and the throughput **with the byte count and elapsed time it
was computed from**. Showing the inputs is what makes the number arguable
instead of oracular.

It reads the app's service through `ctx.bridge` with the module-level fallback
`/usage` uses (`builtins/usage.py:35-50`), so it works under `aegis serve` with
no TUI in the process.

## Config

```yaml
network:
  enabled: true          # false = no probes, no rows
  interval: 20
  trace_interval: 300
  speed_interval: 0      # 0 = off, the opt-in timer
  speed_bytes: 1000000
  anchors: ["1.1.1.1:443", "8.8.8.8:443"]
```

`NetworkConfig`, a frozen dataclass beside `VoiceConfig`
(`src/aegis/config/__init__.py:22`), built by `_build_network` mirroring
`_build_voice` (`config/yaml_loader.py:353`), and documented in a `## Network`
section of `docs/configuration.md`. That section is not optional: `.rift.yaml`
carries a rule that every config section is documented, and shipping the block
without it turns a green linter red.

`enabled: true` with `speed_interval: 0` is the deliberate default. Latency and
the exit IP cost 300 bytes every five minutes, which no network notices and no
administrator sees. The 1 MB fetch is a repeating download from a speed-test
host, which on a restricted network is the shape that gets noticed, so it ships
off and you turn it on where you own the link.

## Putting tok/s back

Independent of everything above, and the reason both issues share a cycle:
`_context()` discards five figures whenever the sidebar draws its own CTX bar.

`MetricsModel.render_tiers()` puts the generation speed only in T0
(`tui/metrics.py:344,351`). `_context()` selects `m.metrics[-1:]` — T3 — the
moment `m.ctx` is set, which is every Claude session (`sidebar.py:315-319`). T0
minus T3 is `⚡ N tok/s`, the cached share, the reasoning share, the tool count
with its error count, and the compaction counter.

The comment above that line justifies the choice as "the gauge takes the
fraction; the leftover tier takes the rest". That is sound for the context
percentage, which the gauge does replace. It was never true of the other five,
none of which the CTX bar draws. They were collateral.

The fix reads scalars off `MetricsModel` instead of scavenging a rendered
string, the way `SidebarModel` already carries `ctx: ContextGauge` beside the
`metrics` tuple it superseded. `recent_tps()` (`metrics.py:231`) is correct and
does not change. The tier tuple stays, because a remote pane is handed rendered
strings and no numbers, and it remains the fallback for exactly that case.

## Testing

The thing this design can most easily get wrong is a check that passes because
the network happened to work, so no test in this set touches the internet.

- `reach` against a local `asyncio.start_server` — a real socket and a real
  RTT, plus a closed port for the failure path and an unroutable address for
  the timeout path.
- `trace` and `throughput` on `pytest_httpx` (already in the `dev` group,
  `pyproject.toml:120`). A truncated-body case asserts `throughput` reports the
  short count rather than the flattering rate, and that the renderer refuses it.
- `NetService` with injected probe callables and a fake clock: the down→up force
  fires exactly once per transition, a failed reach leaves the last known IP in
  place, and the speed probe is never called at `speed_interval: 0`.
- `render_sidebar` at 36, 56 and 80 cells, asserting the narrow column drops the
  IP row and keeps the reach row, and that `· ⋯` appears before the first sample
  where `0ms` must not.

Two disciplines from the workspace's own record apply directly. **No test
asserts a literal the code owns** — anchors, thresholds and interval defaults
are bound from `NetworkConfig`, because a literal written on both sides of an
assertion restates the author's model and passes for no reason. And **the
red-on-no-egress assertion gets broken on purpose** before it is trusted, with
`cmp` confirming the mutation actually landed in the file, since a mutation that
does not mutate produces the same green as a check that cannot fail.

## Out of scope

No RTT history and no sparkline: the question is "is it working now", and a
trend line is a different feature with a different storage cost. No
per-interface breakdown. One exit IP, whichever the stack chose, because that is
the one the traffic uses — reporting both stacks doubles the rows to answer a
question nobody asked. No proxy or VPN detection beyond what the colo row
already implies by naming a city.

## One interaction worth recording

The exit IP renders on a surface `webterm` serves, and stage 6's one remaining
task drops Caddy's basic auth from `dev.apiad.net`
(`docs/superpowers/plans/2026-09-27-aegis-stage-6-delete-the-old-web.md`, Task
6 Step 5). Not an argument against the feature, and not a blocker — but the two
land on the same page, and `enabled: false` in the VPS config is the escape
hatch if that host should not advertise its egress.
