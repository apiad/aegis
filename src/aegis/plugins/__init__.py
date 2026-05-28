"""Aegis plugin substrate — manifest, install, uninstall, lockfile."""
from aegis.plugins.install_context import InstallContext
from aegis.plugins.manifest import ManifestError, PluginManifest, load_manifest

__all__ = ["InstallContext", "ManifestError", "PluginManifest", "load_manifest"]
