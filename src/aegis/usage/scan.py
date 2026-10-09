"""Every transcript store, deduplicated, read for one repo's cost.

Two kinds of store: aegis's own (``transcripts/``, see ``store.py``) and
Claude Code's (``~/.claude/projects``, purged after thirty days, plus any
directory named with ``--extra-root``, gzipped or not). They overlap, so every
assistant message is counted once, by its message id. Claude Code's own files
are read first: they are the same lines aegis stored, and reading them first
keeps the ids aegis would add from counting twice.

Cost is priced from tokens through ``prices.py``, never taken from Claude's
running ``total_cost_usd``: Claude Code's files do not carry it, and a model
without a price is counted as unpriced rather than charged at another's rate.
Only Claude sessions are priced. An OpenCode session's tokens, from its
``step-finish`` parts, are counted as unpriced work. A Codex session's requests,
from its ``thread/tokenUsage/updated`` lines, are priced by ``codex_prices_for``
with the model of their turn's ``aegis/turn`` line.
"""

from __future__ import annotations

import collections
import gzip
import json
import logging
import re
import time
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from .locality import (
    NO_MODULE,
    NOISE_DIRS,
    ROOT_MODULE,
    SPLIT_DIRS,
    mention_re,
    module_of,
    repos_mentioned,
)
from .prices import codex_prices_for, prices_for
from .store import lines, sessions, split_cache

log = logging.getLogger(__name__)

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
    """Price one call; None when there is no price for it. Only Claude Code
    sessions have prices: another harness's model ids are not Claude's."""
    if provider == DEFAULT_PROVIDER:
        prices = prices_for(model)
    elif provider == "codex":
        prices = codex_prices_for(model)
    else:
        prices = None
    if prices is None:
        return None
    return float(prices.cost(inp=inp, out=out, cc5=cc5, cc1=cc1, cache_read=cache_read))


