"""Assembling one repo's answer: cost, volume, coverage, and the error bar.

Two attribution rules run on every measurement and both are reported.
Proportional gives every session its share; strict counts only sessions above
0.8 and counts them whole. The band between them is the error bar: 3.2% on
une-tools and 7.5% on aegis. A report that hides it invites the question it
cannot answer.
"""

from __future__ import annotations

import collections
import json
import os
from collections.abc import Iterable
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from aegis.cost.gitfacts import GitFacts, coverage, git_facts
from aegis.cost.locality import (
    NO_MODULE,
    SPLIT_DIRS,
    WHOLE_REPO,
    repo_roots,
    session_share,
)
from aegis.cost.scan import Scanner, active_hours

STRICT_FLOOR = 0.8

BANDS = (
    "0 (no mention)",
    "<0.1 (passing mention)",
    "0.1-0.8 (mixed)",
    "0.8-0.95 (almost only this repo)",
    "1.0 (this repo only)",
)


def _band(share: float) -> str:
    if share == 0:
        return BANDS[0]
    if share < 0.1:
        return BANDS[1]
    if share < STRICT_FLOOR:
        return BANDS[2]
    if share < 0.95:
        return BANDS[3]
    return BANDS[4]


@dataclass
class CostOptions:
    since: str | None = None
    until: str | None = None
    split_dirs: frozenset[str] = SPLIT_DIRS
    exclude: tuple[str, ...] = ()
    foreign: bool = True
    extra_roots: tuple[Path, ...] = ()
    state_dir: Path | None = None
    home_projects: Path = field(
        default_factory=lambda: Path.home() / ".claude" / "projects"
    )


@dataclass
class RepoCost:
    repo: str
    path: str
    since: str | None
    until: str | None
    generated: str
    cost_usd: float
    # ``None``, never 0.0, for anything a given path did not compute. `sweep`
    # skips the per-session breakdowns, and a consumer reading strict_usd: 0.0
    # would conclude the strict attribution is zero — which is the error bar the
    # whole measurement exists to publish.
    strict_usd: float | None
    workspace_usd: float | None
    tokens: dict[str, float] | None
    calls: float
    sessions: float | None
    hours: float
    coverage: float
    first_seen: str | None
    weeks: dict[str, dict[str, float]] | None
    modules: dict[str, float] | None
    models: dict[str, float] | None
    sources: dict[str, float] | None
    # Calls and tokens the price registry had no rate for, attributed the same
    # way as cost. Reported rather than folded into zero: see Scanner._add.
    unpriced: dict[str, float]
    bands: dict[str, dict[str, float]] | None
    git: GitFacts
    elapsed_s: float
    # The store the figures came from, so a report can say where it looked when
    # it found nothing.
    state_dir: str | None = None

    def to_dict(self) -> dict:
        data = asdict(self)
        data["git"] = asdict(self.git)
        data["git"].pop("dates")  # one line per commit, and nothing reads it back
        return data


def claude_roots_for(
    paths: Iterable[Path], home_projects: Path
) -> list[tuple[Path, str, str]]:
    """The ``~/.claude/projects`` directories that belong to these paths.

    Claude Code names a project directory after its cwd with "/" turned into
    "-", so a path prefix selects that tree and everything under it, and
    leaves /tmp and pytest scratch directories out.
    """
    if not home_projects.exists():
        return []
    prefixes = {str(Path(p).resolve()).replace("/", "-") for p in paths}
    roots: list[tuple[Path, str, str]] = []
    for entry in sorted(home_projects.iterdir()):
        if not entry.is_dir():
            continue
        if any(entry.name == p or entry.name.startswith(p + "-") for p in prefixes):
            roots.append((entry, "claude", "c"))
    return roots


def _roots(paths: Iterable[Path], options: CostOptions) -> list[tuple[Path, str, str]]:
    roots = claude_roots_for(paths, options.home_projects)
    return roots + [(Path(p), "extra", "x") for p in options.extra_roots]


