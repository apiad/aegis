import pytest
from aegis.config import (
    Agent, Permission, Effort, INIT_TEMPLATE,
    load_config, write_init_scaffold, ConfigError,
)


def test_agent_constructs_with_enums():
    a = Agent(harness="claude-code", model="opus",
              effort="high", permission="auto")
    assert a.permission is Permission.auto
    assert a.effort is Effort.high
    assert a.harness == "claude-code"
    assert a.model == "opus"


def test_init_template_parses_to_default_agent(tmp_path):
    f = tmp_path / ".aegis.py"
    f.write_text(INIT_TEMPLATE)
    agents, default = load_config(search_paths=[f])
    assert default == "default"
    assert agents["default"].model == "opus"
    assert agents["default"].permission is Permission.auto


def test_load_config_missing_everywhere_points_to_init(tmp_path):
    with pytest.raises(ConfigError, match="aegis init"):
        load_config(search_paths=[tmp_path / "nope.py",
                                  tmp_path / "also-nope.py"])


def test_load_config_cwd_shadows_home(tmp_path):
    cwd = tmp_path / ".aegis.py"
    home = tmp_path / "home.py"
    cwd.write_text('from aegis import Agent\n'
                    'agents={"default":Agent(harness="claude-code",'
                    'model="sonnet",effort="low",permission="read")}\n'
                    'default_agent="default"\n')
    home.write_text('from aegis import Agent\n'
                     'agents={"default":Agent(harness="claude-code",'
                     'model="opus",effort="max",permission="full")}\n'
                     'default_agent="default"\n')
    agents, _ = load_config(search_paths=[cwd, home])
    assert agents["default"].model == "sonnet"


def test_load_config_default_agent_not_a_key(tmp_path):
    f = tmp_path / ".aegis.py"
    f.write_text('from aegis import Agent\n'
                  'agents={"default":Agent(harness="claude-code",'
                  'model="opus",effort="high",permission="auto")}\n'
                  'default_agent="missing"\n')
    with pytest.raises(ConfigError, match="default_agent"):
        load_config(search_paths=[f])


def test_load_config_bad_permission_names_field(tmp_path):
    f = tmp_path / ".aegis.py"
    f.write_text('from aegis import Agent\n'
                  'agents={"default":Agent(harness="claude-code",'
                  'model="opus",effort="high",permission="banana")}\n'
                  'default_agent="default"\n')
    with pytest.raises(ConfigError, match="permission"):
        load_config(search_paths=[f])


def test_write_init_scaffold_refuses_overwrite(tmp_path):
    f = tmp_path / ".aegis.py"
    f.write_text("# existing\n")
    with pytest.raises(ConfigError, match="exists"):
        write_init_scaffold(f)


def test_write_init_scaffold_writes_template(tmp_path):
    f = tmp_path / ".aegis.py"
    write_init_scaffold(f)
    assert f.read_text() == INIT_TEMPLATE
