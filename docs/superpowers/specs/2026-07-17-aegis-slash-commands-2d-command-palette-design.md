# Slash commands 2D — Command palette (drop-up typeahead) — design spec

**Date:** 2026-07-17
**Status:** Approved — ready for implementation plan
**Owner:** Alex + Claude
**Builds on:** 2A (parser + registry, `ArgSpec`/`Args`) and 2B (full builtin
coverage). Independent of 2C (prompt/plugin commands), which can land later.

## Summary

2A/2B gave slash commands a typed-arg parser, a `source`-tagged registry, and
a broad builtin set, all driven by typing `/verb args` blind. 2D adds the
**discovery UX**: an inline **drop-up** completion panel that rises above the
input as you type `/`, offering fuzzy-matched commands, their subverbs, and —
the payoff — **live argument values** (agent names, session handles, queue
names, themes). It works identically in the TUI and the web client, both
rendering the output of one pure `complete()` function in the harness-agnostic
commands core.

```
 ┌────────────────────────────────────────────┐
 │ › opus       claude-code · opus · full      │  ← highlighted
 │   default    claude-code · sonnet · auto     │
 │   reviewer   claude-code · haiku · auto      │
 ├────────────────────────────────────────────┤
 │ <agent> ▸ [prompt]                          │  ← ghost usage hint
 └────────────────────────────────────────────┘
 › /spawn opus█                                     ← input (bottom-anchored)
```

## Motivation

Blind `/verb args` requires memorising every command, its subverbs, and the
exact spelling of agent/queue/session/theme names. The registry and `ArgSpec`
are already fully introspectable (command name, `summary`, `usage`,
positionals, flags), so the information needed to guide the operator exists —
it is just not surfaced. 2D surfaces it, and adds a small **completer seam** so
argument *values* (not just names) can be enumerated live from the bridge.

## Non-goals (2D)

- **Prompt/plugin commands (2C).** 2D completes whatever is in `REGISTRY`;
  when 2C adds user/plugin commands they appear automatically (same registry),
  but 2D ships no loader.
- **A separate modal command palette.** The selector is the inline drop-up in
  the input flow, not a VSCode-style centered overlay (decided in
  brainstorming).
- **Subverb-dependent argument completion.** A positional's completer is
  chosen by position, not by the value of an earlier positional (e.g. `/groups
  status <name>` and `/groups dissolve <name>` share the same `<name>`
  completer). Good enough for the builtin set; noted as a limitation.
- **History/AI ranking.** Ranking is fuzzy-match score only, no usage-frequency
  learning.

## Design

### 1. The completer seam — `Arg.completer`

`Arg` (in `commands/args.py`) gains one optional field:

```python
# A completer is a static list of choices, or a callable of the bridge.
# Either form may yield bare "value" strings or "(value, detail)" pairs;
# complete() normalises both (see §5).
Choice = str | tuple[str, str]
Completer = tuple[Choice, ...] | Callable[[object], list[Choice]]

@dataclass(frozen=True)
class Arg:
    name: str
    required: bool = True
    greedy: bool = False
    completer: Completer | None = None   # static choices or (bridge) -> choices
```

This single field unifies the completion hierarchy:
- **Subverbs** are the first positional's completer — a static tuple, e.g.
  `Arg("subverb", required=False, completer=("list", "status", "dissolve"))`.
- **Dynamic values** are a later positional's bridge-driven completer, e.g.
  `Arg("agent", completer=lambda b: b.list_agents())`.
- No completer → the argument contributes only its name to the usage hint.

`completer` is data the parser ignores (parsing is unchanged); it is read only
by `complete()`. The `bridge` parameter is typed `object` (same structural
approach the `AppBridge` protocol uses) so `args.py` stays dependency-free.

### 2. The pure entry point — `complete(text, bridge) -> Completions`

New function in `commands/__init__.py` (harness-agnostic, unit-testable):

```python
@dataclass(frozen=True)
class Completion:
    insert: str      # text spliced into the input for this choice
    label: str       # the matched text shown (e.g. "/spawn" or "opus")
    detail: str = "" # dim right-column (summary / agent config / "")

@dataclass(frozen=True)
class Completions:
    items: tuple[Completion, ...]
    hint: str = ""   # ghost usage for the current command, e.g. "<agent> ▸ [prompt]"
```

`complete(text, bridge)` decides *what token is being typed* and returns the
candidates:

1. **Empty or verb-in-progress** (`/`, `/sp`, no trailing space): fuzzy-match
   `REGISTRY` names (builtins first, then other sources). Each `Completion`:
   `insert="/verb "`, `label="/verb"`, `detail=summary`.
2. **Past the verb**: resolve the command; if unknown, no items. Tokenise the
   argument portion to count *completed* positionals and isolate the current
   partial token. Then:
   - partial starts with `--` → complete declared `Flag` names.
   - else → find the `Arg` at the current positional index; if it has a
     `completer`, enumerate it (calling the callable with `bridge`, or using
     the static tuple), fuzzy-filter by the partial, and emit a `Completion`
     per candidate (`insert=candidate + " "`, `label=candidate`,
     `detail=""` — or enriched for agents, see §5).
   - Always compute `hint` from the command's remaining positionals
     (`usage`-style: `<agent> ▸ [prompt]`, greying already-bound args).
3. A greedy positional in progress (free-text prompt) yields no items and an
   empty hint — free text is not completed.

`complete()` never raises: a completer callable that throws is swallowed and
contributes no items (a bad completer must not break typing).

### 3. Fuzzy matching

