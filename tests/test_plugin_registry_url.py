"""Registry URL parsing."""
from __future__ import annotations

import pytest

from aegis.plugins.registry import RegistryURL, parse_registry_url


def test_minimal_gh() -> None:
    u = parse_registry_url("gh:apiad/aegis")
    assert u.scheme == "gh"
    assert u.owner == "apiad"
    assert u.repo == "aegis"
    assert u.ref == "main"
    assert u.path == "plugins/"


def test_gh_with_path() -> None:
    u = parse_registry_url("gh:apiad/aegis#plugins/")
    assert u.path == "plugins/"


def test_gh_with_ref() -> None:
    u = parse_registry_url("gh:apiad/aegis@v0.1.0")
    assert u.ref == "v0.1.0"
    assert u.path == "plugins/"


def test_gh_full() -> None:
    u = parse_registry_url("gh:apiad/aegis@v0.1.0#custom/path")
    assert u.ref == "v0.1.0"
    assert u.path == "custom/path"


def test_file_url() -> None:
    u = parse_registry_url("file:///home/me/plugins")
    assert u.scheme == "file"
    assert u.path == "/home/me/plugins"


def test_bad_url_fails_loud() -> None:
    with pytest.raises(ValueError, match="unsupported registry URL"):
        parse_registry_url("https://example.com/x")
