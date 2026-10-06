# send_file: agents hand files to the person

**Status: designed, 2026-10-06** (issue #142). Designed with Alex. Not built yet.

## What it delivers

An agent that makes a file (an image, a PDF, an HTML report, a CSV, a log) calls
`send_file`, and the file appears in its session's transcript. When the browser
can show it, the preview sits in the transcript; every file has Open, which
shows it in a new tab, and Download. aegis serves the file itself, so the same
link works from a browser on the server's machine and from one reaching it
remotely.

Today an agent can only print a path. A path means nothing to a browser on
another machine, and nothing at all once the agent overwrites or deletes the
file. aegis serves `/`, `/static`, `/ws` and `/mcp` and nothing else, and the
transcript has no entry kind for a file.

Out of this design: a viewer page that renders Markdown, tables and code for
Open (the second step, below); files from the person to an agent; whole
folders; deleting a session's files (sessions are never deleted today); a
configurable size limit.

## Decisions

| Question | Decision | Why |
|---|---|---|
| Copy or point at the path | Copy into the server's state when sent | Agents overwrite and delete their working files, and an archived session must still show what it was sent |
| How a browser is allowed to fetch it | The URL is the capability: `/files/<id>/<name>`, `<id>` 128 random bits | The token lives in the tab's `sessionStorage` and only the websocket carries it; `<img src>`, a new tab and a download cannot. An unguessable link works in all three |
| What Open shows | The raw file, served with a type the browser displays | Covers images, PDFs, HTML reports, audio and video, most of what agents send. A viewer page follows once Markdown and CSV are common |
| Active content | HTML, SVG and XML are served with `Content-Security-Policy: sandbox`, and framed with `sandbox` | On aegis's origin a script in an agent's HTML could read the token from `sessionStorage` and drive every agent. The sandbox gives it an opaque origin |
| PDF | Served without the sandbox policy | Chrome refuses to render a PDF under `CSP: sandbox`, and a PDF in the browser's viewer has no access to the page's storage |
| Where the preview kind is decided | On the server, when the file is recorded | The fold already makes every drawing decision (entries.py); the client only draws |
| One file or many per call | One | An agent with several files calls the tool once per file; each gets its own caption |
| URL handed to the agent | Relative, `/files/<id>/<name>` | aegis may be reached through a proxy whose host it does not know; the transcript already shows the file to the person |

## The tool

`send_file`, an agent operation (`file.send` in the registry, so the tool is
`mcp__aegis__file_send`; the registry's naming rule decides, see slice 3):

```
file.send(path: str, caption: str | None = None)
  -> {url, name, size, mime}
```

- `path` is resolved against the calling session's working directory; `~` is
  expanded. Symlinks are followed.
- Refused with an `OpError`: not a regular file (`not_a_file`), larger than
  100 MB (`too_large`), unreadable (`unreadable`).
- Only an agent's own session can send (the same `own(caller)` check the other
  agent operations use). A person has the files already.

## Storage

`<state>/files/<id>/<name>`, where `<name>` is the file's base name and `<id>`
is `secrets.token_urlsafe(16)`. The copy is written to a temporary name in the
same directory and renamed, so a half-copied file is never served.

The session records one store record:

```json
{"kind": "file", "file_id": "…", "name": "chart.png", "mime": "image/png",
 "size": 48213, "preview": "image", "caption": "Weekly cost", "excerpt": null}
```

The record, not the directory, is the source of truth for the transcript. A
store that refers to a missing file still folds; the entry draws, and the link
returns 404.

## Preview kinds

Decided once, at send, from the type `mimetypes` guesses from the name, with a
sniff of the first 4 KB when the guess is empty or `application/octet-stream`
(no NUL byte and valid UTF-8 means text).

| kind | types | in the transcript | served for Open as |
|---|---|---|---|
| `image` | png, jpeg, gif, webp, avif, svg | `<img>`, at most ~360 px tall; click opens | its type; SVG sandboxed |
| `pdf` | pdf | `<iframe loading=lazy>`, ~480 px tall | `application/pdf` |
| `html` | html, htm | `<iframe sandbox=allow-scripts loading=lazy>`, ~480 px | `text/html`, sandboxed |
| `markdown` | md, markdown | excerpt rendered with the transcript's Markdown renderer | `text/plain; charset=utf-8` |
| `text` | any other text: code, csv, json, yaml, logs | excerpt in a monospace box | `text/plain; charset=utf-8` |
| `audio` | audio/* | `<audio controls preload=metadata>` | its type |
| `video` | video/* | `<video controls preload=metadata>` | its type |
| `other` | everything else | a card: name, size, type | `attachment` (download) |

The excerpt, for `markdown` and `text` only, is the first 40 lines or 4 KB,
whichever is shorter, cut at a line boundary, and stored in the record. The
preview then needs no second request, and the transcript stays readable if the
file is lost.

Every file entry, whatever its kind, shows the caption, the name, the size,
**Open** (`/files/<id>/<name>`, new tab) and **Download**
(`/files/<id>/<name>?download=1`).

## The transcript entry

`Fold._own` turns a `file` record into one entry:

```
kind "file", status "ok", glyph a file glyph,
title  = name, summary = "<size> · <mime>", md = caption,
detail = {url, download, preview, mime, size, excerpt}
```

- Fleet cards read the entry as the session's activity: `sent chart.png`.
- `peer_read` renders it as `file: chart.png (48 KB) /files/<id>/chart.png`.

## The route

`GET /files/{file_id}/{name}`, public like `/static` (the id is the secret):

- 404 when the id does not exist, when `name` does not match the stored name,
  or when the `Host` header is not one the websocket accepts.
- `X-Content-Type-Options: nosniff` on every response.
- `Cache-Control: private, max-age=31536000, immutable`; a sent file never
  changes.
- `?download=1`: `Content-Disposition: attachment; filename*=…`.
- Without it: the "served for Open as" column above. `Content-Security-Policy:
  sandbox` on `html` and SVG, and on any XML type; `attachment` for `other`.
- `Referrer-Policy: no-referrer`, so a page an HTML report links to does not
  learn the capability URL.

## Testing

- Fold: one test per preview kind, from a `file` record to the entry, and a
  record whose file is gone.
- The operation: refused for a directory, a missing path and an oversized file
  (the size limit is a module constant the test lowers); a relative path
  resolves against the session's cwd; the copy survives the original's deletion.
- The route (`test_web.py`): the sandbox header on HTML and SVG and not on PDF,
  `nosniff`, `attachment` with `?download=1` and for `other`, 404 for an
  unknown id, a wrong name and a foreign `Host`.
- End to end (`test_agents.py`): the fake claude calls `file_send` over the real
  `/mcp` with its own token, and the entry reaches the `transcript:` channel.
- Browser (`test_browser.py`): an image preview loads with a non-zero natural
  width; Open's and Download's links fetch the bytes that were sent.

## The second step: a viewer page

Open goes to `/view/<id>/<name>`, an aegis page that renders Markdown, shows CSV
as a table and code with line numbers, and passes every other kind through to
the raw route; the viewer links to the raw file. Designed when the first step
has been in use.
