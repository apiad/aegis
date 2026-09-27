"""The aegis-aware web layer is gone, and the daemon is fine without it.

It reached into the brain by attribute — eleven manager methods — and grew a
message per feature until it fell behind the TUI. `aegis web` replaced it
with a relay that knows no aegis concept.
"""

import pytest

import contextlib
import importlib.util
import os
import re
import signal
from pathlib import Path

import aegis

_TESTS = Path(__file__).resolve().parent


def test_every_js_test_imports_a_module_that_exists():
    """pytest does not collect `.mjs`, and the JS tests have no make target, so
    a deleted JS module leaves its test passing-by-absence: nothing runs it and
    nothing reports it. Deleting `web/static/js/` orphaned seven of these, each
    failing with ERR_MODULE_NOT_FOUND, and no gate noticed.
    """
    orphans = []
    for test in sorted(_TESTS.rglob("*.test.mjs")):
        for spec in re.findall(r"""from\s+["']([^"']+)["']""", test.read_text()):
            if not spec.startswith("."):
                continue  # a bare specifier is node's to resolve, not ours
            if not (test.parent / spec).resolve().exists():
                orphans.append(f"{test.relative_to(_TESTS)} -> {spec}")
    assert not orphans, (
        "JS tests importing modules that no longer exist:\n" + "\n".join(orphans)
    )


def test_the_web_package_is_gone():
    assert importlib.util.find_spec("aegis.web") is None
    assert not (Path(aegis.__file__).parent / "web").exists()


_LEGACY_CONFIG = (
    "default_agent: main\nagents:\n  main:\n    provider: claude-code\n"
    "    model: opus\nweb:\n  bind: 127.0.0.1\n  port: 8899\n  token: secret\n"
)


def _seed_an_upgraded_install(root: Path) -> None:
    """A root as an install from before stage 5b left it: a token-bearing
    `web:` block, the port the old frontend recorded, and view files written
    by a version whose view code is deleted."""
    (root / ".aegis.yaml").write_text(_LEGACY_CONFIG, encoding="utf-8")
    from aegis.state.workspace import state_dir

    sd = state_dir(root)
    sd.mkdir(parents=True, exist_ok=True)
    (sd / "web.port").write_text("8899")
    (sd / "views").mkdir(exist_ok=True)
    (sd / "views" / "legacy.json").write_text("{}")
    (sd / "views" / "web-deadbeef.json").write_text("{}")


def test_a_config_with_a_web_block_still_resolves_a_boot(tmp_path):
    """Review Focus 3: the daemon stopped reading the block in stage 5b, and a
    config written before that must not raise now the code is deleted.

    Through `resolve_boot`, not `load_boot_config`: `load_boot_config` only
    parses YAML, so it cannot notice a boot that breaks on a legacy root.
    `resolve_boot` is the sequence `aegis server` and `aegis.embed` both run --
    it builds the session factory, the host registry and the queues, and it is
    handed `roots.state_dir`.
    """
    from aegis.cli import resolve_boot

    _seed_an_upgraded_install(tmp_path)
    resolved = resolve_boot(tmp_path)
    assert resolved.boot.default_agent == "main"
    assert not hasattr(resolved.boot, "web"), "a `web:` block must reach nothing now"
    assert resolved.make_session is not None, "resolve_boot built no session factory"
    assert resolved.roots.state_dir.exists(), "the legacy state dir was not adopted"


# ~3s even on an idle machine (it boots a real daemon / builds a real
# world), so it sat within 1% of the fast lane's 3s budget and flipped
# `make test` red or green with ambient load. CI runs `-m "not live"`,
# which includes slow, so nothing stops being checked there.
@pytest.mark.slow
async def test_a_daemon_really_boots_on_a_root_from_the_old_version(
    tmp_path, monkeypatch
):
    """Review Focus 3 and 5, against the artifact rather than a parse.

    The previous version of this test seeded `web.port` and `views/*.json` and
    then asserted `load_boot_config`, which reads only `config_root` -- so the
    seeding could not influence the assertion, and deleting it left the test
    passing. This boots a real daemon on the seeded root and waits for the
    socket to accept a connection, which is what "still boots" means.
    """
    import asyncio

    from aegis.daemon.lifecycle import ensure_daemon, socket_path
    from aegis.config.roots import AegisRoots

    monkeypatch.setenv("AEGIS_DAEMON_DIR", str(tmp_path / "daemons"))
    _seed_an_upgraded_install(tmp_path)

    path = await ensure_daemon(tmp_path)
    try:
        assert path == socket_path(AegisRoots.for_project(tmp_path))
        reader, writer = await asyncio.open_unix_connection(str(path))
        writer.close()
        with contextlib.suppress(ConnectionError):
            await writer.wait_closed()
    finally:
        pid_text = tmp_path / ".aegis" / "state" / "daemon.pid"
        if pid_text.exists():
            with contextlib.suppress(ProcessLookupError, ValueError):
                os.kill(int(pid_text.read_text().split()[0]), signal.SIGTERM)