A small pure scorer in `commands/fuzzy.py`: subsequence match (all query chars
appear in order), scored by contiguity + start-of-word bonuses, case-insensitive.
Returns `(score, positions)` so the panel can bold matched characters. Non-matches
score `None` and are dropped. Ordering: score desc, then source rank
(builtin first), then alphabetical. This is the ranking for both command names
and argument values.

### 4. Interaction & keys (both frontends)

The panel is visible whenever the input starts with `/` and `complete()`
returns a non-empty `items` (or a `hint`). Keys while the panel is open:

- **Up / Down** move the highlight. While open this **overrides** the input's
  Up/Down sent-message history recall; when the panel is closed, Up/Down recall
  as today. (The drop-up orientation means Down moves toward the input, Up away
  — the highlight starts on the bottom-most/closest item.)
- **Tab / Enter** accept the highlighted completion: splice its `insert` at the
  current token, re-run `complete()`; keep the panel open if more args remain,
  close it when the command is fully specified. **Enter with the panel closed
  submits** the message (unchanged from 2A/2B).
- **Esc** dismisses the panel without accepting; a second Esc clears/interrupts
  as today.
- Typing re-runs `complete()` live and refilters.

### 5. Argument-value enrichment

For the common completers, `detail` is filled so the panel is genuinely
informative, reusing existing bridge reads:
- agents (`/spawn`, `/queues new`, `/agents add` agent slot): `detail` =
  `harness · model · permission` (from the bridge agent map, same as `/agents`
  list).
- sessions (`/close`): `detail` = `agent_slug · state`.
- themes (`/themes`): no detail.
- queues/groups/schedules names: `detail` = a short descriptor when cheap,
  else empty.

Enrichment is optional per completer and lives with the command's wiring, not
in `complete()` core — the core only needs the candidate strings; a command may
supply a richer completer returning `(value, detail)` pairs. To keep one shape,
`Completer` callables may return either `list[str]` or `list[tuple[str, str]]`
(value, detail); `complete()` normalises both.

### 6. Frontends

- **TUI** — a `CommandPalette` widget (Textual) mounted **above** `GrowingInput`
  in `ConversationPane` (the same slot the `PendingStrip` already occupies above
  the input), rendered as a bordered `OptionList`-style panel that grows upward.
  `on_text_area_changed`/input-changed calls `complete(text, self.app)` and
  repopulates; key handling for Up/Down/Tab/Enter/Esc is intercepted in the
  pane's input handler while the panel is open. Selection splices text into
  `GrowingInput`.
- **Web** — a `complete` WS RPC (`wssession.py`) returns the `Completions` as
  JSON (`{items:[{insert,label,detail}], hint}`); `app.js` renders a drop-up
  `<div>` above the textarea, filters on `input` events, and handles the same
  keys. Dynamic completers run server-side (bridge = `SessionManager`), so both
  frontends show identical candidates.

Web parity is a first-class part of the slice, threaded through as in 2A/2B.

## Component boundaries

- `commands/args.py` — `Arg.completer` field only (data; parser unchanged).
- `commands/fuzzy.py` — pure scorer. No registry, no bridge, no UI.
- `commands/__init__.py` — `Completion`/`Completions` types + `complete()`.
  Depends on the registry + `fuzzy` + `ArgSpec`. No UI.
- `commands/builtins/*` — add completers to existing Args (wiring only).
- `tui/` — `CommandPalette` widget + pane key interception (the only
  Textual-aware code).
- `web/wssession.py` + `app.js` — the `complete` RPC and the drop-up panel
  (the only web-aware code).

## Testing

Hermetic (`-m "not live"`), TDD:
- **`fuzzy`** — subsequence match/no-match; ranking (contiguous > scattered,
  start-of-word bonus); case-insensitivity; matched positions returned.
- **`complete()`** — verb-in-progress lists commands (fuzzy, builtins first);
  past-verb completes the current positional's completer; static-tuple
  (subverb) vs callable (agent names via a fake bridge); `--` completes flags;
  greedy positional yields nothing; unknown command yields nothing; a throwing
  completer is swallowed; `hint` reflects remaining args; `(value, detail)`
  pairs normalise.
- **completer wiring** — each builtin's Args expose the expected completer
  (e.g. `/spawn` agent Arg → the fake bridge's agent list; `/groups` subverb →
  the static tuple).
- **TUI** (`tests/test_pane_palette.py`, run_test) — typing `/sp` shows the
  panel with `/spawn`; Down/Tab accept splices `/spawn `; Esc dismisses; the
  panel overrides history-recall while open; flaky-aware re-run per AGENTS.md.
- **Web** (`tests/test_web_complete.py`) — the `complete` RPC returns the
  expected `items`/`hint` for a slash input and empties for a plain message.

`app.js` panel rendering is browser-smoked in the verification task (no JS unit
harness).

## Slices (for the plan)

1. **Completer seam + `complete()` + fuzzy** — `Arg.completer`, `fuzzy.py`,
   `Completion`/`Completions`, `complete()`. Pure, fully unit-tested. No UI.
2. **Wire completers onto builtins** — subverb tuples, agent/session/theme/
   queue/group completers, `(value, detail)` enrichment. Unit-tested via
   `complete()` against a fake bridge.
3. **TUI drop-up panel** — `CommandPalette` widget + pane key interception +
   splice-on-accept. run_test coverage.
4. **Web drop-up panel** — `complete` RPC + `app.js` panel + keys + browser
   smoke.

## Estimate

Larger than 2B — a genuinely new UI surface in two frontends plus a completion
engine — but each slice is independently shippable and testable. Slice 1 is
pure and small; slices 3–4 carry the UI weight. Well within a focused span at
our pace.
