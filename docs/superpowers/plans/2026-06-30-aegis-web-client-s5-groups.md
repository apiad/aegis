# Aegis Web Client — S5 (Group Dashboard) Implementation Plan

> superpowers:executing-plans (inline). Builds on S4.

**Goal:** A group dashboard modal (Alt+G) showing Members / Current broadcast per group.

**Architecture:** Unlike queues, the groups substrate has no event subscription, so a live stream would need registry mutation hooks. Groups are rare and the dashboard is on-demand, so S5 uses a **poll-on-open `group_status` RPC** (the modal fetches on open + refreshes on a 2s timer while open) rather than a push stream. A live `group_state` stream can be added later if needed.

## Global Constraints
- Build on S4; tests stay green. No new deps. Read-only. No-op gracefully when no groups bridge.
- Keyboard: **Alt+G** (Ctrl+G is browser find).
- Commit to **main**; conventional commits.

## Tasks

### Task 1 — `group_status` RPC (backend)
- `SubscriptionRegistry.group_status() -> list[dict]`: from `manager.groups`, enumerate `groups.runtime.registry.names()`; for each, `await groups.status(name)`; enrich each member with `state` from `manager.list_sessions()`. Defensive (no groups / errors → `[]`).
- `WSSession._call`: `group_status` → `{"groups": await self._reg.group_status()}`.
- Test `tests/test_web_group_status.py`: fake manager with a `groups` bridge (`runtime.registry.names()` + async `status`) + a `list_sessions` for state enrichment; assert the RPC returns groups with members (incl. state) + current_broadcast. Drive through a real WSSession over FakeTransport.

### Task 2 — Group dashboard modal (frontend)
- `app.js`: `openGroupDashboard()` — modal; `client.rpc("group_status")` → render per-group: a header (name + member count), a **Members** list (handle · profile · state dot), and **Current broadcast** (objective + started_at) when present. Empty → "no groups". Refresh every 2s while open (clear on close). Member rows with a live tab are click-to-jump.
- `wireKeys`: Alt+G → openGroupDashboard.
- `css`: reuse `.modal`/`.qd-*`-style classes (add `.gd-*` minimal).
- Acceptance: `node --check` + route tests green. DOM via smoke (when saidkick is back).

### Task 3 — Smoke (deferred to saidkick reconnect)
Group activity needs an agent to spawn a group + broadcast (advanced). Backend verified by Task 1's test + a node `group_status` RPC check against a real serve (returns `{groups:[]}` when none). Full visual when saidkick reconnects.

## Self-Review
**Coverage:** RPC (T1), modal (T2). **Deferred (documented):** live push stream (poll-on-open instead); "Recent broadcasts" panel (status() exposes only current; recent needs log parsing — show Members + Current in S5). Member-state enrichment is best-effort.