def measure(repo_path: Path, options: CostOptions) -> RepoCost:
    repo_path = Path(repo_path).resolve()
    name = repo_path.name
    facts = git_facts(
        repo_path,
        since=options.since,
        until=options.until,
        split_dirs=options.split_dirs,
        exclude=options.exclude,
    )

    # Two levels up is the workspace under the repos/<name> layout this was
    # written for. It only widens which ~/.claude/projects directories are
    # read, so a repo laid out differently loses transcripts rather than
    # gaining wrong ones, and --extra-root puts them back.
    workspace = repo_path.parent.parent
    scanner = Scanner(
        name,
        repo_path,
        container=repo_path.parent.name,
        since=options.since,
        until=options.until,
        split_dirs=options.split_dirs,
    )
    scanner.set_bare_modules(sorted(facts.loc))
    scanner.run(
        _roots([workspace, repo_path], options),
        options.state_dir,
        foreign=options.foreign,
    )

    roots_for_repo = repo_roots(repo_path)
    weeks: dict[str, collections.Counter] = collections.defaultdict(collections.Counter)
    modules: collections.Counter = collections.Counter()
    models: collections.Counter = collections.Counter()
    sources: collections.Counter = collections.Counter()
    totals: collections.Counter = collections.Counter()
    bands: dict[str, collections.Counter] = {b: collections.Counter() for b in BANDS}
    unpriced: collections.Counter = collections.Counter()
    n_sessions = strict_usd = workspace_usd = hours = 0.0

    for scan in scanner.sessions.values():
        share = session_share(name, scan.repo_records, scan.cwd, roots_for_repo)
        own_cost = sum(b["cost_micro"] for b in scan.usage.values()) / 1e6
        workspace_usd += own_cost
        band = bands[_band(share)]
        band["sessions"] += 1
        band["cost"] += own_cost
        band["attributed"] += own_cost * share
        if share >= STRICT_FLOOR:
            strict_usd += own_cost
        if share <= 0:
            continue

        n_sessions += share
        if scan.unpriced:
            unpriced["sessions"] += share
            for key in ("calls", "tokens"):
                unpriced[key] += scan.unpriced[key] * share
        for week, seconds in active_hours(scan.timestamps).items():
            weeks[week]["active_s"] += seconds * share
            hours += seconds * share / 3600
        mine_cost = 0.0
        for (week, model), bucket in scan.usage.items():
            cost = bucket["cost_micro"] / 1e6 * share
            tokens = (
                bucket["input"]
                + bucket["output"]
                + bucket["cc5"]
                + bucket["cc1"]
                + bucket["cache_read"]
            ) * share
            weeks[week]["cost"] += cost
            weeks[week]["tokens"] += tokens
            weeks[week]["calls"] += bucket["calls"] * share
            models[model] += cost
            for key in ("input", "output", "cc5", "cc1", "cache_read"):
                totals[key] += bucket[key] * share
            totals["cost"] += cost
            totals["tokens"] += tokens
            totals["calls"] += bucket["calls"] * share
            mine_cost += cost
        sources[scan.source] += mine_cost

        named = dict(scan.modules)
        loose = named.pop(NO_MODULE, 0)
        total_named = sum(named.values())
        if total_named:
            # Repo-wide records follow the session's own module mix.
            for module, count in named.items():
                modules[module] += mine_cost * count / total_named
        elif loose:
            modules[WHOLE_REPO] += mine_cost

    return RepoCost(
        repo=name,
        path=str(repo_path),
        since=options.since,
        until=options.until,
        generated=datetime.now(timezone.utc).isoformat(),
        cost_usd=totals["cost"],
        strict_usd=strict_usd,
        workspace_usd=workspace_usd,
        tokens={
            k: totals[k]
            for k in ("input", "output", "cc5", "cc1", "cache_read", "tokens")
        },
        calls=totals["calls"],
        sessions=n_sessions,
        hours=hours,
        coverage=coverage(facts.dates, scanner.first_seen),
        first_seen=scanner.first_seen,
        weeks={k: dict(v) for k, v in sorted(weeks.items())},
        modules=dict(modules.most_common()),
        models=dict(models.most_common()),
        sources=dict(sources),
        unpriced=dict(unpriced),
        bands={k: dict(v) for k, v in bands.items()},
        git=facts,
        elapsed_s=scanner.elapsed_s,
        state_dir=str(options.state_dir) if options.state_dir else None,
    )


