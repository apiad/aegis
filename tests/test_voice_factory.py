"""The real harp-session factory must be tuned for push-to-talk dictation:
no warm-up delay, overlap-based finalization (transcribe_segments), and a
process-cached engine so repeated recordings don't reload the model."""
import harp
import harp.vad
import harp.whisper

import aegis.voice.session as vs
from aegis.config import VoiceConfig


class _SpyHarpSession:
    calls = []

    def __init__(self, **kwargs):
        _SpyHarpSession.calls.append(kwargs)
        self.kwargs = kwargs


class _SpyEngine:
    instances = 0

    def __init__(self, **kwargs):
        _SpyEngine.instances += 1
        self.kwargs = kwargs

    def transcribe(self, *a, **k):
        return ""

    def transcribe_segments(self, *a, **k):
        return []


def _patch(monkeypatch):
    _SpyHarpSession.calls = []
    _SpyEngine.instances = 0
    vs._ENGINE_CACHE.clear()
    monkeypatch.setattr(harp, "HarpSession", _SpyHarpSession, raising=False)
    monkeypatch.setattr(harp, "MicrophoneSource", lambda *a, **k: object(),
                        raising=False)
    monkeypatch.setattr(harp.vad, "SileroDetector", lambda *a, **k: object(),
                        raising=False)
    monkeypatch.setattr(harp.whisper, "LocalWhisperEngine", _SpyEngine,
                        raising=False)


def test_factory_disables_warmup(monkeypatch):
    _patch(monkeypatch)
    vs._default_factory(VoiceConfig(model="base"))
    kw = _SpyHarpSession.calls[-1]
    assert kw["warmup"] == 0.0


def test_factory_passes_transcribe_segments(monkeypatch):
    _patch(monkeypatch)
    vs._default_factory(VoiceConfig(model="base"))
    kw = _SpyHarpSession.calls[-1]
    # overlap-based finalization: the segments fn must be wired, not omitted
    assert callable(kw.get("transcribe_segments"))


def test_engine_is_cached_across_recordings(monkeypatch):
    _patch(monkeypatch)
    vs._default_factory(VoiceConfig(model="base"))
    vs._default_factory(VoiceConfig(model="base"))
    assert _SpyEngine.instances == 1
