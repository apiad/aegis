"""Background refresh of the model registry from GitHub.

aegis fires ``maybe_refresh()`` once per CLI boot. If the cache file at
``~/.cache/aegis/models.yaml`` is missing or older than ``TTL_SECONDS``,
it spawns a daemon thread that fetches the upstream YAML and writes the
cache. Failures are silent — the existing cache (or bundled fallback)
keeps working.

The spawned thread never blocks startup; results are picked up on the
NEXT process boot. This is intentional: pricing-sensitive code already
runs against a valid registry, so there's no value in racing the fetch
against the first lookup.
"""
from __future__ import annotations

import logging
import threading
import time
from pathlib import Path

import httpx

# Import the module (not the names) so monkeypatching ``aegis.models.cache_path``
# in tests is visible here. Binding via ``from aegis.models import ...`` would
# capture a local reference that ignores later attribute swaps.
import aegis.models as _models_mod

_LOG = logging.getLogger("aegis.models.refresh")

DEFAULT_URL = ("https://raw.githubusercontent.com/apiad/aegis/main/"
               "src/aegis/data/models.yaml")
TTL_SECONDS = 24 * 60 * 60  # 24h
HTTP_TIMEOUT = 5.0

# Process-wide guard so two concurrent boot paths don't fire two fetches.
_fired = False
_fired_lock = threading.Lock()


def _is_stale(path: Path, ttl_seconds: int = TTL_SECONDS) -> bool:
    if not path.exists():
        return True
    age = time.time() - path.stat().st_mtime
    return age > ttl_seconds


def _fetch_and_write(url: str, dest: Path) -> None:
    """Run in a background thread. Best-effort: log on failure, never raise."""
    try:
        r = httpx.get(url, timeout=HTTP_TIMEOUT,
                       follow_redirects=True)
        r.raise_for_status()
        body = r.text
        # Parse-validate before persisting so we never corrupt the cache
        # with a 404/HTML body or a partial download.
        from io import StringIO
        from ruamel.yaml import YAML
        parsed = YAML(typ="safe").load(StringIO(body))
        if not isinstance(parsed, dict) or "providers" not in parsed:
            raise ValueError("upstream models.yaml: missing 'providers'")
        dest.parent.mkdir(parents=True, exist_ok=True)
        tmp = dest.with_suffix(dest.suffix + ".tmp")
        tmp.write_text(body, encoding="utf-8")
        tmp.replace(dest)
        _LOG.debug("models.yaml refreshed from %s → %s", url, dest)
        # Force the in-memory singleton to re-read on next access. Cheap:
        # we just clear the cache; the next load_registry() call reparses.
        try:
            _models_mod.load_registry(force=True)
        except Exception:  # noqa: BLE001
            pass
    except Exception as e:  # noqa: BLE001
        _LOG.debug("models.yaml refresh failed: %s", e)


def maybe_refresh(*, url: str = DEFAULT_URL,
                  ttl_seconds: int = TTL_SECONDS) -> bool:
    """Spawn a background thread to refresh ~/.cache/aegis/models.yaml
    if it's missing or stale. Returns True iff a fetch was spawned.

    Idempotent within a single process: only the first call may spawn
    a thread; subsequent calls are no-ops.
    """
    global _fired
    with _fired_lock:
        if _fired:
            return False
        _fired = True
    dest = _models_mod.cache_path()
    if not _is_stale(dest, ttl_seconds):
        return False
    # Bump TTL so back-to-back boots within the same window don't refetch
    # if the previous thread is still in flight.
    t = threading.Thread(
        target=_fetch_and_write, args=(url, dest),
        name="aegis-models-refresh", daemon=True)
    t.start()
    return True


def _reset_fired_for_tests() -> None:
    """Test-only: reset the process-wide guard so a second test in the
    same pytest run can exercise the spawn path."""
    global _fired
    with _fired_lock:
        _fired = False
