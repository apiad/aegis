"""/feedback: the turn that hands a person's report about aegis to the agent.

The procedure (which kind, what to ask, how to redact, how to file) lives in the
help page ``feedback``, not here: agents read it for the version they run, the
primer's offer sends them to the same page, and it changes without a release of
this code. This module only builds the turn, adding what the server knows and
the agent would otherwise dig up: versions, the platform and where to file.
"""

from __future__ import annotations

import platform
from importlib.metadata import PackageNotFoundError, metadata, version

ISSUES_FALLBACK = "https://github.com/apiad/aegis/issues"


def issues_url() -> str:
    """The package's ``Issues`` URL, so a fork files to itself; the upstream
    tracker for a checkout with no installed metadata."""
    try:
        urls = metadata("aegis-harness").get_all("Project-URL") or []
    except PackageNotFoundError:
        return ISSUES_FALLBACK
    for entry in urls:
        name, _, url = entry.partition(",")
        if name.strip().lower() == "issues":
            return url.strip()
    return ISSUES_FALLBACK


def facts(harness: str, harness_version: str | None) -> str:
    try:
        aegis = version("aegis-harness")
    except PackageNotFoundError:
        aegis = "unknown"
    known = harness_version not in (None, "?")
    parts = [
        f"aegis {aegis}",
        f"{harness} {harness_version}" if known else harness,
        f"{platform.system()} {platform.release()}",
        f"Python {platform.python_version()}",
    ]
    return " · ".join(parts)


def turn(typed: str, facts: str, issues: str, now: str) -> tuple[str, str]:
    if typed:
        said = f"The person typed: /feedback {typed}"
    else:
        said = (
            "The person typed /feedback with no text. Work out from this "
            "conversation what they want to report, and ask if it is not clear."
        )
    body = (
        f"{said}\n\n"
        "Help them turn this into an issue for aegis. Read the help page first\n"
        "(help_read feedback) and follow it. Show them the draft and file nothing\n"
        "until they say yes.\n\n"
        f"{facts}\n"
        f"issues: {issues}\n"
    )
    return f"> from feedback · {now}", body
