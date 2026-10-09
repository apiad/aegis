# Dictation: text while you talk

**Status: designed, 2026-10-09.** Issue #210. Extends the dictation shipped in
2.3.0 (`2026-10-08-dictation-design.md`, #201), which put a live preview out of
scope. Alex tried 2.3.0 on zion the evening it shipped, saw nothing for the first
20 seconds and read it as the recording having stopped, and chose provisional
text that a final pass corrects over shorter chunks. The probe that produced the
numbers below is `.playground/whistle-web/probe.cjs` in the workspace.

## What this delivers

- Words appear in the composer a second or two after each pause in your speech,
  instead of once every 20 seconds. By the time you stop talking, nearly
  everything you said is on screen, and the rest lands within about two seconds.
- The quality of what stays is the quality of 2.3.0. Each 20-to-28-second stretch
  is transcribed again as one piece a few seconds after it closes, and that text
  replaces the provisional text of the stretch. The correction is visible, like
  live captions.
- No more `Thank you.` or `Vielen Dank.` between your Spanish sentences. A piece
  of audio with under a second of voice is not transcribed on its own.
- A recording that the browser ends on its own, because another app took the
  microphone or the audio context was suspended, says so under the composer
  instead of leaving the mic button lit while nothing is captured.

## What was measured

### Where the hallucinations come from

Whistle on pieces with little or no speech, zion, pinned browser build:

| Piece | Text |
|---|---|
| digital silence, 5 s and 20 s | empty, in 15 ms |
| white noise at RMS 0.001, 0.003 and 0.006, 5 s and 20 s | empty |
| 2 s of speech + 18 s of noise at RMS 0.002, either order | the words, correct |
| 2 s of speech alone | the words, correct |
| 1 s of Spanish speech | `This is...`, in English |
| 0.5 s of speech alone | `Hey.` |
| 0.5 s of speech + 4.5 s of noise | `Thank you.` |

The engine's own gate returns nothing for silence and noise. A piece with under a
second of voice is what hallucinates, and `Chunker.finish` in 2.3.0 makes such
pieces: the tail at stop is split at its quietest point, and a half that holds a
breath or the end of one word is transcribed alone. Its drop rule, whole-piece
RMS under 0.001, never fires on it.

On Alex's ten dictations, 200 ms frames over RMS 0.01 (the chunker's pause
threshold) cover 74% to 87% of each clip, and the 10th-percentile frame is at
RMS 0.0015 to 0.005. Voice sits well above 0.01 and pauses well below it, so the
same threshold separates speech frames from pause frames.

### What short pieces cost

Median of three on zion with nothing else running, pieces cut from one dictation:

| Piece | ms | s of CPU per s of audio |
|---|---|---|
| 1 s | 276 | 0.28 |
| 2 s | 445 | 0.22 |
| 4 s | 790 | 0.20 |
| 8 s | 1567 | 0.20 |
| 10 s | 2044 | 0.20 |
| 20 s | 5215 | 0.26 |
| 30 s | 9001 | 0.30 |

A call costs about 0.1 s whatever its length, and a piece of 4 to 10 s is the
cheapest per second of audio. A provisional piece of 4 s shows its words about
0.8 s after the pause that cut it, on zion.

### What provisional text is worth

Ten dictations, 987 s, cut at the first 300 ms pause after a minimum or at the
quietest 200 ms before a maximum. The keywords were 16 words of the aegis
vocabulary in `dictation.py`, not the 16 workspace names of #201, so the first row
reads 2.7 points above #201's 27.7% and only rows of this run compare:

| Pieces | WER | s of CPU per s of audio |
|---|---|---|
| 20 to 30 s, the 2.3.0 rule | 30.4% | 0.37 |
| 4 to 8 s, the provisional lane | 35.8% | 0.29 |
| 4 to 8 s joined up to the first boundary past 20 s, the final lane (20 to 27.2 s) | 29.4% | 0.36 |

Provisional text is 6.4 points worse than final text, and only the final text
stays. Final pieces made of provisional pieces are a point better than the 2.3.0
cut, so the two lanes cutting in the same places costs nothing. Together they cost
0.65 s of CPU per second of audio on zion. Alex's phone measured 0.45 against
zion's 0.31 in #201, so about 0.95 there, across two workers: each is busy about
half the time.

## Decisions

| Question | Decision | Why |
|---|---|---|
| Live text | Two lanes: provisional pieces of 4 to 8 s, and final pieces of 20 to 28 s that replace them | Alex's call, over shorter chunks alone. Words appear within about two seconds of a pause; what stays is as accurate as 2.3.0 |
| Where a final piece ends | At the first provisional boundary 20 s or more after the previous final cut | Provisional pieces are at most 8 s, so a final is 20 to 28 s, under Whistle's 30 s, and the two lanes never cut in different places |
| How provisional text looks | Plain text, no marker | A textarea cannot dim a span. The mic button already shows the recording is live, and the replacement a few seconds later is the signal that it was provisional |
| A final whose provisional text was edited | The edit stays; the final is dropped | Overwriting what the person typed would cost more than a missed correction |
| Pieces with under 1 s of voice | Not transcribed on their own. A provisional one is skipped; the final over the same audio still covers it. A final one is skipped too, unless it is the whole recording | The measured hallucinations are all pieces of this kind. A one-word recording is still transcribed, because dropping the whole recording would read as a failure |
| Queue order | Provisional before final | The provisional lane is what you watch. A final that waits 5 s is a correction that arrives later; a provisional that waits is a blank screen |
| The tail at stop | What is left goes out as one provisional piece first, then the tail since the last final cut goes out as a final, in two halves if longer than 10 s | The last words show about 1.5 s after stop. The final pass over the tail replaces them a few seconds later |
| A capture the browser ends | The recording stops, and the composer shows `Microphone stopped: <reason>` | In 2.3.0 the button stays lit and no text comes; the reason is lost |
| Configuration | None | Every value above was measured on Alex's dictations, as in #201 |

