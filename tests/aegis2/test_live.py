"""One real Claude Code session through aegis2: a prompt, an interrupt, a stop
and a resume that keeps the context.

Spends a few cents of Haiku. Run with ``make test-live``."""

import shutil
from pathlib import Path

import pytest

from aegis2.meta import MetaStore
from aegis2.session import Session, SpawnSpec
from aegis2.transcript.entries import fold_records
from aegis2.transcript.store import Store, read_store

from .conftest import until

pytestmark = pytest.mark.live
HAIKU = "claude-haiku-4-5-20251001"


async def test_a_real_prompt_interrupt_stop_and_resume(tmp_path: Path):
    claude = shutil.which("claude")
    assert claude, "claude is not on PATH"
    path = tmp_path / "log.jsonl"
    s = Session(
        log_id="live",
        spec=SpawnSpec("haiku", HAIKU, "low", "full", tmp_path),
        handle="live-test",
        store=Store(path),
        stderr_path=tmp_path / "stderr.log",
        claude_bin=claude,
        publish=lambda ch, ops: None,
        metas=MetaStore(tmp_path / "sessions"),
    )
    await s.start()
    try:
        await s.send("Remember the word PELICAN. Reply with the single word OK.")
        await until(
            lambda: s.status == "idle" and s.cost_usd,
            timeout=90,
            what="the first result",
        )
        assert s.context_window and s.context_tokens and s.claude_session_id

        await s.send(
            "Run exactly this bash command in the foreground: python3 -c 'import time; time.sleep(40)'"
        )
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

        await s.stop()
        await s.send("What word did I ask you to remember? Reply with just the word.")
        await until(lambda: s.status == "idle", timeout=90, what="the resumed turn")
        prose = [e["md"] for e in s.entries() if e["kind"] == "prose"]
        assert "PELICAN" in prose[-1]
    finally:
        await s.stop()
    records, damaged = read_store(path)
    assert damaged == 0 and fold_records(records).entries() == s.entries()
