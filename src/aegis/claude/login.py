"""Claude Code's sign-in: a long-lived token, and a card when sign-in fails.

Several Claude Code processes on one stored sign-in race to refresh it: the
refresh token is single-use, so the processes that lose fail with a 401 and
the stored sign-in can end up revoked (anthropics/claude-code#24317, #27933).
A token from ``claude setup-token`` lasts a year and is never refreshed, so
there is no race. aegis keeps one in ``token_path()`` and gives it to every
Claude Code process it starts as the server's own user, as
``CLAUDE_CODE_OAUTH_TOKEN``, which Claude Code ranks above the stored sign-in.

When a turn fails to sign in (``stream.Result.auth_failed``), ``SignIn`` puts
one card in that session's transcript: the steps to get a token and a field
for it. Other sessions that fail while it is open join its list. The token the
person pastes is checked for its shape, then by one ``claude -p`` request in a
scratch config directory, written here at mode 0600, and every session on the
list restarts and is told to go on. aegis never runs any step of the OAuth
flow itself: the person runs ``claude setup-token``, which is Anthropic's.

Spec: docs/superpowers/specs/2026-10-23-claude-token-login-design.html.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import secrets
import stat
import tempfile
import time
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import TYPE_CHECKING

from .. import launch

if TYPE_CHECKING:
    from ..session import Session

log = logging.getLogger("aegis.claude.login")

VAR = "CLAUDE_CODE_OAUTH_TOKEN"
PREFIX = "sk-ant-oat01-"
# What a setup-token is made of after its prefix.
_BODY = re.compile(r"[A-Za-z0-9_-]{20,400}")
LIFE_S = 365 * 86400
WARN_S = 14 * 86400
PROBE_TIMEOUT_S = 60.0
PROBE_MODEL = "haiku"
FAILED_LINE = "Claude sign-in failed"


def token_path() -> Path:
    """Where the server's token is kept. ``AEGIS_CLAUDE_TOKEN_FILE`` overrides."""
    if override := os.environ.get("AEGIS_CLAUDE_TOKEN_FILE"):
        return Path(override)
    base = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    return Path(base) / "aegis" / "claude-oauth-token"


def shape_error(value: str) -> str | None:
    """Why ``value`` is not a setup-token, in words for the person, or None."""
    v = value.strip()
    if not v:
        return "Paste the token first."
    if "#" in v:
        # The browser's authorization code is "<code>#<state>": the commonest
        # wrong paste, since the browser shows it and the terminal waits for it.
        return (
            "This is the code from the browser. Paste it into the terminal "
            "running claude setup-token; the token is the line it prints next."
        )
    if not v.startswith(PREFIX):
        return f"A Claude Code token starts with {PREFIX}"
    if not _BODY.fullmatch(v[len(PREFIX) :]):
        return "The token has characters a Claude Code token does not; paste the whole line again."
    return None


def file_problem(path: Path) -> str | None:
    """Why the token file must not be used, or None when it may. A missing
    file is not a problem: the sessions use the stored sign-in."""
    try:
        st = path.stat()
    except FileNotFoundError:
        return None
    except OSError as e:
        return f"{path}: {e.strerror}"
    if st.st_uid != os.getuid() or stat.S_IMODE(st.st_mode) & 0o077:
        return f"{path} must be mode 0600, owned by the server's user"
    return None


def read_token(path: Path | None = None) -> str | None:
    """The token, or None when there is no usable one. Never raises."""
    p = path or token_path()
    if file_problem(p) is not None:
        return None
    try:
        value = p.read_text().strip()
    except OSError:
        return None
    return value if value and shape_error(value) is None else None


def with_token(
    env: Mapping[str, str] | None, user: str | None
) -> dict[str, str] | None:
    """A Claude Code child's environment with the token added: ``env`` (the
    server's own when None) unchanged for a member's process, or when the
    variable is already set (the server's environment or the pool file win),
    or when there is no usable token file."""
    if user is not None:
        return None if env is None else dict(env)
    base = os.environ if env is None else env
    if base.get(VAR):
        return None if env is None else dict(env)
    token = read_token()
    if token is None:
        return None if env is None else dict(env)
    return {**base, VAR: token}


def write_token(token: str, path: Path | None = None) -> Path:
    """Write the token atomically at mode 0600, its directory at 0700."""
    p = path or token_path()
    p.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=p.parent, prefix=".claude-token-")
    try:
        with os.fdopen(fd, "w") as f:
            f.write(token.strip() + "\n")
        os.chmod(tmp, 0o600)
        os.replace(tmp, p)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise
    return p


