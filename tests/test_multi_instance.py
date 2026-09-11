"""The gate for the whole plan. Asserts on a config *write*, not on path
disjointness: paths differ as soon as roots are threaded, while the
late-bound MCP lookups can still resolve through the process cwd."""
from pathlib import Path

CONFIG = """\
agents:
  opus:
    provider: claude-code
    model: opus
default_agent: opus
"""


def _project(tmp_path: Path, name: str) -> Path:
    root = tmp_path / name
    root.mkdir()
    (root / ".aegis.yaml").write_text(CONFIG, encoding="utf-8")
    return root


async def test_two_instances_do_not_share_state(tmp_path):
    import aegis

    a, b = _project(tmp_path, "a"), _project(tmp_path, "b")
    async with aegis.embed(a) as ae_a, aegis.embed(b) as ae_b:
        assert ae_a.manager is not ae_b.manager
        assert ae_a.roots.state_dir != ae_b.roots.state_dir
        assert ae_a.roots.state_dir.is_relative_to(a)
        assert ae_b.roots.state_dir.is_relative_to(b)


async def test_a_config_write_in_one_instance_does_not_touch_the_other(
        tmp_path, monkeypatch):
    """End-to-end version of the Task 4 gate: the tool is reached through
    a real embedded instance's own MCP server, not a stub bridge.

    The process cwd sits inside project **b** on purpose. Without it the
    test is a proxy: a tool that resolved its root by walking up from the
    cwd would land in the pytest tmp dir, which is neither project, and
    b's file would stay byte-identical for the wrong reason. Standing in
    b is what makes "A's server wrote to A" a claim about threaded roots
    rather than about where the test happened to be run from.
    """
    import aegis

    from tests.test_mcp_config_tools import _call

    a, b = _project(tmp_path, "a"), _project(tmp_path, "b")
    before_b = (b / ".aegis.yaml").read_text(encoding="utf-8")
    monkeypatch.chdir(b)

    async with aegis.embed(a) as ae_a, aegis.embed(b):
        res = await _call(ae_a.mcp.server, "aegis_config_add_agent",
                          slug="sonnet", harness="claude-code",
                          model="sonnet")
        assert res.get("ok") is True, res

    assert (b / ".aegis.yaml").read_text(encoding="utf-8") == before_b
    assert "sonnet" in (a / ".aegis.yaml").read_text(encoding="utf-8")


async def test_embed_installs_no_signal_handlers(tmp_path):
    """An embedded aegis runs in the host's loop and must not touch its
    signals — sindri owns SIGINT/SIGTERM."""
    import signal

    import aegis

    before = signal.getsignal(signal.SIGINT)
    async with aegis.embed(_project(tmp_path, "a")):
        assert signal.getsignal(signal.SIGINT) is before
    assert signal.getsignal(signal.SIGINT) is before
