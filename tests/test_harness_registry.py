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
