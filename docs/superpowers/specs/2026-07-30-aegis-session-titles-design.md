---
title: Session titles, and a one-shot generation seam
date: 2026-07-30
status: slice 1 implemented (2026-08-07); slices 2-4 outstanding
plan: docs/superpowers/plans/2026-08-07-aegis-session-titles-slice1.md
---

# Session titles, and a one-shot generation seam

Ten open tabs read `lucid-knuth`, `deep-dijkstra`, `honest-hellman`. The
0.28 arc made many tabs *fast*; it did not make them *legible*. You still
cannot tell which one is fixing the eviction race.

The current answer is a workaround, and it lives in the workspace
CLAUDE.md: *"rename yourself once the session's purpose has settled."* It
asks the agent to spend a thought on labelling, it is skipped whenever
the agent is busy — which is exactly when tabs pile up — and it
overloads a rename with a job renames should not have.

**The handle is identity.** It is `from_handle` on every MCP call, the
inbox routing key, and half of the immutable log id. A title is a label.
Conflating them is the actual bug.

So: add a title *beside* the handle. The handle keeps doing what it does
today. Nothing about naming, routing, or provenance changes.

## The model

A session carries an optional `title` and a `title_source`:

| source | set by | beats |
|---|---|---|
| `auto` | first-turn generation | nothing |
| `agent` | `aegis_title`, or `aegis_rename(title=…)` | `auto` |
| `human` | `/title <text>` | everything |

Precedence is strictly `human > agent > auto`, and it is the whole
concurrency story: a write is applied only if its source outranks (or
equals) the current one. A slow generation whose result lands after the
operator typed `/title` is discarded on arrival because `auto` cannot
overwrite `human`. No request ids, no in-flight bookkeeping.

t3code solves the same problem with `canReplaceThreadTitle`, comparing
the current title by *value* against a seed the client set optimistically.
That works, but it infers intent from a string. Recording the source is
smaller and says what it means.

## Where it lives

`SessionMeta` is already the mutation record — a rename appends a second
one carrying the new name rather than rewriting anything (see AGENTS.md
on the log-id invariant). A title change is the same shape: append a
`SessionMeta` with the new title.

```python
@dataclass(frozen=True)
class SessionMeta:
    handle: str
    profile: str
    provider: str
    cwd: str
    created_at: str
    origin: str
    preview: str = ""
    title: str = ""          # new
    title_source: str = ""   # new — "" | auto | agent | human
```

Both default to `""`, so `state/event_codec.py`'s legacy-record decode
keeps loading older logs unchanged. Reading a log folds SessionMeta
records in order; last-write-wins gives the current title, which is also
what `history.py` needs for the `Ctrl+R` rows — the history modal gets
readable rows for free, which is arguably worth more than the tab bar.

## The driver seam

Titles need a cheap one-shot structured call. That is not a session, and
it should not be modelled as one. It is a second, much smaller capability
on the driver:

```python
class HarnessDriver(abc.ABC):
    supports_oneshot: bool = False

    async def generate(self, agent: Agent, cwd: str,
                       schema: type[BaseModel],
                       *instructions: str) -> BaseModel | None:
        """One-shot structured generation. No session, no MCP, no tools.

        Returns None on any failure — callers treat this as best-effort.
        """
        return None
```

Every driver already has a mechanism, verified on zion 2026-07-30:

| driver | mechanism | schema? |
|---|---|---|
| `claude-code` / `claude-sdk` | `claude -p --output-format json --json-schema <schema>` | ✅ native |
| `gemini` | `gemini -p -o json` | ❌ no `--json-schema` — prompt + tolerant parse |
| `opencode` | `opencode run --format json` | ❌ same |
| `lovelaice` | `lingo.Engine.create(context, model, *instructions) -> T` | ✅ Pydantic |

`lingo` is already in the venv transitively via `lovelaice>=2.11`, so the
lovelaice path costs no new dependency, and the claude path costs none
either — it is the binary aegis already spawns.

Two consequences worth stating rather than discovering:

- **The unschema'd drivers need a tolerant parser.** Ask for JSON, accept
  fenced JSON, accept a bare object embedded in prose, give up and return
  `None`. Shared helper, not per-driver.
