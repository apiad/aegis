"""End-to-end: a real daemon, a real client in a pty, the fake claude.

Opt-in (``AEGIS_BENCH_E2E=1``): it takes seconds and spawns processes.
It asserts the measurement works, never how fast anything is.
"""
import os

import pytest

pytestmark = pytest.mark.skipif(os.environ.get("AEGIS_BENCH_E2E") != "1",
                                reason="set AEGIS_BENCH_E2E=1")


def test_markers_reach_the_terminal(tmp_path):
    from aegis.bench.launcher import resolve_target
    from aegis.bench.records import Recorder, read_jsonl
    from aegis.bench.rig import Rig, wait_until
    from aegis.bench.script import make_script, synthetic_blocks
    from aegis.bench.world import (
        build_world, client_argv, start_daemon, teardown)

    target = resolve_target(None)
    world = build_world(tmp_path, target, script=make_script(
        {"go": synthetic_blocks(20, 20)}))
    rec = Recorder(tmp_path / "frames.jsonl")
    emit = tmp_path / "emit.jsonl"
    rig = None
    try:
        start_daemon(world)
        rig = Rig(client_argv(world, view="bench-a"), cwd=world.root,
                  env=world.env, cols=120, rows=40, label="a", recorder=rec)
        rig.start()
        assert wait_until([rig], lambda: rig.contains("type a message"), 60)

        def prompts():
            return sum(1 for r in read_jsonl(emit) if r["k"] == "prompt")

        # A cold attach leaves focus on the tab bar, so click the input
        # the way a user would before typing.
        assert rig.click_text("type a message")
        rig.write(b"go")
        for _ in range(5):
            rig.write(b"\r")
            if wait_until([rig], lambda: prompts() > 0, 3):
                break
        assert prompts() == 1

        def emitted():
            return [r["marker"] for r in read_jsonl(emit)
                    if r["k"] == "marker"]

        assert wait_until([rig], lambda: len(emitted()) == 20 and all(
            m in rig.markers_seen for m in emitted()), 60)
        assert rig.saw_sync

        # The probe ran inside the real daemon, not headless, with sync on,
        # against the aegis the launcher was asked for. Its sampler flushes
        # once a second, so give it a beat before reading.
        wait_until([rig], lambda: False, 1.5)
        recs = read_jsonl(tmp_path / "probe.jsonl")
        assert any(r["k"] == "tick" for r in recs)
        gate = [r for r in recs if r["k"] == "gate"][-1]
        assert gate["sync"] is True and gate["headless"] is False
        assert gate["aegis_file"] == target.aegis_file
    finally:
        if rig is not None:
            rig.close()
        teardown(world)
