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
