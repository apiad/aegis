import json

from aegis.bench.launcher import resolve_target
from aegis.bench.world import build_world, teardown
from aegis.usage import quota_claude, quota_opencode


def test_a_world_never_reads_the_operators_quota_credentials(tmp_path, monkeypatch):
    """The TUI polls quota with whatever credentials it finds. A world
    inherits HOME, so without its own override it spends real accounts."""
    home = tmp_path / "home"
    (home / ".claude").mkdir(parents=True)
    (home / ".claude" / ".credentials.json").write_text(
        json.dumps({"claudeAiOauth": {"accessToken": "real"}})
    )
    (home / ".local" / "share" / "opencode").mkdir(parents=True)
    (home / ".local" / "share" / "opencode" / "auth.json").write_text(
        json.dumps({"opencode-go": {"key": "real"}})
    )
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.delenv("CLAUDE_CREDS", raising=False)
    monkeypatch.delenv("OPENCODE_AUTH", raising=False)
    monkeypatch.delenv("XDG_DATA_HOME", raising=False)
    assert quota_claude.read_token() == "real"
    assert quota_opencode.read_key() == "real"

    world = build_world(tmp_path / "run", resolve_target(None), script={})
    try:
        for key, value in world.env.items():
            monkeypatch.setenv(key, value)
        assert quota_claude.read_token() is None
        assert quota_opencode.read_key() is None
    finally:
        teardown(world)
