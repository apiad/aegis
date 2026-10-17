"""Run the token bench: each task in bench/tasks/ as stock Claude Code and as an
aegis session, fenced to a folder under /tmp, then the hidden check and the count.

The design is docs/superpowers/specs/2026-10-17-token-bench-design.html; the
counting and the comparison live in bench_tokens_count.py, which reads files only.

Every run gets a fresh root under /tmp holding ``work`` (the task's starting
state, where the agent works) and ``config`` (an empty CLAUDE_CONFIG_DIR, so no
user instructions, skills, plugins, hooks or MCP servers load). Never inside a
repository: a CLAUDE.md in a parent directory would load into the prompt.

The fence is Claude Code's own sandbox, written to ``work/.claude/settings.json``
so both arms run under the same one. Bash commands may write only inside the
working directory and the per-user temp directory, and may not read /home: the
``Read(//home/**)`` deny rule is merged into the sandbox's denyRead, and it also
stops the Read tool. The sandbox may not fall back to running a command
unsandboxed, and Claude Code refuses to start when bubblewrap cannot. So the
agent's PATH drops every entry under the home directory: a ``python3`` there
could not even be executed.

Every claude an arm starts, and everything it starts, runs in a systemd scope
with a memory cap. A seeded bug (ledger-fix's month rollover) allocates without
bound, and on a small host one runaway test would take the machine down. Not
``ulimit -v``: it caps address space, and both node and AddressSanitizer reserve
terabytes of it they never touch.
"""

import json
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import tomllib
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TASKS = ROOT / "bench" / "tasks"
# Per claude process tree: the CLI, its Bash commands, a compile and an ASan run
# fit with room to spare, and three arms at a time still leave a 7 GB host alive.
MEMORY_MAX = "2G"

APPARMOR_PROFILE = """\
abi <abi/4.0>,
include <tunables/global>
profile bwrap /usr/bin/bwrap flags=(unconfined) {
  userns,
}"""


class FenceUnavailable(Exception):
    """The sandbox cannot start here. The message holds the reason and the fix."""


def preflight() -> None:
    """Refuse to run anything unless the fence and the memory cap both work."""
    missing = [b for b in ("bwrap", "socat") if shutil.which(b) is None]
    if missing:
        raise FenceUnavailable(
            f"{' and '.join(missing)} not on PATH: Claude Code's sandbox needs "
            "bubblewrap and socat. Fix: sudo apt install bubblewrap socat"
        )
    r = subprocess.run(
        ["bwrap", "--ro-bind", "/", "/", "--unshare-net", "true"],
        capture_output=True,
        text=True,
    )
    if r.returncode != 0:
        err = r.stderr.strip()
        if "setting up uid map" in err or "RTM_NEWADDR" in err:
            raise FenceUnavailable(
                f"bwrap cannot create a user namespace ({err}): AppArmor restricts "
                "unprivileged user namespaces. Fix: grant userns to bubblewrap alone, "
                "not the restriction turned off. Write /etc/apparmor.d/bwrap:\n"
                f"{APPARMOR_PROFILE}\nthen: sudo apparmor_parser -r /etc/apparmor.d/bwrap"
            )
        raise FenceUnavailable(f"bwrap fails: {err}")
    r = subprocess.run(capped(["true"]), env=_with_runtime_dir(dict(os.environ)))
    if r.returncode != 0:
        raise FenceUnavailable(
            "systemd-run --user --scope fails, so a run's memory cannot be capped. "
            "Fix: run from a login session with a user systemd (loginctl enable-linger)"
        )


def _with_runtime_dir(env: dict) -> dict:
    # systemd-run --user finds the user bus through XDG_RUNTIME_DIR, which a
    # service or an ssh command without a login session may not set.
    env.setdefault("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}")
    return env


def capped(argv: list[str]) -> list[str]:
    """argv run in a transient scope with the memory cap and no swap. The scope
    execs the command, so it keeps its pid."""
    return [
        "systemd-run",
        "--user",
        "--scope",
        "--quiet",
        "-p",
        f"MemoryMax={MEMORY_MAX}",
        "-p",
        "MemorySwapMax=0",
        "--",
        *argv,
    ]


def fence_settings(config: Path | None = None) -> dict:
    """The project settings written into every run's work folder. ``config`` is
    the run's private config directory, denied too: it holds the login."""
    deny = ["Read(//home/**)", "Edit(//home/**)"]
    if config is not None:
        deny += [f"Read(/{config}/**)", f"Edit(/{config}/**)"]
    return {
        "sandbox": {
            "enabled": True,
            "failIfUnavailable": True,
            "allowUnsandboxedCommands": False,
            "autoAllowBashIfSandboxed": True,
        },
        "permissions": {"deny": deny},
    }


@dataclass
class Run:
    task: Path
    root: Path
    work: Path
    config: Path
    auth: str
    turns: list[str]
    timeout_s: int


def _login(config: Path, timeout_s: int) -> str:
    """How the clean configuration logs in: a long-lived token, an API key, or
    the person's credentials copied without their refresh token. A copy that
    refreshed would rotate the refresh token under the person's own sessions,
    so the copy cannot refresh, and a token that would expire mid-run is refused."""
    if os.environ.get("CLAUDE_CODE_OAUTH_TOKEN"):
        return "oauth-token"
    if os.environ.get("ANTHROPIC_API_KEY"):
        return "api-key"
    src = Path.home() / ".claude" / ".credentials.json"
    if not src.exists():
        raise RuntimeError(
            "no login for the bench: set CLAUDE_CODE_OAUTH_TOKEN (from "
            "`claude setup-token`) or ANTHROPIC_API_KEY, or log in to Claude Code"
        )
    creds = json.loads(src.read_text())
    oauth = creds.get("claudeAiOauth", {})
    oauth.pop("refreshToken", None)
    left = oauth.get("expiresAt", 0) / 1000 - time.time()
    if left < timeout_s + 300:
        raise RuntimeError(
            f"the Claude Code login expires in {left / 60:.0f} min, before this "
            f"run's {timeout_s // 60} min timeout. Use any claude once to refresh it, "
            "or set CLAUDE_CODE_OAUTH_TOKEN"
        )
    dst = config / ".credentials.json"
    dst.write_text(json.dumps(creds))
    dst.chmod(0o600)
    return "credentials-copy"


