import pytest
from aegis.config import (
    Agent, Permission, Effort,
    load_config, ConfigError,
    find_project_root, load_telegram_config,
)

DEFAULT_PROMPT = (
    "You are replying via telegram, keep response compact and "
    "focused if possible, only resort to long responses if it really matters")


def test_agent_constructs_with_enums():
    a = Agent(harness="claude-code", model="opus",
              effort="high", permission="auto")
    assert a.permission is Permission.auto
    assert a.effort is Effort.high
    assert a.harness == "claude-code"
    assert a.model == "opus"


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


def test_find_project_root_in_cwd(tmp_path):
    (tmp_path / ".aegis.py").write_text("agents={}\n")
    assert find_project_root(tmp_path) == tmp_path


def test_find_project_root_in_ancestor(tmp_path):
    (tmp_path / ".aegis.py").write_text("x=1\n")
    deep = tmp_path / "a" / "b"
    deep.mkdir(parents=True)
    assert find_project_root(deep) == tmp_path


def test_find_project_root_closest_wins(tmp_path):
    (tmp_path / ".aegis.py").write_text("x=1\n")
    inner = tmp_path / "inner"
    sub = inner / "sub"
    sub.mkdir(parents=True)
    (inner / ".aegis.py").write_text("x=1\n")
    assert find_project_root(sub) == inner


def test_find_project_root_none(tmp_path):
    assert find_project_root(tmp_path) is None


def test_telegram_config_defaults(tmp_path, monkeypatch):
    p = tmp_path / ".aegis.py"
    p.write_text("telegram_chat_id=5\n")
    monkeypatch.delenv("AEGIS_TELEGRAM_TOKEN", raising=False)
    cfg = load_telegram_config(p)
    assert cfg.chat_id == 5 and cfg.token is None
    assert cfg.auto_prompt == DEFAULT_PROMPT


def test_env_token_wins(tmp_path, monkeypatch):
    p = tmp_path / ".aegis.py"
    p.write_text("telegram_token='infile'\n")
    monkeypatch.setenv("AEGIS_TELEGRAM_TOKEN", "fromenv")
    assert load_telegram_config(p).token == "fromenv"


def test_empty_auto_prompt_disables(tmp_path):
    p = tmp_path / ".aegis.py"
    p.write_text("auto_add_to_telegram_prompt=''\n")
    assert load_telegram_config(p).auto_prompt == ""
