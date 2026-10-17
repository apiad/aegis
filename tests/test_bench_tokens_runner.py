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
