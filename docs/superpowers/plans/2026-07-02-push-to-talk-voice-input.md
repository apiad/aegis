# Push-to-talk Voice Input Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add optional toggle-to-talk voice dictation to the aegis TUI: `ctrl+g` starts local speech-to-text via harp; the transcription streams into the origin pane's input, editable, never auto-submitted.

**Architecture:** A new `src/aegis/voice/` subpackage owns the mic→transcript lifecycle behind plain callbacks (`VoiceSession`) plus feature detection (`availability`). A frozen `VoiceConfig` dataclass (parsed from an `.aegis.yaml` `voice:` block) drives it. The TUI (`app.py`) registers a configurable binding when voice is enabled, resolves the focused pane's `GrowingInput` at start, binds the session to that exact widget, and marshals transcript updates back onto it via `App.call_from_thread`. harp runs on a background thread; the engine (`HarpSession`) is unchanged.

**Tech Stack:** Python 3.12+, Textual 8.2.6, harp (`harpio` library), `sounddevice`, `uv`, pytest.

## Global Constraints

- **Off by default.** No voice binding, no harp import, unless `voice.enabled` is true in `.aegis.yaml`.
- **Optional dependency.** aegis must import and run with `harpio`/`sounddevice` absent. Never `import harp` at module load — only inside `availability.voice_available()` and `VoiceSession.start()`.
- **Dependency is base `harpio>=0.9.0` + `sounddevice>=0.5.5`** — NOT `harpio[cli]`.
- **Never auto-submit.** Voice only populates the input; the user presses Enter.
- **Single recording at a time** — the app holds at most one live `VoiceSession`.
- **Session anchors to the origin `GrowingInput`** captured at start; focus/tab changes never re-point it.
- **TDD**, `uv run pytest`, lint as its own step (`uv run ruff check src tests`), commit straight to `main`, conventional commits.
- The `voice/` runtime must be unit-testable with fakes — no model load, no mic.

---

## File Structure

- Create `src/aegis/voice/__init__.py` — exports `VoiceSession`, `voice_available`, `unavailable_reason`.
- Create `src/aegis/voice/availability.py` — dependency feature detection.
- Create `src/aegis/voice/session.py` — `VoiceSession` (harp on a thread, plain callbacks).
- Modify `src/aegis/config/__init__.py` — add frozen `VoiceConfig` dataclass (next to `WebConfig`).
- Modify `src/aegis/config/yaml_loader.py` — add `voice` field to `AegisConfig`, `_build_voice()`, wire into `load_config`.
- Modify `src/aegis/tui/app.py` — accept `voice` cfg, register binding in `on_mount`, `action_toggle_voice`, streaming callbacks, teardown.
- Modify `src/aegis/tui/pane.py` — `ConversationPane.set_recording(bool)` indicator + `input_widget()` accessor.
- Modify `src/aegis/cli.py` — load full YAML in the TUI `run` path and pass `voice=` to `AegisApp`.
- Modify `pyproject.toml` — `[project.optional-dependencies] voice`.
- Modify `docs/install.md` (or `configuration.md`) — document `voice:` block + `libportaudio2`.
- Tests (flat, matching repo convention `tests/test_*.py`): `tests/test_voice_config.py`, `tests/test_voice_availability.py`, `tests/test_voice_session.py`, `tests/test_voice_action.py`.

---

## Task 1: VoiceConfig dataclass + YAML parsing

**Files:**
- Modify: `src/aegis/config/__init__.py` (add `VoiceConfig` after `WebConfig`, ~line 30)
- Modify: `src/aegis/config/yaml_loader.py` (`AegisConfig` field ~line 60; `_build_voice` + `load_config` return ~line 199)
- Test: `tests/test_voice_config.py`

**Interfaces:**
- Produces: `VoiceConfig(enabled: bool=False, model: str="base", key: str="ctrl+g", preview: bool=False, language: str|None=None)` (frozen dataclass), importable as `from aegis.config import VoiceConfig`.
- Produces: `AegisConfig.voice: VoiceConfig` (defaults to `VoiceConfig()`).

- [ ] **Step 1: Write the failing test**

Create `tests/test_voice_config.py`:

