import builtins

import aegis.voice.availability as av


def test_available_when_both_import(monkeypatch):
    av._CACHE.clear()
    monkeypatch.setattr(av, "_probe", lambda name: True)
    assert av.voice_available() is True
    assert av.unavailable_reason() == ""


def test_unavailable_names_missing_dep(monkeypatch):
    av._CACHE.clear()
    monkeypatch.setattr(av, "_probe",
                        lambda name: name != "sounddevice")
    assert av.voice_available() is False
    reason = av.unavailable_reason()
    assert "sounddevice" in reason
    assert "aegis-harness[voice]" in reason


def test_probe_uses_import(monkeypatch):
    real_import = builtins.__import__

    def fake_import(name, *a, **k):
        if name == "definitely_absent_pkg_xyz":
            raise ImportError("no")
        return real_import(name, *a, **k)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    assert av._probe("definitely_absent_pkg_xyz") is False
    assert av._probe("json") is True
