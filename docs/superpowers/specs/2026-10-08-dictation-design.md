# Dictation: a mic button that transcribes in the browser

**Status: built, 2026-10-08 (#201).** Closes #89. Designed with Alex in a
brainstorm after a playground run on his own dictations and on his phone. The
playground (page, Node harness, benchmark logs, phone reports) is in the
workspace at `.playground/whistle-web/`, not in this repo.

## What this delivers

Alex dictates prompts to agents, often long and rambling, in Spanish with English
technical words mixed in, from a desktop browser and from Chrome on his Android
phone. After this work:

- The session composer and the new-tab composer each have a mic button, and Alt+M
  toggles it from the keyboard.
- Speech is transcribed in the browser by Cactus Whistle (16.9 MB, Apache-2.0)
  running as WebAssembly. No audio leaves the browser, and the aegis server never
  sees it.
- Text lands in the composer while you talk, about every 20 seconds, and the rest
  lands a few seconds after you stop. Nothing is sent: you review the text and send
  it yourself.
- Whistle is biased toward words aegis already knows: the vocabulary of agent work
  (agent, harness, issue, pull request, repo, …), the open sessions' handles, their
  working directories' names, and the agents and queues in `.aegis.yaml`.

This replaces the TUI-era voice input, which recorded from the server's
microphone through harp and so could never work from a browser (#89).

## What was measured

### The engine

Cactus publishes a browser build of its engine, `needle.js` and `needle.wasm`
(62 KB and 0.9 MB), in the `wasm/` folder of the `Cactus-Compute/needle3`
Hugging Face repo. The same engine loads `whistle.cact` (16.9 MB) from
`Cactus-Compute/whistle`. Both are Apache-2.0. Their own demo page runs it in a
3.4 KB Web Worker; the playground's worker is that file with the download-count
ping removed. The calls aegis needs are `createNeedle({wasmBinary})`,
`_needle_load(ptr, len)` and `_needle_transcribe(pcm, n, language, keywords, 0,
out, cap)`, which writes a JSON object with `text` and `language`.

The build is single-threaded with SIMD and uses no `SharedArrayBuffer`, so it
needs no cross-origin isolation headers, and aegis's client sends no
Content-Security-Policy that would block it. `needle.js` contains no hardcoded
URL. One worker's WASM heap is 16 MB empty, 39 MB with the model loaded and
47 MB after a 30-second clip.

### Accuracy, on Alex's own dictations

Ten dictations about dev work, 987 seconds in all, 55 to 163 seconds each, with
reference transcripts (`.playground/cactus-eval/data/alex_es`). WER uses a
simple normalizer (lower case, punctuation removed), so it reads higher than a
Whisper-normalizer figure would.

| Variant | WER |
|---|---|
| Cut at the quietest point between 20 and 30 s, language detected | 30.4% |
| Same, language forced to Spanish | 30.4% (detection chose Spanish every time) |
| Blind cuts every 30 s | 29.9% |
| Quietest-point cuts, 16 workspace names as keywords | 28.7% |
| Cut at the first 300 ms pause after 20 s, keywords | 27.7% |
| Cut at the first pause after 10 s, keywords | 30.9% |
| Cut at the first pause after 5 s, keywords | 32.3% |

The browser build matches native Whistle (the `cactus-needle` Python package)
within 0.35 points per clip on the eight clips both finished. Most errors are
English names inside Spanish ("clipper" became "clic", "landing page" became
"ending page"); a keyword fixes the ones it names. Shorter chunks cost accuracy,
so chunks stay long while you talk.

Keywords are cheap: one 20-second clip took 4.8 s with none and 5.1 to 5.5 s
with 16, 100 or 300. Whistle writes a keyword with the casing it was given.

### Speed, and the wait after stop

| | zion, desktop Chromium | Alex's phone, Android, Chrome 154, 8 cores |
|---|---|---|
| Seconds of compute per second of audio | 0.31 | 0.45 |
| Model download and load, first time, from Hugging Face | 190 s (zion's link) | 92 s |
| Load from the browser's cache | about 0.3 s | not measured |

Chunks are transcribed while you keep talking, so the only wait you see is the
audio after the last cut, transcribed once you press stop. On a 163-second
dictation in desktop Chromium that tail was 19.7 s and the wait was 8.0 s. On the
phone a 20-second tail costs about 9 s, and a 30-second one about 13.5 s.

Splitting the tail at its quietest point and transcribing the halves in two
workers at once took 2.8 s against 5.3 s for the whole 20 seconds on zion,
1.89 times faster. The halves lost a little context ("landing page" became
"ending page"), which the keyword list covers.

Firefox runs the same pipeline (capture at 48 kHz, model load, silence returns an
empty transcript in 22 ms). Its fake microphone plays only a tone, so its text
quality was not checked.

## Decisions

| Question | Decision | Why |
|---|---|---|
| Where transcription runs | In the browser, in Web Workers | Alex's call. No audio on the wire, nothing on the server per request, and it works through any proxy that serves the page |
| Engine | Cactus Whistle, browser build, pinned by Hugging Face revision and sha256 | 16.9 MB, Apache-2.0, the same WER as native, runs on the phone at 0.45 s per audio second |
| Chunking while you talk | Cut at the first 300 ms pause after 20 s; with no pause, at the quietest 200 ms between 20 and 30 s | 27.7% WER against 30.9% at 10 s. Whistle takes at most 30 s per pass |
| The tail at stop | Two workers. A tail over 10 s is split at its quietest point in the middle third and both halves run at once | The wait after stop is what you feel. 1.89 times faster on zion; the phone's 30-second worst case goes from about 13.5 s to about 7 s |
| Where the model comes from | The aegis server downloads it once from Hugging Face, checks its sha256, keeps it in a cache directory and serves it with an immutable cache header | The browser always reaches aegis, and on a restricted network (UH) it may not reach Hugging Face. Tests can point the server at a stub engine, so they stay hermetic. A wheel with the model in it would add 17.8 MB to every install |
| When the model loads | On the first mic press in a page load. Capture starts at once and chunks wait for the workers | Nobody waits to start talking. The first press after install waits for the download only at the end |
| What happens to the text | Inserted into the composer the recording started in, never sent | Alex's call. He reviews before Enter |
| Switching tabs while recording | Stops the recording. Text still in flight lands in the draft of the session it started in | The composer is one textarea whose value is swapped per session; text must not cross sessions |
| Keywords | The server returns them: a fixed vocabulary, open sessions' handles, their working directories' names, agent and queue names | "Python decides, the browser draws". Alex asked for the agent-work vocabulary. Nobody maintains a list by hand |
| Language | Detected per chunk | Alex dictates in Spanish and English. Detection chose correctly on every clip |
| Configuration | None. No `.aegis.yaml` key | The feature costs nothing until the button is pressed, and every value above was measured, not chosen per user |

## Design

### Server: `dictation.py` and one operation

`dictation.py` holds the pins: for `needle.js`, `needle.wasm` and `whistle.cact`,
the Hugging Face URL at a fixed revision and the sha256.

| File | Revision | sha256 |
|---|---|---|
| `needle.js` | `Cactus-Compute/needle3@c7c415a3d1b3d929014bc6e866d51ebb971f7089`, `wasm/` | `f3f7366dcad9555b792ee519d2518f3c506038bcb2ffd179e76e850000749359` |
| `needle.wasm` | same | `c19b9ddf9c7de4eb4f37e5f1811c5bbea9f099041d2a27284daf89789ee8523d` |
| `whistle.cact` | `Cactus-Compute/whistle@b358ddadd89b7a713b5aa131f23032d3cca1b251` | `b6e02f048568ac5d01a2042556c658061e699acbc0aa2a1439f52f3d461dffeb` |

The files live in a cache directory, `$XDG_CACHE_HOME/aegis/dictation/<pin>/`
by default, which the CLI passes to `App` as `dictation_dir`. `<pin>` is the
first 12 hex digits of a hash over the three sha256s, so a new pin is a new
directory and never a stale mix of files. A test passes its own directory.

`dictation.prepare` (a person's operation, not an agent's) makes sure the three
files are present, then returns `{"base": "/dictation/<pin>/", "keywords": [...]}`.
A missing file is downloaded with httpx inside `asyncio.wait_for`, because httpx's
`timeout=` is per operation and a slow stream would otherwise never end; it is
written to a temporary name, checked against its sha256 and renamed. A mismatch
deletes the temporary file and fails the operation with `dictation_unavailable`
and the file's name. Concurrent calls share one download. A directory that
already holds the files is served without a download, and without a hash check on
every call. Checking 17.8 MB on each press would cost more than the check is
worth for files aegis itself verified when it wrote them.

`/dictation/<pin>/<file>` serves the three files with `Cache-Control: public,
max-age=31536000, immutable`, which is safe because the path changes with the
pin. Any other name is a 404.

The keywords: the fixed vocabulary in `dictation.py` (agent, agents, harness,
issue, pull request, PR, repo, worktree, branch, commit, merge, CI, aegis,
Claude, Claude Code, OpenCode, MCP, monitor, queue, handoff, session, subagent,
spec, plan, transcript), then each open session's handle with hyphens read as
spaces, the last component of each open session's working directory, and the
names of the agents and queues in the config. Duplicates are dropped
case-insensitively and the list is capped at 300 phrases.

### Client: `js/dictation.js`, a worker and a worklet

Three files, plain ES modules like the rest of the client:

- `js/capture.worklet.js` mixes the mic to mono on the audio thread and posts
  128-frame blocks.
- `js/dictation.worker.js` imports `needle.js` from the prepared base, loads the
  model and answers `transcribe` messages with `{id, text, language, ms}`.
- `js/dictation.js` owns one recording at a time: capture, the resampler, the
  chunker, the two workers, ordering results, and inserting text.

**Capture.** `getUserMedia({audio: {channelCount: 1, echoCancellation: true,
noiseSuppression: true}})`, an `AudioContext` at the device's rate, the worklet,
and a streaming box-filter resampler to 16 kHz in the main thread. Asking the
`AudioContext` for 16 kHz directly is avoided, because Firefox has refused to
connect a microphone to a context at another rate.

**The chunker.** It holds the 16 kHz samples not yet sent. Once it holds 20 s,
a 300 ms window with RMS under 0.01 cuts at its middle. At 30 s with no pause it
cuts at the quietest 200 ms between 20 and 30 s. Each chunk goes to whichever
worker is idle, or to the one with the shorter queue.

**Stop.** The tail goes out at once. If it is longer than 10 s, it is cut at the
quietest 200 ms in its middle third and the halves go to the two workers. A tail
quieter than RMS 0.001 is dropped.

**Ordering and insertion.** Chunks carry increasing ids, and text is inserted in
id order, so a fast second half never lands before a slow first half. The
recording remembers the textarea and the offset where it started, inserts each
piece there with a space between pieces, and moves the offset past it. If the
person typed before the offset, the offset is clamped to the text's length. If
the recording's session is no longer the one shown, text goes into that
session's `aegis.draft.<log_id>` in `localStorage`, at its end.

**Workers.** Two, created on the first press in a page load and kept after it.
They fetch the three files from `base`; the immutable header makes every later
load a browser-cache hit. Until both report ready, chunks wait in the queue.

### The button and the key

`#mic` sits in the composer's `.acts` before `#send`, and in the new-tab
composer's box. It has four states, drawn from one `data-state` attribute:
`idle`, `loading` (workers not ready, capture already running), `listening`
(with the input level), and `finishing` (stopped, pieces in flight). A press in
`listening` stops; a press in `finishing` starts a new recording into the same
textarea. Under `(pointer: coarse)` it gets the 44 px target like every other
button.

Sending a box that a recording targets (Enter, the send button, or starting the
session from the new-tab box) first stops the recording and waits for its last
piece, so what was said goes out with the rest instead of landing in an emptied
box a few seconds later.

Alt+M is one row in `keys.js`: it toggles dictation into the focused composer,
or into the session composer when no text field has focus. Esc keeps its order
and does not stop a recording, because Esc interrupts the agent and the two
must not share a key.

A browser with no `navigator.mediaDevices`, which is any page on plain http
other than localhost, gets the button disabled with the reason in its title.

### Errors

Each error stops the recording, keeps what was already inserted, and shows its
sentence in the composer's existing `#send-error`:

- microphone permission refused or no device: "Microphone unavailable: <reason>";
- `dictation.prepare` failed: "Dictation model unavailable: <reason>", and the
  audio recorded so far is discarded, because nothing can transcribe it;
- a worker reports an error for a chunk: that chunk's text is skipped, and the
  error names its seconds.

## Testing

- **Server, hermetic.** `dictation.prepare` against a local HTTP server that
  serves fixture files: it downloads, checks the hash and returns the base; a
  wrong hash fails with `dictation_unavailable` and leaves no file; two
  concurrent calls make one download; a pre-filled directory makes none. The
  route serves the three names with the immutable header and 404s anything else.
  Keywords include the fixed vocabulary, a spawned session's handle with spaces,
  its directory name and the config's agent names, with no duplicates.
- **Browser, hermetic.** The test server's `dictation_dir` holds a stub
  `needle.js` that implements the six members the worker uses (`_malloc`,
  `_free`, `HEAPU8`, `UTF8ToString`, `_needle_load`, `_needle_transcribe`) and
  answers each chunk with its own length in seconds. Chromium runs with
  `--use-file-for-fake-audio-capture` on a generated WAV: speech-like noise with
  silences at known places. The test presses the mic, waits, presses again and
  asserts the cut points, the order of the inserted pieces, that a 25-second
  tail went to both workers, that switching tabs mid-recording put the late
  piece in the first session's draft, and that Alt+M toggles. It also asserts
  the button is disabled where `navigator.mediaDevices` is missing.
- **Live.** One `live` test loads the real model through the server from
  Hugging Face, plays a LibriSpeech test-clean clip (CC BY 4.0) through the fake
  microphone and asserts a WER under 15%. It runs with `make test-live`.
- **Not covered by tests.** The phone itself and a real voice. Both were checked
  in the playground and are rechecked by hand on `aegis serve` before the PR,
  then reported as checked by hand.

## Rules this adds to DESIGN.md

- Audio never leaves the browser. The server serves the dictation model and the
  keywords, and nothing else about dictation.

## Out of scope

- Dictation from a terminal, which aegis 2 does not have.
- A live preview of words as you speak, at the time. Alex asked for it the
  evening this shipped; it is designed in `2026-10-09-dictation-live-text-design.md`
  (#210) as a provisional lane that a final pass corrects.
- A per-user keyword list in `.aegis.yaml`. Add one when the derived list misses
  words that matter.
- Sending on stop, or voice commands.
- Issue #110 (`voice.preview` in the legacy config), which belongs to `legacy/`.
