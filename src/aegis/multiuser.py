"""/multiuser: the turn that hands the multi-user setup to the agent.

The host work (users, groups, the unit, credentials) lives in the help page
``multi-user``, written for an agent: agents read it for the version they run.
This module only builds the turn, adding what the server knows: its version,
its Linux user, the machine and whether the mode is on.
"""

from __future__ import annotations

import os
import platform
import pwd
from importlib.metadata import PackageNotFoundError, version


def facts(on: bool) -> str:
    try:
        aegis = version("aegis-harness")
    except PackageNotFoundError:
        aegis = "unknown"
    try:
        user = pwd.getpwuid(os.getuid()).pw_name
    except KeyError:
        user = str(os.getuid())
    parts = [
        f"aegis {aegis}",
        f"server user: {user}",
        f"{platform.system()} {platform.machine()}",
        f"multi-user mode: {'on' if on else 'off'}",
    ]
    return " · ".join(parts)


def turn(typed: str, on: bool, facts: str, now: str) -> tuple[str, str]:
    if typed:
        said = f"The person typed: /multiuser {typed}"
    else:
        said = "The person typed /multiuser with no text."
    if on:
        how = (
            "This server is already in multi-user mode. Read the sections\n"
            "help_read multi-user add-a-person, help_read multi-user remove-a-person\n"
            "and help_read multi-user check, ask what they want to change, show\n"
            "the commands and run nothing until they say yes.\n"
        )
    else:
        how = (
            "They want several people to use this server, each as their own Linux\n"
            "user. Read the help page in full first (help_read multi-user) and\n"
            "follow it with them:\n"
            "- look at the host before changing anything;\n"
            "- explain in a few lines what the mode changes and what it does not\n"
            "  protect against;\n"
            "- ask who the people are, which folders they share and where the\n"
            "  harness credentials come from;\n"
            "- show the plan as a list of commands, and run nothing until they say yes;\n"
            "  a step that needs root runs with sudo only if they agree, or goes to\n"
            "  them to paste;\n"
            "- tell them before the restart that this session stops and continues\n"
            "  on its own;\n"
            "- finish with aegis doctor clean in the mode and one sign-in per\n"
            "  member token, which they check in a second browser.\n"
        )
    return f"> from multiuser · {now}", f"{said}\n\n{how}\n{facts}\n"
