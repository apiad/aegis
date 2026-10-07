"""The bench's `aegis serve` must never read the real quota token or cache:
it would poll the vendors from every bench run and write the cache the
running aegis shows (final review of #146)."""

import importlib.util
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "bench.py"
spec = importlib.util.spec_from_file_location("bench", SCRIPT)
bench = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bench)


def test_the_bench_server_reads_no_real_quota_credentials_or_cache(
    tmp_path, monkeypatch
):
    for key in ("CLAUDE_CREDS", "OPENCODE_AUTH", "AEGIS_QUOTA_CACHE"):
        monkeypatch.delenv(key)
    env = bench.server_env(tmp_path, {})
    for key in ("CLAUDE_CREDS", "OPENCODE_AUTH", "AEGIS_QUOTA_CACHE"):
        assert Path(env[key]).is_relative_to(tmp_path), key