def expiry(path: Path | None = None) -> tuple[float, float] | None:
    """When the token file was written and when its token expires, about a
    year later, as Unix times; None without a usable file."""
    p = path or token_path()
    if read_token(p) is None:
        return None
    written = p.stat().st_mtime
    return written, written + LIFE_S


async def probe(
    binary: str, token: str, timeout: float = PROBE_TIMEOUT_S
) -> str | None:
    """Ask Claude Code itself whether ``token`` signs in: one short request on
    the cheapest model, in a scratch config directory so the stored sign-in
    is neither read nor written. None when it answered; else why not."""
    with tempfile.TemporaryDirectory(prefix="aegis-claude-probe-") as scratch:
        # A key in the server's environment outranks the token and would
        # make any token pass.
        env = {
            k: v
            for k, v in os.environ.items()
            if k not in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN")
        }
        env |= {VAR: token, "CLAUDE_CONFIG_DIR": scratch}
        try:
            proc = await launch.child(
                [
                    binary,
                    "-p",
                    "--model",
                    PROBE_MODEL,
                    "--output-format",
                    "json",
                    "--tools",
                    "",
                    "--setting-sources",
                    "",
                ],
                user=None,
                via=launch.resolve_via(),
                cwd=scratch,
                env=env,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
            )
        except OSError as e:
            return f"could not run {binary}: {e.strerror}"
        try:
            out, _ = await asyncio.wait_for(
                proc.communicate(b"Reply with the word ok."), timeout
            )
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()
            return f"Claude Code did not answer in {timeout:g}s"
    try:
        res = json.loads(out)
    except ValueError:
        return f"Claude Code exited with code {proc.returncode} and no answer"
    if not isinstance(res, dict):
        return "Claude Code gave no answer"
    if res.get("is_error"):
        return str(res.get("result") or "Claude Code could not sign in")
    return None


class SignIn:
    """One open card at a time, and the sessions waiting on it.

    ``sessions`` maps a log id to its session; ``binary`` is Claude Code's;
    ``toast`` shows the person a line. All state is in memory: after a server
    restart the card still takes a token, and only the list is empty."""

    def __init__(
        self,
        sessions: Mapping[str, "Session"],
        binary: Callable[[], str],
        toast: Callable[[str, "Session"], object],
    ) -> None:
        self._sessions = sessions
        self._binary = binary
        self._toast = toast
        self.card: tuple[str, str] | None = None  # (log id, card id)
        self.waiting: list[str] = []

    def failed(self, s: "Session") -> None:
        """A turn of ``s`` failed to sign in. Members' sessions sign in with
        their own account, which this server's token must not replace."""
        if s.spec.linux_user is not None or s.log_id in self.waiting:
            return
        self.waiting.append(s.log_id)
        if self.card is None or self.card[0] not in self._sessions:
            self.card = (s.log_id, f"login-{secrets.token_hex(4)}")
            self._toast(f"{FAILED_LINE}: {s.handle} needs a new token", s)
        holder = self._sessions[self.card[0]]
        holder.record_card(
            {
                "kind": "claude_login",
                "card_id": self.card[1],
                "handles": [
                    self._sessions[k].handle
                    for k in self.waiting
                    if k in self._sessions
                ],
                "path": str(token_path()),
            }
        )

    async def provide(self, holder: "Session", card_id: str, token: str) -> dict:
        """Check ``token``, keep it, restart the waiting sessions. Raises
        ``ValueError`` with the person's words when it is refused."""
        token = token.strip()
        if why := shape_error(token):
            raise ValueError(why)
        if why := await probe(self._binary(), token):
            raise ValueError(f"Claude Code refused it: {why}")
        await asyncio.to_thread(write_token, token)
        waiting, self.waiting = self.waiting, []
        if self.card is not None and self.card[1] == card_id:
            self.card = None
        restarted = []
        for key in waiting:
            s = self._sessions.get(key)
            if s is None or s.archived:
                continue
            try:
                await s.restart()
                await s.deliver(
                    f"> from aegis:login · renewed · {time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())}",
                    "The Claude sign-in was renewed. Continue where you stopped.",
                )
                restarted.append(s.handle)
            except Exception as e:  # one session must not keep the others down
                log.warning("%s: restart after a new sign-in failed: %s", s.handle, e)
        holder.record_card(
            {"kind": "claude_login_done", "card_id": card_id, "restarted": restarted}
        )
        return {"restarted": restarted}
