"""The real harp-session factory must be tuned for push-to-talk dictation:
no warm-up delay, overlap-based finalization (transcribe_segments), and a
process-cached engine so repeated recordings don't reload the model."""
import pytest

# Voice is an optional extra (`harpio`, imported as `harp`); the hermetic CI
# run doesn't install it. Skip the whole module rather than erroring at
# collection when the dep is absent.
pytest.importorskip("harp")

import harp  # noqa: E402
import harp.vad  # noqa: E402
import harp.whisper  # noqa: E402

import aegis.voice.session as vs  # noqa: E402
from aegis.config import VoiceConfig  # noqa: E402


class _SpyDictation:
    calls = []

    def __init__(self, audio, transcribe, **kwargs):
        _SpyDictation.calls.append({"transcribe": transcribe, **kwargs})
        self.audio = audio


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
    _SpyDictation.calls = []
    _SpyEngine.instances = 0
    vs._ENGINE_CACHE.clear()
    monkeypatch.setattr(harp, "DictationSession", _SpyDictation, raising=False)
    monkeypatch.setattr(harp, "MicrophoneSource", lambda *a, **k: object(),
                        raising=False)
    monkeypatch.setattr(harp.vad, "SileroDetector", lambda *a, **k: object(),
                        raising=False)
    monkeypatch.setattr(harp.whisper, "LocalWhisperEngine", _SpyEngine,
                        raising=False)


def test_factory_builds_dictation_session(monkeypatch):
    _patch(monkeypatch)
    session = vs._default_factory(VoiceConfig(model="base"))
    assert isinstance(session, _SpyDictation)
    assert callable(_SpyDictation.calls[-1]["transcribe"])


def test_factory_passes_language(monkeypatch):
    _patch(monkeypatch)
    vs._default_factory(VoiceConfig(model="base", language="es"))
    assert _SpyDictation.calls[-1].get("language") == "es"


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
