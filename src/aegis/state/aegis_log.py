"""The log that survives the crash.

Per-session JSONL under ``.aegis/state/sessions/`` records what each *agent*
said. Nothing recorded what **aegis itself** did — so when the TUI died, the
only account of it was a Rich traceback printed as Textual tore the terminal
down, on the alternate screen, after the app had already stopped. By the
time Alex could look, it was gone. A crash that leaves no artifact cannot be
debugged; it can only be re-witnessed and described.

This module gives aegis one process-level log at
``.aegis/state/aegis.log``: plain text, one line per record, tracebacks
indented beneath their banner, rotated at 5 MB. Read it with ``aegis logs``.

Four doors an exception can leave by, all of them wired here:

- ``sys.excepthook`` — uncaught on the main thread;
- ``threading.excepthook`` — uncaught on a worker thread;
- the asyncio loop exception handler — the "Task exception was never
  retrieved" class, which otherwise prints to stderr and vanishes;
- ``AegisApp._handle_exception`` — Textual's own hook, which fires while the
  app is still up and is therefore the one that matters for the TUI.

Every crash write is flushed before returning. The whole point is that the
record is on disk *before* the process gets to exit, so ordering here is not
an optimisation detail — it is the feature.
"""
from __future__ import annotations

import logging
import logging.handlers
import sys
import threading
import traceback
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path

LOG_NAME = "aegis.log"
MAX_BYTES = 5 * 1024 * 1024
BACKUPS = 3

# A crash banner is what `aegis logs --crashes` greps for. Keep it literal
# and unmistakable — a prefix that also appears in ordinary records would
# make the filter lie.
CRASH_MARK = "!! CRASH"

_log_path: Path | None = None
_handler: logging.Handler | None = None
_context: Callable[[], str] | None = None
_lock = threading.Lock()


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def path() -> Path | None:
    """The active log file, or None when nothing has configured one."""
    return _log_path


def default_path(state_dir: Path) -> Path:
    return Path(state_dir) / LOG_NAME


def configure(state_dir: Path, *,
              context: Callable[[], str] | None = None,
              install_hooks: bool = True) -> Path:
    """Point the aegis log at ``state_dir`` and take over the crash doors.

    ``context`` is called when a crash is recorded and its return value is
    written beside the traceback — the roster of live handles, say, which is
    exactly what a duplicate-handle crash needs and what a bare traceback
    does not carry. It is invoked inside a ``try``: a context provider that
    raises must not be able to eat the crash it was there to explain.

    Idempotent. Safe to call from tests with ``install_hooks=False``.
    """
    global _log_path, _handler, _context
    with _lock:
        _context = context
        target = default_path(state_dir)
        if _log_path == target and _handler is not None:
            if install_hooks:
                _install_hooks()
            return target
        target.parent.mkdir(parents=True, exist_ok=True)
        handler = logging.handlers.RotatingFileHandler(
            target, maxBytes=MAX_BYTES, backupCount=BACKUPS,
            encoding="utf-8", delay=False)
        handler.setFormatter(logging.Formatter(
            "%(asctime)s %(levelname)-7s %(name)s: %(message)s",
            datefmt="%Y-%m-%dT%H:%M:%SZ"))
        handler.setLevel(logging.INFO)

        root = logging.getLogger("aegis")
        if _handler is not None:
            root.removeHandler(_handler)
            _handler.close()
        root.addHandler(handler)
        root.setLevel(logging.INFO)
        # aegis owns this file; anything above it in the hierarchy has its
        # own destination and its own opinions about verbosity.
        root.propagate = False

        _log_path, _handler = target, handler

    if install_hooks:
        _install_hooks()
    write(f"aegis log opened (pid {_pid()})")
    return target


def _pid() -> int:
    import os
    return os.getpid()


def write(message: str, *, level: int = logging.INFO,
          logger: str = "aegis") -> None:
    """Record one line. A no-op when nothing configured a log yet, so
    callers never have to guard."""
    if _handler is None:
        return
    logging.getLogger(logger).log(level, message)
    _flush()


def _flush() -> None:
    h = _handler
    if h is not None:
        try:
            h.flush()
        except Exception:                                     # noqa: BLE001
            pass


