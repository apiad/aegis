"""Cascades: which failures move a session to its next model, and which model.

Zen's free usage limit, measured 2026-10-18 and 2026-10-20 (the second run on
OpenCode v2.0.28): about 520 ``opencode run -m opencode/beam-free "say ok"``
calls over two runs (37 and 4 minutes) never got a 429 FreeUsageLimitError, so
its scope is not reproduced and stays ``model``. What came instead was an error
for the whole host: ``provider.auth`` (status 403, FreeTierError) "OpenCode's
free models aren't available from your current network because we've detected
abusive traffic from this IP address", on big-pickle, deepseek-v4.1-flash-free
and glm-5.3-flash-free, while beam-free still answered. It is an auth error, so
it stays: every other free model would fail the same way.
"""
