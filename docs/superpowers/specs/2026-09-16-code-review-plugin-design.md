# The code-review plugin

> **Status:** design, 2026-09-16. Brainstormed with Alex the same day, after a
> study of alibaba/open-code-review (OCR). The study is in the workspace vault at
> `vault/+/agent_drafts/design_docs/2026-09-16-open-code-review-lessons-for-aegis.md`
> and the clone at `.playground/open-code-review/repo`, commit `e556dfa`.

An agent that has just implemented something should be able to ask aegis for a
review before it commits, get back a findings file it can act on, and know which
files were actually reviewed. Today `review_branch` (`src/aegis/workflows/review_branch.py`)
sends a free-text prompt to three reviewers and concatenates their replies. It
records no file or line, no severity and no coverage, and nothing checks the
findings.

OCR showed that a general agent reviewing in its own context skips files, drifts
on line numbers and varies with small prompt changes. Its answer is to let code do
every step that must not go wrong and let the model only review. This plugin
brings that design into aegis, built entirely on aegis pieces: workflows, spawned
agent profiles and plugin MCP tools. No OCR binary and no direct LLM calls.

## Scope

In v1:

1. Deterministic file selection with a typed reason for every exclusion.
2. Per-path review rules in layered YAML files, with a check command.
3. Deterministic grouping of files into review units.
4. One freshly spawned reviewer per group, reporting only through plugin tools.
5. Anchoring of findings by verbatim excerpt, computed by code.
6. A judge pass per group that removes findings only on narrow grounds.
7. A sealed coverage manifest and a run state derived from it.
8. `result.json` for the calling agent and `report.md` for a human.

Deferred: multiple review rounds, a plan phase, LLM-based grouping, LLM-based
re-anchoring, resume, telemetry, posting to pull requests, OCR's built-in
per-language rule docs (they are Apache-2.0 and can be added later as a built-in
rule layer without changing the format), and a mode where the calling agent reviews
by itself using the deterministic tools.

## How an agent uses it

The agent calls
`aegis_run_workflow("code_review", {"base": "main"}, from_handle=<own handle>)`.
The call returns at once. When the run ends, the result arrives in the agent's
inbox as a normal turn from `workflow:code_review`. The message is about ten lines:
run state, coverage counts, finding counts by severity, the absolute paths of
`result.json` and `report.md`, and the instruction to read `result.json` in full
rather than through `head` or `tail`. The full JSON never goes into the message,
so a large review does not fill the caller's context.

Workflow arguments:

| argument | default | meaning |
|---|---|---|
| `mode` | `branch` | `workspace` (`git diff HEAD` plus untracked files), `branch` (`merge-base(base)..HEAD`), `commit` |
| `base` | `main` | base ref for `branch` mode |
| `commit` | none | the commit for `commit` mode |
| `paths` | none | limit the run to these paths or globs |
| `background` | `""` | what the change is for, passed to reviewers |
| `include_removed` | `false` | keep judge-removed findings in `result.json`, flagged |

## Layout

```
plugins/code-review/
  plugin.toml
  _install.py          # writes .aegis/review/rules.yaml stub, adds two agent profiles
  _uninstall.py
  code_review/
    diffsource.py      # pure: run git, parse the diff into files and hunks
    selection.py       # pure: select(files, config) -> list[Decision]
    rules.py           # pure: layered rule resolution with provenance
    grouping.py        # pure: files -> groups
    anchoring.py       # pure: excerpt + hunks -> line range
    manifest.py        # pure: register, seal, mark, finalize
    report.py          # pure: result.json and report.md
    prompts/           # reviewer.md, judge.md, nudge.md
    store.py           # run directory access
    tools.py           # @tool review_comment, review_done, review_verdict, review_rules_check
    workflow.py        # @workflow code_review
```

The pure modules import nothing from aegis and are tested without a daemon. Only
`store.py`, `tools.py` and `workflow.py` touch aegis.

The installer adds two profiles to `.aegis.yaml`, `code-reviewer` and
`review-judge`. The user points them at any harness. A cheap setup is lovelaice
with Qwen3 32B for both.

## Run flow

1. **Read the diff.** `diffsource.py` runs git with `--no-ext-diff --no-textconv
   -c core.quotepath=false` and parses per-file hunks. Untracked files in
   `workspace` mode become synthetic all-added diffs. A git failure ends the run
   as `failed` before any agent is spawned.
