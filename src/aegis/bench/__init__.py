"""aegis bench: drive a real daemon and client in a pty and measure them.

Spec: ``docs/superpowers/specs/2026-09-13-aegis-bench-design.md``.
"""

from __future__ import annotations


class BenchError(RuntimeError):
    """A benchmark could not produce a trustworthy measurement."""


class ScenarioSkipped(Exception):
    """A scenario does not apply to this target; the reason is reported."""
