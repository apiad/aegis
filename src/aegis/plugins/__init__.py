"""Aegis plugin substrate — manifest, install, uninstall, lockfile."""
from aegis.plugins.manifest import ManifestError, PluginManifest, load_manifest

__all__ = ["ManifestError", "PluginManifest", "load_manifest"]