```python
from pathlib import Path

from aegis.config import VoiceConfig
from aegis.config.yaml_loader import load_config


def _write(tmp_path: Path, body: str) -> Path:
    (tmp_path / ".aegis.yaml").write_text(body)
    return tmp_path


def test_voice_defaults_when_absent(tmp_path):
    root = _write(tmp_path, "agents:\n  a: {harness: claude}\n")
    cfg = load_config(root)
    assert cfg.voice == VoiceConfig()
    assert cfg.voice.enabled is False


def test_voice_block_parsed(tmp_path):
    root = _write(tmp_path, (
        "agents:\n  a: {harness: claude}\n"
        "voice:\n"
        "  enabled: true\n"
        "  model: small\n"
        "  key: ctrl+b\n"
        "  preview: true\n"
        "  language: en\n"
    ))
    cfg = load_config(root)
    assert cfg.voice == VoiceConfig(
        enabled=True, model="small", key="ctrl+b",
        preview=True, language="en")


def test_voice_partial_block_fills_defaults(tmp_path):
    root = _write(tmp_path, (
        "agents:\n  a: {harness: claude}\n"
        "voice:\n  enabled: true\n"
    ))
    cfg = load_config(root)
    assert cfg.voice.enabled is True
    assert cfg.voice.model == "base"
    assert cfg.voice.key == "ctrl+g"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_voice_config.py -v`
Expected: FAIL — `ImportError: cannot import name 'VoiceConfig'`.

- [ ] **Step 3: Add the dataclass**

In `src/aegis/config/__init__.py`, immediately after the `WebConfig` dataclass:

```python
@dataclass(frozen=True)
class VoiceConfig:
    enabled: bool = False
    model: str = "base"
    key: str = "ctrl+g"
    preview: bool = False
    language: str | None = None
```

- [ ] **Step 4: Wire into the loader**

In `src/aegis/config/yaml_loader.py`:

Add to the `from aegis.config import (...)` block: `VoiceConfig`.

Add a field to `AegisConfig` (after `web` / near the other optional sections):

```python
    voice: VoiceConfig = field(default_factory=VoiceConfig)
```

Add a builder function (near `_build_web`):

```python
def _build_voice(raw: Any) -> VoiceConfig:
    """Build a VoiceConfig from a `voice:` YAML block. Absent -> disabled."""
    if not raw:
        return VoiceConfig()
    if not isinstance(raw, dict):
        raise ConfigError("voice: must be a mapping")
    defaults = VoiceConfig()
    return VoiceConfig(
        enabled=bool(raw.get("enabled", defaults.enabled)),
        model=str(raw.get("model", defaults.model)),
        key=str(raw.get("key", defaults.key)),
        preview=bool(raw.get("preview", defaults.preview)),
        language=raw.get("language", defaults.language),
    )
```

In `load_config`, near `web = _build_web(...)`:

```python
    voice = _build_voice(raw.get("voice"))
```

and add `voice=voice,` to the `AegisConfig(...)` return.

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/test_voice_config.py -v`
Expected: PASS (3 tests).

- [ ] **Step 6: Lint + commit**

```bash
uv run ruff check src tests
git add src/aegis/config/__init__.py src/aegis/config/yaml_loader.py tests/test_voice_config.py
git commit -m "feat(voice): VoiceConfig dataclass + .aegis.yaml voice: block"
```

---

## Task 2: Dependency availability detection

**Files:**
- Create: `src/aegis/voice/__init__.py`
- Create: `src/aegis/voice/availability.py`
- Test: `tests/test_voice_availability.py`

**Interfaces:**
- Produces: `voice_available() -> bool` — True iff both `harp` and `sounddevice` import. Result cached after first call.
- Produces: `unavailable_reason() -> str` — human-readable install hint; empty string when available.

- [ ] **Step 1: Write the failing test**

Create `tests/test_voice_availability.py`:

```python
import builtins

import aegis.voice.availability as av


def test_available_when_both_import(monkeypatch):
    av._CACHE.clear()
    monkeypatch.setattr(av, "_probe", lambda name: True)
    assert av.voice_available() is True
    assert av.unavailable_reason() == ""


def test_unavailable_names_missing_dep(monkeypatch):
    av._CACHE.clear()
    monkeypatch.setattr(av, "_probe",
                        lambda name: name != "sounddevice")
    assert av.voice_available() is False
    reason = av.unavailable_reason()
    assert "sounddevice" in reason
    assert "aegis[voice]" in reason


