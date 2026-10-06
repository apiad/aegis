from pathlib import Path

import pytest

from aegis.profiles import ProfileError, load_profiles


def test_agents_map_parses(tmp_path: Path):
    (tmp_path / ".aegis.yaml").write_text(
        "agents:\n"
        "  opus: {model: opus, effort: high, permission: full}\n"
        "  deepseek: {harness: opencode, model: opencode-go/deepseek, permission: full}\n"
        "default_agent: opus\n"
    )
    by_name = {p.name: p for p in load_profiles(tmp_path)}
    opus = by_name["opus"]
    assert (opus.harness, opus.model, opus.effort, opus.permission) == (
        "claude-code",
        "opus",
        "high",
        "full",
    )
    assert opus.enabled
    assert not by_name["deepseek"].enabled


def test_defaults_fill_missing_fields(tmp_path: Path):
    (tmp_path / ".aegis.yaml").write_text("agents:\n  bare: {model: sonnet}\n")
    (p,) = load_profiles(tmp_path)
    assert (p.effort, p.permission) == ("high", "auto")


def test_no_config_means_no_profiles(tmp_path: Path):
    assert load_profiles(tmp_path) == []


def test_malformed_config_names_the_file(tmp_path: Path):
    (tmp_path / ".aegis.yaml").write_text("agents: [1, 2\n")
    with pytest.raises(ProfileError, match=r"\.aegis\.yaml"):
        load_profiles(tmp_path)


def test_nested_provider_form(tmp_path: Path):
    (tmp_path / ".aegis.yaml").write_text(
        "agents:\n  x:\n    provider: {name: claude-code, model: haiku, effort: low, permission: read}\n"
    )
    (p,) = load_profiles(tmp_path)
    assert (p.harness, p.model, p.effort, p.permission) == (
        "claude-code",
        "haiku",
        "low",
        "read",
    )


def test_provider_as_a_string_names_the_harness(tmp_path: Path):
    # The form the Workspace's own .aegis.yaml uses.
    (tmp_path / ".aegis.yaml").write_text(
        "agents:\n"
        "  opus: {provider: claude-code, model: opus, effort: high, permission: full}\n"
        "  deepseek: {provider: opencode, model: opencode-go/deepseek-v4-pro, permission: full}\n"
    )
    by_name = {p.name: p for p in load_profiles(tmp_path)}
    assert by_name["opus"].harness == "claude-code" and by_name["opus"].enabled
    assert by_name["deepseek"].harness == "opencode" and not by_name["deepseek"].enabled
