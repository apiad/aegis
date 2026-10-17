import os
import time

import pytest

from aegis import harness_versions as hv

R = hv.ranges()["claude-code"]


@pytest.mark.parametrize(
    "text,want",
    [
        ("2.1.296 (Claude Code)", (2, 1, 296)),
        ("codex-cli 0.163.0", (0, 163, 0)),
        ("2.0.31", (2, 0, 31)),
        ("2.1.302-beta.1 (Claude Code)", None),
        ("0.0-fake (Claude Code)", None),  # "0.0-f": pre-release suffix
        ("", None),
    ],
)
def test_parse_version(text, want):
    assert hv.parse_version(text)[0] == want


def test_judge_states():
    acc = {"claude-code": ["2.1.400"]}

    def st(v):
        return hv.judge("claude-code", "Claude Code", v, "/x/claude", acc).state

    assert st(f"{R.max_s} (Claude Code)") == "in_range"
    assert st("99.0.0") == "newer"
    assert st("0.1.0") == "older"
    assert st("2.1.400") == "accepted"
    assert st(None) == "unknown"


def test_install_command_by_path():
    def v(p):
        return hv.install_command(R, p, "2.1.301")[0]

    assert (
        v("/home/u/.local/share/claude/versions/2.1.301") == f"claude install {R.max_s}"
    )
    assert (
        v("/usr/lib/node_modules/@anthropic-ai/claude-code/cli.js")
        == f"npm install -g @anthropic-ai/claude-code@{R.max_s}"
    )
    assert v("/opt/homebrew/Cellar/claude/2/bin/claude").startswith("brew upgrade")
    cmd, notes = hv.install_command(R, "/opt/tools/claude", "2.1.301")
    assert cmd.startswith("npm install -g") and any(
        "does not recognise" in n for n in notes
    )


def test_native_claude_notes_autoupdater():
    _, notes = hv.install_command(
        R, "/h/.local/share/claude/versions/2.1.301", "2.1.301"
    )
    assert any("DISABLE_AUTOUPDATER=1" in n for n in notes)


def test_opencode_1_removes_old_package():
    oc = hv.ranges()["opencode"]
    cmd, _ = hv.install_command(
        oc, "/usr/lib/node_modules/opencode-ai/bin/opencode", "1.19.3"
    )
    assert (
        cmd
        == f"npm uninstall -g opencode-ai && npm install -g @opencode/cli@{oc.max_s}"
    )


def test_messages_name_path_range_and_command():
    v = hv.judge("claude-code", "Claude Code", "99.0.0", "/p/claude", {})
    for text in (v.message(), v.notice(started_anyway=True)):
        assert (
            "/p/claude" in text
            and R.min_s in text
            and R.max_s in text
            and v.install in text
        )
    assert "nobody was there to ask" in v.notice(started_anyway=True)


async def test_read_version_caches_by_mtime(tmp_path):
    b = tmp_path / "h"
    b.write_text("#!/bin/sh\necho 1.2.3\n")
    b.chmod(0o755)
    assert (await hv.read_version(str(b)))[0] == "1.2.3"
    b.write_text("#!/bin/sh\necho 1.2.4\n")
    os.utime(b, ns=(time.time_ns(), time.time_ns() + 10**9))
    assert (await hv.read_version(str(b)))[0] == "1.2.4"


async def test_read_version_hang_is_unknown_within_timeout(tmp_path, monkeypatch):
    monkeypatch.setattr(hv, "VERSION_TIMEOUT_S", 0.5)
    b = tmp_path / "h"
    b.write_text("#!/bin/sh\nsleep 30\n")
    b.chmod(0o755)
    t = time.monotonic()
    raw, path = await hv.read_version(str(b))
    assert raw is None and path == str(b.resolve()) and time.monotonic() - t < 2


async def test_read_version_missing_flag_is_unknown(tmp_path):
    b = tmp_path / "h"
    b.write_text("#!/bin/sh\nexit 2\n")
    b.chmod(0o755)
    assert (await hv.read_version(str(b)))[0] is None


async def test_read_version_skips_warnings_before_the_version(tmp_path):
    b = tmp_path / "h"
    b.write_text(
        "#!/bin/sh\necho '(node:42) [DEP0040] DeprecationWarning: punycode' >&2\n"
        "echo 'a banner line'\necho '2.1.296 (Claude Code)'\n"
    )
    b.chmod(0o755)
    assert hv.parse_version((await hv.read_version(str(b)))[0])[0] == (2, 1, 296)


async def test_read_version_runs_the_name_on_path_not_its_target(tmp_path):
    shim = tmp_path / "shim.sh"
    shim.write_text(
        '#!/bin/sh\ncase "$(basename "$0")" in claude) echo 2.1.296;; *) exit 3;; esac\n'
    )
    shim.chmod(0o755)
    link = tmp_path / "claude"
    link.symlink_to(shim)
    raw, path = await hv.read_version(str(link))
    assert raw == "2.1.296" and path == str(shim.resolve())


async def test_a_hanging_read_is_not_paid_twice(tmp_path, monkeypatch):
    monkeypatch.setattr(hv, "VERSION_TIMEOUT_S", 0.5)
    b = tmp_path / "h"
    b.write_text("#!/bin/sh\nsleep 30\n")
    b.chmod(0o755)
    await hv.read_version(str(b))
    t = time.monotonic()
    raw, _ = await hv.read_version(str(b))
    assert raw is None and time.monotonic() - t < 0.3


async def test_concurrent_reads_of_one_binary_run_it_once(tmp_path):
    import asyncio

    count = tmp_path / "count"
    b = tmp_path / "h"
    b.write_text(f"#!/bin/sh\necho x >> {count}\nsleep 0.3\necho 1.2.3\n")
    b.chmod(0o755)
    got = await asyncio.gather(*(hv.read_version(str(b)) for _ in range(5)))
    assert {g[0] for g in got} == {"1.2.3"}
    assert count.read_text().count("x") == 1
