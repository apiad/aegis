import asyncio
import re

import pytest

from aegis import artifacts
from aegis.artifacts import (
    ArtifactError,
    body,
    check,
    header,
    mint_id,
    skeleton,
)
from aegis.transcript.store import read_store

from .test_session import Harness


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


def recs(h):
    return read_store(h.path)[0]


def art_entries(s):
    return [e for e in s.entries() if e["kind"] == "artifact"]


def test_a_draft_is_in_the_board_and_not_in_the_transcript(tmp_path, fake_claude):
    h = Harness(tmp_path, fake_claude)
    s = h.session
    a = s.artifacts.create("Pick", "cap", {"n": 0})
    assert a.status == "draft" and a.id.startswith("art-")
    assert artifacts.draft_path(s.state_root, a.id).read_text() == skeleton("Pick")
    assert art_entries(s) == [] and s.artifacts.read(a.id)["status"] == "draft"
    with pytest.raises(ArtifactError) as e:
        s.artifacts.live(a.id)
    assert e.value.code == "not_live"
    with pytest.raises(ArtifactError) as e:
        s.artifacts.get("art-ffffffff")
    assert e.value.code == "no_artifact"


async def test_state_writes_are_coalesced_to_one_record_a_second(
    tmp_path, fake_claude, monkeypatch
):
    monkeypatch.setattr(artifacts, "STATE_EVERY_S", 0.2)
    h = Harness(tmp_path, fake_claude)
    s = h.session
    a = s.artifacts.create("T", None, None)
    s.artifacts.land(a, "F", "index.html", None, True)
    for n in range(5):
        s.artifacts.state(a.id, {"n": n}, "page")
    states = [r for r in recs(h) if r.get("kind") == "artifact_state"]
    assert len(states) == 1 and states[0]["state"] == {"n": 0}  # the first at once
    assert s.artifacts.read(a.id)["state"] == {"n": 4}  # memory is never behind
    await asyncio.sleep(0.3)
    states = [r for r in recs(h) if r.get("kind") == "artifact_state"]
    assert [x["state"] for x in states] == [{"n": 0}, {"n": 4}]  # then the latest, once
    s.artifacts.state(a.id, {"n": 5}, "page")
    s.artifacts.event(a.id, "hover", 1)  # an event flushes first
    kinds = [r["kind"] for r in recs(h) if r["kind"].startswith("artifact")]
    assert kinds[-2:] == ["artifact_state", "artifact_event"]
    assert art_entries(s)[0]["detail"]["state"] == {"n": 5}
    assert h.refold_matches()


def test_an_agents_write_is_recorded_at_once_and_marked(tmp_path, fake_claude):
    h = Harness(tmp_path, fake_claude)
    s = h.session
    a = s.artifacts.create("T", None, None)
    s.artifacts.land(a, "F", "index.html", None, None)
    s.artifacts.state(a.id, {"x": 1}, "page")
    s.artifacts.state(a.id, {"x": 2}, "agent")
    states = [r for r in recs(h) if r.get("kind") == "artifact_state"]
    assert [(x["state"], x["by"]) for x in states] == [
        ({"x": 1}, "page"),
        ({"x": 2}, "agent"),
    ]


def test_events_are_rate_limited_and_names_checked(tmp_path, fake_claude, monkeypatch):
    monkeypatch.setattr(artifacts, "EMITS_PER_MINUTE", 2)
    h = Harness(tmp_path, fake_claude)
    s = h.session
    a = s.artifacts.create("T", None, None)
    s.artifacts.land(a, "F", "index.html", None, None)
    s.artifacts.event(a.id, "a", 1)
    s.artifacts.event(a.id, "b", 2)
    with pytest.raises(ArtifactError) as e:
        s.artifacts.event(a.id, "c", 3)
    assert e.value.code == "rate_limited"
    with pytest.raises(ArtifactError) as e:
        s.artifacts.event(a.id, "submit", 3)
    assert e.value.code == "bad_name"


