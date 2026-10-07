# aegis 2: quota and host gauges

**Status: implemented, 2026-10-07** (issue #146), following
`docs/superpowers/plans/2026-10-07-aegis-2-quota-gauges.md`. Designed with Alex, who approved
the visual design from an HTML mockup that uses the real client CSS and themes.
The mockup is in `.playground/aegis-quota-mockup/` in the Workspace (untracked).

## What this delivers

The gauges the legacy tree had, back in aegis 2:

- **A band at the top of the Fleet view** with three columns. The first counts
  this server's sessions by state. The second holds host gauges: CPU, RAM, disk
  and average context. The third holds one quota gauge per window of every
  provider aegis holds credentials for.
- **A Quota section in the session sidebar**, under Context. It shows Claude's
  5-hour and weekly windows, because Claude is the only harness aegis 2 runs.
- **`quota.read`**, an operation agents get as `mcp__aegis__quota_read`, so an
  agent can check quota before it fans out workers.

Since the 2.0 switch nothing polls quota. `src/aegis/` has no quota code, and
`~/.cache/aegis/quota/claude.json` was last written by the legacy daemon on
2026-10-06 at 15:36 CDT.

Out of scope: cost analytics (`/usage` in the legacy tree, and #134), a quota
indicator in the top bar, and moving this into a plugin. The module is shaped
so it can move when the plugin runtime from #113 exists.

## Decisions

| Question | Decision | Why |
|---|---|---|
| Core module or plugin | A core module, like `monitors.py`, with its own channels | aegis 2 has no plugin runtime yet. The client knows only channel names, so a later move into a plugin changes no protocol |
| Where the numbers come from | A copy of the legacy `QuotaService` and its two providers, adapted, never imported | DESIGN.md: copy, never import from `legacy/`. The legacy code has already been through #41 (429s, stale readings, placeholders) |
| One reading per machine | Keep the legacy cache path and format, `~/.cache/aegis/quota/<provider>.json`, now honouring `XDG_CACHE_HOME`; `AEGIS_QUOTA_CACHE` overrides the directory | The Claude endpoint 429s under light polling. Every aegis process on the machine adopts a reading another fetched inside the floor and honours a backoff another saw |
| Who decides severity and projection | The server, re-evaluated once a minute | DESIGN.md: Python decides, the browser draws. Both depend on the clock, so the server republishes when either changes |
| Who computes the tick and the countdown | The browser, from `starts_at` and `resets_at` | Pure geometry and clock text, as `ago()` already is for the cards. Republishing every second for a countdown would cost more than the gauges |
| Session counts and average context | The browser, from the `sessions` channel it already holds | No new server data. A count of states the client already has is drawing, not deciding |
| Host sampling | `/proc/stat`, `/proc/meminfo` and `os.statvfs`, stdlib only, every 5 s while anyone is subscribed | No psutil dependency. Nobody watching means nothing sampled |
| Agents forcing a fetch | Never. `quota.read` returns the cached snapshot | An agent polling the endpoint would starve the gauges into 429s |
| Which providers the sidebar shows | Claude only | The sidebar is about the session in view, and every aegis 2 session is Claude Code. The band shows every provider |

## The quota service

### Modules

- `src/aegis/quota/core.py`: `QuotaWindow`, `QuotaSnapshot`, `QuotaProvider`,
  `QuotaError`, `QuotaService`, `window_pace`, `pace_severity`, and the shared
  cache. Copied from `legacy/aegis/usage/quota.py` without the Rich renderers
  (`format_quota_bar`, `quota_lines`, `quota_report`, `_paint`).
- `src/aegis/quota/claude.py` and `src/aegis/quota/opencode.py`: copied from
  `quota_claude.py` and `quota_opencode.py`.
- `src/aegis/quota/__init__.py`: `Quota`, the object `App` owns. It holds one
  `QuotaService` per provider and runs one loop that ticks every 60 s. Each tick
  asks every service to refresh (each service's floor decides whether it really
  fetches), rebuilds the wire snapshot, and publishes it if it changed. So one
  loop carries both the fetch cadence and the minute re-evaluation.

`quota` is a package because it holds three modules. `host.py` is one module.

### Rules carried over unchanged

- **Cadence.** Claude polls every 180 s, and a turn end may refresh it no sooner
  than 60 s after the last fetch. OpenCode Go polls every 60 s with a 10 s turn
  floor. A 429 backs off for 300 s, and the backoff is shared through the cache.
- **Severity** is the worse of level and pace. Level is the API's own
  `severity`, or 80% and 95% when the API gives none. Pace is the projected
  spend at reset, `percent / max(elapsed_fraction, 0.15)`: 80% or more is a
  warning, above 100% is critical. A reset time further out than the window's
  span (plus 60 s of skew) gives no projection, and the window keeps its level
  colour.
- **Spans.** Claude: `session` 5 h; `weekly_all`, `weekly_opus` and
  `weekly_scoped` 7 days. OpenCode Go: `rolling` 5 h, `weekly` 7 days, `monthly`
  30 days.
- **Which windows draw.** Claude: `session` and `weekly_all`. OpenCode Go:
  `rolling`, `weekly` and `monthly`. Labels are "5 hours", "week" and "month".
- **A stale reading** (the last good one while fetches fail) keeps its bar, gets
  no severity and no projection, and carries its age.
- **A provider with credentials and no reading** is a row with a note and no
  bar, because an empty bar would claim 0%.
- **A provider without credentials** does not appear.

### Fixed in the copy

- #72: `QuotaService.stop` swallows a `CancelledError` meant for its caller. The
  copy re-raises it when the caller is being cancelled.
- The turn-end refresh is routed by harness in the legacy tree. aegis 2 runs only
  Claude Code, so `Registry.turn_ended` nudges the Claude provider.

### The `quota` channel

The snapshot:

```json
{
  "providers": [
    {
      "name": "claude",
      "label": "Claude",
      "state": "ok",
      "note": "",
      "read_at": 1791365117.3,
      "windows": [
        {
          "kind": "session",
          "label": "5 hours",
          "percent": 58.0,
          "severity": "normal",
          "projected": 76.3,
          "starts_at": 1791357917.0,
          "resets_at": 1791375917.0
        }
      ]
    }
  ]
}
```

- `state` is `ok`, `stale` or `failed`. `failed` has a `note`
  ("rate limited", "auth expired", "unreachable") and no windows. `stale` has
  windows, its `note`, `severity: "normal"` and `projected: null` on every
  window.
- `starts_at` is `resets_at` minus the window's span, or `null` when the
  projection is declined. The browser draws no tick for a `null`.
- Times are wall-clock epoch seconds.
- A patch is one op, `{"set": <snapshot>}`. The whole snapshot is a few hundred
  bytes, so diffing it would cost more code than it saves.
- `Quota` publishes when the wire snapshot differs from the last one it
  published. `projected` is rounded to a whole percent and `read_at` to a whole
  second, so the minute re-evaluation publishes at most once a minute, and only
  while a window's projection is moving.
- A provider whose 429 backoff is running carries `retry_at` (epoch seconds),
  so the Quota heading can say "rate limited, retrying in 4m". Otherwise
  `retry_at` is `null`.

`quota.read` (agent operation, no params) returns the same snapshot.

## The host sampler

`src/aegis/host.py`, class `HostSampler` (`session.py` already has a `Host`):

- **CPU** is the busy fraction between two reads of the `cpu` line of
  `/proc/stat`, 5 s apart.
- **RAM** is `MemTotal - MemAvailable` over `MemTotal` from `/proc/meminfo`, with
  used and total in GB.
- **Disk** is `os.statvfs(config_root)`, used and total in GB, for the
  filesystem that holds aegis's state.
- It samples only while `channels.subscribers("host") > 0`, and publishes
  `{"set": …}` only when a whole-percent value changes.
- On a system without `/proc` the snapshot is `null`, and the browser hides the
  Host column.

The snapshot: `{"cpu": 23, "ram": {"pct": 34, "used_gb": 10.4, "total_gb": 30.0},
"disk": {"pct": 71, "used_gb": 62.0, "total_gb": 91.0}}`.

Host severity is level only: warning at 80%, critical at 95%. The browser
computes it, because the thresholds are a drawing rule for three numbers and no
pace is involved.

## The client

- **`js/gauges.js`** exports `gauge(label, percent, severity, opts)`, which
  returns one row Node: label, bar, value. Its options are a tick position, a
  projection, a countdown, a tail text, and stale. It also exports
  `noteRow(note)`. The band and the sidebar both use it.
- **`js/app.js`** holds one `quota` subscription for the life of the page,
  because both views draw from it, and renders the sidebar's Quota section.
- **`js/fleet.js`** renders the band above the cards from that quota snapshot
  and from a `host` subscription it holds only while the Fleet view is shown.
- **`css/base.css`** gets the rules from the mockup: `.band`, `.gauge`,
  `.bar.q` and `.bar.h` with `.warning` and `.critical`, `.tick`, `.stale`, and
  `.side .qrow`. It reads only theme variables.

### Visual rules

- Quota bars are `--ok` when normal, `--warn` at warning, `--err` at critical.
  Host bars are `--muted` when normal. Quota bars are not `--fill`, because in
  Ink `--fill` and `--warn` are the same amber.
- The pace tick is a 2 px mark at the elapsed fraction of the window. Fill past
  the tick means spending faster than the window refills.
- The projection (`→ 187%`) shows only at 80% or more.
- The band always shows the countdown (`↻ 3h 06m`). The sidebar puts it on a dim
  line with the reset time (`resets in 3h 06m`, `08:36`).
- A stale reading is drawn in `--faint`, without a tick or a projection, and its
  age goes in the provider line ("Claude · 14m old"). The Quota heading carries
  the reading's age, or "rate limited, retrying in 4m".
- Hovering a gauge shows the whole reading as a sentence: "Claude, 5-hour
  window: 71% used, 38% of the window gone, on pace for 187% at reset. Resets
  08:36, in 3h 06m."
- Below 1100 px the band wraps the Quota column onto its own row.

## Testing

- **Unit:** pace, severity, the parsers, cache adoption and the shared backoff,
  copied from `legacy/tests/test_quota*.py`. New tests: the wire snapshot for
  `ok`, `stale` and `failed`; `null` `starts_at` when the projection is
  declined; publish only on change; the #72 fix; and `Host` parsing of fixture
  `/proc` text.
- **Never the real endpoint.** `conftest.py` points `CLAUDE_CREDS` and
  `OPENCODE_AUTH` at missing files and `AEGIS_QUOTA_CACHE` at a temporary
  directory for every test. Not `XDG_CACHE_HOME`: Playwright looks for its
  Chromium under it. A test that wants a reading writes a token file and a fresh
  cache file. The service adopts the cached reading inside the floor and does
  not fetch.
- **Browser:** against a real `aegis serve` with a seeded cache, the Fleet band
  shows the Claude rows with their severity class, a stale provider shows grey,
  and the session sidebar shows the Quota section. To prove the test can fail,
  break the severity mapping on purpose, confirm the test goes red, and restore
  it.
- **Agents:** `quota.read` through `/mcp` returns the snapshot. A test checks
  that calling it does not fetch.

## Delivery

1. Issue #146 (filed).
2. Worktree `feat/quota-gauges` from `origin/main`.
3. This spec.
4. The plan, `docs/superpowers/plans/2026-10-07-aegis-2-quota-gauges.md`.
5. Implement. Open a PR with the `make bench` table, a `changelog.d/` fragment,
   and DESIGN.md updated if a cross-module rule changes.
6. Smoke test with `bin/aegis-dev` at the PR's commit, in its own window beside
   the released server:
   `AEGIS_REF=feat/quota-gauges bin/aegis-dev serve --window --port 8790 --root <scratch>`,
   where the scratch root holds a copy of the Workspace's `.aegis.yaml`. Quota is
   an account property read from `~/.cache`, so the gauges show real readings
   from any root. Alex smoke-tests in that window before the merge.
