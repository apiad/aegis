from aegis.config import NOTICES_DEFAULTS, notices_from
from aegis.notices import cpu_pct, next_level, psi, read_signals

DISK = NOTICES_DEFAULTS["disk"]


def test_a_level_turns_on_escalates_and_clears_with_a_gap():
    assert next_level(None, 89, DISK) is None
    assert next_level(None, 90, DISK) == "warn"
    assert next_level("warn", 86, DISK) == "warn"  # above clear: held
    assert next_level("warn", 97, DISK) == "critical"
    assert next_level("critical", 92, DISK) == "critical"  # never steps down
    assert next_level("critical", 84, DISK) is None
    assert next_level(None, 98, DISK) == "critical"


def test_cpu_has_no_critical():
    cpu = NOTICES_DEFAULTS["cpu"]
    assert next_level(None, 99, cpu) == "warn"
    assert next_level("warn", 29, cpu) is None


def test_psi_parses_and_cpu_falls_back_to_load():
    p = psi(
        "some avg10=67.69 avg60=62.36 avg300=63.95 total=1\n"
        "full avg10=0.00 avg60=0.00 avg300=0.00 total=0"
    )
    assert p["some"]["avg60"] == 62.36
    assert cpu_pct(p, None, 4) == 62.36
    assert cpu_pct(None, "8.00 7.00 6.00 3/900 1", 4) == 50.0  # load 2x CPUs reads 50
    assert cpu_pct(None, None, 4) is None
    assert psi("garbage") is None


def test_a_host_without_proc_reads_no_memory_or_cpu(tmp_path):
    r = read_signals([tmp_path], proc=tmp_path / "noproc")
    assert r["memory"] is None and r["cpu"] is None
    assert r["disk"]["pct"] >= 0


def test_the_notices_block_falls_back_and_turns_kinds_off():
    assert notices_from(None) == NOTICES_DEFAULTS
    got = notices_from({"disk": {"on": 80}, "cpu": False})
    assert got["disk"] == {"on": 80, "critical": 97, "clear": 85}
    assert got["cpu"] is False
    bad = notices_from({"disk": {"onn": 1}})
    assert "error" in bad and bad["disk"] == NOTICES_DEFAULTS["disk"]
