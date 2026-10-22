"""The rules of the multi-user mode (#548): which Linux user a token may name,
the ``multi_user`` block of ``.aegis.yaml``, and the pool file whose variables
every person's harness gets.

Pure functions and one small reader: nothing here starts a process
(``launch.py`` does, with what this module builds). The design is
docs/superpowers/specs/2026-10-21-linux-users-design.html, sections 04 to 06.
"""

from __future__ import annotations

import logging
import os
import pwd
import re
import shutil
from collections.abc import Mapping
from dataclasses import dataclass, replace
from pathlib import Path
from typing import TYPE_CHECKING

from .ops import OpError

if TYPE_CHECKING:
    from .session import SpawnSpec

log = logging.getLogger("aegis.users")

LOGIN_DEFS = Path("/etc/login.defs")
UID_MIN_DEFAULT = 1000
ENV_PATH_DEFAULT = "/usr/local/bin:/usr/bin:/bin"
ENV_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
# The capabilities the server needs to start a child as another user and drop
# every capability from it (the bounding set needs CAP_SETPCAP), by bit.
CAPS = {"CAP_SETGID": 6, "CAP_SETUID": 7, "CAP_SETPCAP": 8}
UNIT_LINES = (
    "AmbientCapabilities=CAP_SETUID CAP_SETGID CAP_SETPCAP",
    "CapabilityBoundingSet=CAP_SETUID CAP_SETGID CAP_SETPCAP",
)


def _login_defs(path: Path) -> dict[str, str]:
    """The ``KEY value`` lines of login.defs; {} when it cannot be read."""
    try:
        text = path.read_text()
    except OSError:
        return {}
    out = {}
    for line in text.splitlines():
        parts = line.split(None, 1)
        if len(parts) == 2 and not parts[0].startswith("#"):
            out[parts[0]] = parts[1].strip()
    return out


def uid_min(login_defs: Path = LOGIN_DEFS) -> int:
    """The lowest uid of a regular account on this host."""
    try:
        return int(_login_defs(login_defs)["UID_MIN"])
    except (KeyError, ValueError):
        return UID_MIN_DEFAULT


def env_path(login_defs: Path = LOGIN_DEFS) -> str:
    """The PATH a regular user's login gets on this host."""
    v = _login_defs(login_defs).get("ENV_PATH", "")
    return v.removeprefix("PATH=") or ENV_PATH_DEFAULT


def allowed(
    name: str, *, server_uid: int, login_defs: Path = LOGIN_DEFS
) -> pwd.struct_passwd:
    """``name``'s account, when a token may run as it: an existing regular
    account that is neither root nor the server's own user, which can read
    every token."""
    try:
        pw = pwd.getpwnam(name)
    except KeyError:
        raise OpError("no_such_user", f"no Linux user named {name}") from None
    if pw.pw_uid == 0:
        raise OpError("user_not_allowed", f"{name} is root")
    if pw.pw_uid == server_uid:
        raise OpError("user_not_allowed", f"{name} is the server's own user")
    low = uid_min(login_defs)
    if pw.pw_uid < low:
        raise OpError(
            "user_not_allowed", f"{name} has uid {pw.pw_uid}, below UID_MIN ({low})"
        )
    return pw


@dataclass(frozen=True)
class MultiUser:
    """``multi_user:`` in .aegis.yaml. Its presence turns the mode on."""

    env_file: Path | None = None


def multi_user_from(data: dict) -> tuple[MultiUser | None, str | None]:
    """The mode from the whole document: ``multi_user:`` with nothing after it
    is YAML null, and still turns the mode on."""
    if "multi_user" not in data:
        return None, None
    raw = data["multi_user"]
    if raw is None:
        return MultiUser(), None
    if not isinstance(raw, dict):
        return (
            None,
            "multi_user is a map, e.g. multi_user: {env_file: /etc/aegis/pool.env}",
        )
    for k in raw:
        if k != "env_file":
            return None, f"multi_user: unknown key {k}"
    env_file = raw.get("env_file")
    if env_file is None:
        return MultiUser(), None
    if not isinstance(env_file, str) or not Path(env_file).is_absolute():
        return None, "multi_user.env_file must be an absolute path"
    return MultiUser(env_file=Path(env_file)), None


def parse_env(text: str) -> dict[str, str]:
    """``NAME=value`` lines. Blank and ``#`` lines are ignored, one pair of
    matching quotes around a value is stripped, and any other line is logged
    by number and skipped."""
    out = {}
    for n, line in enumerate(text.splitlines(), 1):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        name, eq, value = line.partition("=")
        if not eq or not ENV_NAME.match(name):
            log.warning("aegis: pool file line %d is not NAME=value; skipped", n)
            continue
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        out[name] = value
    return out


