import json
import subprocess
from pathlib import Path

import pytest

from aegis import version
from aegis.version import Versions, running, status

RELEASE = {"version": "2.0.1", "commit": None, "ref": None, "dev": False}


@pytest.mark.parametrize(
    "run, latest, want",
    [
        (RELEASE, "2.0.1", "current"),
        (RELEASE, "2.0.10", "behind"),
        (RELEASE, "1.9.9", "current"),
        (RELEASE, None, "unknown"),
        (RELEASE, "2.1.0rc1", "unknown"),
        ({**RELEASE, "dev": True, "commit": "abc"}, "9.0.0", "dev"),
    ],
)
def test_status(run, latest, want):
    assert status(run, latest) == want


class FakeDist:
    def __init__(self, direct: dict | None):
        self.version = "2.0.1"
        self._direct = direct

    def read_text(self, name):
        assert name == "direct_url.json"
        return json.dumps(self._direct) if self._direct else None


def test_a_wheel_from_pypi_is_its_release_number(monkeypatch):
    monkeypatch.setattr(version, "distribution", lambda _: FakeDist(None))
    assert running() == RELEASE


def test_a_build_from_git_carries_its_commit_and_ref(monkeypatch):
    direct = {
        "url": "https://github.com/apiad/aegis",
        "vcs_info": {
            "vcs": "git",
            "commit_id": "39d80a886fef55255bacf634da7a98d521fd4419",
            "requested_revision": "feat/aegis-app-window",
        },
    }
    monkeypatch.setattr(version, "distribution", lambda _: FakeDist(direct))
    assert running() == {
        "version": "2.0.1",
        "commit": "39d80a886fef55255bacf634da7a98d521fd4419",
        "ref": "feat/aegis-app-window",
        "dev": True,
    }


def test_an_editable_install_reads_its_source_tree(monkeypatch, tmp_path):
    git = ["git", "-C", str(tmp_path), "-c", "user.name=t", "-c", "user.email=t@t"]
    subprocess.run([*git, "init", "-q", "-b", "topic"], check=True)
    subprocess.run([*git, "commit", "-q", "--allow-empty", "-m", "x"], check=True)
    head = subprocess.run(
        [*git, "rev-parse", "HEAD"], capture_output=True, text=True, check=True
    ).stdout.strip()
    direct = {"url": tmp_path.as_uri(), "dir_info": {"editable": True}}
    monkeypatch.setattr(version, "distribution", lambda _: FakeDist(direct))
    assert running() == {
        "version": "2.0.1",
        "commit": head,
        "ref": "topic",
        "dev": True,
    }


def releases(path: Path, v: str) -> None:
    path.write_text(json.dumps({"info": {"version": v}}))


async def test_latest_is_cached_and_a_failed_fetch_is_none(monkeypatch, tmp_path):
    feed = tmp_path / "pypi.json"
    monkeypatch.setenv("AEGIS_RELEASES_URL", feed.as_uri())
    v = Versions()
    assert await v.latest() is None  # no file: offline, not an error

    v = Versions()
    releases(feed, "2.0.1")
    assert await v.latest() == "2.0.1"
    releases(feed, "2.0.2")
    assert await v.latest() == "2.0.1", "PyPI is asked at most once an hour"

    v._checked -= version.FRESH_S
    assert await v.latest() == "2.0.2"
    feed.unlink()
    v._checked -= version.FRESH_S
    assert await v.latest() == "2.0.2", "a failed fetch keeps the last answer"
