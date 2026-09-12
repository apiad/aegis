"""One attached client, and the listener that accepts them.

``serve_view`` takes duck-typed reader/writer rather than a socket, because
stage 5b hands it a WebSocket adapter and the spec's transport-equivalence
assertion is only meaningful if both transports run this same code. A
second implementation for the second transport is exactly the divergence
that assertion exists to catch.
"""
from __future__ import annotations

import asyncio
import contextlib
import logging
from pathlib import Path

from aegis.daemon.protocol import FrameDecoder, ProtocolError, parse_hello

log = logging.getLogger(__name__)


async def serve_view(reader, writer, registry, *, on_close=None) -> None:
    """Own one client for its whole lifetime.

    ``reader.read(n)`` returns ``b""`` at EOF; ``writer`` needs ``write``,
    ``drain``, ``close`` and ``wait_closed``.
    """
    decoder = FrameDecoder()
    view = None
    sink_fn = None
    try:
        view_id, width, height = await _read_hello(reader, decoder)
        if registry.get(view_id) is not None:
            # One client per view id. ViewRegistry.open returns the
            # EXISTING view for a live id, so attaching twice would give
            # one app two screens: the second client's repaint lands on
            # the first client's terminal and both drive the same focus.
            raise ProtocolError(f"view {view_id!r} already has a client")

        view = await registry.open(view_id, (width, height))

        loop = asyncio.get_running_loop()
        pending: list[bytes] = []

        def sink_fn(data: bytes) -> None:          # noqa: F811
            # Called from Textual's render path, which is on this loop.
            # The socket write happens in the flusher below so a slow
            # client cannot block a render.
            pending.append(data)

        # Attached BEFORE run(), and that ordering is the whole of it.
        # start_application_mode emits the alt-screen, mouse-mode and
        # bracketed-paste escapes during boot (`views/driver.py`), and a
        # sink attached afterwards buffers them into a list nobody reads —
        # so the client would render the app's content into its normal
        # scrollback, with no mouse and no alt screen, and repaint() cannot
        # recover it because those escapes are terminal MODE, not screen
        # content.
        view.sink.attach(sink_fn)
        flusher = loop.create_task(_flush(writer, pending))
        try:
            # No repaint here, and that is a deliberate absence.
            #
            # Textual emits deltas unless the whole screen is dirty
            # (`_compositor.py:1118`), so a client attaching to a view that
            # has ALREADY rendered would receive deltas against a screen it
            # has never seen -- which is what View.repaint() exists for. In
            # this stage that client cannot exist: the refusal above means
            # registry.open() can only return a freshly built view, whose
            # own boot render is full. A repaint() call here is therefore
            # structurally dead, and verified so -- the whole gate passes
            # with it removed.
            #
            # 5b reintroduces the case (a browser reconnecting to a view
            # the daemon kept warm). The repaint belongs with it, guarded
            # by a test that can fail, rather than parked here where none
            # can.
            await view.run()
            await _pump(reader, decoder, view)
        finally:
            flusher.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await flusher
    except ProtocolError as e:
        log.info("view client refused: %s", e)
    except (ConnectionResetError, BrokenPipeError):
        pass
    except asyncio.TimeoutError:
        log.info("view client sent no hello in time")
    finally:
        if view is not None and sink_fn is not None:
            view.sink.detach(sink_fn)
        with contextlib.suppress(Exception):
            writer.close()
            await writer.wait_closed()
        if view is not None:
            # Closing persists the ViewState and stops the app. The brain
            # is untouched: the app's owns_brain is False, so its quit path
            # closes no panes and stops no plane.
            await registry.close(view.view_id)
        if on_close is not None:
            on_close()


async def _read_hello(reader, decoder: FrameDecoder) -> tuple[str, int, int]:
    """Block until the first whole frame, which must be a hello.

    Bounded, because an unauthenticated client that dribbles bytes forever
    otherwise holds a task and a socket for as long as it likes.
    """
    async with asyncio.timeout(10):
        while True:
            chunk = await reader.read(65536)
            if not chunk:
                raise ProtocolError("client closed before hello")
            for kind, payload in decoder.feed(chunk):
                if kind != "M":
                    raise ProtocolError(f"first frame is {kind!r}, not meta")
                return parse_hello(payload)


async def _pump(reader, decoder: FrameDecoder, view) -> None:
    driver = view.app._driver
    while True:
        chunk = await reader.read(65536)
        if not chunk:
            return
        for kind, payload in decoder.feed(chunk):
            frame = (b"D" if kind == "D" else b"M")
            frame += len(payload).to_bytes(4, "big") + payload
            try:
                driver.feed(frame)
            except Exception:  # noqa: BLE001
                # driver.feed already swallows per-frame damage; this is
                # the _ExitInput the client's {"type":"exit"} raises, and
                # anything a Textual upgrade adds. Either way the client
                # is done, and no single client may raise into the accept
                # loop that serves the others.
                return


async def _flush(writer, pending: list[bytes]) -> None:
    """Carry buffered frames to the socket without blocking a render."""
    while True:
        if not pending:
            await asyncio.sleep(0.005)
            continue
        batch = b"".join(pending)
        pending.clear()
        writer.write(batch)
        await writer.drain()


class UnixSocketServer:
    """The listener. Everything about a connection is in ``serve_view``."""

    def __init__(self, path: Path, registry) -> None:
        self.path = Path(path)
        self._registry = registry
        self._server: asyncio.AbstractServer | None = None

    async def start(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        # A socket file survives SIGKILL. Removing a stale one is safe
        # because the caller has already established (via the daemon
        # registry) that no live daemon owns this root.
        with contextlib.suppress(FileNotFoundError):
            self.path.unlink()
        self._server = await asyncio.start_unix_server(
            self._on_client, path=str(self.path))
        # The socket's mode bits ARE the auth on this transport (the spec
        # says so explicitly), and the daemon runs `permission: full`.
        self.path.chmod(0o600)

    async def _on_client(self, reader, writer) -> None:
        await serve_view(reader, writer, self._registry)

    async def stop(self) -> None:
        if self._server is not None:
            self._server.close()
            with contextlib.suppress(Exception):
                await self._server.wait_closed()
            self._server = None
        with contextlib.suppress(FileNotFoundError):
            self.path.unlink()
