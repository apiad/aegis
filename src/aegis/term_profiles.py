"""Terminal profiles: the ``terminals:`` block of .aegis.yaml (#362).

A profile names a command and where it runs::

    terminals:
      api: {cmd: "uvicorn app.main:app --reload", cwd: app, restart: on-failure}

``cmd`` and ``cwd`` are required. A profile missing one, or with a key aegis
does not read, is kept with an ``error`` naming what is wrong, as an agent
missing its harness is, so one bad entry never hides the others from the
picker or from aegis doctor. ``restart`` is ``no`` when absent, because absence
and ``no`` mean the same thing. Without a profile named ``shell`` the picker
still offers one: the server user's login shell in the config root.
"""

from __future__ import annotations

import os
import pwd

PROFILE_KEYS = ("cmd", "cwd", "restart", "env", "access")
RESTARTS = ("no", "on-failure", "always")
SHELL = {"cmd": None, "cwd": ".", "restart": "no", "env": {}, "access": {}}


def login_shell() -> str:
    return os.environ.get("SHELL") or pwd.getpwuid(os.getuid()).pw_shell or "/bin/sh"


def terminals_from(raw: object) -> tuple[dict[str, dict], str | None]:
    """Each profile by name, and why the block itself is wrong, if it is."""
    if raw is None:
        return {}, None
    if not isinstance(raw, dict):
        return {}, "terminals must be a mapping of a name to a profile"
    return {str(name): _profile(entry) for name, entry in raw.items()}, None


def _profile(entry: object) -> dict:
    if not isinstance(entry, dict):
        return {"error": "a profile is a mapping with cmd and cwd"}
    entry = {str(k): v for k, v in entry.items()}
    if unknown := [k for k in entry if k not in PROFILE_KEYS]:
        return {
            "error": f"{', '.join(unknown)}: not a profile key; "
            f"it takes {', '.join(PROFILE_KEYS)}"
        }
    if missing := [k for k in ("cmd", "cwd") if not entry.get(k)]:
        verb = "is" if len(missing) == 1 else "are"
        return {"error": f"{' and '.join(missing)} {verb} missing"}
    restart = str(entry.get("restart", "no"))
    if restart not in RESTARTS:
        return {"error": f"restart {restart!r} is not one of {', '.join(RESTARTS)}"}
    env, access = entry.get("env") or {}, entry.get("access") or {}
    if not isinstance(env, dict) or not isinstance(access, dict):
        return {"error": "env and access are mappings"}
    return {
        "cmd": str(entry["cmd"]),
        "cwd": str(entry["cwd"]),
        "restart": restart,
        "env": {str(k): str(v) for k, v in env.items()},
        "access": {str(k): str(v) for k, v in access.items()},
    }


def with_shell(profiles: dict[str, dict]) -> dict[str, dict]:
    """The profiles the picker offers: the built-in shell first, unless one of
    the file's is named shell."""
    return profiles if "shell" in profiles else {"shell": dict(SHELL), **profiles}


def argv(profile: dict) -> list[str]:
    if profile["cmd"] is None:
        return [login_shell(), "-l"]
    return ["/bin/sh", "-c", profile["cmd"]]
