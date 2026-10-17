from pathlib import Path

import pytest
from ruamel.yaml import YAML

from aegis.config import (
    AgentDoc,
    ConfigDoc,
    QueueDoc,
    doc_from,
    stamp_of,
    validate,
    write,
)
from aegis.ops import OpError

# The Workspace's own .aegis.yaml as of 2026-10-08: comments, the string
# `provider:` form, a pinned model.
WORKSPACE = """\
agents:
  opus:
    provider: claude-code
    model: opus
    effort: high
    permission: full
  # Cheap read-only profile for small one-shot sessions. Pinned rather than
  # the `haiku` alias so its cost does not drift with the CLI.
  haiku:
    provider: claude-code
    model: claude-haiku-4-5-20251001
    effort: low
    permission: read
  # Background research and bulk work on Alex's OpenCode Go allowance.
  deepseek:
    provider: opencode
    model: opencode-go/deepseek-v4-pro
    effort: high
    permission: full
default_agent: opus
queues:
  general:
    agent: opus
    max_parallel: 5
  deepseek:
    agent: deepseek
    max_parallel: 6
"""


def setup(tmp_path: Path, text: str) -> tuple[Path, ConfigDoc]:
    p = tmp_path / ".aegis.yaml"
    p.write_text(text)
    return p, doc_from(YAML(typ="safe").load(text))


def safe(p: Path) -> dict:
    return YAML(typ="safe").load(p.read_text())


def test_comments_forms_and_untouched_agents_survive_an_edit(tmp_path):
    p, doc = setup(tmp_path, WORKSPACE)
    doc.agents[0].effort = "max"
    assert write(p, doc, stamp_of(p)) == []
    text = p.read_text()
    assert "# Cheap read-only profile" in text and "# Background research" in text
    assert "harness:" not in text, "the provider: form is kept"
    data = safe(p)
    assert data["agents"]["opus"] == {
        "provider": "claude-code",
        "model": "opus",
        "effort": "max",
        "permission": "full",
    }
    assert data["agents"]["haiku"]["model"] == "claude-haiku-4-5-20251001"
    assert text.replace("effort: max", "effort: high", 1) == WORKSPACE


def test_the_nested_provider_form_is_edited_where_its_fields_are(tmp_path):
    p, doc = setup(
        tmp_path,
        "agents:\n  n:\n    provider: {name: claude-code, model: haiku, effort: low, permission: read}\n",
    )
    doc.agents[0].model = "opus"
    assert write(p, doc, stamp_of(p)) == []
    assert safe(p)["agents"]["n"] == {
        "provider": {
            "name": "claude-code",
            "model": "opus",
            "effort": "low",
            "permission": "read",
        }
    }


def test_unknown_keys_are_kept(tmp_path):
    p, doc = setup(tmp_path, "scheduler: {tick_seconds: 5}\n" + WORKSPACE)
    assert write(p, doc, stamp_of(p)) == []
    assert safe(p)["scheduler"] == {"tick_seconds": 5}


def test_added_removed_and_emptied_sections(tmp_path):
    p, doc = setup(tmp_path, WORKSPACE)
    doc.agents = [a for a in doc.agents if a.name != "haiku"]
    doc.agents.append(
        AgentDoc(
            name="new",
            harness="claude-code",
            model="sonnet",
            effort="low",
            permission="read",
        )
    )
    doc.queues = []
    assert write(p, doc, stamp_of(p)) == []
    data = safe(p)
    assert list(data["agents"]) == ["opus", "deepseek", "new"]
    assert data["agents"]["new"] == {
        "harness": "claude-code",
        "model": "sonnet",
        "effort": "low",
        "permission": "read",
    }
    assert "queues" not in data


def test_a_multiline_priming_is_a_literal_block(tmp_path):
    p, doc = setup(tmp_path, WORKSPACE)
    doc.agents[0].priming = "You review.\nRank by severity.\n"
    assert write(p, doc, stamp_of(p)) == []
    assert "priming: |" in p.read_text()
    assert safe(p)["agents"]["opus"]["priming"] == "You review.\nRank by severity.\n"


def test_a_stale_stamp_is_refused_and_nothing_is_written(tmp_path):
    p, doc = setup(tmp_path, WORKSPACE)
    old = stamp_of(p)
    p.write_text(WORKSPACE + "# edited in an editor\n")
    before = p.read_bytes()
    with pytest.raises(OpError) as e:
        write(p, doc, old)
    assert e.value.code == "stale"
    assert p.read_bytes() == before


def test_expecting_no_file_when_one_exists_is_stale(tmp_path):
    p, doc = setup(tmp_path, WORKSPACE)
    with pytest.raises(OpError) as e:
        write(p, doc, None)
    assert e.value.code == "stale"


def test_a_write_over_a_broken_file_is_refused(tmp_path):
    p = tmp_path / ".aegis.yaml"
    p.write_text("agents: [1, 2\n")
    before = p.read_bytes()
    with pytest.raises(OpError) as e:
        write(p, ConfigDoc(), stamp_of(p))
    assert e.value.code == "bad_config"
    assert p.read_bytes() == before


def test_an_invalid_document_writes_nothing_and_names_each_row(tmp_path):
    p, doc = setup(tmp_path, WORKSPACE)
    doc.agents[0].effort = "huge"
    doc.queues.append(QueueDoc(name="orphan", agent="nobody", max_parallel=0))
    doc.default_agent = "ghost"
    before = p.read_bytes()
    problems = write(p, doc, stamp_of(p))
    assert p.read_bytes() == before
    assert {f.row for f in problems} == {
        "agents.opus",
        "queues.orphan",
        "default_agent",
    }
    assert all(f.level == "error" for f in problems)


