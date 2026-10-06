"""One real Claude Code session through aegis2: a prompt and an interrupt.

Spends a few cents of Haiku. Run with ``make test-live``."""

import shutil
from pathlib import Path

import pytest

from aegis2.claude.process import build_argv
from aegis2.session import Session, SpawnSpec
from aegis2.transcript.entries import fold_records
from aegis2.transcript.store import Store, read_store

from .conftest import until

pytestmark = pytest.mark.live
HAIKU = "claude-haiku-4-5-20251001"


async def test_a_real_prompt_and_a_real_interrupt(tmp_path: Path):
    claude = shutil.which("claude")
    assert claude, "claude is not on PATH"
    path = tmp_path / "log.jsonl"
    published = []
    s = Session(
        log_id="live",
        spec=SpawnSpec("haiku", HAIKU, "low", "full", tmp_path),
        store=Store(path),
        argv=build_argv(claude, HAIKU, "low", "full"),
        stderr_path=tmp_path / "stderr.log",
        publish=lambda ch, ops: published.append((ch, ops)),
    )
    await s.start()
    try:
        await s.send("Reply with the single word PONG and nothing else.")
        await until(
            lambda: s.status == "idle" and s.cost_usd,
            timeout=90,
            what="the first result",
        )
        prose = [e["md"] for e in s.entries() if e["kind"] == "prose"]
        assert any("PONG" in p for p in prose)
        assert s.context_window and s.context_tokens

        await s.send("Run the bash command `sleep 30` and then say DONE.")
        await until(
            lambda: any(e["kind"] == "tool" for e in s.entries()),
            timeout=90,
            what="the Bash call",
        )
        await s.interrupt()
        await until(
            lambda: s.status == "idle", timeout=20, what="idle after the interrupt"
        )
        assert s.entries()[-1]["summary"].startswith("interrupted")
    finally:
        await s.close()
    records, damaged = read_store(path)
    assert damaged == 0 and fold_records(records).entries() == s.entries()
