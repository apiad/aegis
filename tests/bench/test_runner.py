from aegis.bench.runner import failed, runs_dir
from aegis.bench.scenarios import DEFAULT, QUICK, SCENARIOS


def test_scenario_sets_are_registered():
    assert set(QUICK) <= set(DEFAULT) <= set(SCENARIOS)
    assert "soak" in SCENARIOS and "soak" not in DEFAULT
    assert QUICK == ["startup", "block-stream", "acp-stream", "resize"]


def test_quick_and_default_only_name_registered_scenarios():
    assert set(QUICK) <= set(DEFAULT) <= set(SCENARIOS)


def test_runs_dir_honours_env(tmp_path, monkeypatch):
    monkeypatch.setenv("AEGIS_BENCH_HOME", str(tmp_path))
    assert runs_dir() == tmp_path / "runs"


def test_failed_counts_gate_failures_and_failed_status():
    ok = {"scenarios": {"a": {"status": "ok", "gates": [[{"ok": True}]]}}}
    bad_gate = {"scenarios": {"a": {"status": "ok",
                                    "gates": [[{"ok": False}]]}}}
    bad = {"scenarios": {"a": {"status": "failed", "gates": []}}}
    skipped = {"scenarios": {"a": {"status": "skipped", "gates": []}}}
    assert not failed(ok)
    assert failed(bad_gate)
    assert failed(bad)
    assert not failed(skipped)