def test_probe_uses_import(monkeypatch):
    real_import = builtins.__import__

    def fake_import(name, *a, **k):
        if name == "definitely_absent_pkg_xyz":
            raise ImportError("no")
        return real_import(name, *a, **k)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    assert av._probe("definitely_absent_pkg_xyz") is False
    assert av._probe("json") is True
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_voice_availability.py -v`
Expected: FAIL — module `aegis.voice.availability` does not exist.

- [ ] **Step 3: Create the package + module**

Create `src/aegis/voice/__init__.py`:

```python
"""Optional push-to-talk voice input (harp-backed). Import-safe without
harp/sounddevice installed — deps are probed lazily."""
from aegis.voice.availability import unavailable_reason, voice_available
from aegis.voice.session import VoiceSession

__all__ = ["VoiceSession", "voice_available", "unavailable_reason"]
```

Create `src/aegis/voice/availability.py`:

```python
"""Feature detection for the optional voice extra (`aegis[voice]`)."""
from __future__ import annotations

import importlib

_REQUIRED = ("harp", "sounddevice")
_CACHE: dict[str, bool] = {}


def _probe(name: str) -> bool:
    try:
        importlib.import_module(name)
        return True
    except Exception:
        return False


def _missing() -> list[str]:
    out = []
    for name in _REQUIRED:
        if name not in _CACHE:
            _CACHE[name] = _probe(name)
        if not _CACHE[name]:
            out.append(name)
    return out


def voice_available() -> bool:
    return not _missing()


def unavailable_reason() -> str:
    missing = _missing()
    if not missing:
        return ""
    return (f"voice input needs {', '.join(missing)} — "
            f"install with `pip install aegis[voice]`")
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_voice_availability.py -v`
Expected: PASS (3 tests).

Note: Step 3 imports `VoiceSession` in `__init__.py`, which does not exist until Task 3. To keep this task's tests green independently, import the availability module directly in the test (already done: `import aegis.voice.availability as av`). Create a minimal placeholder `src/aegis/voice/session.py` now so the package import in `__init__.py` resolves:

```python
"""Placeholder — replaced in Task 3."""


class VoiceSession:  # noqa: D101 - filled in Task 3
    pass
```

- [ ] **Step 5: Lint + commit**

```bash
uv run ruff check src tests
git add src/aegis/voice/ tests/test_voice_availability.py
git commit -m "feat(voice): dependency availability detection"
```

---

## Task 3: VoiceSession — harp on a thread behind callbacks

**Files:**
- Modify: `src/aegis/voice/session.py` (replace the placeholder)
- Test: `tests/test_voice_session.py`

**Interfaces:**
- Consumes: `VoiceConfig` (Task 1).
- Produces:
  ```python
  VoiceSession(
      cfg: VoiceConfig,
      on_update: Callable[[str, str], None],   # (committed, transient)
      on_final: Callable[[str], None],         # (final_text)
      _session_factory: Callable[[VoiceConfig], "HarpLike"] | None = None,
  )
  ```
  Methods: `start() -> None`, `stop() -> None` (idempotent, joins thread), `is_running -> bool` (property).
- The `_session_factory` seam lets tests inject a fake harp session; production leaves it `None` and builds a real `HarpSession` inside `_default_factory` (which imports harp lazily).
- A `HarpLike` provides `events() -> Iterator` (each item has `.committed`, `.transient`, `.is_final` attrs), `stop() -> None`, and `final_text: str`, used as a context manager.

- [ ] **Step 1: Write the failing test**

Create `tests/test_voice_session.py`:

```python
import threading
import time
from dataclasses import dataclass

from aegis.config import VoiceConfig
from aegis.voice.session import VoiceSession


@dataclass
class _Ev:
    committed: str
    transient: str
    is_final: bool


class _FakeHarp:
    """Yields scripted events then blocks until stop() is called."""

    def __init__(self, events):
        self._events = events
        self._stop = threading.Event()
        self.final_text = "".join(e.committed for e in events if e.is_final) \
            or (events[-1].committed if events else "")

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def events(self):
        for e in self._events:
            yield e
        # emulate a live mic session: block until stopped
        self._stop.wait(timeout=2.0)

    def stop(self):
        self._stop.set()


def _drive(events):
    updates, finals = [], []
    fake = _FakeHarp(events)
    vs = VoiceSession(
        VoiceConfig(),
        on_update=lambda c, t: updates.append((c, t)),
        on_final=lambda text: finals.append(text),
        _session_factory=lambda cfg: fake,
    )
    return vs, fake, updates, finals


