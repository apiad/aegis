import re

import pytest

from aegis import artifacts
from aegis.artifacts import ArtifactError, body, check, header, mint_id, skeleton


def test_the_skeleton_loads_the_script_and_the_stylesheet_and_calls_ready():
    html = skeleton("Pick a layout")
    assert '<script src="/static/js/artifact.js"></script>' in html
    assert '<link rel="stylesheet" href="/static/css/artifact.css">' in html
    assert "<title>Pick a layout</title>" in html and "<h2>Pick a layout</h2>" in html
    assert "aegis.ready(" in html
    with pytest.raises(ArtifactError) as e:
        check(html)  # unedited, the skeleton answers nothing, and the check says so
    assert e.value.code == "no_answer"


def test_a_title_is_escaped_in_the_skeleton():
    assert (
        "&lt;b&gt;" in skeleton("<b>")
        and "<b>" not in skeleton("<b>").split("<body>")[1]
    )


def test_ids_are_art_dash_eight_hex():
    assert re.fullmatch(r"art-[0-9a-f]{8}", mint_id())
    assert mint_id() != mint_id()


def test_write_draft_puts_the_skeleton_under_the_state_root(tmp_path):
    p = artifacts.write_draft(tmp_path, "art-00000001", "Hi")
    assert p == tmp_path / "artifacts" / "art-00000001" / "index.html"
    assert p.read_text() == skeleton("Hi")


def test_check_refuses_a_page_without_the_script_or_without_an_answer():
    with pytest.raises(ArtifactError) as e:
        check("<h1>hi</h1><script>aegis.submit({})</script>")
    assert e.value.code == "no_script" and "/static/js/artifact.js" in e.value.message
    with pytest.raises(ArtifactError) as e:
        check('<script src="/static/js/artifact.js"></script><h1>hi</h1>')
    assert e.value.code == "no_answer" and "aegis.submit" in e.value.message
    for call in ("aegis.submit(", "aegis.emit(", "aegis.state("):
        check(
            f'<script src="/static/js/artifact.js"></script><script>{call}1)</script>'
        )


@pytest.mark.parametrize(
    "name,ok",
    [
        ("pick", True),
        ("a-b_c9", True),
        ("submit", False),
        ("error", False),
        ("close", False),
        ("Pick", False),
        ("9a", False),
        ("a" * 33, False),
        ("", False),
    ],
)
def test_event_names(name, ok):
    assert artifacts.valid_event(name) is ok


def test_json_size_counts_the_compact_encoding():
    assert artifacts.json_size({"a": [1, 2]}) == len(b'{"a":[1,2]}')


def test_json_size_survives_a_lone_surrogate():
    assert (
        artifacts.json_size("\ud800") == 5
    )  # two quotes and the surrogate's three bytes


def test_the_header_and_body_formats():
    h = header("art-3f9a12c0", "submit")
    assert h.startswith("> from artifact:art-3f9a12c0 · submit · 20")
    assert (
        body({"layout": "b"}, "Picked B") == 'Picked B\n```json\n{"layout": "b"}\n```'
    )
    assert body({"n": 1}) == '```json\n{"n": 1}\n```'
    err = body(None, error=("ReferenceError: d3 is not defined", "a\nb\nc\nd\ne\nf\ng"))
    assert err == "ReferenceError: d3 is not defined\n```\na\nb\nc\nd\ne\n```"
