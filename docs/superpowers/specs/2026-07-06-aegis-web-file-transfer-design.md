# Aegis Web File Transfer — Design

**Status:** draft
**Date:** 2026-07-06
**Scope:** bidirectional file transfer in the web/PWA client — upload a file
from the browser to the agent, and let the agent hand a file back as a
clickable download. Replaces the two capabilities the (now-removed) Telegram
frontend had (`sendDocument` + inbound files), reimagined for the web transport.

## Motivation

With the web client deployed for remote coding (`dev.apiad.net`), the file gap
Telegram used to fill reopens: from a phone or a browser away from the machine,
there's no way to hand the agent a file (a CSV, a screenshot, a PDF) or to
retrieve a file the agent produced. Local dev doesn't have this problem — the
files are on the same disk. This is a **remote-access** feature.

## Feature-parity note (deliberate exception)

The founding principle of the web effort was **full parity between the TUI and
the web client**. File transfer is the one deliberate exception, and it's a
justified one:

- **Upload is inherently remote.** The TUI runs on the same machine as
  `aegis serve`; files are already on disk, directly reachable by path. There
  is nothing to "upload." The need only exists across the network gap that only
  the web frontend spans.
- **Download is inherently remote.** In the TUI the agent's output file is
  already on your local disk — you open it directly (the existing
  `aegis_view_file` opens it in a viewer tab). "Download" only means something
  when the file is on a *remote* server.

So the divergence is in *presentation*, not in the agent-facing contract. The
one agent-visible tool, **`aegis_offer_file`**, is parity-preserving: it
degrades per frontend — the web renders a download bubble; the TUI opens the
file in its viewer (reusing the `aegis_view_file` path); a headless session with
no UI no-ops gracefully. The agent writes the same call regardless of who is
connected. This is the only place the two first-class UIs intentionally differ,
and it differs because the underlying capability (moving bytes across a network)
is only meaningful for the remote UI.

## Grounding (real symbols this builds on)

- `src/aegis/web/server.py::build_web_app` — the `Route(...)` / `Mount(...)`
  registry; adds `POST /upload` and `GET /download`. `files_root` (the served
  project tree, `~/Workspace` on the VPS) is already threaded in.
- `src/aegis/web/subscriptions.py::SubscriptionRegistry.file_read` — the
  path-safety pattern to mirror: `(root / path).resolve()` then
  `.relative_to(root)`, reject on `ValueError`.
- `web_cfg.token` — the WS token; `/upload` and `/download` require it as a
  query param so they can't be probed blind (in addition to Caddy basic-auth).
- `src/aegis/mcp/server.py::aegis_view_file` — the sibling tool + the
  `getattr(bridge, "open_file", None)` graceful-degradation pattern
  `aegis_offer_file` mirrors.
- `src/aegis/web/static/js/app.js` — `wireComposer` (the `deliver` rpc on
  send), `openFileViewer` / `openFilePicker` (the viewer to add a Download
  button to). `renderEvent.js` — the per-kind renderer that special-cases the
  `aegis_offer_file` tool event into a download bubble.
- Uploads land under `files_root` (`.aegis/uploads/<handle>/…`) so they are
  both agent-reachable and serveable by the path-safe `/download` route.

## Architecture

Four additive pieces; nothing existing changes shape.

1. **`POST /upload`** (multipart) — files can't ride the JSON WebSocket. Query
   params: `t=<token>`, `handle=<session>`. Saves each part to
   `files_root/.aegis/uploads/<handle>/<sanitized-filename>`; returns
   `{files: [{name, path, size}]}` (path is `files_root`-relative). Rejects >
   `MAX_UPLOAD_BYTES` (50 MB) with a 413 + JSON error.
2. **`GET /download?path=…&t=<token>`** — resolves `path` under `files_root`
   (same safety as `file_read`), streams it with
   `Content-Disposition: attachment; filename="…"`. 404 on missing, 403 on
   traversal.
3. **`aegis_offer_file(path, label?)`** MCP tool — validates `path` is a file
   under `files_root`; returns `{status: "offered", url, name}` (or
   `{status:"no_file"}`). The offer is visible because the tool call is a normal
   transcript event (below).
4. **Web UI** — an attach (📎) control in the composer, pending-attachment
   chips, and a download-bubble renderer + a Download button on the viewer.

## Upload flow (you → agent)

Chat-attachment model:

1. 📎 in the composer opens the native file/photo picker (`<input type="file"
   multiple>`; on mobile this surfaces camera/photos).