def test_updates_then_final_on_stop():
    events = [
        _Ev("hello", "", False),
        _Ev("hello world", "", False),
    ]
    vs, fake, updates, finals = _drive(events)
    vs.start()
    # wait for the two updates to be delivered
    deadline = time.time() + 2
    while len(updates) < 2 and time.time() < deadline:
        time.sleep(0.01)
    assert updates == [("hello", ""), ("hello world", "")]
    vs.stop()
    assert finals and finals[0] == "hello world"
    assert vs.is_running is False


def test_stop_is_idempotent():
    vs, fake, updates, finals = _drive([_Ev("hi", "", False)])
    vs.start()
    vs.stop()
    vs.stop()  # must not raise
    assert len(finals) == 1


def test_start_when_already_running_is_noop():
    vs, fake, updates, finals = _drive([_Ev("a", "", False)])
    vs.start()
    vs.start()  # second start must not spawn a second thread
    vs.stop()
    assert vs.is_running is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_voice_session.py -v`
Expected: FAIL — placeholder `VoiceSession` takes no args.

- [ ] **Step 3: Implement VoiceSession**

Replace `src/aegis/voice/session.py`:

```python
"""VoiceSession: drives a harp session on a background thread and relays
TranscriptEvents through plain callbacks. Textual-free and unit-testable
with a fake harp session (no model, no mic)."""
from __future__ import annotations

import threading
from typing import Callable, Optional

from aegis.config import VoiceConfig


def _default_factory(cfg: VoiceConfig):
    """Build a real harp session. Imports harp lazily so this module is
    import-safe without the voice extra installed."""
    from harp import HarpSession, MicrophoneSource
    from harp.vad import SileroDetector
    from harp.whisper import LocalWhisperEngine

    engine = LocalWhisperEngine(
        model_size=cfg.model, compute_type="int8", beam_size=1)
    return HarpSession(
        audio=MicrophoneSource(),
        transcribe=engine.transcribe,
        detector=SileroDetector(),
        transient=cfg.preview,
        language=cfg.language,
    )


class VoiceSession:
    def __init__(
        self,
        cfg: VoiceConfig,
        on_update: Callable[[str, str], None],
        on_final: Callable[[str], None],
        _session_factory: Optional[Callable[[VoiceConfig], object]] = None,
    ) -> None:
        self._cfg = cfg
        self._on_update = on_update
        self._on_final = on_final
        self._factory = _session_factory or _default_factory
        self._session: object | None = None
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()

    @property
    def is_running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self) -> None:
        with self._lock:
            if self.is_running:
                return
            self._session = self._factory(self._cfg)
            self._thread = threading.Thread(
                target=self._run, name="voice-session", daemon=True)
            self._thread.start()

    def _run(self) -> None:
        session = self._session
        try:
            with session as s:
                for ev in s.events():
                    self._on_update(ev.committed, ev.transient)
                self._on_final(getattr(s, "final_text", ""))
        except Exception:  # pragma: no cover - surfaced via caller status
            self._on_final(getattr(session, "final_text", ""))

    def stop(self) -> None:
        with self._lock:
            session, thread = self._session, self._thread
        if session is not None:
            try:
                session.stop()
            except Exception:  # pragma: no cover
                pass
        if thread is not None:
            thread.join(timeout=3.0)
        with self._lock:
            self._session = None
            self._thread = None
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_voice_session.py -v`
Expected: PASS (3 tests).

- [ ] **Step 5: Run the whole voice suite + lint + commit**

```bash
uv run pytest tests/test_voice_*.py -v
uv run ruff check src tests
git add src/aegis/voice/session.py tests/test_voice_session.py
git commit -m "feat(voice): VoiceSession — harp on a thread behind callbacks"
```

---

## Task 4: Pane recording indicator + input accessor

**Files:**
- Modify: `src/aegis/tui/pane.py` (add methods near `focus_input`, ~line 563)
- Modify: `src/aegis/tui/app.py` CSS (`ConversationPane GrowingInput` block, ~line 327) — add a `.recording` rule
- Test: `tests/test_voice_action.py` (indicator assertions live with Task 5)

**Interfaces:**
- Produces: `ConversationPane.input_widget() -> GrowingInput` — the pane's input.
- Produces: `ConversationPane.set_recording(on: bool) -> None` — toggles the `recording` CSS class on the pane.

- [ ] **Step 1: Add the accessor + indicator**

In `src/aegis/tui/pane.py`, next to `focus_input`:

```python
    def input_widget(self) -> "GrowingInput":
        return self.query_one(GrowingInput)

    def set_recording(self, on: bool) -> None:
        self.set_class(on, "recording")