2. **Select.** `selection.select` returns one decision per file. No reviewable
   file ends the run as `skipped`.
3. **Resolve rules and group.** Every reviewable file gets its rule from
   `rules.resolve`. `grouping.group` builds groups. Every reviewable file is
   registered in the manifest, and the manifest is sealed.
4. **Check profiles.** A missing `reviewer_profile` or `judge_profile` fails the
   run before anything is spawned.
5. **Review.** For each group, at most `max_parallel` at a time, the workflow spawns
   a reviewer, writes `{handle: group_id}` to `assignments.json` before the first
   prompt, and sends the reviewer prompt. The reviewer reads code with its own
   harness tools, reports with `review_comment` and closes with `review_done`.
6. **Wait for completion.** A turn boundary is not completion (DESIGN.md). After
   each `engine.send` returns, the workflow checks the store for the group's
   `review_done` record. If there is none, it sends `prompts/nudge.md`, up to
   `max_nudges` (2) times. A group that runs out of nudges or passes
   `group_timeout_s` (900) is closed as `failed`; its findings stay.
7. **Judge.** For each group with at least one anchored or refiled finding, and
   with `judge` enabled, the workflow spawns a judge, records it in
   `assignments.json` and sends the judge prompt. The judge calls `review_verdict`
   once. Unanchored findings skip the judge and are flagged in the output.
8. **Finish.** The manifest is finalized, `result.json` and `report.md` are
   written atomically (temp file, fsync, rename), every spawned agent is closed and
   the summary is returned.

Cancelling the workflow closes the agents (the engine already tracks them) and
finalizes the manifest from whatever is on disk.

## The run directory

`<state_dir>/review/<run_id>/`, where `state_dir` comes from the roots, never from
the process cwd:

| file | written | holds |
|---|---|---|
| `run.json` | once, at start | arguments, mode, resolved refs, sha256 of the diff |
| `manifest.json` | atomically, on each change | selected set, sealed flag, per-file state and reason |
| `assignments.json` | atomically, before each prompt | handle to group id, and role (`reviewer` or `judge`) |
| `findings.jsonl` | appended by `review_comment` | one record per finding, with anchor result |
| `groups.jsonl` | appended by `review_done` and the workflow | group closures |
| `verdicts.jsonl` | appended by `review_verdict` | judge analysis and removed ids |
| `result.json`, `report.md` | at the end | the output |

Readers skip damaged JSONL lines. v1 has no resume, but a stopped run can be read.

## Selection

`select(files, config) -> list[Decision]`, a pure function. The run and any later preview
call this one function, so they cannot disagree. A decision is `reviewable` or
carries one reason from a closed set. Gates run in this order:

1. `binary`: a NUL byte in the first 8000 bytes.
2. `secret`: `.env*` except `.env.example`, `.env.sample`, `.env.template`;
   `.ssh/**`; `id_rsa*`, `id_ed25519*`; `*.pem`; `*.key`; `.netrc`; `.npmrc`;
   `.pypirc`.
3. `vendored`: any path segment in `node_modules`, `vendor`, `.venv`, `venv`,
   `dist`, `build`, `target`, `.git`.
4. `deleted`.
5. `user_exclude`.
6. `user_include` short-circuits to reviewable.
7. `generated`: `uv.lock`, `package-lock.json`, `pnpm-lock.yaml`, `yarn.lock`,
   `*.lock`, `*.min.js`, `*.min.css`, `*_pb2.py`, `*.pb.go`, `*.generated.*`,
   `*.snap`.
8. `too_large`: the file's diff exceeds `max_file_diff_chars` (60000).

A user include never re-admits gates 1 to 3, so a broad include rule cannot send a
`.env` file to a model. Sizes are measured in characters so the plugin needs no
tokenizer.

## Rules

`.aegis/review/rules.yaml`:

```yaml
include: ["tests/**"]
exclude: ["docs/generated/**"]
rules:
  - path: "src/aegis/mcp/**"
    rule: "Every tool that takes from_handle must resolve the caller token first."
  - path: "**/*.py"
    rule: .aegis/review/python.md
```