## Design

### The chunker: two lanes over one buffer

`Chunker` keeps the 16 kHz samples since the last final cut and the provisional
pieces emitted from them. Its `emit` receives one of two shapes:

- `{ audio, final: false }`: a provisional piece, 4 to 8 s;
- `{ audio, final: true }`: a final piece whose audio is the whole stretch since
  the previous final cut, including any provisional piece that was skipped by the
  voice gate. It replaces every provisional piece emitted since that cut.

Cutting a provisional piece follows the 2.3.0 rule with smaller numbers: once the
pending audio holds 4 s, a 300 ms window under RMS 0.01 cuts at its middle; at
8 s with no pause, the quietest 200 ms frame between 4 and 8 s. After each
provisional cut the chunker checks the stretch since the last final cut: at 20 s
or more, that boundary is also the final cut, and the stretch goes out as a
final.

**The voice gate.** `voiceSecs(a)` counts 200 ms frames, 50 ms apart, whose RMS
is over 0.01, and returns their count times 0.05. A provisional piece with under
1 s of voice is not emitted; its audio stays part of the stretch, so the final
pass hears it. A final piece with under 1 s of voice is not emitted either, and
no provisional piece of its stretch was, so nothing is replaced.

`finish(onlyPiece)` returns the pieces at stop, in order: the pending audio as a
provisional piece if it passes the gate, then the stretch as a final. A stretch
longer than 10 s is split at the quietest 200 ms of its middle third into two
final pieces that share one replacement, so a fast second half never lands before
a slow first half and the two texts replace the provisional text together. A
stretch with under 1 s of voice is dropped, unless `onlyPiece` is true, which
`Dictation` passes when nothing was emitted in this recording.

### Dictation: jobs, groups and replacement

`Dictation` assigns each piece an id and sends it to a worker as today. A final
piece, or the pair of halves at stop, forms a *group* that names the provisional
ids it replaces: every provisional id emitted since the previous group. The queue
holds provisional jobs ahead of final jobs; a worker that frees takes the first
provisional, else the first final.

Insertion keeps the 2.3.0 rule for provisional pieces, in id order, at the
recording's offset, with a space between pieces, and now records each inserted
piece's text and its range. When a group's pieces are all done, their texts are
joined with spaces and replace the span from the first covered piece's start to
the last covered piece's end, if the textarea still holds exactly the text that
was inserted there. The recording's offset moves by the difference in length when
it sat at or after the span. If the span no longer matches, the person edited it:
the group's text is dropped. A group that covers no provisional piece (every one
was skipped by the gate, or it is the one-word recording) inserts its text at the
offset like a provisional piece.

The same replacement applies to a draft in `localStorage` when the textarea has
moved to another session, on the draft string instead of the textarea value.

### Stopping, and why it stopped

`stop(reason)` takes one of `button`, `key`, `send`, `tab`, `track`, `suspended`.
`micSource` listens to the audio track's `ended` event and to the
`AudioContext`'s `statechange`; when the track ends or the context leaves
`running` during a recording, it calls the stop callback with `track` or
`suspended`, the recording finishes as if the button had been pressed, and
`onError` shows `Microphone stopped: <reason>`. The four requested reasons show
nothing. `app.js` passes the reason at each of its calls: the mic button, Alt+M,
`send` and `follow`.

### The button

Unchanged: `idle`, `loading`, `listening` with the level, `finishing`. The
provisional lane makes `listening` show text within seconds, which is what the
2.3.0 button lacked.

## Testing

- **Chunker, in the browser against the module** (`run_dictation` in
  `tests/test_browser.py`): a 22 s tone with a 0.5 s gap at 5 s emits a
  provisional piece at the gap, then provisional pieces every 8 s at quietest
  points, then a final covering the stretch at the first boundary past 20 s;
  0.5 s of tone in 4 s of silence emits no provisional piece but the final still
  spans it; `finish` returns the pending provisional piece then two final halves
  for a 16 s stretch, drops a stretch of 0.5 s of tone unless `onlyPiece`.
- **Insertion.** The stub engine answers each piece with its length, so a
  provisional `[4.2s]` and a final `[21.0s]` differ: the test asserts the
  provisional texts appear first and are replaced by the final text, that typing
  inside a provisional span keeps the typed text and drops the final, and that
  typing before the span leaves the final landing in the right place.
- **Queue order.** With both workers busy on finals (the stub takes 400 ms for a
  piece of 12 s or more), a provisional piece enqueued after a final piece is
  transcribed before it.
- **Stop reasons.** A `source` whose returned track emits `ended` stops the
  recording and shows `Microphone stopped: track`; a button stop shows nothing.
- **Live** (`make test-live`). Unchanged: the LibriSpeech clip's final text keeps
  WER under 15%.
- **By hand**, on `aegis serve` from the branch: Alex dictates on zion and on his
  phone and reports the time to first words, the correction, and the absence of
  `Thank you.`. Reported as checked by hand in the PR.

## Changes to other documents

- `2026-10-08-dictation-design.md`: the out-of-scope line on a live preview points
  here.
- `DESIGN.md`, "Audio never leaves the browser": the paragraph describes two
  lanes, provisional and final, in the same two workers.

## Out of scope

- A visual mark on provisional text. A textarea cannot style a span; an overlay
  that mirrors the textarea is a project of its own, and nobody asked for it.
- A configurable piece length or voice threshold. Every value was measured.
- Sending on stop, voice commands, a per-user keyword list (as in #201).
