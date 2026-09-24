"""English terminal tables for a repo measurement.

Plain text on purpose, matching ``aegis.usage.render``: one column of labels
and one of numbers, so a terminal, a pipe and a test all read the same thing.
Charts and narrative live outside aegis.
"""

from __future__ import annotations

from aegis.cost.measure import RepoCost


def _usd(value: float) -> str:
    return f"{value:,.2f}"


def _num(value: float) -> str:
    return f"{value:,.0f}"


def repo_lines(result: RepoCost) -> list[str]:
    git = result.git
    code_written = sum(w.get("code", 0) for w in git.weeks.values())
    loc_code = sum(v.get("code", 0) for v in git.loc.values())
    partial = (
        "   PARTIAL: commits predate the oldest transcript "
        f"({(result.first_seen or '?')[:10]}); pass --since to compare repos"
        if result.coverage < 0.98
        else ""
    )
    lines = [
        f"{result.repo}  {result.path}",
        f"window {result.since or (git.first or '?')[:10]} .. "
        f"{result.until or (git.last or '?')[:10]}   "
        f"scanned in {result.elapsed_s:.0f}s",
        "",
        f"  cost (proportional)   {_usd(result.cost_usd)} USD",
        f"  cost (strict >= 0.8)  {_usd(result.strict_usd)} USD",
        f"  workspace total       {_usd(result.workspace_usd)} USD",
        f"  tokens                {_num(result.tokens.get('tokens', 0) / 1e6)} M",
        f"  model calls           {_num(result.calls)}",
        f"  sessions              {result.sessions:.1f}",
        f"  assisted hours        {result.hours:.0f}",
        f"  commits               {_num(git.n_commits)}",
        f"  code lines in tree    {_num(loc_code)}",
        f"  coverage              {100 * result.coverage:.0f}%{partial}",
        "",
    ]
    if result.cost_usd > 0:
        lines += [
            "unit cost",
            f"  per commit            {_usd(result.cost_usd / max(git.n_commits, 1))}",
            "  per 1k code lines     "
            f"{_usd(result.cost_usd / max(code_written, 1) * 1000)}",
            f"  per assisted hour     {_usd(result.cost_usd / max(result.hours, 1e-9))}",
            "",
        ]
    if result.modules:
        lines.append("cost by module")
        for module, value in list(result.modules.items())[:14]:
            lines.append(f"  {module:<28} {_usd(value):>12}")
        lines.append("")
    if result.unpriced:
        lines += [
            "unpriced work (counted, not charged)",
            f"  {result.unpriced.get('sessions', 0):.1f} sessions, "
            f"{_num(result.unpriced.get('calls', 0))} calls, "
            # Raw tokens, not millions: these counts are small by construction
            # and the whole point of the line is how much work is unaccounted
            # for, which "0 M" does not say.
            f"{_num(result.unpriced.get('tokens', 0))} tokens have no rate "
            "in the model registry",
            "  A session whose recorded model is a harness name (OpenCode) or "
            "absent (Gemini) cannot be priced.",
            "",
        ]
    lines.append("attribution bands (the error bar)")
    for band, row in result.bands.items():
        lines.append(
            f"  {band:<34} {int(row.get('sessions', 0)):>4} sessions"
            f"  own {_usd(row.get('cost', 0)):>12}"
            f"  attributed {_usd(row.get('attributed', 0)):>12}"
        )
    if git.excluded:
        lines += ["", "excluded from the git side: " + ", ".join(git.excluded)]
    lines += [
        "",
        "sources: "
        + (", ".join(f"{k} {_usd(v)}" for k, v in result.sources.items()) or "none"),
        "Dollars are API list-price equivalents, not an invoice.",
    ]
    return lines


def sweep_lines(rows: list[RepoCost]) -> list[str]:
    lines = [
        f"{'repo':<26}{'cost USD':>12}{'commits':>10}{'code':>10}"
        f"{'hours':>8}{'coverage':>10}"
    ]
    for row in rows:
        loc_code = sum(v.get("code", 0) for v in row.git.loc.values())
        lines.append(
            f"{row.repo:<26}{_usd(row.cost_usd):>12}{_num(row.git.n_commits):>10}"
            f"{_num(loc_code):>10}{row.hours:>8.0f}{100 * row.coverage:>9.0f}%"
        )
    total = sum(r.cost_usd for r in rows)
    lines += ["", f"total {_usd(total)} USD across {len(rows)} repos"]
    thin = [r for r in rows if r.coverage < 0.8 and r.git.n_commits > 20]
    if thin:
        floor = (rows[0].first_seen or "?")[:10]
        lines.append(
            f"coverage below 80% in {len(thin)} repos: their commits predate the "
            f"oldest surviving transcript ({floor}), so they look falsely cheap. "
            f"Re-run with --since {floor} to compare them: "
            + ", ".join(f"{r.repo} {100 * r.coverage:.0f}%" for r in thin[:12])
        )
    return lines
