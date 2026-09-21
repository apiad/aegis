# The suggested reply, and the operator's own block

> **Status:** designed 2026-09-21, not yet implemented. Extends the recap
> schema of `2026-09-17-aegis-unified-recap-design.md` with a fifth field and
> changes when a recap result reaches a view. Calibration lives in
> `.playground/reply-suggestion-probe/` (workspace playground, not in this
> repo).

## Why

Two complaints, one turn boundary.

**The input box is empty when the answer is obvious.** A turn ends on a
question — "one call or two?", "web in or out?" — and the operator types the
same short reply they would have typed every other time. Claude Code's own
client suggests that reply as dim text and completes it on Tab; aegis does
not, so every obvious answer is typed in full.

**The operator's message is the plainest thing in the transcript.**
`render_user_line` draws it as one Rich `Text` on a `user_bg` tint that stops
where the text stops. A pasted multi-line prompt, a fenced code block, a
numbered list all land as one undifferentiated run. Meanwhile the recap —
which is *not* the conversation — gets a panel, a header and full Markdown.
The thing the operator actually said is the worst-looking block on screen.

## The decision

### One call, five fields

`StandingRecap` grows `suggestion`. No second model call, no second prompt,
no extra latency.

| field | meaning |
|---|---|
| `task` | unchanged |
| `outcome` | unchanged |
| `next` | unchanged |
| `attention` | unchanged |
| `suggestion` | what the operator would plausibly type next, in their own voice and language. Empty when there is no obvious next message. |

Riding the recap is not thrift for its own sake. The turn recap already fires
on **every** turn (`recap/gate.py`: paid always, drawn only when the turn
moved something), and its window already carries three turns of `user:`
lines. Those lines are the only calibration material that exists for "what
would Alex type", and they are already in the prompt. A second call would pay
$0.016 to assemble the same window and read the same lines.

The risk this takes on is style contamination: the recap prompt speaks in the
third person to an operator glancing at a dashboard and forbids naming files,
commits and hashes, while a suggestion speaks in the first person *as* the
operator and often names exactly those things. The probe below measures
whether `outcome` degrades. If it does, the field splits into its own call
and we pay the second $0.016 then — splitting later is a prompt move, not a
redesign.

### The prompt addendum

Appended to `SYSTEM`, after the existing language rule:

> SUGGESTION: `suggestion` is a draft of the operator's *own* next message,
> written as if they typed it — first person, their language, their register,
> their length. Copy how the `user:` lines in the window actually sound:
> if they are short and blunt, be short and blunt. Unlike the other fields it
> may name a file, a command or a number, because the operator's messages do.
> At most 12 words, one line, no trailing punctuation flourish. Leave it
> empty unless the next message is genuinely obvious: a question was asked, a
> choice was offered, work was presented for approval, or the session is one
> plain step from continuing. An empty suggestion is the correct answer most
> of the time.

Two properties worth naming because the UI depends on both:

- **One line, twelve words.** A Textual placeholder renders on the first row
  and truncates. The cap is not a style preference; it is the widget.
- **Empty is the default.** A wrong suggestion costs more than a missing one,
  because the operator has to read it, reject it, and then type anyway.

### How a suggestion reaches the box

`Recap` carries `suggestion`. `RecapNote` persists it beside `task` and
`next`, so a daemon restart puts it back without paying again; records
written before this change decode with it empty.

**The emit gate splits in two.** Today `_run_recap` calls `_emit_recap` only
when the turn moved the substrate or the attention category changed. That
gate exists to keep a conversation of pure questions free of repeated
transcript blocks (anthropics/claude-code#56346, by another road) and it
stays exactly as it is. But a turn that moved nothing is precisely the turn
that ended on a question, so the suggestion has to arrive anyway. The session
gains a second, cheap notification — observers on `on_suggestion`, fired
whenever a recap returns a non-empty `suggestion`, regardless of whether the
recap block draws.

The session clears the suggestion when a turn starts. A suggestion computed
for the turn before last is worse than none, and the same reasoning already
governs `_cancel_recap`.

### The input box

`GrowingInput` gains a `suggestion` property. Set it and — **only while the
box is empty** — it becomes the widget's `placeholder`; clear it and
`"type a message…"` comes back.

That single line is the whole ghost-text mechanism. Textual already renders
placeholders dimmed and already hides them on the first keystroke, so
"dimmed in the box" and "on key press it overwrites" cost nothing and cannot
drift from the rest of the widget's behaviour. Nothing is inserted into the
document, so history recall, the command palette's key interceptor and the
voice lock are all untouched.

Tab is the one new key. `_on_key` intercepts it when a suggestion is live and
the box is empty: fill the text, move the cursor to the end, clear the
suggestion. Every other Tab keeps today's `tab_behavior="focus"`.

The suggestion is also cleared when the operator submits, and when a turn
starts — the same two moments the session clears its own copy.

### The operator's block

`render_user_line` becomes `render_user_block`, returning a `Panel`:

- an accent `›` header;
- the body rendered as `Markdown`;
- `colors.user_bg` behind the whole block;
- a heavy left edge in `colors.user`.

Deliberately **not** the `_aside` surface. `_aside` means "in the transcript
but not the conversation" — a `/btw` note, an `@peer` answer, a recap. The
operator's message is the conversation. It takes the recap's proportions and
its own colours, so the two read as siblings rather than as the same kind of
thing.

Three call sites move: `render_event` (replay) and the two mount points in
`pane.py`. `test_render_event.py`'s assertions on the old single-line form
move with them.

**Known cost, accepted.** Markdown eats `snake_case` underscores and turns
`**/*.py` bold. The operator asked for full Markdown and fenced blocks,
lists and backticks are worth more than the occasional mangled glob — a
fenced block renders correctly regardless. Recorded here so the first
mangled path is a known trade and not a bug report.

## Calibration

`.playground/reply-suggestion-probe/`, modelled on
`recap-abstract-probe/probe_real.py`.

It walks real session logs from `.aegis/state/sessions/`, finds every point
where a turn ended and the operator's next `UserMessage` followed, generates a
recap from the prefix **only**, and writes a side-by-side of the suggestion
against what the operator actually typed. The same run prints `task` and
`outcome` for every call, against the current shipped recap on the same
prefixes, so contamination is visible rather than inferred.

What the results have to answer, in order:

1. Would the operator have pressed Tab? Judged by reading, not by a
   similarity score — a suggestion can be worded differently and still be
   right, and can share every word and still be wrong.
2. How often is a suggestion offered when the honest answer is none? This is
   the failure that makes the feature annoying rather than merely useless.
3. Did `outcome` drift back toward inventory style?

The prompt addendum above is the starting point, not the answer. It is
expected to change once before implementation lands.

## Out of scope

- A second model call for the suggestion. Revisit only if the probe shows
  contamination.
- Multi-line suggestions, or any rendering that is not the widget's own
  placeholder.
- Suggesting a reply for a turn nobody asked for (an unsolicited harness
  turn), which is already filed in `TASKS.md` for the recap itself.
