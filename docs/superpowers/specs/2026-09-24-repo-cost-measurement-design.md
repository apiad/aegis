# Measuring what a repo cost to build — design

**Status:** implemented 2026-09-24. `src/aegis/cost/`, `aegis usage repo`,
`aegis usage repos`, `aegis_repo_cost`. Plan and its rulings:
`docs/superpowers/plans/2026-09-24-repo-cost-measurement.md`.

`aegis usage` answers "what did my sessions cost, by month, by model, by tool".
It cannot answer "what did *this repository* cost to build", because an agent
session carries no project label and because the question needs git as well as
tokens. This spec adds that second cut.

The arithmetic already exists and is in production: `bin/dev-cost-report` in the
Workspace repo produced the 35-page cost report that `repos/une-tools` ships to
its funders. What follows moves its engine into aegis and leaves its narration
outside. The five traps that tool paid for are restated here, with a sixth this
work found, because a
reimplementation that loses them is a regression even with a green suite.

## Why this belongs in aegis

aegis owns the primary data. Three of the four transcript stores are its own
(`<state>/sessions/`, `<state>/backfill/`, `<state>/claude-import/`), it already
maintains the price registry the arithmetic needs (`aegis.models.get_prices`),
and it already ships a cost command group. The one signal that would make this
unnecessary does not exist: `DigestCollector` records which repos a turn wrote
to, but only in memory for the recap and the loop judge, and nothing persists it
to the session log. Attribution over the history therefore has to be inferred,
which is what the next section describes.

## What it measures

**Cost.** Token counts priced against the model registry, reported as
API-list-price equivalents. The invoice is a flat-rate subscription, so the
dollar figure exists to compare weeks and modules against each other, and to
price the same work somewhere that does pay per token.

`Result.cost_usd` is not read at all. An earlier draft of this spec asked for it
beside the token-priced figure as a cross-check; that was dropped during
implementation, because a column present for claude-code sessions and blank for
the two foreign stores, whose per-turn-versus-cumulative meaning also differs by
harness, is a second number a reader must be taught to distrust. `docs/usage.md`
explains the gap between `aegis usage` and `aegis usage repo` in prose instead,
and the cross-check that actually ran was against the predecessor tool: 0.14% on
`aegis` over sixteen weeks, recorded in the workspace know-how.

**Volume.** Commits, churn per module, and lines in the tree today, each
classified into code, prose, data and binary.

**Coverage.** The share of the repo's commits that fall inside the window the
transcripts actually cover. This is a first-class output, not a footnote.

**Out of scope.** GitHub issues and pull requests stay outside aegis: `gh` is an
external binary aegis invokes nowhere else, and the open-work section is part of
the report, not part of the measurement. Charts, narrative prose and Spanish also
stay outside. aegis emits a table and JSON.

## Attribution: locality

For each record in a transcript, note which repositories it mentions. A session's
share of a repo is the fraction of its repo-mentioning records that name that
one. A session whose working directory is already inside the repo counts whole.

The distribution comes out almost binary in practice, which is what makes the
method defensible. Measured on `une-tools` over sixteen weeks: of $24,946 of
workspace-wide spend, sessions at share 0 held $21,104 and sessions at share
≥ 0.8 held $3,310. The genuinely mixed sessions contributed $302 attributed,
about 9% of the repo's total.

Two rules run on every measurement and both are reported: **proportional**, where
every session contributes its share, and **strict**, where only sessions above
0.8 count and count whole. The band between them is the error bar. It was 3.2% on
`une-tools` and 7.5% on `aegis`. A report that hides it invites the question it
cannot answer.

## Reading the four stores

| store | format | usage lives on | dedup key |
|---|---|---|---|
| `~/.claude/projects/**/*.jsonl` | claude-code transcript | `message.usage` per assistant message | `message.id` |
| `<state>/claude-import/*.jsonl.gz` | same, gzipped | same | same |
| `<state>/sessions/*.jsonl`, `<state>/backfill/*.jsonl`, claude-code | aegis event log | `AssistantText`/`AssistantThinking`/`ToolUse` `.usage` | `message_id` |
| the same two, ACP and lovelaice | aegis event log | `Result.usage` only | `(file stem, result index)` |

`~/.claude/projects` is a **foreign** source: aegis did not write it and does not
own its retention. It is read by default, because excluding it would turn the
pre-aegis history into a silent zero, and `--no-foreign` excludes it. Its rows
are labelled as foreign in the source breakdown.

**Per session, exactly one path runs.** If any per-message event in an aegis
session log carries a non-null `usage`, the session is read by message.
Otherwise it is read by `Result`. Mixing them would double-count a claude-code
session, whose `Result` repeats what its messages already reported.

Both paths price tokens the same way, through `aegis.models.get_prices`. Neither
path derives cost from `cost_usd`. The ACP `Result` carries a `cost_usd` whose
cumulative-versus-per-turn semantics differ by harness, and guessing wrong is a
silent multiplier.

