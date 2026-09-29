"""Warn before a message re-reads a large context uncached.

After the prompt cache expires, the next message to a session pays for its
whole context again as a cache write instead of a cache read. Measured over
7,033 Claude Code transcripts from the 30 days to 2026-09-29, grouped by the
gap since the previous call: 99% of calls under 5 minutes hit the cache, 90%
between 5 minutes and an hour, and 1% past an hour. That puts the TTL at one
hour. 131 resumes past the hour carried a context of 100k tokens or more, 130
of them missed, and together they re-wrote 51.2M tokens. At the Opus prices in
``data/models.yaml`` the median one (383k tokens) costs about $3.83 against
$0.19 warm.

A fresh agent handed the thread by ``/spawn`` starts small instead, which is
what the warning offers. ``/compact`` would not help once the cache is gone:
compacting is itself a call over the whole context. Issue #25.
"""

from __future__ import annotations

# Prompt-cache TTL per harness, in seconds. Only harnesses whose TTL was
# measured are listed; for any other the warning stays silent rather than
# guessing.
CACHE_TTL_S: dict[str, int] = {"claude-code": 3600}

# Below this, a cold re-read costs too little to interrupt anyone about.
MIN_CONTEXT_TOKENS = 100_000


def cold_cache_warning(
    *, harness: str, idle_s: float, context_tokens: int, agent_slug: str
) -> str | None:
    """The warning line for a session idle this long, or None."""
    ttl = CACHE_TTL_S.get(harness)
    if ttl is None or idle_s < ttl or context_tokens < MIN_CONTEXT_TOKENS:
        return None
    from aegis.tui.metrics import _fmt_tokens

    return (
        f"The prompt cache has expired, so the next message re-reads all "
        f"{_fmt_tokens(context_tokens)} tokens of this context uncached. "
        f"To carry on in a fresh context instead: "
        f"/spawn {agent_slug} continue this task"
    )