- **Layers**, highest first: the project file `<config_root>/.aegis/review/rules.yaml`,
  then the user file `~/.config/aegis/review/rules.yaml`. The first layer with a
  matching glob wins. Inside a layer the first matching rule wins. There is no
  merge in v1.
- **Include and exclude** come from the highest layer that sets either list.
- **Globs** use `**` semantics and match the repo-relative path.
- **Rule values.** A value is a file path when it is one line, has no whitespace and
  ends in `.md` or `.txt`. The file must resolve inside the repo for the project
  layer (checked after resolving symlinks) and be under 256 KB. Anything else is
  inline text.
- **Provenance.** `resolve(path)` returns `(rule_text, layer, pattern)`.
  `review_rules_check(paths)` returns that triple per path, so a user or agent can
  see which rule applies and why.

A malformed rules file fails the run before spawning, naming the file and the error.

## Grouping

Deterministic, no LLM:

1. If the run has fewer than 4 reviewable files and under 200 changed lines, all
   files form one group.
2. Otherwise files are bucketed by parent directory.
3. A bucket over `max_files_per_group` (8) or `max_group_chars` (80000) of diff is
   split into chunks in path order.
4. A bucket with one file and under 200 changed lines merges into the nearest
   sibling bucket (longest common path prefix, ties by path order) when that stays
   under both limits.

Group ids are `g-1`, `g-2`, … in the order of each group's first path.

## Reviewer prompt

`prompts/reviewer.md`, sections in fixed order, empty sections removed whole:

1. Role and bar: report defects a maintainer would fix, prefer precision to recall,
   no style comments unless a rule asks for them.
2. The group's diffs, each as `<file path="...">…</file>`.
3. The other changed files in the run, paths only, for context.
4. Rules. Files with identical rule text share one `<rules for="a, b">` block, and
   blocks are sorted, so the prompt prefix stays byte-stable for provider caching.
5. `background`, when given.
6. Protocol: check every `<file>` in the group; report only through
   `review_comment`, quoting `existing_code` verbatim from the diff; call
   `review_done` when every file has been checked.

## Tools

All four take `ctx: ToolContext` (see core changes). None takes a run id or group
id from the model. The group comes from `ctx.caller_handle` looked up in the
`assignments.json` of the active runs; a handle that is not assigned gets an error
naming the problem.

### `review_comment(comments: list[Comment])`

`Comment` fields: `path`, `existing_code`, `content`, `severity` (`critical`,
`high`, `medium`, `low`), `category` (`bug`, `security`, `concurrency`,
`error-handling`, `performance`, `maintainability`, `other`), optional
`suggestion`.

For each comment the tool:

1. Normalizes: unknown severity becomes `low`, unknown category becomes `other`, the
   path is made repo-relative. A comment with no path or no content is dropped with
   a per-item message.
2. Anchors it (next section).
3. Appends it to `findings.jsonl` with a run-wide id `c-N`, the group id and the
   reviewer handle.
4. Replies per item: `c-3 anchored src/x.py:40-44`, `c-4 unanchored: excerpt not
   found in any file of the diff`, `c-5 refiled to src/y.py:10-12`.

The reply lets the reviewer fix an excerpt in the same session. An unanchored
finding is kept, never dropped.

### `review_done(status: "done" | "failed", note: str = "")`

Appends the group closure. `done` marks every file of the group `reviewed`.
`failed` marks files that have at least one finding `reviewed` and the rest
`failed`. A second call for the same group returns an error and changes nothing.

### `review_verdict(analysis: str, remove_ids: list[str])`

Only for a handle assigned as `judge`. `analysis` is declared first so the model
writes its reasoning before choosing ids; OCR records that with the order reversed
the model picked ids first and could not retract them. Every id must exist and
belong to the judge's group, otherwise the whole call is rejected with the bad ids
listed. An empty list approves all. A second call returns an error.

### `review_rules_check(paths: list[str])`

Returns `{path, layer, pattern, rule_excerpt}` per path. It does not need an
assignment.

## Anchoring

`anchor(excerpt, path, files) -> Anchor`, pure, over the parsed hunks.

1. Normalize lines: strip surrounding whitespace, strip a leading `+`, `-` or space
   diff marker, drop blank lines.
