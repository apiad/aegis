from aegis.claude.control import Model
from aegis.codex.config import (
    PERMISSIONS,
    SANDBOX_MODE,
    SANDBOX_POLICY,
    TOKEN_ENV,
    argv,
    catalog_from,
    child_env,
    provider_model,
    split_model,
)


def test_a_model_splits_on_its_first_slash():
    assert split_model("openrouter/nvidia/nemotron:free") == (
        "openrouter",
        "nvidia/nemotron:free",
    )
    assert split_model("openai/gpt-5.5") == ("openai", "gpt-5.5")
    assert split_model("") == ("", "")


def test_argv_overrides_come_after_the_subcommand():
    a = argv("codex", "http://127.0.0.1:9/mcp")
    assert a[:2] == ["codex", "app-server"]
    assert a[2:6] == ["--disable", "plugins", "--disable", "remote_plugin"]
    joined = " ".join(a)
    assert 'mcp_servers.aegis.url="http://127.0.0.1:9/mcp"' in joined
    assert (
        f'mcp_servers.aegis.env_http_headers={{"X-Aegis-Session"="{TOKEN_ENV}"}}'
        in joined
    )
    assert 'mcp_servers.aegis.default_tools_approval_mode="approve"' in joined
    assert f'shell_environment_policy.exclude=["{TOKEN_ENV}"]' in joined
    assert a[-2:] == ["-c", 'approval_policy="never"']


def test_no_token_ever_rides_on_argv_and_no_mcp_means_no_aegis_flags():
    assert not any("tok-" in x for x in argv("codex", "http://h/mcp"))
    assert not any("mcp_servers" in x for x in argv("codex", None))


def test_the_token_is_only_in_the_environment_and_only_with_mcp():
    assert child_env({"PATH": "/bin", TOKEN_ENV: "stale"}, ("http://h/mcp", "tok-1")) == {
        "PATH": "/bin", TOKEN_ENV: "tok-1",
    }  # fmt: skip
    assert child_env({"PATH": "/bin", TOKEN_ENV: "stale"}, None) == {"PATH": "/bin"}


def test_each_permission_allows_strictly_more_than_the_one_before():
    assert list(SANDBOX_MODE) == list(PERMISSIONS) == ["read", "write", "auto", "full"]
    assert SANDBOX_POLICY["read"] == {"type": "readOnly", "networkAccess": False}
    assert SANDBOX_POLICY["write"] == {"type": "workspaceWrite", "networkAccess": False}
    assert SANDBOX_POLICY["auto"] == {"type": "workspaceWrite", "networkAccess": True}
    assert SANDBOX_POLICY["full"] == {"type": "dangerFullAccess"}
    assert SANDBOX_MODE == {"read": "read-only", "write": "workspace-write",
                            "auto": "workspace-write", "full": "danger-full-access"}  # fmt: skip


def test_the_catalog_has_codex_models_skills_the_provider_and_the_current_model():
    listed = [
        {"id": "gpt-5.5", "displayName": "GPT-5.5", "hidden": False,
         "supportedReasoningEfforts": [{"reasoningEffort": "low"}, {"reasoningEffort": "high"}]},
        {"id": "secret", "displayName": "x", "hidden": True, "supportedReasoningEfforts": []},
    ]  # fmt: skip
    skills = [
        {
            "name": "imagegen",
            "description": "Make images. Long text.",
            "path": "/s/imagegen",
        }
    ]
    nemo = provider_model("openrouter", {"id": "nvidia/n:free", "name": "Nemotron", "context_length": 262144,
                                         "pricing": {"prompt": "0", "completion": "0"},
                                         "supported_parameters": ["tools"]})  # fmt: skip
    cat = catalog_from(listed, skills, [nemo], "openrouter/other:free")
    values = [m.value for m in cat.models]
    assert values == [
        "openai/gpt-5.5",
        "openrouter/nvidia/n:free",
        "openrouter/other:free",
    ]
    assert cat.model("openai/gpt-5.5").efforts == ("low", "high")
    assert cat.model("openrouter/nvidia/n:free").window == 262144
    assert cat.model("openrouter/nvidia/n:free").free is True
    assert [c["name"] for c in cat.commands] == ["compact", "review", "imagegen"]
    assert cat.commands[2] == {
        "name": "imagegen",
        "hint": "",
        "doc": "Make images.",
        "source": "skill",
    }


def test_a_provider_entry_without_an_id_is_skipped():
    assert provider_model("p", {"name": "x"}) is None
    assert isinstance(provider_model("p", {"id": "m"}), Model)
