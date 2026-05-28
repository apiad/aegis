"""InstallContext: handed to _install.py::install(ctx) and _uninstall.py."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass
class InstallContext:
    project_root: Path
    aegis_dir:    Path
    plugin_dir:   Path
    plugin_name:  str
    manifest:     dict[str, Any]
    config:       Any                  # AegisConfig; opaque here to avoid cycles
    console:      Any                  # rich.Console | None for headless tests
    _confirm_default: bool = True
    _yes:         bool = False

    def confirm(self, question: str, *, default: bool) -> bool:
        """Prompt the user. Returns `default` automatically in --yes mode."""
        if self._yes:
            return default
        if self.console is None:
            return default
        from rich.prompt import Confirm
        return Confirm.ask(question, default=default, console=self.console)
