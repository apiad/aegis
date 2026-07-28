from aegis.models import models_for, opencode_models


def test_opencode_models_returns_list(monkeypatch):
    monkeypatch.setattr(
        "aegis.models._run_opencode_models",
        lambda: "opencode/mimo-v2.5-free\nopencode/gpt-5.1\n")
    got = opencode_models()
    assert "opencode/mimo-v2.5-free" in got
    assert "opencode/gpt-5.1" in got


def test_opencode_models_empty_when_cli_absent(monkeypatch):
    monkeypatch.setattr("aegis.models._run_opencode_models", lambda: None)
    assert opencode_models() == []


def test_models_for_opencode_surfaces_live_free_models(monkeypatch):
    monkeypatch.setattr(
        "aegis.models._run_opencode_models",
        lambda: "opencode/mimo-v2.5-free\n")
    opts = models_for("opencode")
    assert ("opencode/mimo-v2.5-free", "opencode/mimo-v2.5-free") in opts


def test_models_for_opencode_falls_back_to_registry(monkeypatch):
    monkeypatch.setattr("aegis.models._run_opencode_models", lambda: None)
    opts = models_for("opencode")
    # registry-backed entries remain available when the CLI is absent
    assert len(opts) > 0
