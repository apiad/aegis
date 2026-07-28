from aegis.config.harnesses import (
    HarnessRegistration,
    IMPLICIT_HARNESSES,
    merge_harnesses,
)


def test_implicit_covers_four_drivers():
    assert set(IMPLICIT_HARNESSES) == {
        "claude-code", "gemini", "opencode", "lovelaice"}
    assert IMPLICIT_HARNESSES["opencode"].driver == "opencode"


def test_explicit_overrides_implicit():
    reg = HarnessRegistration(name="opencode", driver="opencode",
                              default_model="opencode/mimo-v2.5-free")
    merged = merge_harnesses({"opencode": reg})
    assert merged["opencode"].default_model == "opencode/mimo-v2.5-free"
    # implicit ones still present
    assert merged["claude-code"].driver == "claude-code"


def test_second_endpoint_same_driver():
    ovr = HarnessRegistration(name="openrouter", driver="lovelaice",
                              base_url="https://openrouter.ai/api/v1")
    merged = merge_harnesses({"openrouter": ovr})
    assert merged["openrouter"].driver == "lovelaice"
    assert merged["lovelaice"].driver == "lovelaice"  # implicit still there


import pytest
from aegis.config import ConfigError
from aegis.config.harnesses import resolve_agent_entry


_HN = merge_harnesses({
    "openrouter": HarnessRegistration(
        name="openrouter", driver="lovelaice",
        base_url="https://openrouter.ai/api/v1",
        api_key_file="~/key.txt", default_model="qwen/qwen3-32b"),
})


def test_harness_ref_resolves_to_driver_and_creds():
    a = resolve_agent_entry(
        {"harness": "openrouter", "model": "qwen/qwen3-32b"}, _HN)
    assert a.harness == "lovelaice"            # driver string for get_driver
    assert a.provider.base_url == "https://openrouter.ai/api/v1"
    assert a.provider.api_key_file == "~/key.txt"


def test_harness_ref_default_model_fallback():
    a = resolve_agent_entry({"harness": "openrouter"}, _HN)
    assert a.model == "qwen/qwen3-32b"


def test_legacy_provider_shape_still_resolves():
    a = resolve_agent_entry(
        {"provider": "claude-code", "model": "opus", "effort": "high"}, _HN)
    assert a.harness == "claude-code"
    assert a.effort.value == "high"


def test_prompt_field_captured():
    a = resolve_agent_entry(
        {"harness": "claude-code", "model": "opus",
         "prompt": ".aegis/personas/reviewer.md"}, _HN)
    assert a.prompt == ".aegis/personas/reviewer.md"


def test_unknown_harness_fails_loud():
    with pytest.raises(ConfigError):
        resolve_agent_entry({"harness": "nope", "model": "x"}, _HN)
