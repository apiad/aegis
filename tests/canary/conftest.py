"""The canary: the published harnesses under a real aegis, with a fake model.

Each test runs once per harness. The binaries come from ``AEGIS_CANARY_BIN``
(a directory, as ``scripts/canary.sh`` installs them) or else from PATH; a
harness that is missing fails, never skips, because a canary that skips is
green while it watches nothing. Every harness gets a home of its own under the
test's tmp dir, so neither the person's login nor their config is read, and
every model request goes to the fake (fakemodel.py).

When a test fails, its report goes to ``AEGIS_CANARY_REPORT`` (default
``.canary/``): the harness and its version, the fake model's request log, the
session's transcript and the harness's stderr. CI turns that directory into
the issue.
"""

from __future__ import annotations

import asyncio
import base64
import json
import os
import shutil
import socket
import subprocess
import textwrap
from collections.abc import AsyncIterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
import uvicorn

from .fakemodel import MODEL, FakeModel

BIN = {"claude-code": "claude", "codex": "codex", "opencode": "opencode"}
# Each setup a person runs: the harness, and the model its agent names. Codex
# twice, because its built-in OpenAI provider (a ChatGPT or API-key login) and
# a provider of the person's own (OpenRouter, a gateway) take different paths.
SETUPS = {
    "claude-code": ("claude-code", MODEL),
    "codex": ("codex", f"canary/{MODEL}"),
    "codex-openai": ("codex", "openai/gpt-5.5"),
    "opencode": ("opencode", f"canary/{MODEL}"),
}


def bin_of(harness: str) -> str:
    name = BIN[harness]
    folder = os.environ.get("AEGIS_CANARY_BIN")
    found = (
        str(Path(folder) / name)
        if folder and (Path(folder) / name).exists()
        else shutil.which(name)
    )
    assert found, f"{name} is not installed: the canary runs the real {harness}"
    return found


def version_of(harness: str) -> str:
    out = subprocess.run(
        [bin_of(harness), "--version"], capture_output=True, text=True, timeout=60
    )
    return (out.stdout or out.stderr).strip().splitlines()[-1]


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


async def _serve(app, port: int) -> tuple[uvicorn.Server, asyncio.Task]:
    server = uvicorn.Server(
        uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning")
    )
    task = asyncio.create_task(server.serve())
    for _ in range(1000):
        if server.started:
            break
        await asyncio.sleep(0.01)
    assert server.started, "uvicorn did not start"
    return server, task


@pytest.fixture(params=list(SETUPS))
def setup(request) -> str:
    return request.param


@pytest.fixture
async def fake_model() -> AsyncIterator[tuple[FakeModel, str]]:
    fm = FakeModel()
    port = _free_port()
    server, task = await _serve(fm.app(), port)
    try:
        yield fm, f"http://127.0.0.1:{port}"
    finally:
        server.should_exit = server.force_exit = True
        await asyncio.wait_for(task, 10)


def _isolate(home: Path, base: str, monkeypatch) -> None:
    """A home per harness, every model request to the fake, no other traffic."""
    claude = home / "claude"
    codex = home / "codex"
    xdg = home / "xdg"
    for d in (claude, codex, xdg / "config" / "opencode"):
        d.mkdir(parents=True, exist_ok=True)
    (codex / "config.toml").write_text(
        textwrap.dedent(f"""\
            model = "{MODEL}"
            model_provider = "canary"
            openai_base_url = "{base}/v1"
            [model_providers.canary]
            name = "canary"
            base_url = "{base}/v1"
            wire_api = "responses"
            env_key = "CANARY_API_KEY"
            """)
    )
    (codex / "auth.json").write_text(
        json.dumps({"auth_mode": "apikey", "OPENAI_API_KEY": "sk-canary"})
    )
    (xdg / "config" / "opencode" / "opencode.json").write_text(
        json.dumps(
            {
                "provider": {
                    "canary": {
                        "npm": "@ai-sdk/openai-compatible",
                        "name": "Canary",
                        "options": {"baseURL": f"{base}/v1", "apiKey": "canary"},
                        "models": {
                            MODEL: {"name": "Canary"},
                            MODEL + "-two": {"name": "Canary two"},
                        },
                    }
                }
            }
        )
    )
    env = {
        "ANTHROPIC_BASE_URL": base,
        "ANTHROPIC_API_KEY": "sk-canary",
        "CLAUDE_CONFIG_DIR": str(claude),
        "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1",
        "DISABLE_AUTOUPDATER": "1",
        "CODEX_HOME": str(codex),
        "CANARY_API_KEY": "canary",
        "XDG_CONFIG_HOME": str(xdg / "config"),
        "XDG_DATA_HOME": str(xdg / "data"),
        "XDG_STATE_HOME": str(xdg / "state"),
        "OPENCODE_DISABLE_AUTOUPDATE": "1",
    }
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    # A login of the person's must never stand in for the fake.
    for k in (
        "CLAUDECODE",
        "CLAUDE_CODE_OAUTH_TOKEN",
        "OPENAI_API_KEY",
        "ANTHROPIC_AUTH_TOKEN",
    ):
        monkeypatch.delenv(k, raising=False)


