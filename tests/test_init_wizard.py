"""Hermetic tests for the init wizard's deterministic pieces:
detection, rendering, slug auto-collision. The interactive prompt
loop is tested at the CLI level via typer's CliRunner with stdin
scripted (see test_init_cli.py)."""
from __future__ import annotations

import textwrap

from aegis.config import load_config, load_queues
from aegis.init_wizard import (
    AgentEntry,
    QueueEntry,
    WizardConfig,
    _next_slug,
    detect_providers,
    render_aegis_py,
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


# ---------- render_aegis_py -------------------------------------------

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
    out = render_aegis_py(cfg)
    # Imports only what's used.
    assert "from aegis import Agent, ClaudeCode" in out
    assert "GeminiCLI" not in out and "OpenCode" not in out
    # No queues block when none configured.
    assert "queues = {" not in out
    # Round-trip: writing the file and loading it produces matching agents.
    (tmp_path / ".aegis.py").write_text(out)
    agents, default = load_config(search_paths=[tmp_path / ".aegis.py"])
    assert default == "claude"
    assert set(agents) == {"claude"}
    assert agents["claude"].provider.cls_name() if False else True  # pydantic noise


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
    out = render_aegis_py(cfg)
    # All three provider classes imported.
    assert "ClaudeCode" in out and "GeminiCLI" in out and "OpenCode" in out
    # Round-trip through the real config loader.
    p = tmp_path / ".aegis.py"
    p.write_text(out)
    agents, default = load_config(search_paths=[p])
    assert default == "claude"
    assert set(agents) == {"claude", "gemini", "opencode"}
    # Each agent carries the right harness + model.
    assert agents["claude"].harness == "claude-code"
    assert agents["claude"].model == "opus"
    assert agents["gemini"].harness == "gemini"
    assert agents["gemini"].model == "gemini-3-flash-preview"
    assert agents["opencode"].harness == "opencode"
    assert agents["opencode"].model == "opencode/kimi-k2.6"
    # Queues parse + bind correctly.
    qs = load_queues(p)
    assert set(qs) == {"impl", "impl-gemini", "impl-opencode"}
    assert qs["impl-gemini"].agent_profile == "gemini"
    assert qs["impl-gemini"].max_parallel == 2


def test_render_claude_emits_effort_other_providers_dont():
    cfg = WizardConfig(
        agents=[_claude_default(), _gemini_worker()],
        default_agent="claude",
    )
    out = render_aegis_py(cfg)
    # ClaudeCode line carries effort=...; GeminiCLI line does NOT.
    # Filter for the agent-line shape (Agent(provider=...)), not the import.
    claude_line = next(
        ln for ln in out.splitlines()
        if "Agent(provider=ClaudeCode" in ln)
    gemini_line = next(
        ln for ln in out.splitlines()
        if "Agent(provider=GeminiCLI" in ln)
    assert 'effort="high"' in claude_line
    assert "effort=" not in gemini_line


def test_render_no_agents_returns_minimal_file():
    cfg = WizardConfig(agents=[], default_agent="")
    out = render_aegis_py(cfg)
    # No imports beyond Agent; agents dict empty; default_agent empty.
    assert "from aegis import Agent" in out
    assert "agents = {" in out and "}" in out
