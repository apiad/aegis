import time

import pytest

from aegis.cascade import DEFAULT_COOL_S, Cooling, Fall, classify, pick
from aegis.quota_gate import Gate

# The verdict task 1 recorded in cascade.py's docstring.
SCOPE_ZEN = "model"

CASES = [
    ("opencode", "APIError: 429 FreeUsageLimitError: free usage limit reached", Fall(SCOPE_ZEN, None)),
    ("opencode", "APIError: Rate limit exceeded, retry after 30s", Fall("model", 30.0)),
    ("opencode", "APIError: 503 Service Unavailable", Fall("model", None)),
    ("opencode", "APIError: overloaded", Fall("model", None)),
    ("opencode", "ProviderModelNotFoundError: model not found", Fall("model", None)),
    ("claude-code", "Claude AI usage limit reached|1792400000", Fall("pool", None)),
    ("claude-code", "API Error: 529 Overloaded", Fall("model", None)),
    # Claude Code's words for an API 429 (the canary): the model, not the pool.
    ("claude-code", "API Error: Request rejected (429) · rate limit reached", Fall("model", None)),
    ("opencode", "aborted", None),
    ("opencode", "interrupted", None),
    ("opencode", "ContextOverflowError: prompt is too long", None),
    ("opencode", "ProviderAuthError: invalid api key", None),
    (
        "opencode",
        "provider.auth: OpenCode's free models aren't available from your current network"
        " because we've detected abusive traffic from this IP address.",
        None,
    ),
    ("opencode", "something nobody has seen", None),
    # An Anthropic API 429 names the maximum tokens; it is a rate limit.
    (
        "claude-code",
        'API Error: 429 {"type":"error","error":{"type":"rate_limit_error","message":'
        '"This request would exceed the rate limit for your organization of 30,000'
        " input tokens per minute. Please reduce the prompt length or the maximum"
        ' tokens requested, or try again later."}}',
        Fall("model", None),
    ),
    # A gateway timeout and a fetch timeout fall; they are not context or abort.
    ("opencode", "UnknownError: context deadline exceeded", Fall("model", None)),
    ("opencode", "TimeoutError: The operation was aborted due to timeout", Fall("model", None)),
    ("opencode", "APIError: model opencode/x not found", Fall("model", None)),
    # 429 inside an id, and a not-found that names no model, stay.
    ("opencode", "APIError: tool_use id toolu_01Xk4290 has no result", None),
    ("opencode", "NotFoundError: Session not found", None),
    ("opencode", "AbortError: The operation was aborted", None),
]  # fmt: skip

T = 1_800_000_000.0


def open_gate(model: str) -> Gate:
    return Gate("open")


@pytest.mark.parametrize("harness,text,want", CASES)
def test_classify(harness, text, want):
    assert classify(harness, text) == want


def test_a_codex_error_never_falls():
    assert classify("codex", "429 rate limit") is None


def test_claude_usage_limit_keeps_its_reset_epoch():
    fall = classify("claude-code", "Claude AI usage limit reached|1792400000")
    assert fall is not None and fall.until == 1792400000.0
    c = Cooling()
    assert c.cool("claude-code", "opus", fall, 1792390000.0) == 1792400000.0


def test_pick_skips_cooling_and_gated_models():
    c = Cooling()
    c.cool("opencode", "opencode/a", Fall("model", None), T)

    def gate(model: str) -> Gate:
        return Gate("closed", "no") if model == "opencode/b" else Gate("open")

    models = ["opencode/a", "opencode/b", "opencode/c"]
    assert pick(models, -1, "opencode", gate, c, T) == ("opencode/c", "")


def test_pick_after_never_wraps():
    model, why = pick(
        ["opencode/a", "opencode/b"], 1, "opencode", open_gate, Cooling(), T
    )
    assert model is None and why.startswith("every model in the cascade")


def test_a_pool_fall_cools_every_model_in_the_pool():
    c = Cooling()
    c.cool("claude-code", "opus", Fall("pool", None), T)
    assert c.until("claude-code", "sonnet", T) == T + DEFAULT_COOL_S
    assert c.until("opencode", "opencode/a", T) is None


def test_an_opencode_pool_fall_cools_its_provider_prefix():
    c = Cooling()
    c.cool("opencode", "opencode/a", Fall("pool", None), T)
    assert c.until("opencode", "opencode/b", T) == T + DEFAULT_COOL_S
    assert c.until("opencode", "opencode-go/x", T) is None


def test_retry_after_sets_the_cooling_end():
    c = Cooling()
    assert c.cool("opencode", "opencode/a", Fall("model", 30.0), T) == T + 30
    assert c.until("opencode", "opencode/a", T) == T + 30
    assert c.until("opencode", "opencode/b", T) is None


def test_cooling_expires():
    c = Cooling()
    c.cool("opencode", "opencode/a", Fall("model", 30.0), T)
    assert c.until("opencode", "opencode/a", T + 31) is None
    assert c.earliest(T + 31) is None and c.wire(T + 31) == []
    assert pick(["opencode/a"], -1, "opencode", open_gate, c, T + 31)[0] == "opencode/a"


def test_why_names_the_earliest_free_time():
    c = Cooling()
    c.cool("opencode", "opencode/a", Fall("model", 600.0), T)
    c.cool("opencode", "opencode/b", Fall("model", 60.0), T)
    model, why = pick(["opencode/a", "opencode/b"], -1, "opencode", open_gate, c, T)
    clock = time.strftime("%H:%M", time.localtime(T + 60))
    assert model is None
    assert (
        why
        == f"every model in the cascade is cooling or gated; the first is free again at {clock}"
    )
    assert c.earliest(T) == T + 60
    assert c.wire(T) == [
        {"harness": "opencode", "model": "opencode/a", "until": T + 600},
        {"harness": "opencode", "model": "opencode/b", "until": T + 60},
    ]
