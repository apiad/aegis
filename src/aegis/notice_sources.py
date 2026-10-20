"""The notice sources a running aegis knows, and each one's defaults.

A per-session notice source (notices.py, ``Notices.set``) declares here the
keys it reads from its entry under ``notices:`` in .aegis.yaml, so config.py
can check that block without naming a source. A module declares its source
when it is imported; a plugin will declare its own through the plugin host.

This module imports nothing from aegis: config.py imports queues.py, so a
registry in config.py or notices.py would make a source import back into it.
"""

from __future__ import annotations

import copy

_SOURCES: dict[str, dict] = {}


def declare(name: str, defaults: dict) -> None:
    """Declare ``name`` with its keys and their defaults. A bool default
    takes a bool, a number default a number above 0 (an int an int). An
    empty dict declares a source that is only on or off."""
    _SOURCES[name] = dict(defaults)


def declared() -> dict[str, dict]:
    return copy.deepcopy(_SOURCES)
