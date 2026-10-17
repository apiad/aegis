"""The token bench's runner on fakes: the fence, the stock arm, the aegis arm and
the CLI. No test here spends quota; the live fence test is in test_live.py."""

import importlib.util
import shutil
import stat
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))
spec = importlib.util.spec_from_file_location(
    "bench_tokens", SCRIPTS / "bench_tokens.py"
)
r = importlib.util.module_from_spec(spec)
sys.modules["bench_tokens"] = r
spec.loader.exec_module(r)

TASKS = r.TASKS


def _exe(path: Path, body: str) -> Path:
    path.write_text(body)
    path.chmod(path.stat().st_mode | stat.S_IEXEC)
    return path


@pytest.fixture(autouse=True)
def _token_login(monkeypatch):
    # Never copy the person's credentials into a test's run.
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "test-token")


@pytest.fixture
def _cleanup():
    roots: list[Path] = []
    yield roots
    for root in roots:
        shutil.rmtree(root, ignore_errors=True)


def _prepare(task: str, roots: list[Path]):
    run = r.prepare(TASKS / task)
    roots.append(run.root)
    return run


def test_preflight_names_the_fix_when_bwrap_is_missing(monkeypatch):
    monkeypatch.setenv("PATH", "/nonexistent")
    with pytest.raises(r.FenceUnavailable, match="bubblewrap"):
        r.preflight()


def test_preflight_names_the_apparmor_fix_when_userns_is_refused(monkeypatch, tmp_path):
    _exe(
        tmp_path / "bwrap",
        "#!/bin/sh\necho 'bwrap: setting up uid map: Permission denied' >&2\nexit 1\n",
    )
    _exe(tmp_path / "socat", "#!/bin/sh\nexit 0\n")
    monkeypatch.setenv("PATH", str(tmp_path))
    with pytest.raises(r.FenceUnavailable, match="apparmor") as e:
        r.preflight()
    assert "userns," in str(e.value)


def test_fence_settings_enable_the_sandbox_and_deny_home():
    s = r.fence_settings()
    assert s["sandbox"]["enabled"] is True
    assert s["sandbox"]["allowUnsandboxedCommands"] is False
    assert s["sandbox"]["failIfUnavailable"] is True
    assert {"Read(//home/**)", "Edit(//home/**)"} <= set(s["permissions"]["deny"])


def test_prepare_builds_work_under_tmp_with_settings_and_a_private_config(_cleanup):
    run = _prepare("ledger-fix", _cleanup)
    assert (
        run.work.is_relative_to("/tmp")
        and (run.work / ".claude/settings.json").exists()
    )
    assert (run.work / ".git").exists() and run.auth in {
        "oauth-token",
        "api-key",
        "credentials-copy",
    }
    assert run.config.is_dir() and not any(run.config.iterdir())
    assert f"Read(/{run.config}/**)" in (run.work / ".claude/settings.json").read_text()


def test_prepare_splits_followups_into_turns(_cleanup):
    assert len(_prepare("ledger-fix", _cleanup).turns) == 3


def test_a_copied_login_cannot_refresh(_cleanup, monkeypatch, tmp_path):
    import json
    import time

    monkeypatch.delenv("CLAUDE_CODE_OAUTH_TOKEN")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))
    (tmp_path / ".claude").mkdir()
    oauth = {
        "accessToken": "a",
        "refreshToken": "r",
        "expiresAt": (time.time() + 7200) * 1000,
    }
    (tmp_path / ".claude/.credentials.json").write_text(
        json.dumps({"claudeAiOauth": oauth})
    )
    run = _prepare("ledger-fix", _cleanup)
    copied = json.loads((run.config / ".credentials.json").read_text())["claudeAiOauth"]
    assert run.auth == "credentials-copy" and "refreshToken" not in copied
    oauth["expiresAt"] = (time.time() + 60) * 1000
    (tmp_path / ".claude/.credentials.json").write_text(
        json.dumps({"claudeAiOauth": oauth})
    )
    with pytest.raises(RuntimeError, match="expires"):
        _prepare("ledger-fix", _cleanup)


