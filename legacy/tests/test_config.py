import pytest
from aegis.config import (
    Agent, Permission, Effort,
    load_config, ConfigError,
    find_project_root,
)


_MIN_AGENT = (
    "default_agent: default\n"
    "agents:\n"
    "  default:\n"
    "    provider: claude-code\n"
    "    model: opus\n"
    "    effort: high\n"
    "    permission: auto\n"
)


def test_agent_constructs_with_enums():
    a = Agent(harness="claude-code", model="opus",
              effort="high", permission="auto")
    assert a.permission is Permission.auto
    assert a.effort is Effort.high
    assert a.harness == "claude-code"
    assert a.model == "opus"


def test_load_config_missing_everywhere_points_to_init(tmp_path):
    # No .aegis.yaml anywhere → load_config raises pointing at `aegis init`.
    import os
    os.chdir(tmp_path)
    with pytest.raises(ConfigError, match="aegis init"):
        load_config()


def test_load_config_with_explicit_root(tmp_path):
    (tmp_path / ".aegis.yaml").write_text(_MIN_AGENT)
    agents, default = load_config(root=tmp_path)
    assert default == "default"
    assert agents["default"].model == "opus"


def test_load_config_default_agent_not_a_key(tmp_path):
    (tmp_path / ".aegis.yaml").write_text(
        "default_agent: missing\n"
        "agents:\n"
        "  default:\n"
        "    provider: claude-code\n"
        "    model: opus\n"
        "    effort: high\n"
        "    permission: auto\n"
    )
    with pytest.raises(ConfigError, match="default_agent"):
        load_config(root=tmp_path)


def test_load_config_bad_permission_value(tmp_path):
    (tmp_path / ".aegis.yaml").write_text(
        "default_agent: default\n"
        "agents:\n"
        "  default:\n"
        "    provider: claude-code\n"
        "    model: opus\n"
        "    effort: high\n"
        "    permission: banana\n"
    )
    with pytest.raises(Exception):
        load_config(root=tmp_path)


def test_find_project_root_in_cwd(tmp_path):
    (tmp_path / ".aegis.yaml").write_text("agents: {}\n")
    assert find_project_root(tmp_path) == tmp_path


def test_find_project_root_in_ancestor(tmp_path):
    (tmp_path / ".aegis.yaml").write_text("agents: {}\n")
    deep = tmp_path / "a" / "b"
    deep.mkdir(parents=True)
    assert find_project_root(deep) == tmp_path


def test_find_project_root_closest_wins(tmp_path):
    (tmp_path / ".aegis.yaml").write_text("agents: {}\n")
    inner = tmp_path / "inner"
    sub = inner / "sub"
    sub.mkdir(parents=True)
    (inner / ".aegis.yaml").write_text("agents: {}\n")
    assert find_project_root(sub) == inner


def test_find_project_root_none(tmp_path):
    assert find_project_root(tmp_path) is None