def crash(where: str, exc: BaseException | None = None, *,
          tb_text: str | None = None) -> None:
    """Record a crash, with context, flushed before returning.

    ``where`` names the door it came through (``tui``, ``asyncio``,
    ``thread:<name>``, ``main``) so the log says which of the four hooks
    caught it — that alone has told us more than one traceback would.
    """
    if _handler is None:
        return
    if tb_text is None and exc is not None:
        tb_text = _format(exc)
    kind = type(exc).__name__ if exc is not None else "unknown"
    summary = str(exc) if exc is not None else ""
    lines = [f"{CRASH_MARK} [{where}] {kind}: {summary}"]
    ctx = _safe_context()
    if ctx:
        lines.append(f"  context: {ctx}")
    if tb_text:
        lines.extend("  " + ln for ln in tb_text.rstrip().splitlines())
    for wrapped in _payloads(exc) if exc is not None else []:
        lines.append(f"  -- carried by {type(exc).__name__} --")
        lines.extend("  " + ln for ln in _format(wrapped).rstrip().splitlines())
    logging.getLogger("aegis.crash").critical("\n".join(lines))
    _flush()


def _format(exc: BaseException) -> str:
    return "".join(traceback.format_exception(
        type(exc), exc, exc.__traceback__))


def _payloads(exc: BaseException, limit: int = 3) -> list[BaseException]:
    """Exceptions a wrapper carries as a *payload* rather than as a cause.

    Python's formatter follows ``__cause__`` and ``__context__`` by itself.
    What it cannot see is a wrapper that stores the real failure in an
    attribute — and that is the case that matters most here, because every
    pane mount runs inside a Textual worker, so the crash arrives as
    ``WorkerFailed(error=DuplicateIds(...))`` whose own traceback is one
    line naming nothing. Without this, the log would faithfully record that
    something failed somewhere.

    Deliberately duck-typed: naming Textual here would buy a coupling and
    miss the next wrapper.
    """
    found: list[BaseException] = []
    seen = {id(exc)}
    for cand in (getattr(exc, "error", None),
                 getattr(exc, "exception", None),
                 *getattr(exc, "args", ())):
        if (isinstance(cand, BaseException) and id(cand) not in seen
                and cand.__traceback__ is not None):
            seen.add(id(cand))
            found.append(cand)
            if len(found) >= limit:
                break
    return found


def _safe_context() -> str:
    fn = _context
    if fn is None:
        return ""
    try:
        return fn()
    except Exception as e:                                    # noqa: BLE001
        # The provider failing is itself worth knowing, and must never
        # displace the crash that triggered it.
        return f"<context unavailable: {type(e).__name__}: {e}>"


# --- the four doors -------------------------------------------------------

_hooks_installed = False


def _install_hooks() -> None:
    global _hooks_installed
    if _hooks_installed:
        return
    _hooks_installed = True

    prev_excepthook = sys.excepthook

    def _excepthook(exc_type, exc, tb):
        crash("main", exc, tb_text="".join(
            traceback.format_exception(exc_type, exc, tb)))
        prev_excepthook(exc_type, exc, tb)

    sys.excepthook = _excepthook

    prev_thread_hook = threading.excepthook

    def _thread_hook(args):
        name = getattr(args.thread, "name", "?")
        crash(f"thread:{name}", args.exc_value, tb_text="".join(
            traceback.format_exception(
                args.exc_type, args.exc_value, args.exc_traceback)))
        prev_thread_hook(args)

    threading.excepthook = _thread_hook


def install_asyncio_hook(loop) -> None:
    """Chain the aegis log onto ``loop``'s exception handler.

    Separate from ``_install_hooks`` because a loop only exists once
    something is running, and ``serve`` and the TUI reach that point by
    different routes.
    """
    prev = loop.get_exception_handler()

    def _handler(lp, ctx):
        exc = ctx.get("exception")
        crash("asyncio", exc,
              tb_text=None if exc is not None else str(ctx.get("message")))
        if prev is not None:
            prev(lp, ctx)
        else:
            lp.default_exception_handler(ctx)

    loop.set_exception_handler(_handler)


# --- reading it back ------------------------------------------------------

def tail(n: int = 200, *, crashes_only: bool = False,
         log_path: Path | None = None) -> list[str]:
    """Last ``n`` lines of the log. ``crashes_only`` keeps each crash banner
    and the block indented beneath it, which is what makes the filter useful
    rather than a list of bare headlines."""
    target = log_path or _log_path
    if target is None or not Path(target).exists():
        return []
    lines = Path(target).read_text(
        encoding="utf-8", errors="replace").splitlines()
    if crashes_only:
        kept: list[str] = []
        in_block = False
        for ln in lines:
            if CRASH_MARK in ln:
                in_block = True
                kept.append(ln)
            elif in_block and ln.startswith("  "):
                kept.append(ln)
            else:
                in_block = False
        lines = kept
    return lines[-n:]
