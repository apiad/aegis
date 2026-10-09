"""aegis's settings against the real ``codex`` binary's config loading: no
model call, no network, about half a second. Skipped without ``codex``."""

import json
import os
import shutil
import signal
import subprocess

import pytest

from aegis.codex.config import SERVER, TOKEN_ENV, argv, child_env

pytestmark = pytest.mark.skipif(shutil.which("codex") is None, reason="needs codex")


def test_a_persons_own_aegis_server_and_excludes_survive(tmp_path, monkeypatch):
    from aegis.codex.process import person_excludes

    home = tmp_path / "codex-home"
    home.mkdir()
    (home / "config.toml").write_text(
        '[mcp_servers.aegis]\ncommand = "true"\n\n'
        '[shell_environment_policy]\nexclude = ["AWS_*"]\n'
    )
    monkeypatch.setenv("CODEX_HOME", str(home))
    args = argv("codex", "http://127.0.0.1:9/mcp", exclude=person_excludes())
    p = subprocess.Popen(
        args, cwd=tmp_path, env=child_env(os.environ, ("http://127.0.0.1:9/mcp", "tok")),
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True, start_new_session=True,
    )  # fmt: skip
    assert p.stdin is not None and p.stdout is not None
    try:
        for msg in (
            {"id": 1, "method": "initialize", "params": {"clientInfo": {"name": "t", "version": "0"}}},
            {"method": "initialized"},
            {"id": 2, "method": "config/read", "params": {}},
        ):  # fmt: skip
            p.stdin.write(json.dumps(msg) + "\n")
        p.stdin.flush()
        config = None
        for raw in p.stdout:
            m = json.loads(raw)
            if m.get("id") == 2:
                config = m["result"]["config"]
                break
        assert config is not None, p.stderr.read() if p.stderr else ""
        assert config["mcp_servers"][SERVER]["url"] == "http://127.0.0.1:9/mcp"
        assert config["shell_environment_policy"]["exclude"] == ["AWS_*", TOKEN_ENV]
    finally:
        p.stdin.close()
        try:
            p.wait(10)
        finally:
            try:
                os.killpg(p.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
