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

import asyncio
import contextlib
import json
import os
import shlex
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import time
import tomllib
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TASKS = ROOT / "bench" / "tasks"
TOKENS = ROOT / "bench" / "tokens"  # stock baselines and history.jsonl
# Per claude process tree: the CLI, its Bash commands, a compile and an ASan run
# fit with room to spare, and three arms at a time still leave a 7 GB host alive.
MEMORY_MAX = "2G"
# How long prepare() waits for the person's login to be refreshed.
LOGIN_WAIT_S = 3600

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
    so the copy cannot refresh. A token that would expire mid-run is waited on
    until the person's own sessions refresh it, then refused."""
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
    waited_until = time.time() + LOGIN_WAIT_S
    while True:
        creds = json.loads(src.read_text())
        oauth = creds.get("claudeAiOauth", {})
        oauth.pop("refreshToken", None)
        left = oauth.get("expiresAt", 0) / 1000 - time.time()
        if left >= timeout_s + 300:
            break
        if time.time() >= waited_until:
            raise RuntimeError(
                f"the Claude Code login expires in {left / 60:.0f} min, before this "
                f"run's {timeout_s // 60} min timeout, and was not refreshed in "
                f"{LOGIN_WAIT_S // 60} min. Use any claude once to refresh it, "
                "or set CLAUDE_CODE_OAUTH_TOKEN"
            )
        # The person's own Claude Code refreshes it as it nears expiry.
        time.sleep(30)
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


QUIET_S = 10  # seconds in a row with nothing running before a turn counts as over
POLL_S = 0.1


def quiet(app) -> bool:
    """No session on the App busy, no monitor live, no queue task waiting or running."""
    return (
        not any(s.busy for s in app.sessions.sessions.values())
        and not app.monitors.items
        and not any(
            t.status in ("pending", "running") for t in app.queues.tasks.values()
        )
    )


def _wrapper(run: Run, exe: str) -> Path:
    """The claude the App starts: the run's environment and the memory cap. The
    App's own children inherit the bench's process environment, which several
    runs at a time cannot each set; this script sets the run's on every exec.
    The App adds its own AEGIS_* variables, so those stay."""
    env = arm_env(run)
    lines = ["#!/bin/sh"]
    lines += [
        f"unset {k}"
        for k in sorted(os.environ)
        if k not in env and not k.startswith("AEGIS_")
    ]
    lines += [
        f"export {k}={shlex.quote(env[k])}"
        for k in ("CLAUDE_CONFIG_DIR", "PATH", "XDG_RUNTIME_DIR")
    ]
    lines.append(f"exec {shlex.join(capped([exe]))} " + '"$@"')
    path = run.root / "claude"
    path.write_text("\n".join(lines) + "\n")
    path.chmod(0o755)
    return path


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


async def _turn(app, s, prompt: str, ended: dict[str, float]) -> float:
    """Send one turn and wait for quiet. The wall ends when the session last
    ended a turn, not when the quiet window closes: a monitor's wake is part of
    the turn, the wait for quiet is not. Turn ends are taken from the App, not
    polled, because a short turn can start and end between two polls."""
    t0 = time.monotonic()
    await s.send(prompt)
    calm_since = None
    while True:
        await asyncio.sleep(POLL_S)
        now = time.monotonic()
        if not quiet(app):
            calm_since = None
        elif calm_since is None:
            calm_since = now
        elif now - calm_since >= QUIET_S:
            return max(ended.get(s.log_id, t0), t0) - t0


