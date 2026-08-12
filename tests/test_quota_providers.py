from aegis.usage.quota import QuotaProvider, QuotaService
from aegis.usage.quota_providers import PROVIDERS, build_services, for_harness


def test_both_providers_are_registered():
    assert [p.name for p in PROVIDERS] == ["claude", "opencode-go"]


def test_every_provider_is_a_descriptor():
    assert all(isinstance(p, QuotaProvider) for p in PROVIDERS)


def test_claude_is_routed_from_its_harness():
    assert for_harness("claude-code").name == "claude"


def test_opencode_is_routed_from_its_harness():
    assert for_harness("opencode").name == "opencode-go"


def test_a_harness_without_quota_routes_nowhere():
    assert for_harness("gemini") is None
    assert for_harness("lovelaice") is None
    assert for_harness("") is None


def test_claude_declares_the_windows_the_bar_showed_before():
    assert for_harness("claude-code").bar_windows == (
        ("session", "5h"), ("weekly_all", "wk"))


def test_opencode_declares_all_three_of_its_windows():
    assert for_harness("opencode").bar_windows == (
        ("rolling", "5h"), ("weekly", "wk"), ("monthly", "mo"))


def test_labels_are_short_and_distinct():
    labels = [p.label for p in PROVIDERS]
    assert labels == ["cc", "oc"]


def test_build_services_makes_one_service_per_provider():
    services = build_services()
    assert set(services) == {p.name for p in PROVIDERS}
    assert all(isinstance(s, QuotaService) for s in services.values())


def test_built_services_are_wired_to_their_own_provider():
    services = build_services()
    # The claude service must read claude credentials, not opencode's.
    from aegis.usage.quota_claude import read_token
    assert services["claude"]._read_token is read_token
