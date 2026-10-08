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