async def run_aegis(
    run: Run, model: str, effort: str, claude: str | None = None
) -> ArmResult:
    """The run as one aegis session on a throwaway App in ``run.root``: real MCP,
    real wakes, real queue workers. Each turn is sent once the App is quiet. A
    turn that ends on needs_you is over, since nobody answers in a bench. At
    ``timeout_s`` every session stops and the App shuts down."""
    import uvicorn

    from aegis.app import App
    from aegis.roots import make_roots
    from aegis.web import build_web

    exe = shutil.which(claude or "claude") or claude or "claude"
    (run.root / ".aegis.yaml").write_text(
        "agents:\n"
        f"  bench: {{harness: claude-code, model: {model}, effort: {effort}, permission: auto}}\n"
        "default_agent: bench\n"
    )
    port = _free_port()
    app = App(
        make_roots(run.root, None),
        claude_bin=str(_wrapper(run, exe)),
        base_url=f"http://127.0.0.1:{port}",
    )
    server = uvicorn.Server(
        uvicorn.Config(
            build_web(app, "bench", {f"127.0.0.1:{port}"}),
            host="127.0.0.1",
            port=port,
            log_level="warning",
        )
    )
    served = asyncio.create_task(server.serve())
    walls: list[float] = []
    error = None
    end = time.monotonic() + run.timeout_s
    try:
        while not server.started:
            if served.done():
                return ArmResult(walls, f"the App did not start: {served.exception()}")
            await asyncio.sleep(0.05)
        ended: dict[str, float] = {}
        host_turn_ended = app.sessions.turn_ended

        def turn_ended(session) -> None:
            ended[session.log_id] = time.monotonic()
            host_turn_ended(session)

        app.sessions.turn_ended = turn_ended
        r = await app.registry.call(
            "session.spawn", {"agent": "bench", "cwd": str(run.work)}
        )
        s = app.sessions.sessions[r["log_id"]]
        for prompt in run.turns:
            walls.append(
                await asyncio.wait_for(
                    _turn(app, s, prompt, ended), end - time.monotonic()
                )
            )
    except TimeoutError:
        error = "timeout"
    finally:
        for s in list(app.sessions.sessions.values()):
            with contextlib.suppress(Exception):
                await s.stop()
        server.should_exit = True
        await asyncio.wait_for(served, 60)
    return ArmResult(walls, error)


def check(run: Run) -> tuple[bool, str]:
    """The task's hidden check, run in place from the task folder against the
    work folder: its helper files are found through its own folder, so nothing
    of tests/ is ever copied where the agent worked. Under the memory cap and
    the agent's PATH, so it uses the agent's python3."""
    test = run.task / "tests" / "test.sh"
    p = run_group(
        capped(["bash", str(test), str(run.work)]), run.work, arm_env(run), 900
    )
    if p is None:
        return False, "FAIL: the check timed out"
    lines = (p.stdout + p.stderr).strip().splitlines()
    return p.returncode == 0, lines[-1] if lines else f"exit {p.returncode}"


def claude_version(claude: str = "claude") -> str:
    out = subprocess.run([claude, "--version"], capture_output=True, text=True).stdout
    return out.split()[0] if out.split() else "unknown"


async def one(task: Path, arm: str, model: str, effort: str, claude: str) -> dict:
    """Prepare, run one arm, check, count: one result."""
    import bench_tokens_count as count

    run = await asyncio.to_thread(prepare, task)
    if arm == "stock":
        res = await asyncio.to_thread(run_stock, run, model, effort, claude)
    else:
        res = await run_aegis(run, model, effort, claude)
    passed, verdict = await asyncio.to_thread(check, run)
    summary = count.summarize(count.read_requests(run.config))
    print(
        f"  {task.name} {arm}: {'pass' if passed else 'FAIL'}, ${summary['cost']:.2f}, "
        f"{summary['requests']} requests, {sum(res.walls):.0f}s",
        flush=True,
    )
    return dict(
        task=task.name,
        arm=arm,
        model=model,
        effort=effort,
        passed=passed and res.error is None,
        verdict=verdict,
        error=res.error,
        wall=round(sum(res.walls), 1),
        walls=[round(w, 1) for w in res.walls],
        auth=run.auth,
        python=interpreter(),
        root=str(run.root),
        **summary,
    )


async def _many(
    jobs: list[tuple[Path, str]], j: int, model: str, effort: str, claude: str
):
    gate = asyncio.Semaphore(j)

    async def go(task: Path, arm: str) -> dict:
        import bench_tokens_count as count

        async with gate:
            try:
                return await one(task, arm, model, effort, claude)
            except Exception as e:  # one broken run is a failed run, not the end
                print(f"  {task.name} {arm}: broke: {e!r}", flush=True)
                return dict(
                    task=task.name,
                    arm=arm,
                    model=model,
                    effort=effort,
                    passed=False,
                    error=repr(e),
                    wall=0.0,
                    **count.summarize([]),
                )

    return await asyncio.gather(*(go(t, a) for t, a in jobs))


def _group(results: list[dict]) -> dict[str, list[dict]]:
    out: dict[str, list[dict]] = {}
    for x in results:
        out.setdefault(x["task"], []).append(x)
    return out


def _prev() -> dict | None:
    path = TOKENS / "history.jsonl"
    lines = path.read_text().splitlines() if path.exists() else []
    if not lines:
        return None
    last = json.loads(lines[-1])
    return {"headline": last["headline"], **last["tasks"]}


