"""Bundled data files (models.yaml, ...).

This module's only purpose is to make ``aegis.data`` a proper package so
``importlib.resources.files("aegis.data").joinpath(...)`` resolves
reliably across pip / uv / hatchling builds.
"""
