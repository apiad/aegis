"""The fake claude speaks the stream-json aegis's own parser reads.

Asserted through ``aegis.events.parse`` rather than against hand-written
JSON, so a drift between the fake and what aegis expects fails here and
not as an empty benchmark run.
"""
import json
import subprocess
import sys

from aegis.bench.records import read_jsonl
from aegis.bench.script import make_script, synthetic_blocks
from aegis.events import AssistantText, Result, SystemInit, parse


def _run(tmp_path, script, *extra, stdin):
    path = tmp_path / "script.json"
    path.write_text(json.dumps(script))
    env = {"AEGIS_BENCH_SCRIPT": str(path),
           "AEGIS_BENCH_EMIT": str(tmp_path / "emit.jsonl"),
           "PATH": "/usr/bin:/bin"}
    return subprocess.run(
        [sys.executable, "-m", "aegis.bench.fake_claude", "-p", *extra],
        input=stdin, capture_output=True, text=True, env=env, timeout=30)


def _user(text):
    return json.dumps({"type": "user",
                       "message": {"role": "user", "content": text}}) + "\n"


def test_turn_parses_as_aegis_events_with_markers(tmp_path):
    out = _run(tmp_path, make_script({"go": synthetic_blocks(3, 0)}),
               stdin=_user("go"))
    assert out.returncode == 0, out.stderr
    events = [parse(line) for line in out.stdout.splitlines()]
    assert isinstance(events[0], SystemInit)
    texts = [e for e in events if isinstance(e, AssistantText)]
    assert len(texts) == 3 and all("«b" in e.text for e in texts)
    assert isinstance(events[-1], Result)
    kinds = [r["k"] for r in read_jsonl(tmp_path / "emit.jsonl")]
    assert kinds[:2] == ["argv", "prompt"]
    assert kinds.count("marker") == 3 and kinds[-1] == "turn_end"


def test_json_schema_one_shot_returns_an_empty_envelope(tmp_path):
    out = _run(tmp_path, make_script({}), "--json-schema", "{}", stdin="")
    assert out.returncode == 0, out.stderr
    assert json.loads(out.stdout)["result"] == ""


def test_stream_events_are_dropped_without_the_partial_flag(tmp_path):
    ev = {"type": "stream_event", "event": {"type": "content_block_delta",
          "delta": {"type": "text_delta", "text": "x"}}}
    script = make_script({"go": [{"dt_ms": 0, "line": ev, "mark": True}]})
    out = _run(tmp_path, script, stdin=_user("go"))
    assert out.returncode == 0, out.stderr
    assert "stream_event" not in out.stdout
    argv = read_jsonl(tmp_path / "emit.jsonl")[0]
    assert argv["k"] == "argv" and argv["partial"] is False


def test_stream_events_pass_with_the_partial_flag(tmp_path):
    ev = {"type": "stream_event", "event": {"type": "content_block_delta",
          "delta": {"type": "text_delta", "text": "x"}}}
    script = make_script({"go": [{"dt_ms": 0, "line": ev, "mark": True}]})
    out = _run(tmp_path, script, "--include-partial-messages",
               stdin=_user("go"))
    assert out.returncode == 0, out.stderr
    assert "«b0001»" in out.stdout
