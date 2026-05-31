"""Live: install memory-system into a tmp project, then drive a real claude
through MCP to call memory_add; assert the entry file lands."""
from __future__ import annotations

import asyncio
import json
import shutil
import subprocess
from pathlib import Path

import pytest

from aegis.hooks.decorator import _reset_registry_for_tests as _reset_hooks
from aegis.tools.decorator import _reset_registry_for_tests as _reset_tools

pytestmark = [
    pytest.mark.live,
    pytest.mark.skipif(shutil.which("claude") is None,
                       reason="claude CLI not on PATH"),
]


@pytest.fixture(autouse=True)
def _isolate_registries():
    _reset_hooks(); _reset_tools()
    yield
    _reset_hooks(); _reset_tools()


def test_install_subprocess_bootstraps_tree(tmp_path: Path) -> None:
    (tmp_path / ".aegis.yaml").write_text(
        "agents:\n"
        "  default:\n    provider: claude-code\n    model: haiku\n"
        "default_agent: default\n",
        encoding="utf-8",
    )
    repo_root = Path(__file__).parent.parent
    src = repo_root / "plugins" / "memory-system"
    res = subprocess.run(
        ["uv", "run", "aegis", "plugin", "install", "memory-system",
         "--from", str(src), "--yes"],
        cwd=tmp_path, capture_output=True, text=True, timeout=120,
    )
    assert res.returncode == 0, f"stdout:\n{res.stdout}\nstderr:\n{res.stderr}"
    assert (tmp_path / ".aegis/memory/MEMORY.md").exists()
    assert (tmp_path / ".aegis/memory/entries").is_dir()
    assert (tmp_path / ".aegis/memory/dreams").is_dir()
    assert (tmp_path / ".aegis/schedules/memory-dream.yaml").exists()


@pytest.mark.asyncio
async def test_round_trip_save_via_real_claude(
        tmp_path: Path, monkeypatch) -> None:
    """End-to-end: a real claude -p subprocess invokes memory_add via the
    strict MCP plane; the entry file lands under .aegis/memory/entries/."""
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".aegis.yaml").write_text(
        "agents:\n"
        "  default:\n    provider: claude-code\n    model: haiku\n"
        "default_agent: default\n",
        encoding="utf-8",
    )
    (tmp_path / ".aegis/memory/entries").mkdir(parents=True)

    import importlib.util, sys
    repo_root = Path(__file__).parent.parent
    path = repo_root / "plugins" / "memory-system" / "memory_system.py"
    spec = importlib.util.spec_from_file_location("_live_memory_system", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules["_live_memory_system"] = module
    spec.loader.exec_module(module)

    from aegis.mcp import AegisMCP, mcp_config_json
    mcp = AegisMCP()
    await mcp.start()
    try:
        argv = [
            "claude", "-p",
            "--input-format", "stream-json",
            "--output-format", "stream-json",
            "--replay-user-messages", "--verbose",
            "--permission-mode", "bypassPermissions",
            "--strict-mcp-config",
            "--mcp-config", mcp_config_json(mcp.url),
        ]
        proc = await asyncio.create_subprocess_exec(
            *argv,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=str(tmp_path),
        )
        msg = {"type": "user", "message": {
            "role": "user",
            "content": ("Please call the memory_add tool with these exact "
                        "arguments: type='fact', name='demo-fact', "
                        "description='live round-trip', "
                        "content='this is the body'. Then briefly confirm.")}}
        proc.stdin.write((json.dumps(msg) + "\n").encode())
        await proc.stdin.drain()
        proc.stdin.close()

        out_bytes = await asyncio.wait_for(proc.stdout.read(), timeout=180)
        await asyncio.wait_for(proc.wait(), timeout=10)
        text = out_bytes.decode("utf-8", "replace")
        entry_path = tmp_path / ".aegis/memory/entries/fact_demo-fact.md"
        assert entry_path.exists(), (
            f"agent did not save the entry. tail of stdout:\n"
            f"{text[-2000:]}"
        )
        body = entry_path.read_text(encoding="utf-8")
        assert "type: fact" in body
        assert "name: demo-fact" in body
    finally:
        await mcp.stop()
