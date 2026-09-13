"""The fake ACP agent speaks enough ACP v1 for aegis's own AcpSession.

Driven through a real ``AcpDriver`` session, the same way
``tests/test_drivers_acp.py`` drives its stub agents, so a drift between
the fake and what aegis sends fails here and not as an empty run.
"""
import json
import sys

from aegis.bench.records import read_jsonl
from aegis.bench.script import acp_chunks, make_script
from aegis.config import Agent, GeminiCLI
from aegis.drivers.acp import AcpDriver
from aegis.events import AssistantText, Result


class _FakeAcpDriver(AcpDriver):
    BASE_CMD = [sys.executable, "-m", "aegis.bench.fake_acp"]

    def build_argv(self, *a, **kw):
        return list(self.BASE_CMD)


async def test_prompt_streams_marked_chunks_and_ends_the_turn(
        tmp_path, monkeypatch):
    script = tmp_path / "script.json"
    script.write_text(json.dumps(make_script(
        {"go": acp_chunks(10, 1000, mark_every=5)})))
    monkeypatch.setenv("AEGIS_BENCH_SCRIPT", str(script))
    monkeypatch.setenv("AEGIS_BENCH_EMIT", str(tmp_path / "emit.jsonl"))

    sess = _FakeAcpDriver().session(
        Agent(provider=GeminiCLI(model="")), str(tmp_path),
        mcp_url="", handle="h")
    await sess.start()
    try:
        await sess.send("go")
        events = [ev async for ev in sess.events()]
    finally:
        await sess.close()

    text = "".join(e.text for e in events if isinstance(e, AssistantText))
    assert text.count("«b") == 2, text
    results = [e for e in events if isinstance(e, Result)]
    assert len(results) == 1 and results[0].is_error is False

    emit = read_jsonl(tmp_path / "emit.jsonl")
    kinds = [r["k"] for r in emit]
    assert kinds.count("prompt") == 1
    assert kinds.count("marker") == 2
    assert kinds[-1] == "turn_end"
    markers = [r["marker"] for r in emit if r["k"] == "marker"]
    assert all(m in text for m in markers)