The aegis event stream does not split 5-minute from 1-hour cache writes.
The 1-hour share is taken from the claude-code rows already scanned in the same
run rather than assumed, and a 1-hour write is priced at twice the 5-minute
rate.

## The six traps

Each one is a defect that shipped once. Each gets a test that fails without the
fix.

**1. A substring test on `cwd` inflates everything.** With `repo="aegis"`,
`"aegis" in cwd` matches `/tmp/aegis-shot-a1b2`, `.aegis/state`, and every
scratch directory with that name in it. It put 1,679 sessions on aegis where the
real number was 394. Compare resolved path components, and treat `…-wt-*`
worktrees as the repo.

**2. `str.splitlines()` eats record separators.** Parsing `git log` with a `\x1e`
delimiter and `splitlines()` returns zero commits, silently, because Python also
breaks lines on `\x1c`–`\x1e`. Use `\x01` and `split("\n")`.

**3. Git counts binaries as lines.** Two PDFs checked into `docs/` were 62,074
"lines added" in one week, which made that week the most productive of the
project at $0.49 per thousand lines. Classify every path before adding anything
up, and never quote the raw `--numstat` total.

**4. Cost only exists from the oldest surviving transcript.** Git counts every
commit a repo ever had. Run a sweep without a window and the two halves answer
different questions, so the oldest repos come out looking free: 93% of `beaver`'s
commits, 85% of `auditorium`'s and 99% of `deepdatatech`'s fell outside the
2026-05-29 floor. The floor is computed from the transcripts actually read, not
remembered as a flag, and a repo under 80% coverage is flagged in its own output.

**5. A report that lives in the repo counts itself.** The second run after
committing one adds its own 498 lines of prose to the module table, and the body
text stops agreeing with the annex. `--exclude <glob>` drops paths from the git
side and the output records what was dropped.

**6. An ACP session prices at zero and says nothing.** OpenCode's `AssistantText`
carries a `message_id` but `usage: null`. A per-message reader takes the dedup
key, adds zeros, and moves on. Measured on this workspace's store 2026-09-24: 6
of 780 sessions are ACP, and all 6 price at zero under the `bin/dev-cost-report`
reader. The `Result` path above is the fix for the token counts.

Pricing them is a second half, and it forced a change to this spec during
implementation. Prices are keyed by provider, and there is no `gemini` model
under `claude-code`, so collapsing a model to an opus/sonnet/haiku/gemini family
and always asking `claude-code` for the rate returns nothing for a Gemini
session. Every call is therefore priced by its session's own
`SessionMeta.provider` and its recorded model name, which `resolve_prices`
already resolves through an exact name, an alias, then a family substring within
that provider.

That still leaves calls with no rate at all, because the recorded model is not
always a model: OpenCode writes `model: "OpenCode"`, the harness's own name, and
the one Gemini session in this store writes none. Their tokens are counted and
reported under **unpriced work**, never charged. Both alternatives are silent
and both are wrong — zero makes real work look free, and falling back to the
default model charges a Gemini turn at Opus rates.

## Commands

```
aegis usage repo <path>            # one repo: cost, volume, coverage, bands
aegis usage repo <path> --json     # the full structure, for a downstream report
aegis usage repos <dir>            # every git repo directly under <dir>, one table
```

Shared options: `--since`, `--until`, `--no-foreign`, `--exclude <glob>`
(repeatable), `--split <dirs>` for monorepo containers, `--extra-root <dir>` for
another machine's transcripts.

The repo is named by path. aegis has no `repos/` convention and three roots that
do not coincide, so inferring a repo directory from a bare name would resolve
against the wrong one when embedded. `aegis usage repos` takes its directory the
same way.

`--json` writes to stdout and also caches to
`<state>/cost/<repo-name>.json`, which is what the MCP tool reads.

## The MCP tool

`aegis_repo_cost(repo, from_handle, refresh=False)` returns the cached summary
for a repo plus the age of that cache in hours. A full sweep reads 3,672
transcript files in 60 seconds of single-threaded CPU on zion, which is too long
for a call that blocks an agent's turn. `refresh=True` recomputes and pays the
minute. An agent asking "what have I spent here" is well served by this
morning's figure; one that needs the current figure can ask for it.

The tool is read-only and returns `{"error": ...}` with the path it looked for
when no cache exists and `refresh` is false.

## What stays outside

`bin/dev-cost-report` in the Workspace repo keeps its name and its job, and
becomes a narrator: it shells out to `aegis usage repo --json`, and keeps the
Spanish `report.md`, the generated SVG charts, and the GitHub section. It loses
its independence from aegis, which is acceptable because aegis is installed on
every host that runs it.

## Consequences

`aegis usage` and `aegis usage repo` will not report the same dollars for the
same window, and the docs must say why rather than let a reader discover it.
`aegis usage` reports claude-code's own billed `cost_usd` over
`<state>/sessions/` alone. `aegis usage repo` reports token math at list price
over four stores. The gap is the two foreign stores plus the pricing method, and
`docs/usage.md` carries that
explanation in its own paragraph, which is what makes the gap inspectable
instead of alarming.
