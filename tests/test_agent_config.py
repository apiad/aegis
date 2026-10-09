from pathlib import Path

import pytest

from aegis.agents import ConfigError, load_agents, model_suggestions, resolve
from aegis.ops import OpError
from aegis.queues import load_queues


def write(tmp_path: Path, text: str) -> Path:
    (tmp_path / ".aegis.yaml").write_text(text)
    return tmp_path


def by_name(root: Path) -> dict:
    return {a.name: a for a in load_agents(root)}


def test_the_three_harness_forms_parse(tmp_path):
    agents = by_name(
        write(
            tmp_path,
            "agents:\n"
            "  flat: {harness: claude-code, model: opus, effort: high, permission: full}\n"
            "  str: {provider: claude-code, model: opus, effort: high, permission: full}\n"
            "  nested:\n"
            "    provider: {name: claude-code, model: haiku, effort: low, permission: read}\n",
        )
    )
    assert all(a.enabled and a.error is None for a in agents.values())
    n = agents["nested"]
    assert (n.harness, n.model, n.effort, n.permission) == (
        "claude-code",
        "haiku",
        "low",
        "read",
    )


def test_a_missing_field_disables_only_that_agent(tmp_path):
    agents = by_name(
        write(
            tmp_path,
            "agents:\n"
            "  ok: {harness: claude-code, model: opus, effort: high, permission: full}\n"
            "  noeffort: {harness: claude-code, model: opus, permission: full}\n"
            "  bare: {}\n",
        )
    )
    assert agents["ok"].enabled
    assert agents["noeffort"].error == "effort is missing"
    assert not agents["noeffort"].enabled
    assert agents["bare"].error == "harness, model, effort, permission are missing"


def test_an_empty_string_counts_as_missing(tmp_path):
    (a,) = load_agents(
        write(
            tmp_path,
            'agents:\n  x: {harness: claude-code, model: "", effort: high, permission: full}\n',
        )
    )
    assert a.error == "model is missing"


def test_a_value_outside_the_vocabulary_is_an_error(tmp_path):
    (a,) = load_agents(
        write(
            tmp_path,
            "agents:\n  x: {harness: claude-code, model: opus, effort: huge, permission: full}\n",
        )
    )
    assert a.error == "effort 'huge' is not one of low, medium, high, xhigh, max"


def test_an_unsupported_harness_is_disabled_but_not_an_error(tmp_path):
    (a,) = load_agents(
        write(
            tmp_path,
            "agents:\n  d: {provider: lovelaice, model: x, effort: high, permission: full}\n",
        )
    )
    assert a.error is None and not a.enabled


def test_priming_is_optional_and_never_listed(tmp_path):
    agents = by_name(
        write(
            tmp_path,
            "agents:\n"
            "  plain: {harness: claude-code, model: opus, effort: high, permission: full}\n"
            "  rev:\n"
            "    harness: claude-code\n    model: opus\n    effort: max\n    permission: read\n"
            "    priming: |\n      You review.\n      Rank by severity.\n",
        )
    )
    assert agents["plain"].priming is None
    assert agents["rev"].priming == "You review.\nRank by severity.\n"
    d = agents["rev"].as_dict()
    assert d["has_priming"] is True and "priming" not in d


def test_no_config_means_no_agents(tmp_path):
    assert load_agents(tmp_path) == []


def test_malformed_config_names_the_file(tmp_path):
    with pytest.raises(ConfigError, match=r"\.aegis\.yaml"):
        load_agents(write(tmp_path, "agents: [1, 2\n"))


def test_model_suggestions_are_the_models_agents_name_without_duplicates(tmp_path):
    agents = load_agents(
        write(
            tmp_path,
            "agents:\n"
            "  a: {harness: claude-code, model: opus, effort: high, permission: full}\n"
            "  b: {harness: claude-code, model: claude-sonnet-5, effort: high, permission: full}\n"
            "  c: {harness: opencode, model: opencode-go/x, effort: high, permission: full}\n"
            "  d: {harness: claude-code, model: opus, effort: low, permission: read}\n",
        )
    )
    assert model_suggestions(agents) == {
        "claude-code": ["opus", "claude-sonnet-5"],
        "opencode": ["opencode-go/x"],
        "codex": [],
    }