def test_the_agent_path_leaves_out_the_home_directory(monkeypatch):
    home = str(Path.home())
    monkeypatch.setenv("PATH", f"{home}/.local/bin:/usr/bin:{home}/x/.venv/bin:/bin")
    assert r.agent_path() == "/usr/bin:/bin"


def test_the_arm_env_drops_the_starting_sessions_variables(_cleanup, monkeypatch):
    monkeypatch.setenv("AEGIS_SESSION_TOKEN", "x")
    monkeypatch.setenv("CLAUDECODE", "1")
    run = _prepare("ledger-fix", _cleanup)
    env = r.arm_env(run)
    assert "AEGIS_SESSION_TOKEN" not in env and "CLAUDECODE" not in env
    assert env["CLAUDE_CONFIG_DIR"] == str(run.config)
    assert env["CLAUDE_CODE_OAUTH_TOKEN"] == "test-token"


FAKE_P = Path(__file__).resolve().parent / "fixtures" / "tokbench" / "fake_claude_p.py"


@pytest.fixture
def fake_p(tmp_path, monkeypatch) -> str:
    """The fake claude -p as an executable; the scope wrapper is left out, since
    CI has no user systemd."""
    monkeypatch.setattr(r, "capped", lambda argv: argv)
    return str(
        _exe(
            tmp_path / "claude", f'#!/bin/sh\nexec "{sys.executable}" "{FAKE_P}" "$@"\n'
        )
    )


def _argvs(run) -> list[list[str]]:
    import json

    return [json.loads(x) for x in (run.config / "argv.jsonl").read_text().splitlines()]


def test_stock_sends_each_turn_and_resumes_the_same_session(fake_p, _cleanup):
    import bench_tokens_count as c

    run = _prepare("ledger-fix", _cleanup)
    res = r.run_stock(run, "m", "high", fake_p)
    assert res.error is None and len(res.walls) == 3
    reqs = c.read_requests(run.config)
    assert len(reqs) == 3 and len({q.transcript for q in reqs}) == 1
    argvs = _argvs(run)
    assert "--resume" not in argvs[0] and all("--resume" in a for a in argvs[1:])
    assert [a[1] for a in argvs] == run.turns


def test_stock_passes_the_fence_mode_model_and_no_mcp(fake_p, _cleanup):
    run = _prepare("ledger-fix", _cleanup)
    r.run_stock(run, "claude-opus-5-5", "high", fake_p)
    a = _argvs(run)[0]
    assert a[a.index("--permission-mode") + 1] == "auto"
    assert a[a.index("--model") + 1] == "claude-opus-5-5"
    assert a[a.index("--effort") + 1] == "high"
    assert (
        "--strict-mcp-config" in a
        and a[a.index("--mcp-config") + 1] == '{"mcpServers":{}}'
    )


def test_a_crashing_claude_is_a_failed_run_not_an_abort(fake_p, _cleanup, monkeypatch):
    monkeypatch.setenv("FAKE_P_MODE", "crash")
    res = r.run_stock(_prepare("ledger-fix", _cleanup), "m", "high", fake_p)
    assert res.error.startswith("claude exited 2") and res.error.endswith(
        "something broke"
    )
    assert len(res.walls) == 1


def test_a_stock_turn_past_the_timeout_is_a_timeout(fake_p, _cleanup, monkeypatch):
    monkeypatch.setenv("FAKE_P_MODE", "sleep")
    run = _prepare("ledger-fix", _cleanup)
    run.timeout_s = 1
    assert r.run_stock(run, "m", "high", fake_p).error == "timeout"


# -- the aegis arm, on tests/fake_claude.py; each waits out a 10 s quiet window --


def _aegis_run(roots, turns, timeout_s=120):
    run = _prepare("ledger-fix", roots)
    run.turns, run.timeout_s = turns, timeout_s
    return run


