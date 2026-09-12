"""FrameSink: a list while nobody listens, a fanout when someone does."""
from aegis.views.sink import FrameSink


def test_with_no_consumer_frames_accumulate():
    s = FrameSink()
    s(b"a")
    s(b"b")
    assert s.frames == [b"a", b"b"]


def test_a_consumer_receives_every_frame():
    s = FrameSink()
    got = []
    s.attach(got.append)
    s(b"a")
    s(b"b")
    assert got == [b"a", b"b"]


def test_an_attached_view_buffers_nothing():
    """The leak this class exists to close. A view attached for a day
    emits frames continuously; none of them are ours to keep."""
    s = FrameSink()
    s.attach(lambda _b: None)
    for _ in range(10_000):
        s(b"x")
    assert s.frames == []


def test_the_detached_buffer_is_capped():
    s = FrameSink(cap=8)
    for i in range(100):
        s(bytes([i]))
    assert len(s.frames) == 8
    assert s.frames[-1] == bytes([99])


def test_detaching_restores_buffering():
    s = FrameSink()
    fn = []
    s.attach(fn.append)
    s(b"live")
    s.detach(fn.append)
    assert s.consumers == 0
    s(b"buffered")
    assert s.frames == [b"buffered"]


def test_detach_of_an_unattached_consumer_is_a_no_op():
    """A client that dies mid-handshake detaches in a finally block that
    may never have attached."""
    s = FrameSink()
    s.detach(print)
    assert s.consumers == 0


def test_one_slow_consumer_does_not_stop_the_others():
    s = FrameSink()
    got = []

    def boom(_b):
        raise RuntimeError("client went away")

    s.attach(boom)
    s.attach(got.append)
    s(b"a")
    assert got == [b"a"]