2. On select, each file **uploads immediately** via `POST /upload` for the
   active tab's `handle`, showing a **pending-attachment chip** in the composer
   (reusing the existing pending-chip visual pattern). Upload-in-flight and
   error states show on the chip.
3. On **send**, the client **weaves the uploaded paths into the delivered
   message text** — no `deliver` protocol change:

   ```
   [attached files:
   - report.csv → .aegis/uploads/swift-bohr/report.csv]

   summarize this
   ```

   The agent (running `permission: full` on `~/Workspace`) reads the path with
   its normal file tools. The transcript renders the user turn with the
   attachment chip client-side (the woven preamble is the durable record).
4. Sending with attachments but no text is allowed (the preamble is the
   message). Attachments not sent (tab closed / cleared) are left in the scratch
   dir; a size-bounded scratch dir is acceptable for v1 (no auto-GC).

## Download flow (agent → you)

**Push (primary):**

1. The agent calls `aegis_offer_file("out/report.pdf")`.
2. That MCP tool call is a normal `ToolUse` event in the transcript stream
   (already persisted to JSONL, already rendered by the web client).
3. `renderEvent.js` **special-cases** an event whose tool name is
   `aegis_offer_file` (or its `ToolResult`): instead of a plain tool chip, it
   renders a **download bubble** — `⤓ <name>` linking to
   `/download?path=<path>&t=<token>`. Because it's a persisted event, it
   **survives reload** — no new stream kind, no extra persistence.

**Pull (bonus):** the existing file viewer/picker (Alt+P → `file_read`) gains a
**Download** button that hits the same `/download` route — so any file under
`~/Workspace` is retrievable on demand, no agent involvement.

## Security, limits, mobile

- **Path safety.** `/upload` sanitizes filenames (basename only, no separators)
  and confines writes to `.aegis/uploads/<handle>/`. `/download` and
  `aegis_offer_file` resolve under `files_root` and reject traversal — mirroring
  `file_read`. Neither can escape `~/Workspace`.
- **Auth.** Both routes require `t=<web_cfg.token>` (query param, since the WS
  token is the app's real secret) on top of Caddy basic-auth. A missing/wrong
  token → 401.
- **Size.** `MAX_UPLOAD_BYTES = 50 * 1024 * 1024`, enforced streaming so a
  larger body is rejected without buffering it all.
- **Mobile.** The 📎 input opens the OS file/photo picker (camera on phones);
  download bubbles are tap-to-save. No separate mobile code path.

## Testing

- **Upload:** `POST /upload` with a multipart body → file exists at
  `.aegis/uploads/<handle>/…`, response path is correct; oversize → 413; bad
  token → 401; filename with `../` → sanitized to basename.
- **Download:** `GET /download` for an in-tree file → 200 + attachment header +
  bytes; `../` traversal → 403; missing → 404; bad token → 401.
- **`aegis_offer_file`:** valid in-tree path → `{status:"offered", url}`;
  outside `files_root` → rejected; no UI bridge → graceful status.
- **Renderer (node):** an `aegis_offer_file` tool event → renders a
  `/download?...` anchor with the filename; a normal tool event is unaffected.
- **Compose weave:** sending with attachments prepends the paths block to the
  delivered message.

## Slices

| # | Slice | Deliverable |
|---|-------|-------------|
| **F1** | Download route + `aegis_offer_file` + renderer | `GET /download` (path-safe, tokened, attachment); `aegis_offer_file` MCP tool; `renderEvent.js` download-bubble special-case. Agent can hand you a file. |
| **F2** | Upload route + composer attach | `POST /upload` (multipart, scratch dir, size cap, tokened); 📎 control + pending-attachment chips; paths woven into `deliver` on send. You can hand the agent a file. |
| **F3** | Viewer Download button | Add Download to the existing file viewer/picker (pull path). Small, rides F1's route. |

**Order:** F1 → F2 (independent, either first; F1 is smaller). F3 after F1.

## Out of scope (v1)

- Auto-GC / retention of the upload scratch dir (leave files; bounded by disk).
- Inline image/PDF *preview* in the transcript (download only; preview is a
  later nicety).
- Drag-and-drop onto the transcript (the 📎 picker covers it; DnD is additive).
- TUI upload/download (deliberately out — see the parity note; `aegis_offer_file`
  degrades to the TUI viewer).
- Per-file access controls beyond the single shared token.
