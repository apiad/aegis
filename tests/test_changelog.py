"""Changelog fragments — one file per change, collated at release.

`CHANGELOG.md` was the only file that conflicted on every PR (issue #10):
every change appends to the top of the same list, so two agents write
adjacent lines in one hunk, which is the one thing git cannot merge. A
fragment per change means two PRs never touch the same bytes.

These test the collator, not the convention: given fragments, does
`CHANGELOG.md` come out right, and does a malformed fragment fail loudly
rather than being silently dropped from the release notes.
"""

from __future__ import annotations

import pytest

import importlib.util
import sys
from pathlib import Path

_SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "changelog.py"
_spec = importlib.util.spec_from_file_location("changelog", _SCRIPT)
changelog = importlib.util.module_from_spec(_spec)
sys.modules["changelog"] = changelog  # @dataclass looks its module up here
_spec.loader.exec_module(changelog)
CATEGORIES = changelog.CATEGORIES
FragmentError = changelog.FragmentError
collate = changelog.collate
parse_fragment_name = changelog.parse_fragment_name
read_fragments = changelog.read_fragments

HEADER = """# Changelog

All notable changes to Aegis are documented here.
The format follows Keep a Changelog; this project uses SemVer (0.x).

## [Unreleased]

### Changed

- **An existing entry nobody should disturb.** Body.

## [0.39.0] - 2026-09-23

### Fixed

- **Something old.** Body.
"""


def _write(d, name: str, body: str):
    p = d / name
    p.write_text(body)
    return p


# ---------- the filename is the category ---------------------------------


def test_the_category_comes_from_the_filename():
    slug, category = parse_fragment_name("10-changelog-fragments.changed.md")
    assert slug == "10-changelog-fragments"
    assert category == "changed"


def test_every_category_in_the_changelog_is_accepted():
    """The set is what this file actually uses, not the full Keep a Changelog
    vocabulary — a category nothing renders is a fragment that vanishes."""
    for c in ("added", "changed", "fixed", "removed", "performance"):
        assert c in CATEGORIES


def test_an_unknown_category_is_refused_by_name():
    """Silently dropping it would lose the entry from the release notes,
    which is the failure the fragments exist to prevent."""
    with pytest.raises(FragmentError) as e:
        parse_fragment_name("10-thing.improvements.md")
    assert "improvements" in str(e.value)
    assert "changed" in str(e.value), "the refusal has to name the real ones"


def test_a_name_with_no_category_is_refused():
    with pytest.raises(FragmentError):
        parse_fragment_name("10-thing.md")


# ---------- reading the directory ----------------------------------------


def test_the_readme_is_not_a_fragment(tmp_path):
    """`changelog.d/README.md` carries the convention and must not be
    collated into the release notes."""
    _write(tmp_path, "README.md", "how to write a fragment\n")
    _write(tmp_path, "7-a.fixed.md", "- **A.** Body.\n")
    frags = read_fragments(tmp_path)
    assert [f.slug for f in frags] == ["7-a"]


def test_an_empty_fragment_is_refused(tmp_path):
    _write(tmp_path, "7-a.fixed.md", "   \n\n")
    with pytest.raises(FragmentError) as e:
        read_fragments(tmp_path)
    assert "7-a.fixed.md" in str(e.value)


def test_fragments_are_read_in_a_stable_order(tmp_path):
    _write(tmp_path, "9-b.fixed.md", "- **B.**\n")
    _write(tmp_path, "10-c.fixed.md", "- **C.**\n")
    _write(tmp_path, "7-a.fixed.md", "- **A.**\n")
    assert [f.slug for f in read_fragments(tmp_path)] == ["10-c", "7-a", "9-b"]


# ---------- collating ----------------------------------------------------


def test_a_fragment_lands_under_its_category(tmp_path):
    _write(tmp_path, "7-a.fixed.md", "- **A thing was fixed.** Body.\n")
    out = collate(HEADER, read_fragments(tmp_path))
    assert "### Fixed\n\n- **A thing was fixed.** Body." in out


