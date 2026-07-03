"""VoiceSession: drives a harp DictationSession and delivers the final
transcript through a callback. Textual-free and unit-testable with a fake
DictationSession (no model, no mic)."""
from __future__ import annotations

import threading
from typing import Callable, Optional

from aegis.config import VoiceConfig
from aegis.voice.availability import voice_available


# Whisper model load is the slow part (~1-2s even from cache); reuse one
# engine per model size across recordings so repeated ctrl+g is instant.
_ENGINE_CACHE: dict[str, object] = {}


def _get_engine(cfg: VoiceConfig):
    # ctranslate2 (under faster-whisper) logs compute-type conversion notices
    # to stderr; in a full-screen TUI those corrupt the display. Silence them.
    try:
        import logging as _logging

        import ctranslate2
        ctranslate2.set_log_level(_logging.ERROR)
    except Exception:
        pass

    from harp.whisper import LocalWhisperEngine

    engine = _ENGINE_CACHE.get(cfg.model)
    if engine is None:
        # device=cpu + compute_type=default is deliberate: harp's whisper
        # wrapper print()s a raw stdout warning on ANY device/compute
        # fallback (CUDA libs missing, int8 unsupported), which lands in the
        # TUI input box. Pinning cpu+default keeps harp's fallback branch
        # dormant (its guard is `device != "cpu" or compute != "default"`),
        # so no stray prints ever reach the screen.
        engine = LocalWhisperEngine(
            model_size=cfg.model, device="cpu",
            compute_type="default", beam_size=1)
        _ENGINE_CACHE[cfg.model] = engine
    return engine


def prewarm(cfg: VoiceConfig) -> None:
    """Eagerly load the whisper + Silero models so the first recording is
    responsive. Both otherwise load lazily inside the streaming worker on the
    first decode (several seconds cold), during which nothing is emitted —
    a short push-to-talk would end before any text appeared. Best-effort:
    swallows errors and no-ops when the voice extra isn't installed."""
    if not voice_available():
        return
    try:
        import numpy as np

        from harp.vad import SileroDetector
        engine = _get_engine(cfg)
        engine.load_model()
        # Warm Silero VAD (faster-whisper caches its model process-wide).
        SileroDetector().speech_segments(np.zeros(1600, dtype=np.float32))
    except Exception:
        pass


def _default_factory(cfg: VoiceConfig):
    """Build a full-mode DictationSession: record while active, decode the
    whole clip once on stop against the warm engine. Imports harp lazily so
    this module is import-safe without the voice extra."""
    from harp import DictationSession, MicrophoneSource

    engine = _get_engine(cfg)
    return DictationSession(
        audio=MicrophoneSource(),
        transcribe=engine.transcribe,
        language=cfg.language,
    )


class VoiceSession:
    """Drives a harp DictationSession: start() records; stop() decodes the
    buffered clip off-thread and delivers the final text via on_final. UI-
    agnostic and testable with a stub DictationSession (no model, no mic)."""

    def __init__(
        self,
        cfg: VoiceConfig,
        on_final: Callable[[str], None],
        _session_factory: Optional[Callable[[VoiceConfig], object]] = None,
    ) -> None:
        self._cfg = cfg
        self._on_final = on_final
        self._factory = _session_factory or _default_factory
        self._session: object | None = None
        self._recording = False
        self._lock = threading.Lock()

    @property
    def is_running(self) -> bool:
        return self._recording

    def start(self) -> None:
        with self._lock:
            if self._recording:
                return
            self._session = self._factory(self._cfg)
            self._session.start()
            self._recording = True

    def stop(self) -> None:
        with self._lock:
            if not self._recording:
                return
            self._recording = False
            session = self._session
            self._session = None

        def _finish() -> None:
            try:
                text = session.stop()
            except Exception:
                text = ""
            self._on_final(text or "")

        threading.Thread(
            target=_finish, name="voice-decode", daemon=True).start()