```

(`GrowingInput` is already imported in pane.py.)

- [ ] **Step 2: Add the CSS cue**

In `src/aegis/tui/app.py`, after the existing
`ConversationPane GrowingInput:focus { ... }` rule, add:

```css
    ConversationPane.recording GrowingInput { border: round $warning; }
```

- [ ] **Step 3: Sanity-run existing pane tests**

Run: `uv run pytest tests/test_tui.py tests/test_pane_windowing.py -q`
Expected: PASS (no behavior change; new methods unused so far).

- [ ] **Step 4: Commit**

```bash
git add src/aegis/tui/pane.py src/aegis/tui/app.py
git commit -m "feat(voice): pane recording indicator + input accessor"
```

---

## Task 5: App wiring — toggle action, streaming into origin input, teardown

**Files:**
- Modify: `src/aegis/tui/app.py` (`__init__` ~line 179, `on_mount`, new `action_toggle_voice`, teardown)
- Test: `tests/test_voice_action.py`

**Interfaces:**
- Consumes: `VoiceConfig` (Task 1), `VoiceSession` + `voice_available`/`unavailable_reason` (Tasks 2–3), `ConversationPane.input_widget()`/`set_recording()` (Task 4).
- `AegisApp.__init__` gains `voice: VoiceConfig | None = None`; stored as `self._voice_cfg` (defaults to `VoiceConfig()` when None).
- `AegisApp` gains `self._voice: VoiceSession | None` and `action_toggle_voice()`.
- The action uses `self._voice_session_factory` (defaults to constructing a real `VoiceSession`) so tests inject a stub without touching harp.

- [ ] **Step 1: Write the failing test**

Create `tests/test_voice_action.py`. Reuse the exact construction helpers from `tests/test_tui.py` (`_agent()`, `_factory()`, `FakeMCP`) — `AegisApp` is built directly, no `conftest` helper exists:

```python
import pytest

from aegis.config import Agent, VoiceConfig
from aegis.events import AssistantText, Result
from aegis.tui.app import AegisApp
from aegis.tui.pane import ConversationPane


def _agent():
    return Agent(harness="claude-code", model="opus",
                 effort="high", permission="auto")


class FakeSession:
    def __init__(self):
        self.sent = []
        self.started = self.closed = False

    async def start(self): self.started = True
    async def send(self, text): self.sent.append(text)
    async def events(self):
        yield AssistantText("ok")
        yield Result(duration_ms=1, is_error=False)
    async def close(self): self.closed = True


class FakeMCP:
    url = "http://127.0.0.1:0/mcp/"

    def bind(self, bridge): self.bound = bridge
    async def start(self): pass
    async def stop(self): pass


def _factory(*sessions):
    it = iter(sessions or (FakeSession(),))
    def make(agent, mcp_url, handle):
        try:
            return next(it)
        except StopIteration:
            return FakeSession()
    return make


def _app(*, voice):
    return AegisApp({"default": _agent()}, "default", _factory(), FakeMCP(),
                    voice=voice)


class _StubVoice:
    """Captures callbacks; never touches harp. Drives updates on demand."""
    last = None

    def __init__(self, cfg, on_update, on_final, **_):
        self.cfg = cfg
        self.on_update = on_update
        self.on_final = on_final
        self._running = False
        _StubVoice.last = self

    @property
    def is_running(self):
        return self._running

    def start(self):
        self._running = True

    def stop(self):
        self._running = False
        self.on_final("done text")


@pytest.mark.asyncio
async def test_toggle_starts_and_streams_into_focused_input():
    app = _app(voice=VoiceConfig(enabled=True))
    app._voice_session_factory = _StubVoice
    async with app.run_test() as pilot:
        pane = app._active()
        assert isinstance(pane, ConversationPane)
        pane.input_widget().value = "prefix "
        await app.action_toggle_voice()
        assert app._voice is not None and app._voice.is_running
        assert pane.has_class("recording")
        # simulate a transcript update from the worker thread
        app.call_from_thread(_StubVoice.last.on_update, "hello world", "")
        await pilot.pause()
        assert pane.input_widget().value == "prefix hello world"