@pytest.fixture
def agents(tmp_path):
    return load_agents(
        write(
            tmp_path,
            "agents:\n"
            "  opus: {harness: claude-code, model: opus, effort: high, permission: full}\n"
            "  rev: {harness: claude-code, model: opus, effort: max, permission: read, priming: You review.}\n"
            "  broken: {harness: claude-code, model: opus, permission: full}\n"
            "  deep: {harness: opencode, model: opencode-go/fake-pro, effort: high, permission: full}\n"
            "  lovelaice-agent: {harness: lovelaice, model: m, effort: high, permission: full}\n",
        )
    )


def none() -> dict:
    return {"harness": None, "model": None, "effort": None, "permission": None}


def test_resolve_takes_the_agents_fields_and_its_priming(agents, tmp_path):
    spec = resolve(agents, None, "rev", none(), tmp_path)
    assert (spec.agent, spec.model, spec.effort, spec.permission) == (
        "rev",
        "opus",
        "max",
        "read",
    )
    assert (spec.harness, spec.priming, spec.overridden) == (
        "claude-code",
        "You review.",
        (),
    )


def test_an_override_replaces_a_field_and_is_recorded(agents, tmp_path):
    spec = resolve(
        agents,
        None,
        "opus",
        none() | {"model": "sonnet", "effort": "high"},
        tmp_path,
        spawned_by="p",
    )
    # effort equals the agent's value, so it is not an override.
    assert (spec.model, spec.overridden, spec.spawned_by) == ("sonnet", ("model",), "p")


def test_no_name_falls_back_to_default_agent(agents, tmp_path):
    assert resolve(agents, "rev", None, none(), tmp_path).agent == "rev"


@pytest.mark.parametrize(
    ("default", "name", "overrides", "code"),
    [
        (None, None, {}, "no_agent"),
        (None, "nope", {}, "unknown_agent"),
        (None, "broken", {}, "bad_agent"),
        ("broken", None, {}, "bad_agent"),
        (None, "lovelaice-agent", {}, "harness_unsupported"),
        (None, "opus", {"harness": "opencode"}, "bad_model"),
    ],
)
def test_resolve_errors(agents, tmp_path, default, name, overrides, code):
    with pytest.raises(OpError) as e:
        resolve(agents, default, name, none() | overrides, tmp_path)
    assert e.value.code == code


def test_bad_agent_says_what_is_wrong(agents, tmp_path):
    with pytest.raises(OpError, match="agent 'broken': effort is missing"):
        resolve(agents, None, "broken", none(), tmp_path)


def test_a_queue_names_its_agent_and_max_parallel(tmp_path):
    root = write(
        tmp_path,
        "queues:\n"
        "  ok: {agent: opus, max_parallel: 3}\n"
        "  nolimit: {agent: opus}\n"
        "  bare: {}\n"
        "  zero: {agent: opus, max_parallel: 0}\n",
    )
    assert load_queues(root) == {
        "ok": {"agent": "opus", "max_parallel": 3},
        "nolimit": {"error": "max_parallel is missing"},
        "bare": {"error": "agent, max_parallel are missing"},
        "zero": {"error": "max_parallel 0 is not a positive integer"},
    }


def test_an_opencode_agent_names_provider_and_model(tmp_path):
    (a,) = load_agents(
        write(
            tmp_path,
            "agents:\n  d: {provider: opencode, model: x, effort: high, permission: full}\n",
        )
    )
    assert (
        a.error
        == "an OpenCode model is provider/model, such as opencode-go/deepseek-v4-pro"
    )
    assert not a.enabled
