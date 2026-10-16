"""Saved workflows: the built-ins and the project's, their versions and
their history (spec §08)."""

import json

import pytest

from aegis.ops import OpError
from aegis.workflow_lang import parse
from aegis.workflow_store import BUILTIN, Store, sha_of

AGENTS = {"opus", "sonnet"}
NAP = (
    "id: nap\ntitle: Nap\nsay: Nap a while.\n"
    "args: {type: object, properties: {secs: {type: string}}, required: [secs]}\n"
    'root:\n  wait: "{{args.secs}}"\n  say: Wait {{args.secs}}.\n'
)
BY = {"kind": "agent", "log_id": "abc", "handle": "calm-hopper"}


@pytest.fixture
def store(tmp_path):
    (tmp_path / "state").mkdir()
    return Store(tmp_path, tmp_path / "state")


def test_the_built_in_loop_lints_and_is_listed(store):
    wf, errors = parse((BUILTIN / "loop.yaml").read_text(), AGENTS)
    assert errors == [] and wf.root.body["at_max"] == "done"
    loop = store.all()["loop"]
    assert loop.source == "builtin" and loop.sha == sha_of(loop.text)


def test_save_writes_the_project_file_with_its_ids_and_a_history_line(store, tmp_path):
    saved = store.save(NAP, AGENTS, BY)
    path = tmp_path / ".aegis" / "workflows" / "nap.yaml"
    assert saved.path == path and saved.source == "project"
    assert "id: n1" in path.read_text()  # the root wait's generated id, written
    assert store.get("nap").sha == saved.sha
    (line,) = [json.loads(x) for x in store.history.read_text().splitlines()]
    assert line["ev"] == "saved" and line["sha"] == saved.sha and line["by"] == BY
    assert store.last_save("nap")["by"] == BY
    assert store.text_of("nap", saved.sha) == path.read_text()


def test_a_hand_edit_is_seen_at_once_and_has_no_saver(store):
    saved = store.save(NAP, AGENTS, BY)
    saved.path.write_text(saved.text.replace("Nap a while.", "Sleep."))
    assert store.get("nap").sha != saved.sha
    assert store.last_save("nap") is None  # nobody saved this version through aegis


def test_a_built_in_id_is_reserved_and_a_broken_workflow_is_not_saved(store):
    with pytest.raises(OpError, match="built-in") as e:
        store.save(NAP.replace("id: nap", "id: loop"), AGENTS, BY)
    assert e.value.code == "reserved"
    with pytest.raises(OpError) as e:
        store.save(NAP.replace("say: Wait", "sya: Wait"), AGENTS, BY)
    assert e.value.code == "lint"
    with pytest.raises(OpError) as e:
        store.delete("loop", BY)
    assert e.value.code == "reserved"


def test_delete_removes_the_file_and_notes_it(store):
    store.save(NAP, AGENTS, BY)
    store.delete("nap", BY)
    with pytest.raises(OpError) as e:
        store.get("nap")
    assert e.value.code == "no_workflow"
    assert json.loads(store.history.read_text().splitlines()[-1])["ev"] == "deleted"


def test_a_project_file_named_like_a_built_in_is_ignored(store, tmp_path):
    d = tmp_path / ".aegis" / "workflows"
    d.mkdir(parents=True)
    (d / "loop.yaml").write_text(NAP.replace("id: nap", "id: loop"))
    assert store.all()["loop"].source == "builtin"


def test_the_listing_says_what_is_wrong_with_a_file(store, tmp_path):
    store.save(NAP, AGENTS, BY)
    (tmp_path / ".aegis" / "workflows" / "odd.yaml").write_text(NAP)  # its id is nap
    rows = {r["id"]: r for r in store.listing(AGENTS)}
    assert rows["nap"]["title"] == "Nap" and rows["nap"]["saved_by"] == BY
    assert rows["odd"]["error"] == "its id is nap, but its file is odd.yaml"
    assert rows["loop"]["args"]["required"] == ["prompt"]
