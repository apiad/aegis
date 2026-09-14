"""The probe loads standalone and fails loudly when it cannot hook.

Every test runs the probe in a subprocess: ``install()`` patches Textual
classes process-wide, and those patches must never leak into the suite.
"""
import pytest
import json
import os
import subprocess
import sys
from pathlib import Path

PROBE_DIR = Path(__file__).resolve().parents[2] / "src" / "aegis" / "bench"


def _py(code, env):
    return subprocess.run(
        [sys.executable, "-c",
         f"import sys;sys.path.insert(0,{str(PROBE_DIR)!r});{code}"],
        capture_output=True, text=True, env=env, timeout=60)


def _env(tmp_path, **extra):
    env = {k: v for k, v in os.environ.items()
           if not k.startswith("AEGIS_BENCH_")}
    env["AEGIS_BENCH_PROBE"] = str(tmp_path / "probe.jsonl")
    env.update(extra)
    return env


def _records(tmp_path):
    lines = (tmp_path / "probe.jsonl").read_text().splitlines()
    return [json.loads(line) for line in lines]


@pytest.mark.slow
def test_install_reports_required_and_counted_hooks(tmp_path):
    out = _py("import probe;probe.install();probe._SINK.flush()",
              _env(tmp_path))
    assert out.returncode == 0, out.stderr
    hooks = next(r for r in _records(tmp_path) if r["k"] == "hooks")
    for name in ("Screen._on_timer_update", "Screen._refresh_layout",
                 "Compositor.render_update", "App._display",
                 "Widget.get_content_height", "Widget.render_lines"):
        assert name in hooks["installed"]


def test_missing_required_hook_fails_loudly(tmp_path):
    code = ("import textual.screen as s;del s.Screen._refresh_layout;"
            "import probe;probe.install()")
    out = _py(code, _env(tmp_path))
    assert out.returncode != 0
    assert "Screen._refresh_layout" in out.stderr


def test_unset_output_path_fails(tmp_path):
    env = _env(tmp_path)
    del env["AEGIS_BENCH_PROBE"]
    out = _py("import probe;probe.install()", env)
    assert out.returncode != 0
    assert "AEGIS_BENCH_PROBE" in out.stderr


@pytest.mark.slow
def test_spans_inside_a_tick_fold_into_the_tick_record(tmp_path):
    code = (
        "import time, probe\n"
        "probe.install()\n"
        "def inner(self): time.sleep(0.002)\n"
        "layout = probe._span('layout', inner)\n"
        "tick = probe._span('tick', lambda self: layout(self))\n"
        "tick(object())\n"
        "layout(object())\n"
        "probe._SINK.flush()\n")
    out = _py(code, _env(tmp_path))
    assert out.returncode == 0, out.stderr
    recs = _records(tmp_path)
    ticks = [r for r in recs if r["k"] == "tick"]
    assert len(ticks) == 1
    assert ticks[0]["layout"] >= 2_000_000
    assert ticks[0]["dur_ns"] >= ticks[0]["layout"]
    standalone = [r for r in recs if r["k"] == "layout"]
    assert len(standalone) == 1 and standalone[0]["dur_ns"] >= 2_000_000


@pytest.mark.slow
def test_sabotage_without_the_paint_hook_fails(tmp_path):
    code = ("import aegis.tui.pane as p;del p.ConversationPane._paint_streaming;"
            "import probe;probe.install()")
    out = _py(code, _env(tmp_path, AEGIS_BENCH_SABOTAGE_MS="40"))
    assert out.returncode != 0
    assert "_paint_streaming" in out.stderr
