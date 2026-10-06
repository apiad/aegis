import importlib.util
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "bench2_compare.py"
spec = importlib.util.spec_from_file_location("bench2_compare", SCRIPT)
bc = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bc)


def test_only_a_regression_past_20_percent_warns():
    base = {
        "cold_load_ms": 100.0,
        "server_line_p50_us": 50.0,
        "cold_load_entries": 2000,
    }
    head = {
        "cold_load_ms": 125.0,
        "server_line_p50_us": 59.0,
        "cold_load_entries": 4000,
    }
    rows, warnings = bc.compare(base, head)
    assert warnings == [
        "::warning title=aegis bench::cold_load_ms is 25% worse (100.00 → 125.00)"
    ]
    assert any("+18%" in r for r in rows)


def test_no_base_reports_head_alone():
    rows, warnings = bc.compare(None, {"cold_load_ms": 1.0})
    assert warnings == [] and "No base result" in "\n".join(rows)