def test_submit_and_close_end_the_artifact_and_a_second_is_refused(
    tmp_path, fake_claude
):
    h = Harness(tmp_path, fake_claude)
    s = h.session
    a = s.artifacts.create("T", None, None)
    s.artifacts.land(a, "F", "index.html", None, None)
    s.artifacts.submit(a.id, {"p": "b"}, "Picked B")
    assert art_entries(s)[0]["status"] == "submitted"
    assert s.artifacts.read(a.id) == {
        "status": "submitted",
        "state": None,
        "events": [],
        "submitted": {"p": "b"},
        "label": "Picked B",
        "errors": 0,
    }
    with pytest.raises(ArtifactError) as e:
        s.artifacts.submit(a.id, {}, "again")
    assert e.value.code == "not_live"
    b = s.artifacts.create("U", None, None)
    s.artifacts.land(b, "G", "index.html", None, None)
    s.artifacts.close(b.id, None)
    assert art_entries(s)[1]["status"] == "closed"
    c = s.artifacts.create("V", None, None)
    s.artifacts.close(c.id, None)  # a draft: gone, nothing recorded
    assert not artifacts.draft_dir(s.state_root, c.id).exists()
    assert len(art_entries(s)) == 2
    with pytest.raises(ArtifactError):
        s.artifacts.get(c.id)


def test_one_error_wakes_per_landed_page(tmp_path, fake_claude):
    h = Harness(tmp_path, fake_claude)
    s = h.session
    a = s.artifacts.create("T", None, None)
    s.artifacts.land(a, "F", "index.html", None, None)
    assert s.artifacts.read(a.id)["errors"] == 0
    assert s.artifacts.error_wakes(a.id) is True
    assert s.artifacts.error_wakes(a.id) is False
    assert s.artifacts.error_wakes(a.id) is False
    assert s.artifacts.read(a.id)["errors"] == 2
    s.artifacts.land(a, "F2", "index.html", None, None)  # a resend
    assert s.artifacts.read(a.id)["errors"] == 0
    assert s.artifacts.error_wakes(a.id) is True


def test_a_restart_reloads_landed_artifacts_from_the_store_and_forgets_drafts(
    tmp_path, fake_claude
):
    h = Harness(tmp_path, fake_claude)
    s = h.session
    a = s.artifacts.create("T", "c", {"n": 0})
    s.artifacts.land(a, "F", "index.html", "c", True)
    s.artifacts.state(a.id, {"n": 9}, "agent")
    s.artifacts.create("draft", None, None)
    s2 = h.make()
    assert s2.artifacts.read(a.id)["state"] == {"n": 9}
    assert s2.artifacts.get(a.id).status == "live"
    assert [x.id for x in s2.artifacts.all()] == [a.id]


async def test_a_probe_answers_when_a_browser_reports_and_none_when_nobody_listens(
    tmp_path, fake_claude, monkeypatch
):
    monkeypatch.setattr(artifacts, "PROBE_TIMEOUT_S", 0.3)
    h = Harness(tmp_path, fake_claude)
    s = h.session
    a = s.artifacts.create("T", None, None)
    assert await s.artifacts.probe(a, "/files/F/index.html", subscribers=0) is None
    published = []
    s._publish = lambda ch, ops: published.append((ch, ops))
    task = asyncio.ensure_future(
        s.artifacts.probe(a, "/files/F/index.html", subscribers=1)
    )
    await asyncio.sleep(0)
    ((ch, ops),) = published
    assert ch == s.channel and ops[0]["probe"]["url"] == "/files/F/index.html"
    pid = ops[0]["probe"]["id"]
    assert ops[0]["probe"]["artifact_id"] == a.id
    assert s.artifacts.probed("nope", True, "", "") is False  # unknown probe: dropped
    assert s.artifacts.probed(pid, False, "ReferenceError: x", "at a:1\nat b:2") is True
    with pytest.raises(ArtifactError) as e:
        await task
    assert e.value.code == "page_error" and "ReferenceError: x" in e.value.message
    slow = asyncio.ensure_future(
        s.artifacts.probe(a, "/files/F/index.html", subscribers=1)
    )
    assert await slow is None  # nobody answered within the timeout: lands unprobed