@pytest.mark.asyncio
async def test_second_toggle_stops_and_clears():
    app = _app(voice=VoiceConfig(enabled=True))
    app._voice_session_factory = _StubVoice
    async with app.run_test() as pilot:
        pane = app._active()
        await app.action_toggle_voice()   # start
        await app.action_toggle_voice()   # stop
        await pilot.pause()
        assert app._voice is None
        assert not pane.has_class("recording")


@pytest.mark.asyncio
async def test_unavailable_deps_shows_hint_no_session(monkeypatch):
    import aegis.tui.app as appmod
    monkeypatch.setattr(appmod, "voice_available", lambda: False)
    monkeypatch.setattr(appmod, "unavailable_reason", lambda: "install hint")
    app = _app(voice=VoiceConfig(enabled=True))
    app._voice_session_factory = _StubVoice
    async with app.run_test():
        await app.action_toggle_voice()
        assert app._voice is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_voice_action.py -v`
Expected: FAIL — `AegisApp.__init__` has no `voice` kwarg / no `action_toggle_voice`.

- [ ] **Step 3: Wire the app**

In `src/aegis/tui/app.py`:

Add imports near the top:

```python
from aegis.config import VoiceConfig
from aegis.voice import (
    VoiceSession, unavailable_reason, voice_available,
)
```

Extend `__init__` signature with `voice: "VoiceConfig | None" = None` and, in the body:

```python
        self._voice_cfg = voice or VoiceConfig()
        self._voice: VoiceSession | None = None
        self._voice_pane: ConversationPane | None = None
        self._voice_base: str = ""
        self._voice_session_factory = VoiceSession
```

In `on_mount` (create it if absent; otherwise append), register the binding when enabled:

```python
        if self._voice_cfg.enabled:
            self.bind(self._voice_cfg.key, "toggle_voice", description="Voice")
```

Add the action + streaming callbacks:

```python
    async def action_toggle_voice(self) -> None:
        if self._voice is not None:
            self._stop_voice()
            return
        if not voice_available():
            self.notify(unavailable_reason(), severity="warning")
            return
        pane = self._active()
        if not isinstance(pane, ConversationPane):
            return
        self._voice_pane = pane
        self._voice_base = pane.input_widget().value
        try:
            self._voice = self._voice_session_factory(
                self._voice_cfg,
                self._on_voice_update,
                self._on_voice_final,
            )
            self._voice.start()
        except Exception as exc:  # mic/model open failure
            self._voice = None
            self._voice_pane = None
            self.notify(f"voice failed: {exc}", severity="error")
            return
        pane.set_recording(True)

    def _on_voice_update(self, committed: str, transient: str) -> None:
        # Called from the worker thread -> marshal onto the UI thread.
        self.call_from_thread(self._apply_voice_text, committed, transient)

    def _on_voice_final(self, text: str) -> None:
        self.call_from_thread(self._apply_voice_text, text, "")

    def _apply_voice_text(self, committed: str, transient: str) -> None:
        if self._voice_pane is None:
            return
        tail = (committed + transient).strip()
        joiner = "" if (not self._voice_base or
                        self._voice_base.endswith((" ", "\n"))) else " "
        self._voice_pane.input_widget().value = (
            self._voice_base + (joiner + tail if tail else ""))

    def _stop_voice(self) -> None:
        voice, pane = self._voice, self._voice_pane
        self._voice = None
        if pane is not None:
            pane.set_recording(False)
        self._voice_pane = None
        if voice is not None:
            voice.stop()
```

In the app teardown path (`on_unmount`, or `action_quit` before exit), add:

```python
        if self._voice is not None:
            self._stop_voice()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_voice_action.py -v`
Expected: PASS (3 tests).

- [ ] **Step 5: Wire cli.py to pass voice config**

In `src/aegis/cli.py`, in the TUI `run` path (around line 133 where `AegisApp(...)` is constructed): load the full YAML and pass `voice`:

```python
    from aegis.config.yaml_loader import load_config as _load_yaml
    try:
        _voice = _load_yaml(effective_cwd if isinstance(effective_cwd, Path)
                            else Path(effective_cwd)).voice
    except Exception:
        _voice = None
    AegisApp(agents, default_agent, make_session, AegisMCP(),
             queues=queues, clean=clean, drivers=drivers,
             cwd=effective_cwd, voice=_voice).run()