@dataclass
class Canary:
    harness: str
    app: Any
    model: FakeModel
    root: Path
    setup: str = ""
    _server: Any = None
    _task: Any = None

    async def boot(self) -> None:
        """A real aegis on this root, as ``aegis serve`` builds it."""
        from aegis.app import App
        from aegis.roots import make_roots
        from aegis.web import build_web

        port = _free_port()
        b = bin_of(self.harness)
        self.app = App(
            make_roots(self.root, None),
            claude_bin=b if self.harness == "claude-code" else "claude",
            codex_bin=b if self.harness == "codex" else "codex",
            opencode_bin=b if self.harness == "opencode" else "opencode",
            base_url=f"http://127.0.0.1:{port}",
        )
        web = build_web(self.app, "t", {f"127.0.0.1:{port}"})
        self._server, self._task = await _serve(web, port)

    async def shutdown(self) -> None:
        for s in list(self.app.sessions.sessions.values()):
            try:
                await asyncio.wait_for(s.stop(), 20)
            except Exception:
                pass
        self._server.should_exit = True
        await asyncio.wait_for(self._task, 30)

    async def restart(self) -> None:
        """The server stops and a new one boots on the same root."""
        await self.shutdown()
        await self.boot()

    async def spawn(self, **params):
        r = await self.app.registry.call("session.spawn", {"agent": "canary", **params})
        return self.app.sessions.sessions[r["log_id"]]

    async def send(self, s, text: str, attachments: list[str] = ()):
        """A line typed in the composer, through the op the browser calls."""
        return await self.app.registry.call(
            "session.send",
            {"log_id": s.log_id, "text": text, "attachments": list(attachments)},
        )

    async def upload(self, s, name: str, body: bytes) -> str:
        """A file dropped on the composer, in the browser's chunks."""
        from aegis import attachments

        call = self.app.registry.call
        uid = (
            await call(
                "attachment.begin",
                {"log_id": s.log_id, "name": name, "size": len(body)},
            )
        )["upload_id"]
        for off in range(0, len(body), attachments.CHUNK_BYTES):
            data = base64.b64encode(body[off : off + attachments.CHUNK_BYTES]).decode()
            await call(
                "attachment.put",
                {"log_id": s.log_id, "upload_id": uid, "offset": off, "data": data},
            )
        return uid


@pytest.fixture
async def canary(setup, fake_model, tmp_path, monkeypatch, request):
    harness, model = SETUPS[setup]
    fm, base = fake_model
    _isolate(tmp_path / "homes", base, monkeypatch)
    root = tmp_path / "root"
    root.mkdir()
    (root / ".aegis.yaml").write_text(
        "default_agent: canary\nagents:\n"
        f"  canary: {{harness: {harness}, model: {model}, "
        "effort: low, permission: full}\n"
        f"  cascade: {{harness: {harness}, models: [{model}, {model}-two], "
        "effort: low, permission: full}\n"
        "queues:\n  canary: {agent: canary, max_parallel: 2}\n"
    )
    c = Canary(harness, None, fm, root, setup)
    await c.boot()
    try:
        yield c
    finally:
        _report(request, c)
        await c.shutdown()


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item, call):
    outcome = yield
    rep = outcome.get_result()
    if rep.when == "call":
        item.canary_failed = rep.failed
        item.canary_error = rep.longreprtext if rep.failed else ""
        crash = getattr(rep.longrepr, "reprcrash", None)
        item.canary_crash = crash.message if crash else ""
        if rep.failed and "canary" not in item.fixturenames:
            # A test without the fixture reports what it named itself.
            props = dict(item.user_properties)
            _write(item, {
                "harness": props.get("harness", ""),
                "setup": props.get("setup", ""),
                "version": props.get("version", ""),
                "model_log": [],
                "sessions": [],
            })  # fmt: skip


def _report(request, c: Canary) -> None:
    item = request.node
    if not getattr(item, "canary_failed", False):
        return
    sessions = []
    for s in c.app.sessions.sessions.values():
        path = Path(s._stderr_path)
        stderr = path.read_text(errors="replace")[-8000:] if path.exists() else ""
        sessions.append(
            {
                "handle": s.handle,
                "status": s.status,
                "entries": s.entries(),
                "stderr": stderr,
            }
        )
    _write(
        item,
        {
            "harness": c.harness,
            "setup": c.setup,
            "version": version_of(c.harness),
            "model_log": c.model.log,
            "sessions": sessions,
        },
    )


def _write(item, fields: dict) -> None:
    out = Path(os.environ.get("AEGIS_CANARY_REPORT", ".canary"))
    out.mkdir(parents=True, exist_ok=True)
    name = item.name.replace("/", "_")
    report = {
        "test": item.nodeid,
        "crash": getattr(item, "canary_crash", ""),
        "error": getattr(item, "canary_error", "")[-6000:],
        **fields,
    }
    (out / f"{name}.json").write_text(json.dumps(report, indent=1, default=str))
