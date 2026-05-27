"""Hermetic tests for the init wizard's deterministic pieces:
detection, rendering, slug auto-collision. The interactive prompt
loop is tested at the CLI level via typer's CliRunner with stdin
scripted (see test_init_cli.py)."""
from __future__ import annotations

from aegis.config import load_config, load_queues
from aegis.init_wizard import (
    AgentEntry,
    QueueEntry,
    WizardConfig,
    _next_slug,
    detect_providers,
    render_aegis_yaml,
)


# ---------- _next_slug ------------------------------------------------

def test_next_slug_unused_returns_base():
    assert _next_slug([], "claude") == "claude"


def test_next_slug_first_collision_returns_dash_2():
    assert _next_slug(["claude"], "claude") == "claude-2"


def test_next_slug_skips_existing_numbered():
    assert _next_slug(["claude", "claude-2", "claude-3"], "claude") == "claude-4"


def test_next_slug_unrelated_existing_does_not_collide():
    assert _next_slug(["gemini", "opencode"], "claude") == "claude"


# ---------- detect_providers (with injected which) -------------------

def test_detect_providers_marks_available_per_which():
    fake_which = {"claude": "/usr/bin/claude",
                  "gemini": None,
                  "opencode": "/usr/bin/opencode"}.get
    out = detect_providers(which=fake_which)
    names = {p.name: p.available for p in out}
    assert names == {"claude-code": True, "gemini": False, "opencode": True}


def test_detect_providers_none_installed():
    out = detect_providers(which=lambda _x: None)
    assert all(not p.available for p in out)


# ---------- render_aegis_yaml -----------------------------------------

def _claude_default() -> AgentEntry:
    return AgentEntry(slug="claude", provider_name="claude-code",
                      cls_name="ClaudeCode",
                      model="opus", permission="auto", effort="high")


def _gemini_worker() -> AgentEntry:
    return AgentEntry(slug="gemini", provider_name="gemini",
                      cls_name="GeminiCLI",
                      model="gemini-3-flash-preview",
                      permission="full", effort=None)


def _opencode_worker() -> AgentEntry:
    return AgentEntry(slug="opencode", provider_name="opencode",
                      cls_name="OpenCode",
                      model="opencode/kimi-k2.6",
                      permission="full", effort=None)


def test_render_single_claude_agent_no_queues(tmp_path):
    cfg = WizardConfig(agents=[_claude_default()], default_agent="claude")
    out = render_aegis_yaml(cfg)
    assert "default_agent: claude" in out
    assert "provider: claude-code" in out
    assert "queues:" not in out
    (tmp_path / ".aegis.yaml").write_text(out)
    agents, default = load_config(root=tmp_path)
    assert default == "claude"
    assert set(agents) == {"claude"}


def test_render_three_providers_with_queues_round_trips(tmp_path):
    cfg = WizardConfig(
        agents=[_claude_default(), _gemini_worker(), _opencode_worker()],
        default_agent="claude",
        queues=[
            QueueEntry(name="impl",          agent_slug="claude",   max_parallel=1),
            QueueEntry(name="impl-gemini",   agent_slug="gemini",   max_parallel=2),
            QueueEntry(name="impl-opencode", agent_slug="opencode", max_parallel=1),
        ],
    )
    out = render_aegis_yaml(cfg)
    assert "provider: claude-code" in out
    assert "provider: gemini" in out
    assert "provider: opencode" in out
    (tmp_path / ".aegis.yaml").write_text(out)
    agents, default = load_config(root=tmp_path)
    assert default == "claude"
    assert set(agents) == {"claude", "gemini", "opencode"}
    assert agents["claude"].harness == "claude-code"
    assert agents["claude"].model == "opus"
    assert agents["gemini"].harness == "gemini"
    assert agents["gemini"].model == "gemini-3-flash-preview"
    assert agents["opencode"].harness == "opencode"
    assert agents["opencode"].model == "opencode/kimi-k2.6"
    qs = load_queues(tmp_path)
    assert set(qs) == {"impl", "impl-gemini", "impl-opencode"}
    assert qs["impl-gemini"].agent_profile == "gemini"
    assert qs["impl-gemini"].max_parallel == 2


def test_render_claude_emits_effort_other_providers_dont():
    cfg = WizardConfig(
        agents=[_claude_default(), _gemini_worker()],
        default_agent="claude",
    )
    out = render_aegis_yaml(cfg)
    # Find each agent block by its provider line, then check whether the
    # next few lines include `effort:`.
    lines = out.splitlines()
    claude_idx = next(i for i, ln in enumerate(lines)
                      if "provider: claude-code" in ln)
    gemini_idx = next(i for i, ln in enumerate(lines)
                      if "provider: gemini" in ln)
    claude_block = "\n".join(lines[claude_idx:claude_idx + 4])
    gemini_block = "\n".join(lines[gemini_idx:gemini_idx + 4])
    assert "effort: high" in claude_block
    assert "effort:" not in gemini_block


def test_render_no_agents_returns_minimal_file():
    cfg = WizardConfig(agents=[], default_agent="")
    out = render_aegis_yaml(cfg)
    assert "agents:" in out
    assert "queues:" not in out