def test_a_fragment_joins_an_existing_section_rather_than_making_a_second(
    tmp_path,
):
    """Inserting at the first matching heading produced two `### Changed`
    blocks under one release — caught by hand during #9, so it is pinned."""
    _write(tmp_path, "7-a.changed.md", "- **A new changed entry.** Body.\n")
    out = collate(HEADER, read_fragments(tmp_path))
    unreleased = out.split("## [0.39.0]")[0]
    assert unreleased.count("### Changed") == 1
    assert "An existing entry nobody should disturb" in unreleased
    assert "A new changed entry" in unreleased


def test_the_existing_entries_keep_their_place(tmp_path):
    """Fragments govern changes from here on; whatever is already written
    under [Unreleased] is not rewritten or reordered."""
    _write(tmp_path, "7-a.changed.md", "- **New.** Body.\n")
    out = collate(HEADER, read_fragments(tmp_path))
    assert out.index("An existing entry nobody should disturb") < out.index("**New.**")


def test_categories_render_in_a_fixed_order(tmp_path):
    _write(tmp_path, "7-f.fixed.md", "- **F.**\n")
    _write(tmp_path, "7-a.added.md", "- **A.**\n")
    out = collate(HEADER, read_fragments(tmp_path))
    unreleased = out.split("## [0.39.0]")[0]
    assert unreleased.index("### Added") < unreleased.index("### Fixed")


def test_an_older_release_is_never_touched(tmp_path):
    _write(tmp_path, "7-a.fixed.md", "- **A.**\n")
    out = collate(HEADER, read_fragments(tmp_path))
    old = out.split("## [0.39.0]")[1]
    assert "**A.**" not in old
    assert "Something old" in old


def test_collating_nothing_leaves_the_file_byte_identical(tmp_path):
    assert collate(HEADER, read_fragments(tmp_path)) == HEADER


def test_a_release_gets_its_own_section(tmp_path):
    _write(tmp_path, "7-a.fixed.md", "- **A.**\n")
    out = collate(HEADER, read_fragments(tmp_path), version="0.40.0", date="2026-09-28")
    assert "## [0.40.0] - 2026-09-28" in out
    # The new section sits above the previous release and below Unreleased.
    assert (
        out.index("## [Unreleased]")
        < out.index("## [0.40.0]")
        < out.index("## [0.39.0]")
    )
    assert "**A.**" in out.split("## [0.40.0]")[1].split("## [0.39.0]")[0]


def test_a_release_leaves_unreleased_empty_of_the_collated_entries(tmp_path):
    _write(tmp_path, "7-a.fixed.md", "- **A.**\n")
    out = collate(HEADER, read_fragments(tmp_path), version="0.40.0", date="2026-09-28")
    unreleased = out.split("## [0.40.0]")[0]
    assert "**A.**" not in unreleased


def test_a_new_section_lands_in_canonical_order_beside_existing_ones(tmp_path):
    """`performance` sorts after `changed`, so a new Performance section goes
    below an existing Changed one rather than above it.

    Placing every new section at the top was stable but put the real
    CHANGELOG's sections out of the order its own header claims to follow.
    Existing sections still are not moved — the new one is just slotted in.
    """
    _write(tmp_path, "7-p.performance.md", "- **Faster.**\n")
    out = collate(HEADER, read_fragments(tmp_path))
    unreleased = out.split("## [0.39.0]")[0]
    assert unreleased.index("### Changed") < unreleased.index("### Performance")


def test_a_new_section_that_sorts_first_goes_above_the_existing_one(tmp_path):
    """The mirror case: `added` sorts before `changed`."""
    _write(tmp_path, "7-a.added.md", "- **New thing.**\n")
    out = collate(HEADER, read_fragments(tmp_path))
    unreleased = out.split("## [0.39.0]")[0]
    assert unreleased.index("### Added") < unreleased.index("### Changed")
