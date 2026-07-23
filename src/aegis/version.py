"""Build identity of the *running* aegis process.

``BUILD`` is computed once at import — deliberately, not lazily. aegis is
normally installed editable from a checkout that keeps moving, so "what
version am I running" and "what version is on disk" diverge the moment a
commit lands under a live TUI. Latching at import means the status bar
answers the first question, which is the one worth asking when behaviour
doesn't match a fix you know you wrote.

Format: ``0.21.0+d35b07a`` from a git checkout, ``0.21.0`` otherwise.
"""
from __future__ import annotations

import subprocess
from pathlib import Path


def _pkg_version() -> str:
    try:
        from importlib.metadata import version
        return version("aegis-harness")
    except Exception:  # noqa: BLE001 — not installed (rare in dev)
        return "0.0.0+unknown"


def _git_sha() -> str:
    """Short HEAD sha of the checkout this package is imported from, or ""."""
    root = Path(__file__).resolve().parent.parent.parent
    if not (root / ".git").exists():
        return ""
    try:
        out = subprocess.run(
            ["git", "-C", str(root), "rev-parse", "--short", "HEAD"],
            capture_output=True, text=True, timeout=2.0)
    except Exception:  # noqa: BLE001 — no git binary, slow disk, whatever
        return ""
    return out.stdout.strip() if out.returncode == 0 else ""


def _build() -> str:
    v, sha = _pkg_version(), _git_sha()
    return f"{v}+{sha}" if sha else v


BUILD = _build()