def _mcp(tool: str, **args) -> str:
    import json

    return f"/mcp {tool} {json.dumps(args)}"


def _fakes_alive(home: Path) -> list[int]:
    """Live processes whose environment names this test's fake-claude home."""
    mark = f"FAKE_CLAUDE_HOME={home}".encode()
    alive = []
    for p in Path("/proc").iterdir():
        try:
            if p.name.isdigit() and mark in (p / "environ").read_bytes().split(b"\0"):
                alive.append(int(p.name))
        except OSError:
            pass
    return alive


@pytest.fixture
def no_cap(monkeypatch):
    monkeypatch.setattr(r, "capped", lambda argv: argv)


@pytest.mark.slow
async def test_a_session_waiting_on_its_monitor_is_not_quiet(
    no_cap, fake_claude, _cleanup, tmp_path
):
    import asyncio

    flag = tmp_path / "flag"
    start = _mcp(
        "monitor_start",
        description="w",
        done=f"test -f {flag}",
        progress=None,
        interval_s=1,
    )
    run = _aegis_run(_cleanup, [start])
    task = asyncio.create_task(r.run_aegis(run, "m", "high", fake_claude))
    await asyncio.sleep(r.QUIET_S + 3)
    assert not task.done()
    flag.touch()
    res = await asyncio.wait_for(task, 60)
    assert res.error is None and res.walls[0] > r.QUIET_S


@pytest.mark.slow
async def test_aegis_arm_sends_every_followup_and_times_each_turn(
    no_cap, fake_claude, _cleanup
):
    run = _aegis_run(_cleanup, ["/sleep 1", "/sleep 2", "hello"])
    res = await r.run_aegis(run, "m", "high", fake_claude)
    assert res.error is None and len(res.walls) == 3
    assert 1 <= res.walls[0] < r.QUIET_S and 2 <= res.walls[1] < r.QUIET_S
    assert res.walls[2] < r.QUIET_S


@pytest.mark.slow
async def test_a_turn_ending_on_needs_you_still_gets_the_followups(
    no_cap, fake_claude, _cleanup
):
    ask = _mcp("turn_end", attention="needs_you", line="Which one?")
    run = _aegis_run(_cleanup, [ask, "hello"])
    res = await r.run_aegis(run, "m", "high", fake_claude)
    assert res.error is None and len(res.walls) == 2


@pytest.mark.slow
async def test_a_hung_agent_is_stopped_at_the_timeout_and_the_app_shut_down(
    no_cap, fake_claude, _cleanup, tmp_path
):
    run = _aegis_run(_cleanup, ["/sleep 600"], timeout_s=5)
    res = await r.run_aegis(run, "m", "high", fake_claude)
    assert res.error == "timeout" and res.walls == []
    assert _fakes_alive(tmp_path / "fake-home") == []


# -- the CLI, with both arms patched: no model runs --


