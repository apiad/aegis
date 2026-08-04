"""The process-launch seam shared by every harness driver.

Both driver families converge on the same shape — an argv, a cwd, an
optional env, and three pipes. ``Launcher`` is that shape as an
interface, so remoteness lives in one place instead of once per driver.
"""
from __future__ import annotations

import asyncio
import os
import shlex
from collections.abc import Mapping
from typing import Protocol, runtime_checkable

from aegis.hosts.models import HostSpec

# claude stream-json and ACP JSON-RPC both put whole tool payloads on one
# line; asyncio's 64 KiB default is far too small. Mirrors
# ``drivers.claude._STREAM_LIMIT``.
STREAM_LIMIT = 16 * 1024 * 1024


@runtime_checkable
class Launcher(Protocol):
    """Starts a harness process somewhere and hands back its pipes."""

    host_key: str
    local_root: str | None

    async def spawn(self, argv: list[str], *, cwd: str,
                    env: dict[str, str] | None
                    ) -> asyncio.subprocess.Process: ...

    def persona_root(self, cwd: str) -> str: ...


class LocalLauncher:
    """Today's behaviour, unchanged: exec here, in this process tree."""

    host_key = "local"

    def __init__(self, local_root: str | None = None) -> None:
        self.local_root = local_root

    def persona_root(self, cwd: str) -> str:
        """Where a relative persona path resolves.

        A persona file always lives in the LOCAL project, even when the
        harness runs on another box — so drivers resolve it against this
        rather than against the (possibly remote) session cwd. Falling
        back to ``cwd`` keeps pre-existing local behaviour identical.
        """
        return self.local_root or cwd

    async def spawn(self, argv: list[str], *, cwd: str,
                    env: dict[str, str] | None
                    ) -> asyncio.subprocess.Process:
        kw: dict = dict(
            cwd=cwd,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            limit=STREAM_LIMIT,
        )
        if env is not None:
            kw["env"] = env
        return await asyncio.create_subprocess_exec(*argv, **kw)


LOCAL = LocalLauncher()


# --- ssh transport --------------------------------------------------------


def env_delta(env: dict[str, str] | None,
              base: Mapping[str, str]) -> dict[str, str]:
    """The environment keys worth sending across the wire.

    A local spawn hands the driver a full copy of ``os.environ`` (that is
    what ``_apply_pre_spawn_hooks`` returns once any hook fires). Shipping
    all of it to another machine would clobber the remote shell's own
    environment with this one's — so only keys that actually differ from
    the local baseline cross: driver-injected ``extra_env`` and whatever a
    pre-spawn hook added or changed.
    """
    if env is None:
        return {}
    return {k: v for k, v in env.items() if base.get(k) != v}


def remote_command(argv: list[str], *, cwd: str,
                   env: dict[str, str], login_shell: bool = False) -> str:
    """The single shell string ssh will run on the far side.

    ``exec`` matters: it replaces the shell with the harness, so signals
    and EOF reach the harness directly rather than a wrapper.

    ``login_shell`` wraps the whole thing in ``bash -lc``. Without it the
    remote command runs in a non-interactive shell that never sources the
    user's profile, so a harness in ``~/.local/bin`` is not on PATH.
    """
    parts = ["cd", shlex.quote(cwd), "&&", "exec"]
    if env:
        parts.append("env")
        parts += [shlex.quote(f"{k}={v}") for k, v in sorted(env.items())]
    parts += [shlex.quote(a) for a in argv]
    inner = " ".join(parts)
    return f"bash -lc {shlex.quote(inner)}" if login_shell else inner


def ssh_argv(spec: HostSpec, control_path: str,
             remote_cmd: str) -> list[str]:
    """A session-carrying ssh invocation that multiplexes over the master.

    ``-T`` disables PTY allocation: stream-json and ACP JSON-RPC need a
    clean byte stream, and a PTY would inject echo and line discipline
    into the middle of the protocol.
    """
    return [
        "ssh", "-T",
        "-o", f"ControlPath={control_path}",
        *spec.ssh_opts,
        spec.ssh,
        remote_cmd,
    ]


def _substitute_mcp_url(argv: list[str], url: str) -> list[str]:
    """Fill in the MCP URL that wasn't known when argv was built.

    ``build_argv`` bakes ``mcp_config_json(mcp_url)`` into the argv at
    session-construction time, but a remote session's URL depends on the
    port sshd allocates when the tunnel opens — which is later. The
    registry hands drivers an empty URL as a placeholder; this rewrites
    exactly that one element just before exec. Matching on anything
    looser (an empty string, a substring) would rewrite unrelated
    arguments.
    """
    from aegis.mcp import mcp_config_json
    placeholder = mcp_config_json("")
    real = mcp_config_json(url)
    return [real if a == placeholder else a for a in argv]


class SshLauncher:
    """Runs the harness on another machine, over a shared ControlMaster."""

    def __init__(self, conn, spec: HostSpec,
                 local_root: str | None = None) -> None:
        self._conn = conn
        self._spec = spec
        self.host_key = spec.name
        self.local_root = local_root
        # Bytes ssh itself wrote — the difference between "the harness
        # exited" and "the link died", which the session needs at EOF.
        self.stderr_tail: list[bytes] = []
        self._proc: asyncio.subprocess.Process | None = None
        self._stderr_task: asyncio.Task | None = None

    def persona_root(self, cwd: str) -> str:
        """A persona file lives in the LOCAL project even when the
        harness runs remotely, so it never resolves under the remote
        cwd."""
        return self.local_root or "."

    async def spawn(self, argv: list[str], *, cwd: str,
                    env: dict[str, str] | None
                    ) -> asyncio.subprocess.Process:
        await self._conn.ensure_open()
        argv = _substitute_mcp_url(argv, self._conn.remote_mcp_url)
        cmd = remote_command(argv, cwd=cwd, env=env_delta(env, os.environ),
                             login_shell=self._spec.login_shell)
        proc = await asyncio.create_subprocess_exec(
            *ssh_argv(self._spec, self._conn.control_path, cmd),
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            limit=STREAM_LIMIT,
        )
        self._proc = proc
        return proc

    def watch_stderr(self) -> None:
        """Start draining ssh's stderr into the ring buffer.

        **Opt-in, and only for a caller that will not read stderr
        itself** — a pipe has exactly one legitimate reader. ClaudeSession
        never touches stderr (so draining it here is a strict improvement:
        otherwise a chatty remote fills the pipe and blocks), whereas
        AcpSession runs its own drain and must not be raced.
        """
        proc = self._proc
        if proc is None or proc.stderr is None or self._stderr_task:
            return
        self._stderr_task = asyncio.create_task(self._drain_stderr(proc))

    async def _drain_stderr(self, proc) -> None:
        """Keep the last of what ssh said. This is where the difference
        between 'harness exited' and 'link died' is written down."""
        try:
            async for raw in proc.stderr:
                self.stderr_tail.append(raw.rstrip())
                del self.stderr_tail[:-40]
        except Exception:                                    # noqa: BLE001
            pass

    def link_failure(self):
        """A ``RemoteLinkLost`` if ssh died, else ``None``.

        rc 0 means the harness ended normally and the link was fine.
        """
        from aegis.hosts.errors import RemoteLinkLost
        proc = self._proc
        if proc is None or proc.returncode in (None, 0):
            return None
        detail = b"\n".join(self.stderr_tail).decode("utf-8", "replace")
        return RemoteLinkLost(self._spec.name, detail.strip())
