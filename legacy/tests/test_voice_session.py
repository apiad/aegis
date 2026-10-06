import time

from aegis.config import VoiceConfig
from aegis.voice.session import VoiceSession


class _FakeDictation:
    def __init__(self, text="hello world"):
        self._text = text
        self.started = False
        self.stopped = False

    def start(self):
        self.started = True

    def stop(self):
        self.stopped = True
        return self._text


def test_start_then_stop_delivers_final_text():
    fake = _FakeDictation("hello world")
    finals = []
    vs = VoiceSession(VoiceConfig(), on_final=finals.append,
                      _session_factory=lambda cfg: fake)
    vs.start()
    assert vs.is_running is True and fake.started is True
    vs.stop()
    deadline = time.time() + 2
    while not finals and time.time() < deadline:
        time.sleep(0.01)
    assert finals == ["hello world"]
    assert vs.is_running is False and fake.stopped is True


def test_stop_without_start_is_noop():
    finals = []
    vs = VoiceSession(VoiceConfig(), on_final=finals.append,
                      _session_factory=lambda cfg: _FakeDictation())
    vs.stop()   # must not raise, must not deliver
    assert finals == []


def test_decode_error_delivers_empty_string():
    class _Boom(_FakeDictation):
        def stop(self):
            raise RuntimeError("decode failed")

    finals = []
    vs = VoiceSession(VoiceConfig(), on_final=finals.append,
                      _session_factory=lambda cfg: _Boom())
    vs.start()
    vs.stop()
    deadline = time.time() + 2
    while not finals and time.time() < deadline:
        time.sleep(0.01)
    assert finals == [""]
