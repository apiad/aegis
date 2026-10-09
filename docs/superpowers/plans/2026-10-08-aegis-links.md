# aegis links: implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task by task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** zion's aegis links the VPS's aegis: people on zion see, prompt and spawn on the VPS; a zion agent hands off to `handle@vps`; nothing on the VPS reaches zion; the archive pages across servers.

**Architecture:** A link is a websocket client inside the home server (`links.py`) that speaks the browser protocol to the linked server with that server's token. Browser messages carry an optional `server`; `web.py` forwards those down the link, naming each forwarded subscription with a link-unique `sid`, and stamps `server` on what comes back. The link client handles five frame kinds and has no request handler, which is the whole security mechanism.

**Tech stack:** Python 3.13, Starlette, `websockets` 16 (asyncio client), httpx (file proxy), pydantic, plain ES modules in the client, pytest with in-process uvicorn servers and Playwright.

**Spec:** `docs/superpowers/specs/2026-10-08-aegis-links-design.md`

## Global constraints

- Protocol version: `PROTO = 3` in both `src/aegis/web.py` and `src/aegis/client/js/protocol.js`.
- Links live in `<state>/links.json`, mode 0600, written by write-then-rename. Never in `.aegis.yaml`, never in a log line, a channel or a reply.
- A server's name: `aegis serve --name`, default `socket.gethostname()`; it is `App.server_name` and goes out in `welcome`.
- Agents never spawn, enqueue or read on another server; the error code is `not_across_links`.
- Imports inside `src/aegis` are relative; nothing imports `legacy/` (`tests/test_imports.py`).
- Nothing below the CLI calls `Path.cwd()` (`tests/test_no_cwd.py`).
- The client asks through `js/dialog.js`, never `confirm(`/`alert(`/`prompt(` (`tests/test_client_rules.py`).
- A user-visible change gets a `changelog.d/` fragment; `make changelog-check` passes.
- Tests over 3 s are `@pytest.mark.slow` (`make test` fails them otherwise).

## Review focus

1. A link whose server restarts while a browser holds VPS tabs open: the tabs must come back live without a page reload. Pinned in Task 3's browser test.
2. A token revoked on the VPS: the link must stop retrying and say `unauthorized`, not hammer the VPS every 30 s. Pinned in Task 2.
3. Two zion browsers watching the same VPS transcript: each gets its own numbered patches; one unsubscribing must not end the other's. Pinned in Task 2.
4. Archive rows that share a `last_activity` across a page boundary: none skipped, none repeated, on one server and merged. Pinned in Task 1.
5. A VPS frame zion never asked for (a `call`, a `sub`, junk): dropped, logged, nothing runs. Pinned in Task 2's attack test.

---

### Task 1: archive paging (slice 0)

**Files:**
- Create: `src/aegis/archive.py`
- Modify: `src/aegis/registry.py` (`Registry.archive`), `src/aegis/app.py` (`ArchiveParams`, `archive.list`), `src/aegis/client/js/app.js` (archive section), `src/aegis/client/js/fleet.js` (`renderArchive` footer), `src/aegis/client/index.html` (footer node)
- Test: `tests/test_archive.py`, `tests/test_registry.py::test_archive_filters_and_pages` (rewritten), `tests/test_browser.py` (a paging test)

**Interfaces:**
- Produces: `archive.Position = tuple[float, str]` (last_activity, log_id), ordered newest first by `key(meta) = (last_activity or 0, log_id)` descending.
- Produces: `archive.encode(positions: dict[str, Position | None]) -> str` and `archive.decode(cursor: str | None) -> dict[str, Position | None]`; urlsafe base64 of JSON; a bad cursor raises `OpError("bad_cursor", …)`. `None` for a server means it is exhausted.
- Produces: `archive.page(metas: Iterable[dict], query: str | None, limit: int, after: Position | None) -> tuple[list[dict], int, Position | None]`: the next `limit` matching metas strictly after `after`, the total matching, and the position of the last item returned (None when nothing is left after it).
- Produces: `archive.merge(pages: dict[str, tuple[list[dict], Position | None]], limit: int, positions: dict) -> tuple[list[dict], dict]`: merge per-server pages, take `limit`, return the items and the new positions (Task 4 uses it).
- Produces: `archive.list` params `{query?, server?, limit=50, cursor?}` and result `{items, total, counts: {server: n}, cursor}`; every item carries `server`.