class Scanner:
    """Walks every transcript store and accumulates per-session usage and locality."""

    def __init__(
        self,
        repo: str | None,
        repo_path: Path,
        *,
        container: str,
        since: str | None = None,
        until: str | None = None,
        split_dirs: frozenset[str] = SPLIT_DIRS,
    ) -> None:
        self.repo = repo
        self.repo_path = Path(repo_path)
        #: The directory sibling repos live in, so a mention is recognised by
        #: the layout in front of us rather than by a hard-coded ``repos/``.
        self.mention_re = mention_re(container)
        self.multi = repo is None
        self.since = since
        self.until = until
        self.split_dirs = split_dirs
        self.sessions: dict[str, SessionScan] = {}
        self.seen: set[str] = set()
        #: Calls whose cache writes wait for the measured 1-hour share.
        self._unsplit: list[tuple] = []
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
        names = repos_mentioned(text, self.mention_re, fold=self.repo)
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
            # gzip only discovers truncation while decompressing, inside this
            # loop, and raises EOFError, which is not an OSError. A
            # claude-import run killed partway through leaves exactly that
            # file, and DESIGN.md's rule is that a damaged file never takes a
            # session down: keep whatever decompressed and move on.
            while True:
                try:
                    line = next(handle, None)
                except Exception:  # noqa: BLE001 — truncated / corrupt archive
                    log.warning(
                        "damaged transcript archive, keeping what parsed: %s", path
                    )
                    return
                if line is None:
                    return
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
                cc5, cc1, _ = split_cache(usage)
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

    # -- aegis's own store -----------------------------------------------
    def scan_store(self, state_dir: Path) -> None:
        """Read aegis's transcript store (``store.py``).

        A line without the cache split (an imported legacy session) waits in
        ``_unsplit`` until every store is read; ``run`` then splits its writes
        by the 1-hour share measured on the lines that did report it, rather
        than pricing them all at the 5-minute rate.
        """
        for stored in sessions(state_dir):
            provider = (
                DEFAULT_PROVIDER if stored.harness == "claude-code" else stored.harness
            )
            key = f"aegis:{stored.log_id}"
            cwd: str | None = None
            # A Codex request never borrows Claude's default model's price.
            model = "" if stored.harness == "codex" else DEFAULT_MODEL
            for line in lines(stored.path):
                if line.src == "aegis" and line.obj.get("kind") == "spawn":
                    cwd = line.obj.get("cwd") or cwd
                    continue
                if line.src not in ("claude", "opencode", "codex"):
                    continue
                obj = line.obj
                if obj.get("type") == "system" and obj.get("subtype") == "init":
                    model = obj.get("model") or model
                if not in_window(line.ts, self.since, self.until):
                    continue
                scan = self._session(key, "aegis", cwd, provider)
                self._note_paths(scan, line.raw)
                if line.src == "codex":
                    model = self._codex_request(scan, line.ts or "", obj, model)
                    continue
                if line.src == "opencode":
                    self._opencode_step(scan, line.ts or "", obj)
                    continue
                if obj.get("type") != "assistant":
                    continue
                message = obj.get("message")
                if not isinstance(message, dict):
                    continue
                # No id, no count: an imported legacy tool call carries its
                # message's usage but not its id, so counting it would charge
                # that message once more per tool call (the legacy engine
                # skipped these too). aegis's own lines always carry the id.
                mid = message.get("id")
                if not mid or mid in self.seen:
                    continue
                self.seen.add(mid)
                usage = message.get("usage") or {}
                cc5, cc1, reported = split_cache(usage)
                call = (
                    scan,
                    line.ts or "",
                    message.get("model") or model,
                    int(usage.get("input_tokens") or 0),
                    int(usage.get("output_tokens") or 0),
                    cc5,
                    cc1,
                    int(usage.get("cache_read_input_tokens") or 0),
                )
                if reported:
                    self._add_call(call, 0.0)
                else:
                    self._unsplit.append(call)

    def _opencode_step(self, scan: SessionScan, ts: str, obj: dict) -> None:
        """OpenCode reports a step's tokens on its ``step-finish`` part, once
        per part id. None of its models has a price here, so they count as
        unpriced work rather than vanishing."""
        if obj.get("type") != "message.part.updated":
            return
        part = (obj.get("properties") or {}).get("part") or {}
        tokens = part.get("tokens")
        if part.get("type") != "step-finish" or not isinstance(tokens, dict):
            return
        key = f"opencode:{part.get('id')}"
        if key in self.seen:
            return
        self.seen.add(key)
        cache = tokens.get("cache") if isinstance(tokens.get("cache"), dict) else {}
        self._add(
            scan,
            ts,
            "opencode",
            inp=int(tokens.get("input") or 0),
            out=int(tokens.get("output") or 0) + int(tokens.get("reasoning") or 0),
            cc5=int(cache.get("write") or 0),
            cc1=0,
            cache_read=int(cache.get("read") or 0),
        )

    def _codex_request(self, scan: SessionScan, ts: str, obj: dict, model: str) -> str:
        """Codex reports each request's tokens in ``thread/tokenUsage/updated``;
        its model is the one its turn was sent with (``aegis/turn``). Returns
        the model in force after this line. Each usage line is one request: the
        store holds each once, and aegis's store is the only one Codex lines
        are in, so there is nothing to deduplicate."""
        params = obj.get("params") or {}
        if obj.get("method") == "aegis/turn":
            return str(params.get("model") or model)
        if obj.get("method") != "thread/tokenUsage/updated":
            return model
        last = (params.get("tokenUsage") or {}).get("last")
        if not isinstance(last, dict):
            return model
        cached = int(last.get("cachedInputTokens") or 0)
        write = int(last.get("cacheWriteInputTokens") or 0)
        self._add(
            scan,
            ts,
            model,
            inp=max(0, int(last.get("inputTokens") or 0) - cached - write),
            out=int(last.get("outputTokens") or 0),
            cc5=write,
            cc1=0,
            cache_read=cached,
        )
        return model

    def _add_call(self, call: tuple, cc1_share: float) -> None:
        scan, ts, model, inp, out, cc5, cc1, cache_read = call
        if cc1_share:
            cc1 = int(round(cc5 * cc1_share))
            cc5 -= cc1
        self._add(
            scan, ts, model, inp=inp, out=out, cc5=cc5, cc1=cc1, cache_read=cache_read
        )

    # -- driver ----------------------------------------------------------
    def run(
        self,
        claude_roots: list[tuple[Path, str, str]],
        state_dir: Path | None,
        *,
        foreign: bool = True,
    ) -> float:
        """Read every store. ``claude_roots`` is ``(path, source, prefix)``.

        ``foreign=False`` drops the stores aegis does not own, which is
        ``~/.claude/projects`` and nothing else. A root the user named with
        ``--extra-root`` carries source ``"extra"`` and is always read: it was
        asked for explicitly, and dropping it silently returns 0.00 USD to
        someone measuring only a synced host.
        """
        started = time.monotonic()
        claude_files: list[tuple[Path, str, str]] = []
        for root, source, prefix in claude_roots:
            if not foreign and source != "extra":
                continue
            if root.exists():
                found = sorted([*root.rglob("*.jsonl"), *root.rglob("*.jsonl.gz")])
                claude_files += [(f, source, prefix) for f in found]

        for path, source, prefix in claude_files:
            self.scan_claude(path, source, prefix)
        if state_dir:
            self.scan_store(Path(state_dir))

        # Take the 1h cache share from the full-fidelity rows already read
        # rather than assuming one.
        short = long_lived = 0
        for scan in self.sessions.values():
            for bucket in scan.usage.values():
                short += bucket["cc5"]
                long_lived += bucket["cc1"]
        total = long_lived + short
        cc1_share = long_lived / total if total else 0.0
        for call in self._unsplit:
            self._add_call(call, cc1_share)
        self._unsplit = []

        self.elapsed_s = time.monotonic() - started
        return cc1_share
