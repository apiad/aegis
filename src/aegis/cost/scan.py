"""The four transcript stores, deduplicated, with one reading path per session.

Three stores are aegis's own (``sessions/``, ``backfill/``, ``claude-import/``)
and one is foreign (``~/.claude/projects``, which claude-code purges after
thirty days). They overlap, so every record is deduplicated globally.

**One path per session, never both.** A claude-code session records its token
counts on each assistant message and repeats them on the turn's ``Result``;
reading both double-counts it. An ACP session (OpenCode, Gemini) records
``usage: null`` on its messages and the real counts only on ``Result``;
reading only messages prices it at zero, takes the dedup key, and says
nothing. So: if any per-message event in a session carries a non-null usage,
the session is read by message. Otherwise it is read by Result.

Neither path derives cost from ``cost_usd``. It is absent from the two foreign
stores, and its cumulative-versus-per-turn meaning differs by harness, so
guessing wrong is a silent multiplier. Both paths price tokens through the
model registry.
"""

from __future__ import annotations

import collections
import gzip
import json
import re
import time
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from aegis.cost.locality import (
    NO_MODULE,
    NOISE_DIRS,
    ROOT_MODULE,
    SPLIT_DIRS,
    module_of,
    repos_mentioned,
)
from aegis.usage.aggregate import resolve_prices

# ProviderPrices carries one cache_write rate. A 1-hour write is twice the
# 5-minute one; the aegis event stream does not split them, so the share is
# taken from the claude-code rows read in the same run.
CACHE_1H_MULT = 2

_PER_MESSAGE_EVENTS = ("AssistantThinking", "AssistantText", "ToolUse")

DEFAULT_PROVIDER = "claude-code"
DEFAULT_MODEL = "opus"


@dataclass
class SessionScan:
    key: str
    source: str
    provider: str = DEFAULT_PROVIDER
    cwd: str | None = None
    first_ts: str | None = None
    last_ts: str | None = None
    n_records: int = 0
    repo_records: collections.Counter = field(default_factory=collections.Counter)
    modules: collections.Counter = field(default_factory=collections.Counter)
    usage: dict[tuple[str, str], collections.Counter] = field(default_factory=dict)
    timestamps: list[str] = field(default_factory=list)
    # Calls and tokens whose (provider, model) has no price in the registry.
    # Counted, never charged: see Scanner._add.
    unpriced: collections.Counter = field(default_factory=collections.Counter)


def iso_week(ts: str) -> str:
    try:
        return datetime.fromisoformat(ts.replace("Z", "+00:00")).strftime("%G-W%V")
    except ValueError:
        return "?"


def in_window(ts: str | None, since: str | None, until: str | None) -> bool:
    if not ts:
        return since is None and until is None
    day = ts[:10]
    if since and day < since:
        return False
    return not (until and day > until)


def active_hours(timestamps: list[str], cap: int = 300) -> dict[str, float]:
    """Assisted working time per ISO week: gaps between consecutive calls,
    each capped at ``cap`` seconds so an overnight pause is not counted."""
    points = []
    for raw in timestamps:
        try:
            points.append(datetime.fromisoformat(raw.replace("Z", "+00:00")))
        except ValueError:
            continue
    points.sort()
    out: collections.Counter = collections.Counter()
    for earlier, later in zip(points, points[1:]):
        gap = (later - earlier).total_seconds()
        if gap > 0:
            out[later.strftime("%G-W%V")] += min(gap, cap)
    return dict(out)


def _cost_of(
    provider: str,
    model: str,
    inp: int,
    out: int,
    cc5: int,
    cc1: int,
    cache_read: int,
) -> float | None:
    """Price one call. ``resolve_prices`` tries an exact name, then an alias,
    then an opus/sonnet/haiku/gemini substring within that provider.

    The provider is not optional. Prices are keyed by provider and there is no
    ``gemini`` model under ``claude-code``, so asking claude-code for every
    price returns None for a Gemini session and charges it nothing, while
    falling back to the default model would charge it at Opus rates.
    """
    prices = resolve_prices(provider, model)
    if prices is None:
        return None
    return (
        float(
            inp * prices.input
            + out * prices.output
            + cc5 * prices.cache_write
            + cc1 * prices.cache_write * CACHE_1H_MULT
            + cache_read * prices.cache_hit
        )
        / 1e6
    )


