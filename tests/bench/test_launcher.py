import sys

from aegis.bench.launcher import aegis_argv, resolve_target


def test_current_target_is_this_interpreter_in_daemon_topology():
    t = resolve_target(None)
    assert t.python == (sys.executable,)
    assert t.topology == "daemon"
    assert t.aegis_file.endswith("aegis/__init__.py")


def test_argv_installs_probe_before_importing_aegis(tmp_path):
    t = resolve_target(None)
    argv = aegis_argv(t, ["serve", "--cwd", "/x"], probe_dir=tmp_path)
    code = argv[argv.index("-c") + 1]
    assert code.index("aegis_bench_probe") < code.index("from aegis.cli")
    assert "'serve', '--cwd', '/x'" in code


def test_argv_without_probe_does_not_mention_it():
    t = resolve_target(None)
    code = aegis_argv(t, ["attach"], probe_dir=None)[-1]
    assert "aegis_bench_probe" not in code
