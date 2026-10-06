"""Pure parser tests for the canvas module — no I/O, no notifications."""
from __future__ import annotations

import pytest

from aegis.canvas import (
    BODY,
    InvalidSection,
    PREAMBLE,
    Section,
    parse_sections,
    render_sections,
    section_names,
    valid_section_name,
)
from aegis.canvas.parser import (
    append_to_section,
    find_section,
    section_line_count,
    write_section,
)


# ---------- valid_section_name ----------

@pytest.mark.parametrize("name", [
    "intro", "data", "section-1", "section_1", "Section One",
    "ABC", "x1", PREAMBLE, BODY,
])
def test_valid_section_names(name):
    assert valid_section_name(name) is True


@pytest.mark.parametrize("name", [
    "", " leading", "trailing ", "with/slash", "with:colon",
    "line\nbreak", "tab\there",
])
def test_invalid_section_names(name):
    assert valid_section_name(name) is False


# ---------- parse_sections ----------

def test_parse_empty_returns_empty_list():
    assert parse_sections("") == []


def test_parse_no_headings_returns_single_body():
    text = "Just some text\nover two lines"
    secs = parse_sections(text)
    assert secs == [Section(name=BODY, body=text)]


def test_parse_one_section_no_preamble():
    text = "## intro\nhello\nworld"
    secs = parse_sections(text)
    assert secs == [Section(name="intro", body="hello\nworld")]


def test_parse_with_preamble_and_two_sections():
    text = (
        "# Title\n"
        "\n"
        "Preamble text.\n"
        "## intro\n"
        "intro body line 1\n"
        "intro body line 2\n"
        "## data\n"
        "data body\n"
    )
    secs = parse_sections(text)
    assert section_names(secs) == [PREAMBLE, "intro", "data"]
    assert secs[0].body == "# Title\n\nPreamble text."
    assert secs[1].body == "intro body line 1\nintro body line 2"
    # Last section gets the final blank-line/EOF behavior
    assert secs[2].body in ("data body\n", "data body")


def test_parse_section_with_empty_body():
    text = "## empty\n## next\nbody"
    secs = parse_sections(text)
    assert section_names(secs) == ["empty", "next"]
    assert secs[0].body == ""
    assert secs[1].body == "body"


def test_parse_heading_must_have_space_after_hash():
    # "##foo" is NOT a heading (no space)
    text = "##foo\nbody"
    secs = parse_sections(text)
    # Treated as plain body
    assert secs == [Section(name=BODY, body=text)]


def test_parse_h3_is_content_not_section():
    text = "## intro\nbody\n### subhead\nmore body"
    secs = parse_sections(text)
    assert section_names(secs) == ["intro"]
    assert "### subhead" in secs[0].body


# ---------- render_sections ----------

def test_render_roundtrip_no_headings():
    text = "single body line"
    assert render_sections(parse_sections(text)) == text


def test_render_roundtrip_with_preamble_and_sections():
    secs = [
        Section(name=PREAMBLE, body="# Title\n\npreamble"),
        Section(name="intro", body="intro body"),
        Section(name="data", body="data body"),
    ]
    out = render_sections(secs)
    # Round-trip back through parse
    re_parsed = parse_sections(out)
    assert section_names(re_parsed) == [PREAMBLE, "intro", "data"]


def test_render_empty_section_keeps_heading():
    secs = [Section(name="empty", body=""),
            Section(name="next", body="body")]
    out = render_sections(secs)
    assert "## empty" in out
    assert "## next" in out


# ---------- write_section ----------

def test_write_section_replaces_existing():
    secs = [Section(name="intro", body="old"),
            Section(name="data", body="d")]
    new = write_section(secs, "intro", "new")
    assert find_section(new, "intro").body == "new"
    assert find_section(new, "data").body == "d"
    # Order preserved
    assert section_names(new) == ["intro", "data"]


def test_write_section_appends_when_missing():
    secs = [Section(name="intro", body="i")]
    new = write_section(secs, "data", "d")
    assert section_names(new) == ["intro", "data"]


def test_write_preamble_when_missing_inserts_at_front():
    secs = [Section(name="intro", body="i")]
    new = write_section(secs, PREAMBLE, "pre")
    assert section_names(new) == [PREAMBLE, "intro"]


def test_write_section_rejects_invalid_name():
    secs = []
    with pytest.raises(InvalidSection):
        write_section(secs, "bad/name", "x")


def test_write_does_not_mutate_input():
    secs = [Section(name="intro", body="old")]
    snapshot = list(secs)
    _ = write_section(secs, "intro", "new")
    assert secs == snapshot


# ---------- append_to_section ----------

def test_append_to_existing_joins_with_newline():
    secs = [Section(name="data", body="line1")]
    new = append_to_section(secs, "data", "line2")
    assert find_section(new, "data").body == "line1\nline2"


def test_append_to_empty_existing_no_leading_newline():
    secs = [Section(name="data", body="")]
    new = append_to_section(secs, "data", "first")
    assert find_section(new, "data").body == "first"


def test_append_to_missing_creates_section():
    new = append_to_section([], "data", "x")
    assert section_names(new) == ["data"]
    assert find_section(new, "data").body == "x"


def test_append_rejects_invalid_name():
    with pytest.raises(InvalidSection):
        append_to_section([], "bad name/", "x")


# ---------- section_line_count ----------

@pytest.mark.parametrize("body,expected", [
    ("", 0),
    ("one", 1),
    ("one\ntwo", 2),
    ("one\ntwo\nthree", 3),
])
def test_section_line_count(body, expected):
    assert section_line_count(body) == expected