def test_validate_refuses_duplicates_and_blank_names(tmp_path):
    same = AgentDoc(
        name="a", harness="claude-code", model="opus", effort="high", permission="full"
    )
    rows = [
        f.where
        for f in validate(
            ConfigDoc(agents=[same, same, same.model_copy(update={"name": " "})])
        )
    ]
    assert "agents.a" in rows and "agents. " in rows


def test_a_new_file_is_created_from_nothing(tmp_path):
    p = tmp_path / ".aegis.yaml"
    doc = ConfigDoc(
        agents=[
            AgentDoc(
                name="opus",
                harness="claude-code",
                model="opus",
                effort="high",
                permission="full",
            )
        ],
        default_agent="opus",
        queues=[QueueDoc(name="general", agent="opus", max_parallel=3)],
    )
    assert write(p, doc, None) == []
    assert p.read_text().startswith("# aegis configuration.")
    data = safe(p)
    assert list(data) == ["default_agent", "agents", "queues"]
    assert doc_from(data) == doc


@pytest.mark.parametrize(
    "text", ["- just\n- a list\n", "agents: !custom {a: 1}\n", "# only a comment\n"]
)
def test_a_write_over_a_file_the_server_cannot_read_is_refused(tmp_path, text):
    # The writer refuses exactly what the reader refuses (config.load): a
    # non-mapping top level, a tag the safe loader rejects, a comment-only file.
    p = tmp_path / ".aegis.yaml"
    p.write_text(text)
    with pytest.raises(OpError) as e:
        write(p, ConfigDoc(), stamp_of(p))
    assert e.value.code == "bad_config"
    assert p.read_text() == text


POOL = WORKSPACE + "# the pool\nworkflows:\n  max_agents: {total: 6, opus: 2}\n"


def test_the_workflow_pool_reads_writes_and_clears(tmp_path):
    p, doc = setup(tmp_path, POOL)
    assert doc.max_agents == {"total": 6, "opus": 2}
    doc.max_agents = {"total": 8, "opus": 2, "haiku": 1}
    assert write(p, doc, stamp_of(p)) == []
    assert safe(p)["workflows"] == {"max_agents": {"total": 8, "opus": 2, "haiku": 1}}
    assert "# the pool" in p.read_text()
    doc.max_agents = {}
    assert write(p, doc, stamp_of(p)) == []
    assert "workflows" not in safe(p)


def test_an_untouched_pool_is_written_as_it_was(tmp_path):
    p, doc = setup(tmp_path, WORKSPACE + "workflows: {max_agents: 6}\n")
    assert doc.max_agents == {"total": 6}
    assert write(p, doc, stamp_of(p)) == []
    assert "workflows: {max_agents: 6}" in p.read_text()


def test_the_pool_names_agents_and_stays_under_its_total(tmp_path):
    _, doc = setup(tmp_path, WORKSPACE)
    doc.max_agents = {"total": 2, "gpt": 1, "opus": 3}
    assert {f.row: f.message for f in validate(doc)} == {
        "workflows.gpt": "no agent named 'gpt'",
        "workflows.opus": "opus 3 is above total 2",
    }


def test_a_broken_pool_is_kept_through_a_write(tmp_path):
    p, doc = setup(tmp_path, WORKSPACE + "workflows: {max_agents: lots}\n")
    assert doc.max_agents == {}
    doc.default_agent = "haiku"
    assert write(p, doc, stamp_of(p)) == []
    assert "max_agents: lots" in p.read_text()


def test_harness_versions_round_trip_keeps_comments(tmp_path):
    text = WORKSPACE + "harness_versions:\n  person: warn  # we track betas\n"
    p, doc = setup(tmp_path, text)
    assert doc.harness_versions.person == "warn"
    doc.harness_versions.accepted = {"codex": ["0.170.0"]}
    assert write(p, doc, stamp_of(p)) == []
    out = p.read_text()
    assert "# we track betas" in out and "# Background research" in out
    assert safe(p)["harness_versions"] == {
        "person": "warn",
        "accepted": {"codex": ["0.170.0"]},
    }
    doc = doc_from(safe(p))
    doc.harness_versions.person = "ask"
    doc.harness_versions.accepted = {}
    assert write(p, doc, stamp_of(p)) == []
    assert "harness_versions" not in safe(p), "every field at its default"


def test_harness_versions_agent_ask_is_a_finding(tmp_path):
    _, doc = setup(tmp_path, WORKSPACE)
    doc.harness_versions.agent = "ask"
    (f,) = validate(doc)
    assert f.where == "harness_versions" and "nobody is there to answer" in f.message


def test_accept_version_twice_leaves_one_entry(tmp_path):
    from aegis.config import accept_version

    p, _ = setup(tmp_path, WORKSPACE)
    accept_version(p, "claude-code", "2.1.301")
    accept_version(p, "claude-code", "2.1.301")
    assert safe(p)["harness_versions"] == {"accepted": {"claude-code": ["2.1.301"]}}
    assert "# Background research" in p.read_text()


def test_accept_version_on_an_unparseable_file_raises(tmp_path):
    from aegis.config import accept_version

    p = tmp_path / ".aegis.yaml"
    p.write_text("agents: [1, 2\n")
    with pytest.raises(OpError) as e:
        accept_version(p, "claude-code", "2.1.301")
    assert e.value.code == "bad_config"