- [ ] Write `tests/test_archive.py`: `test_page_orders_newest_first`, `test_page_after_a_position_skips_nothing_on_ties` (five metas with the same `last_activity`, paged by 2, every id exactly once), `test_page_counts_the_total_matching_the_query`, `test_cursor_round_trips_and_a_bad_one_is_refused`, `test_merge_takes_the_newest_across_servers_and_keeps_positions` (two servers, 3+3 items interleaved, limit 4, then the second page from the returned positions gives the remaining 2 with no repeats).
- [ ] Run `uv run pytest tests/test_archive.py -q`; expect import errors.
- [ ] Write `src/aegis/archive.py` with the functions above.
- [ ] Rewrite `Registry.archive(query, limit, after)` to return `archive.page(self.archived.values(), …)` mapped through `_public`; update `test_archive_filters_and_pages` to the new signature (the `>=` timestamp case becomes the tie case).
- [ ] `archive.list` in `app.py`: decode the cursor, page the local archive at `positions.get(self.server_name)`, return `{items (each with server), total, counts: {name: total}, cursor: encode({name: pos}) or None}`.
- [ ] Client: `loadArchive(reset)` keeps `archived` and `archiveCursor`; the first load resets, "Show 50 more" appends; the footer reads `Showing N of TOTAL`; the button is hidden when `cursor` is null. A query change resets.
- [ ] Browser test `test_the_archive_pages_past_fifty`: seed 120 archived metas on disk, open the Fleet, see 50 rows and `Showing 50 of 120`, click twice, see 120 and no button.
- [ ] Run the touched tests; commit `feat(archive): page past the newest 50, with a cursor that never skips a tie (#203)` plus a `changelog.d/203-archive-paging.fixed.md` fragment.

### Task 2: the link (slice 1, server side)

**Files:**
- Create: `src/aegis/links.py`
- Modify: `src/aegis/web.py` (link hello, no-Origin rule, `sid`, routing by `server`, `/via/<server>/files/…`, `welcome.server`), `src/aegis/app.py` (`Links` construction, `links` channel, `link.*` operations, shutdown), `src/aegis/ops.py` (`Caller.link`, `Caller.via`), `src/aegis/cli.py` (`serve --name`, `aegis link add|remove|list`)
- Test: `tests/test_links.py`, `tests/test_web.py` (origin cases)

**Interfaces:**
- Produces: `Caller(kind, log_id=None, desktop=False, link: str | None = None)`; `link` is the linked-from server's name on a link socket.
- Produces: `links.LinkStore(path)`: `load() -> list[dict]`, `save(entries)`, `stamp()`; entries `{name, url, token, added}`.
- Produces: `links.Link(name, url, token, own_name, user, on_state)`: `start()`, `stop()`, `state` (`connecting|linked|offline|mismatch|unauthorized`), `since: float`, `rtt_ms: int | None`, `remote: dict` (its `welcome`), `error: str`, `async call(op, params, timeout=15) -> Any` (raises `OpError("server_offline", …)` when not linked, `OpError(code, message)` for the remote's errors), `sub(sid, channel, since, send)`, `unsub(sid)`, `drop(prefix)` (forget every sid starting with a browser's prefix).
- Produces: `links.Links(state_root, own_name, user, publish)`: `boot()`, `shutdown()`, `get(name) -> Link | None`, `up() -> list[Link]`, `wire() -> list[dict]` (no tokens), `add(name, url, token)`, `remove(name)`; reloads `links.json` when its stamp changes (checked every second), so `aegis link add` reaches a running server.
- Produces: `links.probe(url, token, own_name, user, timeout=10) -> dict` (the remote's `welcome`), used by `aegis link add`.
- Produces: channel `links` (snapshot `Links.wire()`, patch `{"set": wire}`).

Wire, as `web.py` will speak it:
- A link socket sends `hello {t, token, proto: 3, link: {server, user}}` and no `Origin`. A socket with no `Origin` and no `link` is closed 4403, as today.
- On a link socket, `sub` and `unsub` carry `sid`; every frame for that subscription echoes `sid`. Without `sid`, the channel name is the key, as today.
- A browser frame with `server` other than the home's name goes down that link; the reply, snapshot, patch or error comes back with `server` added (and the browser's own `id`/`channel` restored).
- The link client accepts `welcome`, `reply`, `snapshot`, `patch`, `error`, and drops anything else with one log line per kind.