def main(argv: list[str] | None = None) -> int:
    import argparse

    import bench_tokens_count as count

    ap = argparse.ArgumentParser(
        description="Run the token bench: aegis against stock Claude Code. Spends quota."
    )
    ap.add_argument("--tasks", help="comma-separated task names (default: all)")
    ap.add_argument("--model", default="claude-opus-5-5")
    ap.add_argument("--effort", default="high")
    ap.add_argument("-j", type=int, default=3, help="runs at a time (default 3)")
    ap.add_argument(
        "--refresh-stock",
        action="store_true",
        help="run the stock arm 3 times per task and write its baseline; no aegis run",
    )
    ap.add_argument(
        "--out", default=".aegis-bench-tokens.json", help="where the result goes"
    )
    ap.add_argument(
        "--record-release",
        metavar="VERSION",
        help="append the result in --out to bench/tokens/history.jsonl; runs nothing",
    )
    ap.add_argument("--claude", default="claude", help=argparse.SUPPRESS)
    a = ap.parse_args(argv)
    out = Path(a.out)

    if a.record_release:
        res = json.loads(out.read_text())
        line = dict(
            version=a.record_release,
            date=time.strftime("%Y-%m-%d"),
            claude=res["claude"],
            model=res["model"],
            effort=res["effort"],
            headline=res["headline"],
            tasks={
                r["task"]: r["ratio"] for r in res["rows"] if r["ratio"] is not None
            },
        )
        TOKENS.mkdir(parents=True, exist_ok=True)
        with open(TOKENS / "history.jsonl", "a") as f:
            f.write(json.dumps(line, ensure_ascii=False) + "\n")
        print(f"recorded {a.record_release}: headline {res['headline']:.2f}")
        return 0

    try:
        preflight()
    except FenceUnavailable as e:
        print(
            f"bench-tokens: the fence cannot start, so nothing runs: {e}",
            file=sys.stderr,
        )
        return 1
    for k in [k for k in os.environ if k.startswith("AEGIS_")]:
        del os.environ[k]  # the App sets its own for the claudes it starts
    names = (
        a.tasks.split(",")
        if a.tasks
        else sorted(p.name for p in TASKS.iterdir() if p.is_dir())
    )
    tasks = [TASKS / n for n in names]
    version = claude_version(a.claude)
    baseline = TOKENS / f"stock-{version}-{a.model}-{a.effort}.json"
    stock = json.loads(baseline.read_text()) if baseline.exists() else {}

    if a.refresh_stock:
        results = asyncio.run(
            _many(
                [(t, "stock") for t in tasks for _ in range(3)],
                a.j,
                a.model,
                a.effort,
                a.claude,
            )
        )
        stock.update(_group(results))
        TOKENS.mkdir(parents=True, exist_ok=True)
        baseline.write_text(json.dumps(stock, indent=1, ensure_ascii=False) + "\n")
        print(
            f"wrote {baseline.relative_to(ROOT) if baseline.is_relative_to(ROOT) else baseline}"
        )
        for name, runs in _group(results).items():
            ok = sum(r["passed"] for r in runs)
            costs = ", ".join(f"${r['cost']:.2f}/{r['requests']}" for r in runs)
            print(f"  {name}: {ok}/3 passed; cost/requests {costs}")
        return 0

    missing = [t.name for t in tasks if t.name not in stock]
    if missing:
        others = sorted(p.name for p in TOKENS.glob("stock-*.json"))
        print(
            f"bench-tokens: no stock baseline for Claude Code {version}, {a.model}, {a.effort}"
            f" covering {', '.join(missing)} (have: {', '.join(others) or 'none'}). "
            "Run it with --refresh-stock first.",
            file=sys.stderr,
        )
        return 2
    stock = {t.name: stock[t.name] for t in tasks}
    prev = _prev()
    results = asyncio.run(
        _many([(t, "aegis") for t in tasks], a.j, a.model, a.effort, a.claude)
    )
    cmp = count.compare(stock, _group(results), prev)
    if cmp.rerun:
        print(
            f"moved more than 25% against the last release, once more: {', '.join(cmp.rerun)}"
        )
        again = asyncio.run(
            _many(
                [(TASKS / n, "aegis") for n in cmp.rerun],
                a.j,
                a.model,
                a.effort,
                a.claude,
            )
        )
        results += again
        cmp = count.compare(stock, _group(results), prev)
    print(count.table(cmp))
    for w in cmp.warnings:
        print(w)
    out.write_text(
        json.dumps(
            dict(
                claude=version,
                model=a.model,
                effort=a.effort,
                headline=cmp.headline,
                failed=cmp.failed,
                rerun=cmp.rerun,
                warnings=cmp.warnings,
                rows=cmp.rows,
                table=count.table(cmp),
                aegis=results,
            ),
            indent=1,
            ensure_ascii=False,
        )
        + "\n"
    )
    return 0


if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    sys.exit(main())
