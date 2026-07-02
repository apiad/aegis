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