- [ ] Write `tests/test_links.py` with two in-process servers (a `Pair` fixture modelled on `tests/test_agents.py::World`: `alpha` and `beta` Apps with `server_name`, uvicorn on free ports, alpha linked to beta through `Links.add`). Tests:
  - `test_a_link_comes_up_and_names_the_far_server`
  - `test_a_browser_on_alpha_calls_and_subscribes_on_beta` (a raw websocket client on alpha: `call {server: beta, op: agents.list}`, `sub {server: beta, channel: sessions}`; spawn on beta through it and see the `upsert` arrive with `server: beta`)
  - `test_two_subscriptions_to_one_channel_are_independent` (two alpha sockets subscribe to beta's `sessions`; one unsubscribes; the other still gets patches)
  - `test_the_link_reconnects_and_says_so` (stop beta, see `offline`, restart, see `linked`)
  - `test_a_wrong_token_stops_retrying` (state `unauthorized`, no second connect within 3 s)
  - `test_a_name_mismatch_keeps_the_link_down`
  - `test_beta_cannot_call_or_subscribe_down_the_link` (a fake beta, a bare websockets server, answers `welcome` then sends a `call`, a `sub` and junk; alpha's registry sees no call, the log names the drop)
  - `test_links_json_is_0600_and_tokens_never_reach_the_wire` (the `links` channel and `link.list` carry no token)
  - `test_a_link_socket_cannot_open_files_on_the_desktop` (`file.open` over a link socket gets `not_local`)
  - `test_a_sent_file_is_streamed_through_via` (`GET /via/beta/files/<id>/<name>` on alpha returns beta's bytes and its sandbox headers)
- [ ] `test_web.py`: the `no-origin` case stays 4403; add `test_a_socket_with_no_origin_is_accepted_only_as_a_link` (hello with `link` and the right token: welcome; with `link` and a wrong token: 4401).
- [ ] Run them; expect failures.
- [ ] Implement `ops.Caller.link`; `links.py`; the `web.py` changes; `App.links`, the `links` channel and `link.add`, `link.remove`, `link.list` (people only); `serve --name`; `aegis link add|remove|list` (token from stdin, `probe` first, name check).
- [ ] Run `tests/test_links.py tests/test_web.py`; commit `feat(links): zion links another aegis as a client of it (#203)`.

### Task 3: the person's client (slice 1, browser side)

**Files:**
- Modify: `src/aegis/client/js/protocol.js` (PROTO 3, `server` on calls and subscriptions), `src/aegis/client/js/app.js` (sessions keyed by server, links channel, remote bands, offline, routing `#s=<server>/<log_id>`), `src/aegis/client/js/fleet.js` (bands by class, a server tag), `src/aegis/client/js/tabs.js` (the tag, scroll into view), `src/aegis/client/js/transcript.js` and `entries.js` (a file base for remote transcripts), `src/aegis/client/index.html` (`#remotes`), `src/aegis/client/css/base.css` (tag, off, scrolling tab bar)
- Test: `tests/test_browser.py` (a two-server fixture and tests)

**Interfaces:**
- Consumes: Task 2's wire.
- Produces in `protocol.js`: `conn.call(op, params, server)` and `conn.subscribe(channel, onSnapshot, onPatch, onError, since, server)`; a subscription's key is `server ? server + "\u0000" + channel : channel`.
- Produces in `app.js`: a session key `key(m) = m.server ? `${m.server}/${m.log_id}` : m.log_id`; every meta from a linked server carries `server` and `key`; `forKey(key) -> {server, log_id}`; `callFor(key, op, params)`.

- [ ] Browser fixture `linked` in `test_browser.py`: two `Server` processes, `--name alpha` and `--name beta`, alpha linked by `aegis link add beta <url>` with beta's token on stdin.
- [ ] Tests: `test_a_linked_servers_sessions_show_under_its_band` (spawn on beta through beta's own page; alpha's Fleet shows a `beta` band and the card), `test_a_remote_tab_carries_its_tag_and_takes_prompts` (open the beta tab on alpha, send `hello`, see the fake's answer, and see it in beta's store), `test_a_dropped_link_greys_the_band_and_comes_back` (stop beta: band says offline, card `.off`, composer disabled; restart beta: live again with no reload).
- [ ] Run them; expect failures.
- [ ] Implement the client changes; remote bands are built from a `<template>` of the band and card grid; the tab bar scrolls sideways and the focused tab scrolls into view.
- [ ] Run the browser tests touched; commit `feat(links): the Fleet and tabs show a linked server's sessions (#203)` plus `changelog.d/203-links.added.md`.

### Task 4: spawning, Settings, files and the merged archive (slice 2)

**Files:**
- Modify: `src/aegis/commands.py` (`spawn` in `AEGIS`, `parse_spawn`), `src/aegis/app.py` (`_command` runs `/spawn`, locally or through a link; `archive.list` merges across links), `src/aegis/quota/claude.py` and `src/aegis/quota/core.py` (an `account` hash per provider), client `app.js` (directory line with the server, archive tags and filter), `settings.js` (Servers section, server picker), `fleet.js` (`same account as`), `index.html`
- Test: `tests/test_commands.py`, `tests/test_links.py`, `tests/test_quota_claude.py`, `tests/test_browser.py`

**Interfaces:**
- Produces: `commands.parse_spawn(arg: str) -> SpawnLine(agent, server, prompt, model, effort, cwd, permission)`; `OpError("bad_spawn", usage)` on a missing agent or a flag without a value.
- Produces: `/spawn` result `{log_id, handle, server}`; the client opens no tab focus for it.
- Produces: quota provider readings carry `account: str | None`, the first 12 hex digits of SHA-256 of the account id (Claude: `oauthAccount.accountUuid` in `~/.claude.json`, overridable with `CLAUDE_CONFIG`; OpenCode Go: of its key).

- [ ] Tests: `test_parse_spawn_*` (agent only, `@server`, flags then prompt, prompt verbatim with quotes and dashes, missing agent), `test_slash_spawn_spawns_on_a_linked_server` (in the `Pair`: `/spawn opus@beta do it` sent to an alpha session creates a beta session with that first message and no `spawned_by`), `test_slash_spawn_to_a_down_link_starts_nothing`, `test_the_archive_merges_across_servers_and_pages` (60 archived on alpha, 70 on beta with interleaved times and ties, paged by 50: 130 distinct rows, newest first, counts per server; beta down: alpha's rows and `counts.beta` absent), `test_quota_reading_names_its_account_by_hash`, browser `test_spawn_on_a_linked_server_from_the_new_tab`.
- [ ] Implement; Settings gains the Servers section (link list from the `links` channel, add with URL and token, remove) and a server picker that sends the config editor's calls and subscription to the picked server.
- [ ] Run the touched tests; commit `feat(links): spawn on a linked server, its files, Settings and the merged archive (#203)`.

### Task 5: agents across the link (slice 3)

**Files:**
- Modify: `src/aegis/agent_ops.py` (`peer.handoff`, `peer.deliver`, `session.list`, `peer.read`, `queue.enqueue`), `src/aegis/app.py` (`session.spawn` refuses `@server` for agents), `src/aegis/mcp.py` (the primer's two sentences), `DESIGN.md`, the spec's status
- Test: `tests/test_links.py`, `tests/test_live.py`

**Interfaces:**
- Produces: `split_address(target: str, own: str) -> tuple[str, str | None]` (`knuth@vps` → `("knuth", "vps")`; own name or no `@` → server None).
- Produces: `peer.deliver` params `{target, context, interrupt, sender: {handle, server, user}}`, callable only by a caller with `link` set; header `> from agent:<handle>@<server> (<user>) · <iso>`.

- [ ] Tests in the `Pair`, each driving an alpha fake-claude session through `/mcp <tool> <json>`:
  - `test_a_handoff_reaches_a_session_on_the_linked_server` (beta's session gets one user turn with the header)
  - `test_a_handoff_with_interrupt_cuts_the_far_turn_first`
  - `test_session_list_shows_far_handles_and_states_only`
  - `test_reading_spawning_and_enqueueing_across_a_link_are_refused` (`not_across_links` for each)
  - `test_beta_has_no_route_to_alpha` (a beta agent's `peer_handoff` to `x@alpha` fails with `unknown_server`; its `session_list` has no alpha)
  - `test_peer_deliver_is_only_for_link_sockets` (a browser socket on beta and an agent on beta both refused)
  - `test_no_text_written_on_beta_reaches_an_alpha_agent` (a marker in a beta session's title and transcript; after a handoff, a `session_list` and every refused call, alpha's agent store holds no marker). Prove it can fail: add `title` to the far entries of `session.list`, see it red, revert.
- [ ] Live test `test_a_real_agent_hands_off_across_a_link` (`make test-live`).
- [ ] Implement; DESIGN.md gains the rule "A link is a client, and nothing travels from a linked server into an agent" and the process-model paragraph; the spec's status flips to built.
- [ ] Run `tests/test_links.py`; commit `feat(links): a zion agent hands off to handle@server; nothing comes back (#203)`.

### Finish

- [ ] `make format-check lint typecheck changelog-check lint-docs` and `make test`; the browser tests touched; `make bench` and its table in the PR body.
- [ ] Exercise it the way Alex will: two `aegis serve` on zion (scratch roots), linked, in a real browser through saidkick or Playwright with screenshots; spawn, prompt, kill and restart the far one.
- [ ] A fresh reviewer on the whole branch; fix what it finds.
- [ ] Open the PR (`Closes #203`), with what was measured and what was left out.