- **`lingo.Engine.create` has a known failure mode on reasoning models.**
  It returns `parsed=None` when the model emits its JSON into the
  reasoning channel rather than the content channel — observed on LM
  Studio. Since the whole point of the lovelaice driver is pointing at
  local models, this is a live path, not a hypothetical. Fall back to
  `LLM.chat` plus the same tolerant parse.

### Which model generates

`.aegis.yaml` gains an optional top-level key:

```yaml
text_generation: haiku      # an agent-profile name
```

Unset → use the session's own profile. Set → use that profile's harness
and model. This is the knob that keeps a title from costing Opus tokens,
and it generalises: the same seam is the obvious home for generated
commit subjects and branch names later (t3code drives all four from one
service), which is the reason to shape it as `generate(schema, …)` rather
than `generate_title()`.

## Surfaces

**`/title <text>`** — operator sets it, `source=human`. Lands in
`commands/builtins/session_ctl.py` beside `/rename`, `/close`,
`/themes`, `/clear`, and reaches both frontends through `dispatch()`
like every other command. Bare `/title` regenerates (back to
`source=auto`), which is also how you undo a bad manual one.

**`aegis_title(from_handle, title)`** — an agent titles itself,
`source=agent`. Refused if the current source is `human`, and the refusal
says so rather than failing silently.

**`aegis_rename(old_handle, new_handle, title=None)`** — the existing
tool gains an optional `title`. Applied only when the current source is
not `human`. This is the self-naming path: an agent that has decided what
it is doing can set both at once, and the rename semantics are exactly
what they are today when `title` is omitted.

## Generation

Fires **once, after the first turn's `Result`**, from the opening user
message. Not on every turn — a title that churns is worse than a handle
that doesn't.

The prompt is t3code's, which is well-tuned and worth copying rather than
reinventing: *"You write concise thread titles for coding conversations.
Summarize the user's request, not restate it verbatim. Keep it short and
specific (3-8 words). Avoid quotes, filler, prefixes, and trailing
punctuation."*

Their regeneration variant differs meaningfully and is the right basis
for bare `/title` later: it is handed the previous title, told to
summarize *the thread's current state, not its initial request*, told to
return something different, and — the good detail — truncates the thread
from the **front** when it doesn't fit, because current state lives at
the end.

### Sanitize; never trust the model

Take t3code's `sanitizeThreadTitle` wholesale: first line only, strip
surrounding quotes and backticks, collapse whitespace, empty → fall back,
over-length → truncate on a word boundary with an ellipsis. A model that
ignores every instruction still yields a usable tab label. Cap shorter
than their 50 — the tab bar is a sideways-scrolling single line and the
cell already carries a state dot, an index, the handle, the slug, and a
muted suffix.

### Failure is a log line

Generation failing must never touch the conversation. Catch everything,
log once, leave the title unset. The session is unaffected and the tab
reads as it does today.

## TUI — resolved 2026-08-07: **the title is not in the tab bar**

*(This section originally proposed the tab-bar `suffix` slot, on the
premise that `QueueManager.worker_label` owned it. That premise expired
when the live task list shipped on 2026-08-06: `_tab_suffix`
(`tui/app.py:54-65`) now composes plan roll-up, worker label **and**
`@host` into that one slot. Superseded by the measurement below.)*

Rendering four realistic `_TabCell`s at the Ink palette:

| layout | total width |
|---|---|
| today, no title | **127 cells** |
| title appended to the suffix | 210 cells (+65%) |
| title replacing `·slug·` | 190 cells (+50%) |

Four tabs already overflow a 120-column terminal *before* any title. The
tab bar is not short of a good place to put a title; it is short of room.

**So the title lives in the status bar**, which shows only the *active*
session — one title instead of N — and already has the machinery for it:
a priority ladder that degrades segments until the line fits
(`aegis.tui.fit`). The title enters as `P_TITLE = 25`, above `P_IDENTITY`
(what a session is doing beats which model it is doing it with) and below
`P_METRICS`, which follows the bar's own rule — *on a narrow terminal you
lose what never changes and keep what does*, and a title never changes.
At 80 columns it is dropped along with the identity segment; that is
correct, not a bug.

