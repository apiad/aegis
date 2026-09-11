"""ViewDriver is WebDriver with every process-global assumption removed.

Every test here is `async def`: Driver.__init__ calls
asyncio.get_running_loop(), so a sync test cannot construct a driver at
all — it dies with RuntimeError before reaching any assertion.
"""
import signal

from aegis.views.driver import ViewDriver, view_driver_for


class _FakeApp:
    """Enough App for a driver to construct AND to enter application mode.

    `_post_message` is not decoration: start_application_mode posts an
    initial AppBlur (web_driver.py:173), so an app without it raises
    AttributeError and every assertion below becomes unreachable — a test
    that dies for the wrong reason proves nothing about the right one.
    """

    def __init__(self):
        self.messages = []

    async def _post_message(self, message):
        self.messages.append(message)

    def post_message(self, message):
        self.messages.append(message)

    def call_later(self, fn, *args):
        fn(*args)


async def test_factory_returns_a_webdriver_subclass_bound_to_the_sink():
    frames = []
    cls = view_driver_for(frames.append)
    assert issubclass(cls, ViewDriver)
    drv = cls(_FakeApp(), size=(80, 24))
    drv.write("hello")
    assert frames, "nothing reached the sink"
    assert frames[0].startswith(b"D"), frames[0]


async def test_frame_is_the_textual_wire_format():
    """b'D' + 4-byte big-endian length + utf-8 payload (web_driver.py:87)."""
    frames = []
    drv = view_driver_for(frames.append)(_FakeApp(), size=(80, 24))
    drv.write("hi")
    body = "hi".encode("utf-8")
    assert frames[0] == b"D" + len(body).to_bytes(4, "big") + body


async def test_two_drivers_have_independent_sinks_and_sizes():
    """The whole point. COLUMNS/ROWS is process-global (web_driver.py:52-59),
    so geometry must ride on the explicit size= argument."""
    a, b = [], []
    da = view_driver_for(a.append)(_FakeApp(), size=(80, 24))
    db = view_driver_for(b.append)(_FakeApp(), size=(140, 50))
    da.write("A")
    assert a and not b, "sinks are not independent"
    assert da._size == (80, 24)
    assert db._size == (140, 50)


async def test_constructs_with_no_usable_stdin(monkeypatch):
    """The daemon case, and the reason ViewDriver bypasses
    WebDriver.__init__ rather than overriding run_input_thread.

    InputReader.__init__ registers sys.__stdin__ with a selector at
    CONSTRUCTION (web_driver.py:68). Under systemd stdin is /dev/null, and
    registering /dev/null with epoll raises PermissionError [Errno 1]
    (measured). A driver that cannot be built under systemd cannot run in
    the daemon this whole stage exists to enable.
    """
    import os
    devnull = os.open(os.devnull, os.O_RDONLY)
    try:
        class _DevNullStdin:
            def fileno(self): return devnull
        monkeypatch.setattr("sys.__stdin__", _DevNullStdin())
        frames = []
        drv = view_driver_for(frames.append)(_FakeApp(), size=(80, 24))
        drv.write("built anyway")
        assert frames, "driver could not be built without a real stdin"
    finally:
        os.close(devnull)


async def test_nothing_reaches_the_real_stdout(capfdbinary):
    drv = view_driver_for(lambda _b: None)(_FakeApp(), size=(80, 24))
    drv.write("should not appear")
    drv.flush()
    out, err = capfdbinary.readouterr()
    assert out == b"", out
    assert b"should not appear" not in err


async def test_start_application_mode_installs_no_signal_handlers():
    """web_driver.py:150-152 adds SIGINT/SIGTERM handlers to the running
    loop. N views on one loop means last-registration-wins, and a daemon
    whose views own the host's shutdown."""
    before = signal.getsignal(signal.SIGINT)
    drv = view_driver_for(lambda _b: None)(_FakeApp(), size=(80, 24))
    drv.start_application_mode()
    assert signal.getsignal(signal.SIGINT) is before


async def test_start_application_mode_writes_no_ganglion_handshake():
    """web_driver.py:154. Nothing in aegis consumes it and a stage-5 attach
    client reading raw frames would see it as a malformed frame."""
    frames = []
    drv = view_driver_for(frames.append)(_FakeApp(), size=(80, 24))
    drv.start_application_mode()
    assert not any(b"__GANGLION__" in f for f in frames)


async def test_bracketed_paste_is_still_enabled():
    """Kept deliberately. web_driver.py:170 enables bracketed paste, and
    dropping it makes a multi-line paste arrive as individual keystrokes —
    a silent degradation, since nothing in aegis/tui handles Paste today
    and so nothing would report it.
    """
    frames = []
    drv = view_driver_for(frames.append)(_FakeApp(), size=(80, 24))
    drv.start_application_mode()
    blob = b"".join(frames)
    assert b"?2004h" in blob, "bracketed paste was not enabled"


async def test_every_attribute_webdriver_expects_is_present():
    """ViewDriver bypasses WebDriver.__init__, so it owes that method's
    attributes by hand. This test is the tripwire for a Textual upgrade
    adding an eighth: it fails loudly here rather than as an AttributeError
    deep in a running view.
    """
    drv = view_driver_for(lambda _b: None)(_FakeApp(), size=(80, 24))
    for attr in ("stdout", "fileno", "exit_event", "_key_thread",
                 "_input_reader", "_deliveries", "_write"):
        assert hasattr(drv, attr), f"WebDriver expects {attr!r}"
    # stop_application_mode calls _input_reader.close() (web_driver.py:181)
    drv._input_reader.close()
