"""Parking: the pure rules, the config block and the leftover-process check.
The sessions themselves are in test_park_e2e.py."""

import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from aegis.config import park_after, park_from
from aegis.doctor import _park
from aegis.harness import SESSION_VAR
from aegis.park import strays


def test_the_block_maps_each_harness_to_its_seconds():
    p = park_from({"claude-code": 1200, "codex": 1800})
    assert p == {"claude-code": 1200, "codex": 1800}
    assert park_after(p, "claude-code") == 1200
    assert park_after(p, "opencode") is None


def test_no_block_parks_nothing():
    assert park_from(None) == {}
    assert park_after(park_from(None), "claude-code") is None


@pytest.mark.parametrize(
    "raw, says",
    [
        ([1200], "mapping"),
        ({"cluade-code": 1200}, "no harness 'cluade-code'"),
        ({"claude-code": 0}, "positive"),
        ({"claude-code": True}, "positive"),
        ({"claude-code": "20m"}, "positive"),
    ],
)
def test_a_wrong_block_parks_nothing_and_says_why(raw, says):
    p = park_from(raw)
    assert says in p["error"]
    assert park_after(p, "claude-code") is None


def test_doctor_names_the_mistake_and_warns_on_an_unmeasured_harness():
    assert _park(park_from({"claude-code": 0})).level == "error"
    assert _park({}).level == "ok"
    assert _park({"claude-code": 1200}).level == "ok"
    f = _park({"claude-code": 1200, "codex": 600})
    assert f.level == "warn" and "codex" in f.message


STAND_IN = r"""
import subprocess, sys, time
subprocess.Popen(["sleep", "40"])                                    # a server it keeps
subprocess.run(["bash", "-c", "nohup sleep 41 >/dev/null 2>&1 &"])  # left behind
print("ready", flush=True)
time.sleep(40)
"""


def _pid_of(argv: list[str]) -> int:
    for d in Path("/proc").iterdir():
        if d.name.isdigit():
            try:
                if (d / "cmdline").read_bytes().split(b"\0")[:-1] == [
                    a.encode() for a in argv
                ]:
                    return int(d.name)
            except OSError:
                pass
    raise AssertionError(f"no process {argv}")


def test_strays_are_what_a_park_would_kill_and_not_what_the_harness_keeps():
    env = {**os.environ, SESSION_VAR: "s-strays"}
    h = subprocess.Popen(
        [sys.executable, "-c", STAND_IN], env=env, stdout=subprocess.PIPE, text=True
    )
    try:
        assert h.stdout.readline().strip() == "ready"
        orphan = _pid_of(["sleep", "41"])
        kept = _pid_of(["sleep", "40"])
        found = strays(h.pid, "s-strays", None)
        assert orphan in found
        assert kept not in found and h.pid not in found
        assert strays(h.pid, "another-session", None) == []
    finally:
        h.kill()
        for argv in (["sleep", "41"], ["sleep", "40"]):
            try:
                os.kill(_pid_of(argv), 9)
            except AssertionError:
                pass


def test_without_keep_servers_every_child_is_a_stray():
    env = {**os.environ, SESSION_VAR: "s-all"}
    h = subprocess.Popen(
        [sys.executable, "-c", STAND_IN], env=env, stdout=subprocess.PIPE, text=True
    )
    try:
        assert h.stdout.readline().strip() == "ready"
        kept = _pid_of(["sleep", "40"])
        orphan = _pid_of(["sleep", "41"])
        found = strays(h.pid, "s-all", None, keep_servers=False)
        assert kept in found and orphan in found and h.pid not in found
    finally:
        h.kill()
        for argv in (["sleep", "41"], ["sleep", "40"]):
            try:
                os.kill(_pid_of(argv), 9)
            except AssertionError:
                pass


def test_a_shell_the_harness_runs_is_a_stray():
    env = {**os.environ, SESSION_VAR: "s-shell"}
    h = subprocess.Popen(
        [
            sys.executable,
            "-c",
            "import subprocess,time;subprocess.Popen(['bash','-c','sleep 42; true']);time.sleep(30)",
        ],
        env=env,
    )
    try:
        deadline = time.time() + 3
        while time.time() < deadline and not strays(h.pid, "s-shell", None):
            time.sleep(0.05)
        assert strays(h.pid, "s-shell", None)
    finally:
        h.kill()
        try:
            os.kill(_pid_of(["sleep", "42"]), 9)
        except AssertionError:
            pass