def sweep(directory: Path, options: CostOptions) -> list[RepoCost]:
    """Every git repo directly under ``directory``, one scan for all of them.

    The per-record repo tally already covers every repo, so the scan runs once
    and each repo's share is read off it. Doing it per repo would re-read
    thousands of transcript files once per repo.
    """
    directory = Path(directory).resolve()
    repos = [d for d in sorted(directory.iterdir()) if (d / ".git").exists()]
    scanner = Scanner(
        None,
        directory,
        container=directory.name,
        since=options.since,
        until=options.until,
        split_dirs=options.split_dirs,
    )
    scanner.run(
        _roots([directory.parent, directory], options),
        options.state_dir,
        foreign=options.foreign,
    )

    precomputed = [
        (
            scan,
            sum(b["cost_micro"] for b in scan.usage.values()) / 1e6,
            sum(b["calls"] for b in scan.usage.values()),
            sum(active_hours(scan.timestamps).values()) / 3600,
        )
        for scan in scanner.sessions.values()
    ]

    generated = datetime.now(timezone.utc).isoformat()
    out: list[RepoCost] = []
    for repo_path in repos:
        try:
            facts = git_facts(
                repo_path,
                since=options.since,
                until=options.until,
                split_dirs=options.split_dirs,
                exclude=options.exclude,
            )
        except (RuntimeError, OSError):
            continue  # a broken repo must not stop the sweep
        roots_for_repo = repo_roots(repo_path)
        cost = calls = hours = 0.0
        unpriced: collections.Counter = collections.Counter()
        for scan, own_cost, own_calls, own_hours in precomputed:
            share = session_share(
                repo_path.name, scan.repo_records, scan.cwd, roots_for_repo
            )
            if share <= 0:
                continue
            cost += own_cost * share
            calls += own_calls * share
            hours += own_hours * share
            if scan.unpriced:
                unpriced["sessions"] += share
                for key in ("calls", "tokens"):
                    unpriced[key] += scan.unpriced[key] * share
        out.append(
            RepoCost(
                repo=repo_path.name,
                path=str(repo_path),
                since=options.since,
                until=options.until,
                generated=generated,
                cost_usd=cost,
                strict_usd=None,
                workspace_usd=None,
                tokens=None,
                calls=calls,
                sessions=None,
                hours=hours,
                coverage=coverage(facts.dates, scanner.first_seen),
                first_seen=scanner.first_seen,
                weeks=None,
                modules=None,
                models=None,
                sources=None,
                unpriced=dict(unpriced),
                bands=None,
                git=facts,
                elapsed_s=scanner.elapsed_s,
                state_dir=str(options.state_dir) if options.state_dir else None,
            )
        )
    out.sort(key=lambda r: -r.cost_usd)
    return out


def cache_path(state_dir: Path, repo: str) -> Path:
    return Path(state_dir) / "cost" / f"{repo}.json"


def write_cache(state_dir: Path, result: RepoCost) -> Path:
    """Write the cache, atomically.

    Other agents share this state directory and read the same path. An
    in-place truncate-and-rewrite lets a concurrent reader see a partial file,
    which ``read_cache`` swallows and reports as "no cached measurement" — a
    wrong answer rather than a slow one.
    """
    path = cache_path(state_dir, result.repo)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(result.to_dict(), indent=1, default=str))
    os.replace(tmp, path)
    return path


def read_cache(state_dir: Path, repo: str) -> dict | None:
    try:
        return json.loads(cache_path(state_dir, repo).read_text())
    except (OSError, ValueError):
        return None