`Ctrl+R` is the other surface, and the more valuable one: a whole line
per row, so the title simply replaces the preview when present, and the
filter matches on it.

## Slices

1. ✅ **Storage + manual set.** *(shipped 2026-08-07)* `SessionMeta.title` /
   `title_source`, the precedence rule, `/title <text>`, `aegis_title`, the
   `title=` param on `aegis_rename`, status-bar and `Ctrl+R` rendering (not
   the tab bar — see *TUI*). **No generation at all.** Done when a
   human-set title survives a restart and an agent's `aegis_title` is
   refused against it: both hold, and both took a bug fix to make true.
2. ✅ **The seam.** `supports_oneshot` + `generate()` + the tolerant parser,
   implemented for `claude-code` first, with `text_generation:` config.
   **Already shipped — by `/btw`, not by this feature.** `drivers/base.py:77,113,127`,
   `drivers/oneshot.py` (`parse_structured`, `Generation`),
   `drivers/claude.py:232,288`, `config/yaml_loader.py:75`,
   `btw.generation_agent`. Nothing to build here; see TASKS.md.
3. **Auto-titling.** First-turn generation writing at `source=auto`, the
   sanitizer (built — `state/titles.py`), catch-and-log. **This is now the
   only substantial work left**, and it also flips bare `/title` from
   *clear* to *regenerate*.
4. **Remaining drivers.** gemini / opencode / lovelaice `generate_detailed`,
   each with a live test that skips when its CLI is off PATH. Until then
   they degrade to no title, which `text_generation:` works around.

Slice 1 is independently worth shipping: it makes tabs legible today via
`aegis_rename(title=…)` and CLAUDE.md's existing self-naming habit, and
it does it without a single LLM call.

## Testing

- **Precedence is the surface.** One test per ordered pair: `auto` cannot
  overwrite `agent` or `human`, `agent` cannot overwrite `human`, `human`
  overwrites anything, bare `/title` resets to `auto`. Each asserts the
  refusal reason reaches the caller.
- **Legacy decode.** A `SessionMeta` record written before this change
  must still load. `state/event_codec.py` already has legacy-record
  decode; add a fixture that exercises it.
- The sanitizer is a pure function — table-test it with the adversarial
  cases (multi-line, wrapped in quotes, empty, 200 chars, only
  whitespace).
- Driver `generate()` tests are `live` and skip when the CLI is absent.
  Use `-m "not live"`, never `-k "not live"`.

## Open questions

- ~~**Does the tab bar have room?**~~ **Answered 2026-08-07: no.** Four
  tabs measure 127 cells untitled; a title takes that to 190–210. The
  title went to the status bar and `Ctrl+R` instead — see *TUI* above.
- **Do gemini and opencode reliably emit parseable JSON without a schema
  flag?** Slice 4 answers it. If they don't, `supports_oneshot = False`
  for them is an acceptable outcome, since `text_generation:` can point
  at any profile.

## What slice 1 actually shipped (2026-08-07)

`SessionMeta.title` / `.title_source` + codec; `aegis.state.titles`
(`outranks`, `sanitize_title`); the history fold, `SessionHistoryRow.title`
and `INDEX_VERSION = 2`; `AgentSession.title` with `AppBridge.set_title`
on all four bridge implementations; `/title [text]`; `aegis_title` and
`title=` on `aegis_rename`; `Ctrl+R` display + filter; the status-bar
segment. 2891 tests green, +59 over the pre-slice baseline.

Two defects that only surfaced by driving the real thing, both of the
shape this repo keeps paying for — unit-testable logic that was never
wired to anything:

- **A rename blanked the title.** `_record_rename` re-derives every
  `SessionMeta` field, so the header it appends said `title=""`. Fixed on
  both sides: the fold takes the last *non-empty* title, and the record
  carries the current one forward. `7af708a`
- **A resumed session forgot its title — and with it, the operator's
  authority.** The tracker is per-process, and nothing rebuilt the title
  from the transcript, so after a restart `title_source` was `""` and an
  agent could overwrite what Alex had typed. The precedence rule was
  exactly as strong as one uptime. Rehydrated in `pane.py` beside
  `rehydrate_plan`, which every resume path already funnels through.
  `945190c`