class Scanner:
    """Walks every transcript store and accumulates per-session usage and locality."""

    def __init__(
        self,
        repo: str | None,
        repo_path: Path,
        *,
        since: str | None = None,
        until: str | None = None,
        split_dirs: frozenset[str] = SPLIT_DIRS,
    ) -> None:
        self.repo = repo
        self.repo_path = Path(repo_path)
        self.multi = repo is None
        self.since = since
        self.until = until
        self.split_dirs = split_dirs
        self.sessions: dict[str, SessionScan] = {}
        self.seen: set[str] = set()
        self.first_seen: str | None = None
        self.elapsed_s: float = 0.0
        self.module_re = (
            re.compile(rf"{re.escape(repo)}(?:-wt-[A-Za-z0-9_-]+)?/([A-Za-z0-9._/-]*)")
            if repo
            else None
        )
        self.bare_re: re.Pattern | None = None

    def set_bare_modules(self, modules: Iterable[str]) -> None:
        """Let a record name a module by a bare relative path, not only repos/<x>/."""
        names = [m for m in modules if m and not m.startswith("(")]
        if not names:
            return
        longest_first = sorted(names, key=lambda name: -len(name))
        alt = "|".join(re.escape(name) for name in longest_first)
        self.bare_re = re.compile(
            r"(?:^|[\s\"'(,:=\\])((?:" + alt + r")/[A-Za-z0-9._/-]*)"
        )

    # -- accumulation ----------------------------------------------------
    def _session(
        self,
        key: str,
        source: str,
        cwd: str | None,
        provider: str = DEFAULT_PROVIDER,
    ) -> SessionScan:
        scan = self.sessions.get(key)
        if scan is None:
            scan = self.sessions[key] = SessionScan(
                key=key, source=source, provider=provider, cwd=cwd
            )
        if cwd and not scan.cwd:
            scan.cwd = cwd
        return scan

    def _note_paths(self, scan: SessionScan, text: str) -> None:
        names = repos_mentioned(text, fold=self.repo)
        for name in names:
            scan.repo_records[name] += 1
        if not self.multi and self.repo in names and self.module_re is not None:
            modules = set()
            for match in self.module_re.finditer(text):
                sub = match.group(1)
                if sub:
                    modules.add(module_of(sub, self.split_dirs))
            if self.bare_re:
                for match in self.bare_re.finditer(text):
                    modules.add(module_of(match.group(1), self.split_dirs))
            modules -= NOISE_DIRS
            modules.discard(ROOT_MODULE)
            for module in modules or {NO_MODULE}:
                scan.modules[module] += 1
        scan.n_records += 1

    def _add(
        self,
        scan: SessionScan,
        ts: str,
        model: str,
        *,
        inp: int,
        out: int,
        cc5: int,
        cc1: int,
        cache_read: int,
    ) -> None:
        if ts:
            if self.first_seen is None or ts < self.first_seen:
                self.first_seen = ts
            scan.timestamps.append(ts)
            if not scan.first_ts or ts < scan.first_ts:
                scan.first_ts = ts
            if not scan.last_ts or ts > scan.last_ts:
                scan.last_ts = ts
        bucket = scan.usage.setdefault((iso_week(ts), model), collections.Counter())
        bucket["input"] += inp
        bucket["output"] += out
        bucket["cc5"] += cc5
        bucket["cc1"] += cc1
        bucket["cache_read"] += cache_read
        bucket["calls"] += 1
        cost = _cost_of(scan.provider, model, inp, out, cc5, cc1, cache_read)
        if cost is None:
            # No price for this provider and model, and both wrong answers are
            # silent: charging zero makes the work look free, charging the
            # default model's rate makes a Gemini turn cost Opus money. Real
            # data forces the case — OpenCode records model "OpenCode" and
            # Gemini records none at all — so the tokens are counted here and
            # reported as unpriced.
            scan.unpriced["calls"] += 1
            scan.unpriced["tokens"] += inp + out + cc5 + cc1 + cache_read
            return
        bucket["cost_micro"] += int(round(cost * 1e6))

    # -- claude-code transcripts (plain or gzipped) ----------------------
    def scan_claude(self, path: Path, source: str, prefix: str) -> None:
        opener = (
            (lambda p: gzip.open(p, "rt", errors="ignore"))
            if path.suffix == ".gz"
            else (lambda p: p.open(errors="ignore"))
        )
        try:
            handle = opener(path)
        except OSError:
            return
        with handle:
            for line in handle:
                try:
                    record = json.loads(line)
                except (ValueError, TypeError):
                    continue  # a damaged line never takes the scan down
                if not isinstance(record, dict):
                    continue
                ts = record.get("timestamp")
                if not in_window(ts, self.since, self.until):
                    continue
                key = f"{prefix}:{record.get('sessionId') or path.stem}"
                scan = self._session(key, source, record.get("cwd"))
                self._note_paths(scan, line)
                if record.get("type") != "assistant":
                    continue
                message = record.get("message")
                if not isinstance(message, dict):
                    continue
                mid = message.get("id")
                if mid:
                    if mid in self.seen:
                        continue
                    self.seen.add(mid)
                usage = message.get("usage") or {}
                creation = usage.get("cache_creation") or {}
                cc5 = creation.get("ephemeral_5m_input_tokens", 0) or 0
                cc1 = creation.get("ephemeral_1h_input_tokens", 0) or 0
                if cc5 + cc1 == 0:
                    cc5 = usage.get("cache_creation_input_tokens", 0) or 0
                self._add(
                    scan,
                    ts or "",
                    message.get("model") or DEFAULT_MODEL,
                    inp=usage.get("input_tokens", 0) or 0,
                    out=usage.get("output_tokens", 0) or 0,
                    cc5=cc5,
                    cc1=cc1,
                    cache_read=usage.get("cache_read_input_tokens", 0) or 0,
                )

    # -- aegis event logs ------------------------------------------------
    def scan_aegis(self, path: Path, cc1_share: float) -> None:
        """Read one aegis session log, choosing its reading path per session.

        The file is read into memory first so the message-or-Result decision
        is made before anything is counted. aegis session logs are small
        enough (a few MB at the largest in this workspace) that this costs
        less than a second pass over the disk.
        """
        records: list[tuple[dict, str]] = []
        try:
            with path.open(errors="ignore") as handle:
                for line in handle:
                    try:
                        parsed = json.loads(line)
                    except (ValueError, TypeError):
                        continue
                    if isinstance(parsed, dict):
                        records.append((parsed, line))
        except OSError:
            return

        by_message = any(
            (rec.get("event") or {}).get("t") in _PER_MESSAGE_EVENTS
            and (rec.get("event") or {}).get("usage")
            for rec, _ in records
        )

        key = f"aegis:{path.stem}"
        model, provider, cwd, scan = DEFAULT_MODEL, DEFAULT_PROVIDER, None, None
        result_index = 0
        for record, line in records:
            event = record.get("event") or {}
            kind = event.get("t")
            ts = record.get("aegis_ts")
            if not in_window(ts, self.since, self.until):
                continue
            if kind == "SessionMeta":
                cwd = event.get("cwd") or cwd
                provider = event.get("provider") or provider
            if scan is None:
                scan = self._session(key, "aegis", cwd, provider)
            elif cwd and not scan.cwd:
                scan.cwd = cwd
            self._note_paths(scan, line)
            if kind == "SystemInit":
                model = event.get("model") or model
                continue

            if by_message:
                if kind not in _PER_MESSAGE_EVENTS:
                    continue
                mid = event.get("message_id")
                if not mid or mid in self.seen:
                    continue
            else:
                if kind != "Result":
                    continue
                # No message id on this path, so the key is positional. Two
                # copies of one session (sessions/ and backfill/) share a
                # stem, so the same turn dedupes across them.
                mid = f"{path.stem}#result{result_index}"
                result_index += 1
                if mid in self.seen:
                    continue
            self.seen.add(mid)

            usage = event.get("usage") or {}
            creation = usage.get("cache_creation", 0) or 0
            cc1 = int(round(creation * cc1_share))
            self._add(
                scan,
                ts or "",
                event.get("model") or model,
                inp=usage.get("input", 0) or 0,
                out=usage.get("output", 0) or 0,
                cc5=creation - cc1,
                cc1=cc1,
                cache_read=usage.get("cache_read", 0) or 0,
            )

    # -- driver ----------------------------------------------------------
    def run(
        self,
        claude_roots: list[tuple[Path, str, str]],
        state_dir: Path | None,
        *,
        foreign: bool = True,
    ) -> float:
        started = time.monotonic()
        claude_files: list[tuple[Path, str, str]] = []
        if foreign:
            for root, source, prefix in claude_roots:
                if root.exists():
                    claude_files += [
                        (f, source, prefix) for f in sorted(root.rglob("*.jsonl"))
                    ]
        gz_files: list[Path] = []
        aegis_files: list[Path] = []
        if state_dir:
            imported = Path(state_dir) / "claude-import"
            if imported.exists():
                gz_files = sorted(imported.glob("*.jsonl.gz"))
            for name in ("sessions", "backfill"):
                store = Path(state_dir) / name
                if store.exists():
                    aegis_files += sorted(store.glob("*.jsonl"))

        for path, source, prefix in claude_files:
            self.scan_claude(path, source, prefix)
        for path in gz_files:
            self.scan_claude(path, "aegis-import", "i")

        # Take the 1h cache share from the full-fidelity rows already read
        # rather than assuming one.
        short = long_lived = 0
        for scan in self.sessions.values():
            for bucket in scan.usage.values():
                short += bucket["cc5"]
                long_lived += bucket["cc1"]
        total = long_lived + short
        cc1_share = long_lived / total if total else 0.0
        for path in aegis_files:
            self.scan_aegis(path, cc1_share)

        self.elapsed_s = time.monotonic() - started
        return cc1_share