def prepare(task: Path) -> Run:
    """A fresh root under /tmp with the task's starting state in ``work``, the
    fence in its project settings, and an empty private config that can log in."""
    meta = tomllib.loads((task / "task.toml").read_text())
    root = Path(tempfile.mkdtemp(prefix="tokbench-", dir="/tmp"))
    work, config = root / "work", root / "config"
    work.mkdir()
    config.mkdir()
    subprocess.run(
        ["bash", str(task / "setup.sh"), str(work)],
        check=True,
        capture_output=True,
        timeout=300,
    )
    (work / ".claude").mkdir(exist_ok=True)
    (work / ".claude" / "settings.json").write_text(
        json.dumps(fence_settings(config), indent=2)
    )
    # The fence is the bench's, not the task's: keep it out of `git status`.
    with open(work / ".git" / "info" / "exclude", "a") as f:
        f.write("/.claude/\n")
    turns = [(task / "instruction.md").read_text().strip()]
    if (task / "followups.md").exists():
        text = (task / "followups.md").read_text()
        turns += [t.strip() for t in text.split("\n---\n") if t.strip()]
    timeout_s = int(meta["timeout_s"])
    return Run(task, root, work, config, _login(config, timeout_s), turns, timeout_s)


def agent_path() -> str:
    """PATH for the agent's commands: no entry under the home directory, which
    the fence denies, so ``python3`` is the system's."""
    home = str(Path.home())
    keep = [
        p
        for p in os.environ.get("PATH", "").split(os.pathsep)
        if p and not (p == home or p.startswith(home + os.sep))
    ]
    return os.pathsep.join(keep)


def interpreter() -> str:
    """Which python3 the agent gets and its pytest, recorded in each result."""
    py = shutil.which("python3", path=agent_path())
    if py is None:
        return "none"
    r = subprocess.run(
        [
            py,
            "-c",
            "import pytest, sys; print(sys.version.split()[0], pytest.__version__)",
        ],
        capture_output=True,
        text=True,
    )
    ver = r.stdout.split()
    return (
        f"{py} {ver[0]} pytest {ver[1]}"
        if r.returncode == 0
        else f"{py} without pytest"
    )


def arm_env(run: Run) -> dict:
    """The environment of everything a run starts: the private config, the
    agent's PATH, no aegis or Claude Code variables of the session that started
    the bench, which would point the agent at the person's server."""
    env = {
        k: v
        for k, v in os.environ.items()
        if not k.startswith("AEGIS_")
        and k not in ("CLAUDECODE", "VIRTUAL_ENV", "CLAUDE_CODE_ENTRYPOINT")
        and not (k.startswith("CLAUDE_CODE_") and k != "CLAUDE_CODE_OAUTH_TOKEN")
    }
    env["CLAUDE_CONFIG_DIR"] = str(run.config)
    env["PATH"] = agent_path()
    return _with_runtime_dir(env)


@dataclass
class ArmResult:
    walls: list[float]
    error: str | None


def _tail(text: str, n: int = 400) -> str:
    return text.strip()[-n:]


def run_group(argv: list[str], cwd: Path, env: dict, timeout: float):
    """argv in a process group of its own, killed whole at the timeout: claude
    leaves its Bash commands running when only it is killed. None on timeout."""
    p = subprocess.Popen(
        argv,
        cwd=cwd,
        env=env,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        start_new_session=True,
    )
    try:
        out, err = p.communicate(timeout=max(1.0, timeout))
    except subprocess.TimeoutExpired:
        os.killpg(p.pid, signal.SIGKILL)
        p.communicate()
        return None
    return subprocess.CompletedProcess(argv, p.returncode, out, err)


def run_stock(run: Run, model: str, effort: str, claude: str = "claude") -> ArmResult:
    """Each turn as one ``claude -p`` in the work folder, the follow-ups resuming
    the first turn's session. A claude that fails or prints no JSON ends the run
    as failed, with its stderr's tail; it never raises."""
    exe = shutil.which(claude) or claude
    env = arm_env(run)
    end = time.monotonic() + run.timeout_s
    walls: list[float] = []
    sid = None
    for prompt in run.turns:
        argv = [
            exe,
            "-p",
            prompt,
            "--model",
            model,
            "--effort",
            effort,
            "--permission-mode",
            "auto",
            "--strict-mcp-config",
            "--mcp-config",
            '{"mcpServers":{}}',
            "--output-format",
            "json",
        ]
        if sid:
            argv += ["--resume", sid]
        t0 = time.monotonic()
        p = run_group(capped(argv), run.work, env, end - t0)
        if p is None:
            return ArmResult(walls, "timeout")
        walls.append(time.monotonic() - t0)
        try:
            sid = json.loads(p.stdout)["session_id"]
        except (ValueError, KeyError, TypeError):
            sid = None
        if p.returncode != 0 or sid is None:
            return ArmResult(
                walls, f"claude exited {p.returncode}: {_tail(p.stderr or p.stdout)}"
            )
    return ArmResult(walls, None)


if __name__ == "__main__":
    sys.exit("main() lands with the CLI")
