"""Saved workflows: the built-ins and the project's, their versions and
their history (spec §08)."""

import json

import pytest

from aegis.ops import OpError
from aegis.workflow_lang import parse
from aegis.workflow_store import BUILTIN, Store, check_named, sha_of

from .conftest import until
from .test_agents import CONFIG, World, mcp, turn
from .test_workflow_ops import rejected
from .test_workflows_e2e import ok

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


READER = (
    "  reader: {harness: claude-code, model: opus, effort: high, permission: read}\n"
)


@pytest.fixture
async def world(tmp_path, fake_claude):
    (tmp_path / ".aegis.yaml").write_text(CONFIG.replace("queues:", READER + "queues:"))
    w = await World(tmp_path, fake_claude).start()
    yield w
    await w.stop()


async def test_an_agent_saves_lists_arms_and_deletes(world):
    main = await world.spawn()
    saved = ok(await turn(main, mcp("workflow_save", definition=NAP)))
    assert saved["id"] == "nap" and saved["ids_written"] == 1
    rows = {r["id"]: r for r in ok(await turn(main, mcp("workflow_list")))}
    assert {"nap", "loop"} <= set(rows) and rows["nap"]["saved_by"][
        "log_id"
    ] == main.log_id
    run = ok(await turn(main, mcp("workflow_arm", id="nap", args={"secs": "1s"})))
    st = world.app.workflows.runs[run["run"]]
    assert run["status"] == "armed"  # saved, not launched: a person approves it
    assert st.saved == {"id": "nap", "sha": saved["sha"], "source": "project"}
    await world.app.registry.call("workflow.reject", {"run": run["run"]})
    # The rejection wakes the session; its answer must not read as the next.
    await until(lambda: rejected(main) and main.status == "idle", timeout=10)
    assert ok(await turn(main, mcp("workflow_delete", id="nap"))) == {"deleted": "nap"}


async def test_a_read_session_lists_but_cannot_save(world):
    r = await world.app.registry.call("session.spawn", {"agent": "reader"})
    reader = world.app.sessions.sessions[r["log_id"]]
    assert (await turn(reader, mcp("workflow_list"))).startswith("mcp ok")
    said = await turn(reader, mcp("workflow_save", definition=NAP))
    assert said.startswith("mcp error") and "read permission" in said


async def test_arm_takes_one_of_definition_id_or_run(world):
    main = await world.spawn()
    said = await turn(main, mcp("workflow_arm", id="nap", definition=NAP))
    assert said.startswith("mcp error") and "one of definition, id" in said


async def test_arming_a_file_whose_id_is_not_its_name_is_refused(world, tmp_path):
    main = await world.spawn()
    d = tmp_path / ".aegis" / "workflows"
    d.mkdir(parents=True)
    (d / "odd.yaml").write_text(NAP)  # its id is nap
    said = await turn(main, mcp("workflow_arm", id="odd", args={"secs": "1s"}))
    assert said.startswith("mcp error") and "its file is odd.yaml" in said
    assert world.app.workflows.of_owner(main.log_id) is None


def test_a_file_that_is_not_text_is_an_error_row_and_the_rest_still_list(store):
    store.save(NAP, AGENTS, BY)
    (store.dir / "bad.yaml").write_bytes(b"\xff\xfe\x00bad")
    rows = {r["id"]: r for r in store.listing(AGENTS)}
    assert "UTF-8" in rows["bad"]["error"] and "title" in rows["nap"]
    with pytest.raises(OpError) as e:
        check_named(store.get("bad"), AGENTS)
    assert e.value.code == "lint"


def test_a_folder_named_like_a_workflow_is_skipped(store):
    store.save(NAP, AGENTS, BY)
    (store.dir / "dir.yaml").mkdir()
    assert [r["id"] for r in store.listing(AGENTS)] == ["loop", "nap"]


def test_a_torn_history_line_is_skipped(store):
    saved = store.save(NAP, AGENTS, BY)
    with store.history.open("a") as f:
        f.write('{"t": 1, "id": "nap", "ev": "sa')
    (row,) = [r for r in store.listing(AGENTS) if r["id"] == "nap"]
    assert row["saved_by"] == BY
    assert store.last_save("nap")["sha"] == saved.sha


def test_listing_reads_the_folders_and_the_history_once(store, monkeypatch):
    for i in range(5):
        store.save(NAP.replace("id: nap", f"id: nap{i}"), AGENTS, BY)
    calls = {"all": 0, "lines": 0}
    real_all, real_lines = store.all, store._lines

    def all_():
        calls["all"] += 1
        return real_all()

    def lines():
        calls["lines"] += 1
        return real_lines()

    monkeypatch.setattr(store, "all", all_)
    monkeypatch.setattr(store, "_lines", lines)
    rows = store.listing(AGENTS)
    assert len(rows) == 6 and all("saved_by" in r for r in rows if r["id"] != "loop")
    assert calls == {"all": 1, "lines": 1}


def test_a_save_keeps_the_text_only_of_pinned_and_current_versions(store):
    v1 = store.save(NAP, AGENTS, BY).sha
    store.pinned = lambda: {("nap", v1)}
    v2 = store.save(NAP.replace("Nap a while.", "Nap two."), AGENTS, BY).sha
    v3 = store.save(NAP.replace("Nap a while.", "Nap three."), AGENTS, BY).sha
    assert store.text_of("nap", v1) is not None  # a schedule pins it
    assert store.text_of("nap", v2) is None  # nothing refers to it any more
    assert store.text_of("nap", v3) is not None  # the file
    texts = [r for r in store._lines() if "text" in r]
    assert {r["sha"] for r in texts} == {v1, v3}
    # The history still says who saved each version.
    assert [r["sha"] for r in store._lines() if r["ev"] == "saved"] == [v1, v2, v3]


def test_a_file_whose_args_are_the_wrong_shape_is_an_error_row(store, tmp_path):
    folder = tmp_path / ".aegis" / "workflows"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "nap.yaml").write_text(
        NAP.replace("properties: {secs: {type: string}}", "properties: [secs]")
    )
    rows = {r["id"]: r for r in store.listing(AGENTS)}
    assert rows["nap"]["error"] == (
        "$.args.properties: properties maps a name to a schema"
    )
    assert "loop" in rows


def test_a_file_whose_arg_type_is_a_list_is_an_error_row(store, tmp_path):
    folder = tmp_path / ".aegis" / "workflows"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "nap.yaml").write_text(
        NAP.replace("{secs: {type: string}}", "{secs: {type: [string, integer]}}")
    )
    rows = {r["id"]: r for r in store.listing(AGENTS)}
    assert "loop" in rows and "type names one type" in rows["nap"]["error"]
