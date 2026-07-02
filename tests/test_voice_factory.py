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
    last_kwargs = {}

    def __init__(self, **kwargs):
        _SpyEngine.instances += 1
        _SpyEngine.last_kwargs = kwargs
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


def test_engine_pins_cpu_default_to_avoid_fallback_prints(monkeypatch):
    # harp's whisper wrapper print()s to stdout on any device/compute
    # fallback, corrupting the TUI. Pinning cpu+default disables that branch.
    _patch(monkeypatch)
    vs._default_factory(VoiceConfig(model="base"))
    assert _SpyEngine.last_kwargs["device"] == "cpu"
    assert _SpyEngine.last_kwargs["compute_type"] == "default"


class _LoadableEngine(_SpyEngine):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.loaded = False

    def load_model(self):
        self.loaded = True


def test_prewarm_loads_model_when_available(monkeypatch):
    _patch(monkeypatch)
    monkeypatch.setattr(harp.whisper, "LocalWhisperEngine", _LoadableEngine,
                        raising=False)
    monkeypatch.setattr("aegis.voice.session.voice_available", lambda: True,
                        raising=False)
    # Silero warm-up is best-effort; stub it so the test needs no model.
    monkeypatch.setattr(harp.vad, "SileroDetector",
                        lambda *a, **k: type("D", (), {
                            "speech_segments": lambda self, x: []})(),
                        raising=False)
    from aegis.voice import prewarm
    from aegis.config import VoiceConfig
    prewarm(VoiceConfig(model="base"))
    eng = vs._ENGINE_CACHE["base"]
    assert eng.loaded is True


def test_prewarm_noop_when_unavailable(monkeypatch):
    _patch(monkeypatch)
    monkeypatch.setattr("aegis.voice.session.voice_available", lambda: False,
                        raising=False)
    from aegis.voice import prewarm
    from aegis.config import VoiceConfig
    prewarm(VoiceConfig(model="base"))  # must not raise, must not build
    assert "base" not in vs._ENGINE_CACHE