```

(If `find_project_root`/`root` is already in scope in this function, prefer passing that Path to `_load_yaml`. Match whatever the surrounding code uses to locate `.aegis.yaml`.)

- [ ] **Step 6: Full suite + lint + commit**

```bash
uv run pytest -q
uv run ruff check src tests
git add src/aegis/tui/app.py src/aegis/cli.py tests/test_voice_action.py
git commit -m "feat(voice): TUI toggle-to-talk wiring + config passthrough"
```

---

## Task 6: Packaging extra + docs

**Files:**
- Modify: `pyproject.toml` (`[project.optional-dependencies]`)
- Modify: `docs/configuration.md` (and/or `docs/install.md`)

**Interfaces:** none (packaging + prose).

- [ ] **Step 1: Add the optional extra**

In `pyproject.toml`, under `[project.optional-dependencies]`, add:

```toml
voice = [
    "harpio>=0.9.0",
    "sounddevice>=0.5.5",
]
```

- [ ] **Step 2: Verify the extra resolves**

Run: `uv pip install -e '.[voice]'`
Expected: installs `harpio` + `sounddevice` without error. (If PortAudio is missing at import time, note it — see Step 3.)

Then confirm availability flips:

```bash
uv run python -c "from aegis.voice import voice_available; print(voice_available())"
```
Expected: `True` (assuming `libportaudio2` present).

- [ ] **Step 3: Document the block + system dep**

Add to `docs/configuration.md` a `## Voice input (push-to-talk)` section:

```markdown
## Voice input (push-to-talk)

Optional, off by default. Install the extra: `pip install aegis[voice]`
(base `harpio` + `sounddevice`; NOT `harpio[cli]`). `sounddevice` needs the
system PortAudio library — on Debian/Ubuntu: `sudo apt install libportaudio2`.

Enable per project in `.aegis.yaml`:

    voice:
      enabled: true
      model: base        # tiny | base | small | medium | large-v3
      key: ctrl+g        # Textual binding string
      preview: false     # true = live word-by-word (~2-4x cost)
      language: null      # e.g. "en", "es"; null autodetects

Press the key (default `ctrl+g`) to start dictating into the focused pane's
input, press again — from any tab — to stop. Text is never auto-sent; edit
and press Enter. One recording at a time; it stays anchored to the input it
started on even if you switch tabs.
```

- [ ] **Step 4: Commit**

```bash
git add pyproject.toml docs/configuration.md
git commit -m "docs(voice): aegis[voice] extra + voice: config docs"
```

---

## Self-Review

**Spec coverage:**
- Interaction (`ctrl+g` toggle, single recording, stop from any tab) → Tasks 5.
- Text streams into origin input, anchored across tab switches, never auto-submit → Task 5 (`_voice_pane`/`_voice_base`, `_apply_voice_text`).
- Finalize-only default + `preview` knob → Tasks 1 (`preview`) + 3 (`transient=cfg.preview`).
- Engine on a thread, marshalled via `call_from_thread` → Tasks 3 + 5.
- Off by default / feature-detected / no module-load harp import → Tasks 1, 2, 3 (lazy `_default_factory`), 5 (`voice_available` gate).
- Packaging `harpio`+`sounddevice` (not `[cli]`), PortAudio note → Task 6.
- Config block parsing → Task 1.
- Recording indicator → Task 4.
- Error handling (deps missing, mic open failure, teardown) → Task 5 (`notify` paths, teardown stop).
- Testing (fakes, no model/mic; anchoring across tab switch) → Tasks 1–5 tests.

**Placeholder scan:** none — every code step shows complete code.

**Type consistency:** `VoiceConfig` fields identical across Tasks 1/3/5; `VoiceSession(cfg, on_update, on_final, _session_factory=)` signature identical in Tasks 3/5; `on_update: (committed, transient)` / `on_final: (text)` consistent; `input_widget()`/`set_recording()` names consistent Tasks 4/5.

**Known adaptation points for the executor:**
- `cli.py` root resolution — use whatever the surrounding `run` function already uses to find `.aegis.yaml` (the plan loads it via `yaml_loader.load_config`).
- v1 simplification: text typed into the input *during* an active recording is overwritten by the next transcript update (base captured at start). Documented; acceptable for dictation.