2. In the named file, search for a consecutive match in each hunk's new side
   (context plus added lines), then each hunk's old side (context plus deleted
   lines), then the full new file content. The first hit gives `anchored` with
   start and end lines.
3. Otherwise probe every other file of the diff on a copy. Exactly one hit moves
   path and lines together and gives `refiled`. Zero or several hits leave the
   comment where it was.
4. Otherwise `unanchored`, with no lines.

## Judge

`prompts/judge.md` receives the group's diffs and its anchored and refiled findings
as `c-N` records with path, lines, category, severity, content and excerpt. It
states:

- Removing a real finding costs more than keeping a false one. Approval is the
  default.
- A finding may be removed on two grounds only. (A) The quoted code is not in the
  file's diff. (B) One specific diff line literally contradicts the claim, in a
  single inference step.
- Findings in `security` or `concurrency`, and any finding about a behaviour change,
  are never removed.

A judge that errors, times out or never calls `review_verdict` removes nothing.
Removed findings stay in `findings.jsonl` marked `removed_by_judge` with the
analysis, and leave `result.json` unless `include_removed` is set.

## Manifest

`register(path)` for each reviewable file, then `seal()`. `register` after `seal`
raises. `mark(path, state, reason)` sets `reviewed`, `failed` or `skipped`;
excluded files are recorded as `skipped` with their selection reason and are not
part of the selected set. `finalize()` turns every unmarked selected file into
`failed`. The run state comes only from those sets:

- `complete`: every selected file is `reviewed`;
- `partial`: at least one `reviewed` and at least one `failed`;
- `failed`: none `reviewed`, or the run failed before review;
- `skipped`: nothing was selected.

## Output

`result.json`:

```json
{
  "schema_version": 1,
  "run_id": "01K...",
  "status": "partial",
  "mode": "branch", "base": "main", "head": "<sha>", "merge_base": "<sha>",
  "coverage": {
    "selected": 12, "reviewed": 11, "failed": 1, "skipped": 3,
    "files": [{"path": "src/x.py", "state": "reviewed", "reason": null}]
  },
  "findings": [{
    "id": "c-3", "group": "g-1", "path": "src/x.py", "start_line": 40, "end_line": 44,
    "anchor": "anchored", "severity": "high", "category": "bug",
    "content": "...", "existing_code": "...", "suggestion": null
  }],
  "groups": [{"id": "g-1", "files": ["src/x.py"], "reviewer": "<handle>",
              "state": "done", "note": ""}],
  "warnings": []
}
```

Findings are sorted by severity, then path, then start line. `report.md` renders the
same data: summary, coverage table, findings by file.

## Configuration

Under `plugins.code-review` in `.aegis.yaml` (see core change 3):

| key | default |
|---|---|
| `reviewer_profile` | `code-reviewer` |
| `judge_profile` | `review-judge` |
| `judge` | `true` |
| `max_parallel` | `4` |
| `max_files_per_group` | `8` |
| `max_group_chars` | `80000` |
| `max_file_diff_chars` | `60000` |
| `group_timeout_s` | `900` |
| `max_nudges` | `2` |

## Changes to aegis core

These are part of this work and land before the plugin.

### 1. `ToolContext` for plugin tools

Plugin tools today receive only the model's arguments. They cannot see the
instance's roots, so `plugins/memory-system/memory_system.py` resolves paths with
`Path.cwd()`, against the three-roots rule, and they cannot see who called.

A plugin `@tool` may declare one parameter annotated `aegis.tools.ToolContext`.
`_register_user_tool` (`src/aegis/mcp/server.py`) removes it from the signature
FastMCP turns into a schema and injects it on every call:

```python
@dataclass(frozen=True)
class ToolContext:
    state_dir: Path
    config_root: Path
    harness_cwd: Path
    caller_handle: str | None      # from aegis.mcp.identity.resolve_caller; None if unverifiable
    plugin_config: Mapping[str, Any]  # this plugin's section, see change 3
```

Tools that do not declare the parameter are unchanged. `invoke_tool`
(`src/aegis/tools/runner.py`) must not log the context in its kwargs record.

### 2. `ToolFailureStreakMiddleware`

Agents retry a broken call with the same arguments. This is true of every aegis
tool, so the fix lives in the core, next to `CommsMiddleware`
(`src/aegis/comms/middleware.py`), which is already the one point every MCP call
passes through.

