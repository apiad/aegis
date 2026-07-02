# Push-to-talk voice input for aegis (via harp)

**Date:** 2026-07-02
**Status:** design — approved for planning
**Repo:** aegis (harp consumed as a library, unchanged)

## Goal

Add first-class, **optional** push-to-talk dictation to the aegis TUI. Pressing
a hotkey starts local speech-to-text; the transcription streams **into the pane's
message input** where the user edits and sends it. The engine is
[harp](https://github.com/apiad/harp), consumed as a library. Off by default;
enabled per-project in `.aegis.yaml`; degrades silently when the optional
dependency is absent.

## Non-goals

- **No physical hold-to-talk.** A terminal only sees key *presses* (with
  auto-repeat), never key *releases*, so a literal "hold key while speaking" is
  undetectable from inside the TUI. We ship **toggle-to-talk** instead. A future
  global-evdev variant (like harp's own `harp start` daemon) is out of scope.
- **No auto-submit.** Transcription only *populates* the input; the user always
  presses Enter to send. Voice is dictation, not command execution.
- **No web-client support.** Mic capture is host-local and runs in the TUI
  process. Browser-side capture (MediaRecorder → WS → a server-side
  `WebSocketAudioSource`) is a separate future design.
- **No changes to harp.** harp's library API already provides everything needed.

## Interaction model

- **`ctrl+g`** (configurable) toggles recording. Press once to start on the
  currently-focused pane; press again — **from any tab** — to stop the live
  session. One microphone → **one recording at a time** across the whole app.
- On start, the session **binds to the exact `GrowingInput` it started on** and
  streams into that widget for the entire recording. Blurring the input or
  switching tabs does **not** move the stream — it stays anchored to the origin
  input. Focus is irrelevant after start.
- While recording, the origin pane shows a subtle recording indicator (e.g. a
  `🎙 rec` marker near its input); stopping clears it.
- Transcribed text is **appended** to whatever is already in the origin input
  (respecting the existing cursor/content), so a user can type, dictate, type
  more. Committed text is inserted; the transient hypothesis (preview mode) is
  shown as a trailing tentative segment that is replaced as it firms up.

## Architecture

A single new subpackage, `src/aegis/voice/`, owns the mic→transcript lifecycle
behind a small interface the TUI consumes. The TUI wiring stays thin.

### `src/aegis/voice/session.py` — `VoiceSession`

Wraps a `harp.HarpSession` and runs it on a background thread.

- Constructed with a resolved `VoiceConfig` and two callbacks:
  - `on_update(committed: str, transient: str)` — called on every
    `TranscriptEvent` while recording.
  - `on_final(text: str)` — called once when the session ends.
- `start()` builds `HarpSession(audio=MicrophoneSource(), transcribe=engine,
  detector=SileroDetector(), transient=cfg.preview, language=cfg.language)` and
  spawns a thread that iterates `session.events()`, invoking `on_update` per
  event and `on_final` at the end.
- `stop()` calls `HarpSession.stop()` (thread-safe per harp's API) and joins the
  thread. Idempotent.
- The whisper engine (`LocalWhisperEngine(model_size=cfg.model,
  compute_type="int8", beam_size=1)`) is built lazily on first `start()` and
  cached for the process lifetime (model load is the slow part; reuse it).
- Pure of Textual — takes plain callbacks, so it is unit-testable with a fake
  `HarpSession`/engine and no model load.

### `src/aegis/voice/availability.py` — feature detection

- `voice_available() -> bool` — attempts `import harp` and checks
  `sounddevice` importability without opening a stream. Cached.
- `unavailable_reason() -> str` — human-readable ("harp not installed —
  `pip install aegis[voice]`") for the hint shown when the user presses the key
  with the feature enabled but deps missing.

### `src/aegis/voice/config.py` — `VoiceConfig`

Dataclass mirroring the `.aegis.yaml` `voice:` block, with defaults:

```python
@dataclass(frozen=True)
class VoiceConfig:
    enabled: bool = False
    model: str = "base"          # harp's recommended real-time floor
    key: str = "ctrl+g"          # Textual binding string
    preview: bool = False        # transient live-word preview (~2-4x cost)
    language: str | None = None  # None -> autodetect
```

Parsed in `aegis.config.yaml_loader` alongside the other top-level sections
(`agents`, `queues`, `telegram`, …) and exposed on the loaded config object.
Absent block → `VoiceConfig()` (disabled).

### TUI wiring

- **App level (`app.py`):** when `voice.enabled`, register a
  `Binding(cfg.key, "toggle_voice", "Voice", priority=True)`. `action_toggle_voice`
  is the single entry point:
  - If a `VoiceSession` is live → `stop()` it, clear the indicator, done.
  - Else if `not voice_available()` → surface `unavailable_reason()` as a
    transient status/notification; do nothing else.
  - Else → resolve the **currently-focused pane's `GrowingInput`**, construct a
    `VoiceSession` whose callbacks marshal back onto that specific widget via
    `App.call_from_thread`, `start()` it, show the indicator on the origin pane.
  The app holds at most one `VoiceSession` reference (`self._voice`), enforcing
  single-recording.
- **Streaming into the origin input:** the `on_update` callback (run via
  `call_from_thread`) updates the bound `GrowingInput`. It tracks how much
  committed text it has already inserted and appends only the delta; the
  transient tail is rendered after the committed text and replaced on each
  update. On `on_final`, the transient tail is committed and the indicator
  cleared. The input widget already exposes `value`/text mutation and auto-resize.
- The origin `GrowingInput`/pane reference is captured at start; tab switches and
  focus changes never re-point it.

## Data flow

```
ctrl+g ──► app.action_toggle_voice
             │ (start)
             ├─ resolve focused pane's GrowingInput  ── bind ──┐
             └─ VoiceSession.start()                           │
                   └─ thread: HarpSession.events() ── per event ┤
                        on_update(committed, transient) ──call_from_thread──►
                                                    origin GrowingInput (append delta + transient tail)
ctrl+g ──► app.action_toggle_voice (stop) ──► VoiceSession.stop() ──► on_final ──► commit tail, clear indicator
```

## Packaging

New optional extra in `pyproject.toml`:

```toml
[project.optional-dependencies]
voice = [
    "harpio>=0.9.0",
    "sounddevice>=0.5.5",
]
```

Note we depend on **base `harpio` + `sounddevice`**, not `harpio[cli]`. Base
`harpio` (faster-whisper + numpy) already provides `HarpSession`,
`LocalWhisperEngine`, and `SileroDetector`; the only missing piece for mic
capture is `sounddevice` (PortAudio), the one relevant item in harp's `cli`
extra. We deliberately avoid `evdev`/`pynput`/`python-uinput`/`typer`/`rich`,
which are Wayland-daemon machinery aegis does not use.

**System requirement:** `sounddevice` needs the PortAudio shared library
(`libportaudio2`) present on the host — documented in install docs, not a pip
dependency.

## Error handling

- **Deps missing but `voice.enabled`:** the binding is registered but pressing it
  shows `unavailable_reason()` and no-ops. aegis never imports harp at module
  load — only inside `voice_available()` and `VoiceSession.start()`.
- **Mic open failure** (`sounddevice`/PortAudio raises at stream open): caught in
  `VoiceSession.start()`; surfaced as a one-line status; no session is retained;
  indicator not shown.
- **Model download/load failure:** first `start()` may block while
  faster-whisper fetches the model; failures propagate to the same status-hint
  path and leave the app in a clean (no-recording) state.
- **App teardown while recording:** `on_unmount`/quit path calls
  `VoiceSession.stop()` to join the thread cleanly.

## Testing

- `VoiceSession` with a fake `HarpSession` (yields scripted `TranscriptEvent`s)
  and a fake engine — assert `on_update`/`on_final` callback sequence, delta
  bookkeeping, `stop()` idempotency and thread join. No model, no mic.
- `availability` with monkeypatched import machinery — present/absent branches.
- Config parsing: `voice:` block present/absent/partial → correct `VoiceConfig`.
- App action: focused-pane resolution, single-session enforcement (second
  `ctrl+g` stops rather than starts), unavailable-deps hint path — driven with
  Textual's test harness and a stubbed `VoiceSession`.
- Streaming-into-input: given a scripted committed/transient sequence, assert the
  bound `GrowingInput.value` evolves correctly (append-only committed, replaced
  transient tail) and stays anchored across a simulated tab switch.

Follows the repo convention: TDD, `uv run pytest`, lint as its own step
(`uv run ruff check src tests`), commit straight to `main`.

## Config example

```yaml
# .aegis.yaml
voice:
  enabled: true
  model: base        # tiny | base | small | medium | large-v3
  key: ctrl+g
  preview: false     # true = live word-by-word (costs ~2-4x, may lag on CPU)
  language: null     # e.g. "en", "es"; null autodetects
```
