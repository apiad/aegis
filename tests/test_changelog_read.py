"""The release notes /whats-new tours, read from the installed package (#339).

The 2.6.0 wheel carried 190 files and no changelog, so an installed aegis had
nothing to tour. These pin which sections a tour gets, where the file is read
from, and that the wheel now carries it."""

from __future__ import annotations

import re
import subprocess
import tomllib
import zipfile
from pathlib import Path

import pytest

from aegis import changelog_read
from aegis.changelog_read import changelog_text, sections_between

REPO = Path(__file__).resolve().parents[1]

NOTES = """# Changelog

## [Unreleased]

### Added

- **Not released yet.**

## [2.10.0] - 2026-11-01

### Added

- **Ten.**
  help: sessions

## [2.9.0] - 2026-10-20

### Fixed

- **Nine.**

## [2.6.0] - 2026-10-10

- **Six.**

## [0.42.0] - 2026-10-02

- **Forty-two.**
"""


def versions(text: str) -> list[str]:
    return re.findall(r"^## \[([^\]]+)\]", text, re.M)


@pytest.mark.parametrize(
    ("after", "upto", "toured"),
    [
        # Numbers, not strings: 2.10.0 is newer than 2.9.0. Newest last.
        ("2.6.0", "2.10.0", ["2.9.0", "2.10.0"]),
        ("0.42.0", "2.9.0", ["2.6.0", "2.9.0"]),
        # No recorded tour: the installed release only.
        (None, "2.10.0", ["2.10.0"]),
        (None, "2.6.0", ["2.6.0"]),
        # Nothing left, or a downgrade below the last tour.
        ("2.10.0", "2.10.0", []),
        ("3.0.0", "2.10.0", []),
        # A record that is not a version reads as no record.
        ("garbage", "2.9.0", ["2.9.0"]),
        # An installed version the notes lack, or one that is not a number.
        (None, "2.7.0", []),
        ("2.6.0", "Unreleased", []),
    ],
)
def test_sections_after_the_tour_up_to_the_installed_release(after, upto, toured):
    out = sections_between(NOTES, after, upto)
    assert versions(out) == toured
    assert "Not released yet" not in out


def test_a_section_runs_to_the_next_heading_and_keeps_its_help_line():
    assert sections_between(NOTES, "2.9.0", "2.10.0") == (
        "## [2.10.0] - 2026-11-01\n\n### Added\n\n- **Ten.**\n  help: sessions"
    )


def test_the_package_copy_wins_and_the_checkout_is_the_fallback(tmp_path, monkeypatch):
    packaged, source = tmp_path / "packaged.md", tmp_path / "source.md"
    monkeypatch.setattr(changelog_read, "PACKAGED", packaged)
    monkeypatch.setattr(changelog_read, "SOURCE", source)
    assert changelog_text() is None
    source.write_text("from the checkout")
    assert changelog_text() == "from the checkout"
    packaged.write_text("from the wheel")
    assert changelog_text() == "from the wheel"


def test_a_checkout_reads_its_own_changelog():
    """An editable install, and these tests, have no copy in src/aegis."""
    assert not changelog_read.PACKAGED.exists()
    assert changelog_text() == (REPO / "CHANGELOG.md").read_text(encoding="utf-8")


def test_the_version_being_built_has_its_release_notes():
    """A release without its own section would offer /whats-new and then have
    nothing to tour. `scripts/changelog.py apply` and the version bump land in
    one commit (know-how/releasing.md); this holds them together."""
    meta = tomllib.loads((REPO / "pyproject.toml").read_text())
    version = meta["project"]["version"]
    text = (REPO / "CHANGELOG.md").read_text(encoding="utf-8")
    assert sections_between(text, None, version).startswith(f"## [{version}] - ")


@pytest.mark.slow
def test_the_wheel_carries_the_changelog(tmp_path):
    """The check the 2.6.0 wheel would have failed, on a wheel built the way the
    release workflow builds one (`uv build`: the sdist, then the wheel from it)."""
    built = subprocess.run(
        ["uv", "build", "--out-dir", str(tmp_path), str(REPO)],
        capture_output=True,
        text=True,
        timeout=600,
    )
    assert built.returncode == 0, built.stderr
    [wheel] = tmp_path.glob("*.whl")
    with zipfile.ZipFile(wheel) as z:
        shipped = z.read("aegis/CHANGELOG.md").decode()
    assert shipped == (REPO / "CHANGELOG.md").read_text(encoding="utf-8")