- A counter per (caller, tool name), in memory. The caller comes from
  `resolve_caller`, falling back to the `from_handle` argument. A success resets
  the counter.
- A failure is a raised exception, an MCP result with `isError`, or a dict result
  with an `error` key.
- On the 2nd consecutive failure the middleware appends to the error: "This is the
  2nd consecutive failure of <tool>. Change the arguments instead of repeating the
  call."
- From `threshold` (3) on it appends: "<tool> has failed <N> times in a row for you.
  Stop retrying it; continue without it or report the problem."
- It never blocks a call. OCR stops executing the tool, but aegis tools such as
  `aegis_monitor` or `aegis_claim` are operational, and refusing them could strand
  work. Only the text the model reads changes.
- Config: `tool_failure_streak: {enabled: true, threshold: 3}`.
- It covers aegis MCP tools only, not a harness's own tools such as Claude Code's
  `Bash`. The equivalent for lovelaice belongs in its loop and is out of scope.

### 3. Plugin configuration sections

`load_config` (`src/aegis/config/yaml_loader.py`) keeps only known top-level keys, so the `memory:` section that
`memory-system`'s installer writes is dropped, and a workflow sees only its
`configure()` defaults plus run kwargs (`src/aegis/workflow/runner.py`).

Add a top-level `plugins:` mapping to `.aegis.yaml`, kept on `AegisConfig` as
`plugins: dict[str, dict]`, keyed by the manifest name. A plugin reads its section
through `ToolContext.plugin_config` in tools and through
`engine.plugin_config(name)` in workflows. Defaults from `plugin.toml`'s
`[default_config]` sit under the file's values. Unknown plugin names are kept, not
rejected, because a section may exist before the plugin is installed.

### 4. `memory-system` uses `ToolContext`

Replace `_project_root()`'s `Path.cwd()` with `ctx.config_root`, and read its
section from `plugins.memory-system`, migrating the old top-level `memory:` key on
install. This is existing debt, fixed here because the mechanism is the same.

## Error handling

| situation | result |
|---|---|
| git fails, bad ref | run `failed`, error in `warnings`, nothing spawned |
| rules file malformed | run `failed`, file and error named, nothing spawned |
| profile missing | run `failed`, nothing spawned |
| reviewer never calls `review_done` | nudged `max_nudges` times, then group `failed`, findings kept |
| group timeout | group `failed`, agent closed, findings kept |
| judge fails or never calls `review_verdict` | nothing removed, warning added |
| tool called by an unassigned handle | error returned to the caller, nothing written |
| damaged JSONL line | skipped on read, warning added |
| workflow cancelled | agents closed, manifest finalized from disk, output written |

## Testing

- Pure modules, with fixture diffs:
  - selection: every gate, gate order, include cannot re-admit a secret;
  - rules: layer precedence, first match in a layer, path values, the repo
    boundary and size cap;
  - grouping: the small-run rule, the limits, sibling merge, stable ids;
  - anchoring: new side, old side, full file, unique refile, ambiguous refile, no
    match, whitespace and diff-marker normalization;
  - manifest: register after seal raises, finalize sweeps to `failed`, each derived
    state.
- Tools, with a fake `ToolContext` and a temp run directory: attribution by handle,
  unassigned handle rejected, foreign ids rejected by `review_verdict`, second
  `review_done` rejected.
- Core: `ToolContext` injection hidden from the schema; the middleware's three
  failure kinds, escalation text and reset on success; `plugins:` survives
  `load_config`.
- Workflow, with a fake engine: reviewers that do and do not call `review_done`,
  timeout, judge removal, judge failure.
- **Done means an end-to-end run.** A test repo with a planted bug (an off-by-one in
  a loop bound) on a branch. An agent in the TUI, attached to a daemon started after
  the change, calls `code_review`. The check reads `result.json` and fails unless a
  finding is `anchored` on the line range of the planted bug. Break the check on
  purpose once, by pointing it at a wrong line, and confirm it fails.

## Open after v1

- Resume, keyed by a hash of the diff and the rules config, refusing on mismatch.
- A delegate mode: the calling agent reviews by itself with the same tools and
  manifest.
- OCR's per-language rule docs as a built-in rule layer.
- Replacing the built-in `review_branch` with this plugin, or keeping both.
