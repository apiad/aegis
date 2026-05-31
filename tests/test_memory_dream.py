"""Hermetic test for the dream workflow stages 1-3 with mocked LLM."""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

from aegis.hooks.decorator import _reset_registry_for_tests as _reset_hooks
from aegis.tools.decorator import _reset_registry_for_tests as _reset_tools


@pytest.fixture(autouse=True)
def _isolate_registries():
    _reset_hooks(); _reset_tools()
    yield
    _reset_hooks(); _reset_tools()


def _load(monkeypatch, tmp_path: Path):
    _reset_hooks()
    _reset_tools()
    monkeypatch.chdir(tmp_path)
    repo_root = Path(__file__).parent.parent
    path = repo_root / "plugins" / "memory-system" / "memory_system.py"
    spec = importlib.util.spec_from_file_location("_test_memory_system", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules["_test_memory_system"] = module
    spec.loader.exec_module(module)
    return module


def _drop_session(root: Path, handle: str) -> None:
    sessions = root / ".aegis" / "state" / "sessions"
    sessions.mkdir(parents=True, exist_ok=True)
    (sessions / f"{handle}.jsonl").write_text(
        '{"v":1,"aegis_ts":"2026-05-30T00:00:00Z","event":'
        '{"type":"assistant_text","text":"hi"}}\n',
        encoding="utf-8",
    )


class _FakeEngine:
    """Minimal stand-in for WorkflowEngine with a scripted delegate()."""
    def __init__(self, replies: list[str]) -> None:
        self._replies = list(replies)
        self.calls: list[tuple[str, str]] = []

    async def delegate(self, queue: str, payload: str) -> str:
        self.calls.append((queue, payload))
        return self._replies.pop(0)


@pytest.mark.asyncio
async def test_dream_consolidates_and_writes_log(
        tmp_path: Path, monkeypatch) -> None:
    m = _load(monkeypatch, tmp_path)
    _drop_session(tmp_path, "lucid-knuth")
    _drop_session(tmp_path, "blithe-hopper")
    m.write_entry(tmp_path, "feedback", "phrasing",
                  "old description", "old body")
    stage1_reply = json.dumps({
        "session_handle": "lucid-knuth",
        "summary": "session summary",
        "proposed_entries": [{
            "type": "fact", "name": "docker-quirk",
            "description": "DOCKER_BUILDKIT=1 is required",
            "content": "Run with the env var set.",
            "rationale": "observed three times",
        }],
        "observations": ["agent re-discovered docker quirk twice"],
    })
    stage2_reply = json.dumps({
        "actions": [
            {"action": "add", "type": "fact", "name": "docker-quirk",
             "description": "DOCKER_BUILDKIT=1 is required",
             "content": "Run with the env var set."},
            {"action": "replace", "slug": "feedback_phrasing",
             "description": "consolidated", "content": "new body"},
        ],
        "rationale": "merged duplicate",
    })
    stage3_reply = (
        "Last night I noticed a recurring pattern in three sessions: "
        "the agent kept rediscovering the same Docker quirk. I have now "
        "filed it as a `fact` entry."
    )
    engine = _FakeEngine([
        stage1_reply, stage1_reply,
        stage2_reply, stage3_reply,
    ])
    await m.dream(engine)

    new_entry = m.read_entry(tmp_path, "fact_docker-quirk")
    assert new_entry.description == "DOCKER_BUILDKIT=1 is required"
    replaced = m.read_entry(tmp_path, "feedback_phrasing")
    assert replaced.description == "consolidated"
    dreams = list((tmp_path / ".aegis" / "memory" / "dreams").glob("dream-*.md"))
    assert len(dreams) == 1
    text = dreams[0].read_text(encoding="utf-8")
    assert "Last night" in text
    assert text.startswith("---\n")
    assert "actions:" in text
    assert "sessions_read:" in text


@pytest.mark.asyncio
async def test_dream_respects_lookback_window(
        tmp_path: Path, monkeypatch) -> None:
    m = _load(monkeypatch, tmp_path)
    _drop_session(tmp_path, "recent")
    old = tmp_path / ".aegis" / "state" / "sessions" / "old.jsonl"
    old.parent.mkdir(parents=True, exist_ok=True)
    old.write_text("{}\n", encoding="utf-8")
    import os, time
    ts = time.time() - 30 * 86400
    os.utime(old, (ts, ts))
    engine = _FakeEngine([
        json.dumps({"session_handle": "recent", "summary": "",
                    "proposed_entries": [], "observations": []}),
        json.dumps({"actions": [], "rationale": ""}),
        "no dreams tonight",
    ])
    await m.dream(engine, lookback_days=7, max_session_files=50)
    stage1_calls = [c for c in engine.calls if "transcript" in c[1].lower()]
    assert len(stage1_calls) == 1