@pytest.fixture
def cli(tmp_path, monkeypatch, _cleanup):
    """One trivial task, the baselines in tmp_path, the arms faked: every stock
    run costs one 1k request, every aegis run what ``costs`` says next."""
    import json

    tasks, tokens = tmp_path / "tasks", tmp_path / "tokens"
    t = tasks / "tiny"
    (t / "tests").mkdir(parents=True)
    (t / "task.toml").write_text(
        'category = "x"\nexpert_minutes = 20\ntimeout_s = 60\n'
    )
    (t / "instruction.md").write_text("do it")
    (t / "setup.sh").write_text(
        'cd "$1" && git init -q && git commit -q --allow-empty -m s && git tag start\n'
    )
    (t / "tests" / "test.sh").write_text("echo PASS\n")
    monkeypatch.setattr(r, "TASKS", tasks)
    monkeypatch.setattr(r, "TOKENS", tokens)
    monkeypatch.setattr(r, "preflight", lambda: None)
    monkeypatch.setattr(r, "capped", lambda argv: argv)
    monkeypatch.setattr(r, "claude_version", lambda claude="claude": "2.1.296")
    real_prepare = r.prepare

    def prepare(task):
        run = real_prepare(task)
        _cleanup.append(run.root)
        return run

    monkeypatch.setattr(r, "prepare", prepare)
    calls = {"stock": 0, "aegis": 0}
    costs: list[int] = []

    def transcript(run, ctx):
        d = run.config / "projects" / "x"
        d.mkdir(parents=True)
        line = {
            "type": "assistant",
            "requestId": "q",
            "timestamp": "1",
            "message": {
                "usage": {"cache_creation_input_tokens": ctx, "output_tokens": 0}
            },
        }
        (d / "t.jsonl").write_text(json.dumps(line) + "\n")

    def run_stock(run, model, effort, claude="claude"):
        calls["stock"] += 1
        transcript(run, 1000)
        return r.ArmResult([1.0], None)

    async def run_aegis(run, model, effort, claude=None):
        calls["aegis"] += 1
        transcript(run, costs.pop(0) if costs else 1000)
        return r.ArmResult([2.0, 3.0], None)

    monkeypatch.setattr(r, "run_stock", run_stock)
    monkeypatch.setattr(r, "run_aegis", run_aegis)
    return dict(tokens=tokens, calls=calls, costs=costs, out=tmp_path / "out.json")


def test_refuses_a_baseline_from_another_claude_version(cli, capsys):
    cli["tokens"].mkdir()
    (cli["tokens"] / "stock-2.1.200-claude-opus-5-5-high.json").write_text(
        '{"tiny": []}'
    )
    assert r.main(["--out", str(cli["out"])]) == 2
    err = capsys.readouterr().err
    assert "--refresh-stock" in err and "2.1.296" in err and cli["calls"]["aegis"] == 0


def test_refresh_stock_runs_each_task_three_times_and_writes_the_baseline(cli):
    import json

    assert r.main(["--refresh-stock"]) == 0
    base = json.loads(
        (cli["tokens"] / "stock-2.1.296-claude-opus-5-5-high.json").read_text()
    )
    assert cli["calls"] == {"stock": 3, "aegis": 0} and len(base["tiny"]) == 3


def test_one_result_per_run_carries_task_arm_pass_wall_and_the_summary(cli):
    import json

    r.main(["--refresh-stock"])
    assert r.main(["--out", str(cli["out"])]) == 0
    res = json.loads(cli["out"].read_text())
    (one,) = res["aegis"]
    assert one["task"] == "tiny" and one["arm"] == "aegis" and one["passed"] is True
    assert one["wall"] == 5.0 and one["requests"] == 1 and one["verdict"] == "PASS"
    assert {"cost", "first_ctx", "bookkeeping_cost", "auth", "python"} <= one.keys()
    assert res["rows"][0]["ratio"] == pytest.approx(1.0) and "| tiny |" in res["table"]


def test_a_task_marked_for_rerun_runs_once_more_and_is_averaged(cli):
    import json

    r.main(["--refresh-stock"])
    (cli["tokens"] / "history.jsonl").write_text(
        json.dumps({"version": "0.1", "headline": 1.0, "tasks": {"tiny": 1.0}}) + "\n"
    )
    cli["costs"] += [2000, 1000]  # ratio 2.0, a move of 100%, then 1.0
    assert r.main(["--out", str(cli["out"])]) == 0
    res = json.loads(cli["out"].read_text())
    assert cli["calls"]["aegis"] == 2 and res["rerun"] == ["tiny"]
    assert res["rows"][0]["ratio"] == pytest.approx(1.5)


def test_record_release_appends_the_last_result_to_the_history(cli):
    import json

    r.main(["--refresh-stock"])
    r.main(["--out", str(cli["out"])])
    assert r.main(["--out", str(cli["out"]), "--record-release", "9.9.9"]) == 0
    (line,) = (cli["tokens"] / "history.jsonl").read_text().splitlines()
    rec = json.loads(line)
    assert rec["version"] == "9.9.9" and rec["tasks"] == {"tiny": pytest.approx(1.0)}
