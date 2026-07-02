"""Feature detection for the optional voice extra (`aegis[voice]`)."""
from __future__ import annotations

import importlib

_REQUIRED = ("harp", "sounddevice")
_CACHE: dict[str, bool] = {}


def _probe(name: str) -> bool:
    try:
        importlib.import_module(name)
        return True
    except Exception:
        return False


def _missing() -> list[str]:
    out = []
    for name in _REQUIRED:
        if name not in _CACHE:
            _CACHE[name] = _probe(name)
        if not _CACHE[name]:
            out.append(name)
    return out


def voice_available() -> bool:
    return not _missing()


def unavailable_reason() -> str:
    missing = _missing()
    if not missing:
        return ""
    return (f"voice input needs {', '.join(missing)} — "
            f"install with `pip install aegis[voice]`")
