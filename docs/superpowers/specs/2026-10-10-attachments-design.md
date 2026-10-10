# aegis: attachments, files a person hands to a session

**Status: designed, 2026-10-10** (issue #291). Designed with Alex in a
brainstorm, text only. No plan yet.

## What this delivers

A person drops, pastes or picks files in a session's composer: a screenshot, a
voice note, an HTML report, a PDF, anything. Each file uploads at once and shows
as a chip with its progress. When the person sends the message, the files move
into an inbox folder that belongs to that session, the agent receives the
message with one line per file giving its absolute path, type and size, and
the person's own row in the transcript shows the files as one card with a pager,
the card `file_send` draws today.

What the agent does with a file is its own business. It reads an image with
Read, plays with an HTML file in a shell, installs Whisper for an audio note. aegis
transcribes nothing and converts nothing.

Attaching is a person's operation. No agent gets a tool for it, the same way no
agent types into another session's composer. It works on a session on a linked
server as well as on a local one.

Today files travel only from agent to person. `SendParams` carries `log_id`
and `text`, no route or operation accepts bytes from a browser, and the
composer has no drop or paste handler for files. A person who wants to show an
agent a screenshot copies it onto the server by some other route and types its
path.

## Decisions

| Question | Decision | Why |
|---|---|---|
| How the agent hears of a file | With the message the person sends: the file lines follow the typed text | The person nearly always says something about the file ("transcribe this", "what is wrong here"), and the instruction belongs next to the paths it refers to. A separate inbox wake would split them across two turns |
| A message of files and no text | Allowed; the agent gets the file lines alone | Dropping a file is a message in itself |
| How bytes reach the server | Person operations over the websocket, base64 chunks of 256 KB | Every action is an operation (DESIGN.md), so the upload needs no new route, no new auth path and no cross-site check, and a call naming a linked server already travels down the link. Base64 costs a third more bytes, which a voice note or a screenshot does not notice. An HTTP `/upload` route would stream faster but needs its own cookie and Origin checks, plus a `/via` twin that authenticates to the far server by header, which no route does today |
| Where the files live | `<state>/inbox/<log_id>/`, one folder per session, keyed by the log id | The log id never changes and is never reused; a handle is both (DESIGN.md) |
| Name inside the folder | `<YYYYMMDD-HHMMSS>-<name>`, with `-2`, `-3` before the extension on a clash | Two `image.png` pasted a minute apart must both survive, and the agent can sort the folder by time |
| Until the message is sent | Staged under `<state>/inbox/<log_id>/.staged/<upload_id>/` | The agent never sees a file the person removed from the composer or never sent |
| How the person sees them | The person's row carries the files and draws `fileCard`, one card with a pager for all of them | Six attachments are one card, not six stacked previews (Alex) |
| How the card serves them | Each sent attachment is also entered in the sent-files store, `<state>/files/<id>/<name>`, as a hard link | The card's URLs, the sandbox headers and the `/via` path for a linked server's file all exist for that store already. A hard link costs no disk; a copy falls back when the link fails |
| Limits | `files.MAX_BYTES` (100 MB) per file, 20 files per message | The same cap `file_send` has |
| Commands | A `/` line with attachments is refused | Neither aegis's commands nor a harness's take files |

## The operations

Three new person operations and one changed one. None is marked for agents,
so none is served at `/mcp`.

- **`attachment.begin {log_id, name, size}`** checks `size` against
  `MAX_BYTES` and the name (below), creates the staging folder and answers
  `{upload_id}`, 128 random bits.
- **`attachment.put {log_id, upload_id, offset, data}`** appends the decoded
  `data` (at most 256 KB) to the staged file. `offset` must equal the bytes
  already written, so a chunk a reconnect resent is refused (`bad_offset`, with
  the current size, from which the client resumes) instead of written twice.
  Writing past the declared `size` is refused.
- **`attachment.drop {log_id, upload_id}`** removes the staged file, for the ×
  on a chip.
- **`session.send {log_id, text, attachments}`** takes up to 20 `upload_id`s.
  `text` may be empty when `attachments` is not. Before anything moves, every
  upload must exist and be complete (`upload_incomplete` otherwise), so a
  refused send moves nothing. Then each staged file moves into the inbox, is
  linked into the files store, and the send goes to the harness as below.

A name is the last path component of what the browser gave, with control
characters removed, a leading dot replaced, and at most 120 bytes. A pasted
image has no name, and the client names it `pasted-<HHMMSS>.png`.

The inbox folder and its staging folder are created with mode 0700.

## What the agent reads

```
<the typed text>

Attached files:
- /home/apiad/Workspace/.aegis/state/inbox/20261010-094830-4eec91/20261010-101502-screenshot.png (image/png, 312 KB)
- /home/apiad/Workspace/.aegis/state/inbox/20261010-094830-4eec91/20261010-101502-note.m4a (audio/mp4, 1.4 MB)
```

The type comes from `files.classify`, which `file_send` already uses. With no
typed text the message is the block alone. The block never starts with
`> from `, which is how the fold tells an inbox message from a person's.

The agent's process must be allowed to read the folder, or a session not on
full permission stops at a permission prompt to open the file:

- Claude starts with `--add-dir <inbox>`.
- OpenCode's rules deny `external_directory` in three modes
  (`opencode/config.py`); the inbox becomes an allowed pattern in each.
- Codex: the plan checks whether each sandbox policy can read outside the
  working directory, and adds the folder where it cannot.

## The store and the fold

The `send` record gains two fields: `typed`, the text the person wrote, and
`files`, the files-store records of the attachments (with their inbox `path`).
`text` stays what the harness was sent, so a resume and a refold read the
same file.

The fold's pending entry shows `typed` as its Markdown and carries
`detail.files`. When the echo arrives, the user entry it creates takes the
pending entry's `typed` and `files`, so the person's row shows what they wrote
and one card, never the path block. Matching the echo to its pending send
still compares the echo with the full `text` the harness was sent, which the
pending entry keeps in its detail for that purpose. A send with no files folds exactly as
before, so old transcripts are unchanged.

The client draws a user row's `detail.files` with the same `fileCard` a `file`
entry uses: one card, one pager.

## Linked servers

A browser call that names another `server` already goes down the link and
comes back stamped with it. The three attachment operations and the send ride
that path unchanged: the far server stages, moves and serves the files, and
its agent reads paths on its own disk. The person's card loads them through
this server's `/via`, as it loads a far `file_send` today.

The direction is person to far agent, which the link rule allows: a link only
asks, and the far server treats this server as one more client holding its
token. Nothing here sends a far server's bytes into an agent on this server.

## The composer

- Files arrive by drop on the composer or the transcript, by paste, or through
  a paperclip button beside the microphone. Each becomes a chip with the name,
  the size, a progress bar and an ×.
- Uploads start at once, one chunk in flight per file and at most three files
  at a time.
- Send waits until every chip has finished. A chip whose upload failed shows
  the error and keeps the message from sending until it is removed or retried.
- The chips belong to the tab, beside its draft, and survive switching tabs.
  A reconnect resumes each upload from the offset the server reports. After a
  server restart the staging folders are gone (below), and the chips say so.

## Leftovers

A staged upload the person never sent stays until the server's next boot,
which removes every `.staged` folder. A browser that closed mid-upload leaves
at most one partial file per chip until then. Sent files stay in the inbox
for the life of the session, archived included, as sent files stay today.

## Testing

Each test drives the real app, per AGENTS.md.

- Fake claude: begin, put in several chunks, send with text; the harness
  receives the typed text and the block, the file on disk equals the bytes
  sent, and the argv carries `--add-dir` for the inbox.
- The refusals: a size over the cap, a wrong offset, a chunk past the declared
  size, an unknown upload, an incomplete upload at send, a command line with
  attachments, 21 files. A refused send leaves every staged file in place.
- The fold: a send with files followed by its echo gives one user entry whose
  Markdown is the typed text and whose detail holds the files; a refold equals
  the live entries, and the wire tests' cut-anywhere check covers the new
  fixture.
- Browser: drop two files and paste an image, watch the chips finish, send,
  and find one card with a pager of three on the person's row; the × on a chip
  removes its staged file.
- Links: the same upload through a link lands in the far server's inbox, and
  the card loads through `/via`.
- `make test-live`: a real Claude session receives a screenshot and reads it.

## Left out

- Attachments in an agent's `session_send` or `peer_handoff`. Agents pass
  paths to each other already.
- Transcription, thumbnails, conversion of any kind.
- Deleting attachments from the inbox from the UI.
- Resuming an upload across a server restart.