class Pool:
    """The pool file's variables, read again when its mtime changes."""

    def __init__(self, path: Path | None) -> None:
        self.path = path
        self._seen: int | None = None
        self._vars: dict[str, str] = {}

    def get(self) -> dict[str, str]:
        if self.path is None:
            return {}
        try:
            m = self.path.stat().st_mtime_ns
            if m != self._seen:
                self._vars = parse_env(self.path.read_text())
                self._seen = m
        except OSError:
            self._seen, self._vars = None, {}
        return dict(self._vars)


def caps_of(status: str) -> tuple[int, int]:
    """(CapEff, CapAmb) from the text of /proc/<pid>/status."""
    sets = {"CapEff": 0, "CapAmb": 0}
    for line in status.splitlines():
        key, _, value = line.partition(":")
        if key in sets:
            sets[key] = int(value.strip(), 16)
    return sets["CapEff"], sets["CapAmb"]


def boot_problems(
    state_root: Path,
    mu: MultiUser,
    *,
    status: str | None = None,
    uid: int | None = None,
) -> list[str]:
    """Why this server may not start in multi-user mode, one line each; []
    when it may. A state directory it owns with a looser mode is set to 0700,
    not reported."""
    if status is None:
        status = Path("/proc/self/status").read_text()
    if uid is None:
        uid = os.geteuid()
    out = []
    if uid == 0:
        out.append(
            "aegis serve runs as root; multi_user needs an unprivileged service user"
        )
    eff, amb = caps_of(status)
    missing = [c for c, bit in CAPS.items() if not (eff & amb) >> bit & 1]
    if missing:
        out.append(
            f"multi_user needs {', '.join(missing)} in the server's effective and "
            "ambient sets. Add to the unit's [Service]:\n  "
            + "\n  ".join(UNIT_LINES)
            + "\nor run aegis service install --multi-user"
        )
    if shutil.which("setpriv") is None:
        out.append("setpriv, from util-linux, is not installed")
    try:
        st = state_root.stat()
    except FileNotFoundError:
        st = None
    if st is not None:
        if st.st_uid != uid:
            out.append(f"{state_root} belongs to uid {st.st_uid}, not the server's")
        elif st.st_mode & 0o777 != 0o700:
            state_root.chmod(0o700)
            log.warning("aegis: %s set to mode 0700 for multi_user", state_root)
    if mu.env_file is not None:
        try:
            pst = mu.env_file.stat()
        except OSError as e:
            out.append(f"multi_user.env_file {mu.env_file}: {e.strerror}")
        else:
            if pst.st_uid != uid or pst.st_mode & 0o077:
                out.append(
                    f"multi_user.env_file {mu.env_file} must be mode 0600, owned by "
                    "the server's user"
                )
    return out


def setpriv_argv(pw: pwd.struct_passwd | None, setpriv: str = "setpriv") -> list[str]:
    """The prefix that drops every capability, and switches to ``pw`` when
    there is one (None keeps the server's user). Dropping the bounding set
    needs CAP_SETPCAP, which is why the unit carries it."""
    switch = (
        [f"--reuid={pw.pw_uid}", f"--regid={pw.pw_gid}", "--init-groups"] if pw else []
    )
    return [
        setpriv,
        *switch,
        "--inh-caps=-all",
        "--ambient-caps=-all",
        "--bounding-set=-all",
        "--no-new-privs",
    ]


def added(
    env: Mapping[str, str] | None, base: Mapping[str, str] = os.environ
) -> dict[str, str]:
    """What a caller put in ``env`` beyond the server's own environment: the
    variables a switched child still gets."""
    if env is None:
        return {}
    return {k: v for k, v in env.items() if base.get(k) != v}


def user_env(
    pw: pwd.struct_passwd,
    added: Mapping[str, str],
    pool: Mapping[str, str],
    path: str,
) -> dict[str, str]:
    """A switched child's whole environment: its user's own, as a login gets
    it, then what aegis adds, then the pool. Built here and handed to the
    process, never put in its argv, which every user can read."""
    env = {
        "HOME": pw.pw_dir,
        "SHELL": pw.pw_shell or "/bin/sh",
        "USER": pw.pw_name,
        "LOGNAME": pw.pw_name,
        "PATH": path,
    }
    if "TERM" in os.environ:
        env["TERM"] = os.environ["TERM"]
    return env | dict(added) | dict(pool)


def place(
    spec: "SpawnSpec", *, root: Path, role: str | None, linux_user: str | None
) -> "SpawnSpec":
    """``spec`` with the Linux user it runs as. A member's session that would
    start in the aegis root, which belongs to the server, starts in the
    member's home instead."""
    cwd = spec.cwd
    if linux_user and role == "member" and Path(cwd).resolve() == root.resolve():
        try:
            cwd = Path(pwd.getpwnam(linux_user).pw_dir)
        except KeyError:
            raise PermissionError(f"Linux user {linux_user} does not exist") from None
    return replace(spec, linux_user=linux_user, cwd=cwd)
