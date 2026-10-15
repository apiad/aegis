---
title: Workflows
kind: howto
when: work that needs several agents coordinated, checked or gated, which the person should approve as a plan before it runs
---

A workflow is a plan the server runs. You write it as a YAML tree in which
every node says, in one sentence for the person, what it does; the person
reads that tree as the plan and approves it; then aegis runs it and tells
you when it ends. Use `plan_update` for work you do yourself, even when it
hands a few tasks to a queue in order. Use a workflow when the work needs
coordination.

## Write one

```yaml
id: note-and-check
title: Write a note, then check it
say: One agent writes the note, a second checks it.
root:
  seq:
    - agent:
        agent: sonnet
        prompt: "Write a note about {{args.topic}} in notes/."
        result: {type: object, properties: {path: {type: string}}, required: [path]}
      id: write
      say: "Write a note about {{args.topic}}."
    - agent: {agent: opus, prompt: "Check {{write.path}} for errors and fix them."}
      say: Check the note and fix what is wrong.
  say: Write, then check.
```

Every node needs `say`. A `{{name.field}}` in a prompt or a `say` reads
`args` or the result of a node that finished before this one. This version
runs every kind in the spec.

## Run things at once

`par` runs its nodes at once and ends when all have. `map` runs its `do`
once per item of `over`, which is a list or one `{{reference}}` to a list,
with the item named by `as`; each item goes through all its steps on its
own. Inside the map, `say` reads the item, so each item reads as its own
sentence. A `cap` bounds how many branches run at a time: inside `map:`
for a map, beside `par:` for a par. A branch that has begun keeps its slot
until it finishes, even while a node inside it waits on an `after`, so the
lint refuses an `after` from inside one branch of a capped `par` to another
of its branches; put the `after` on the branch itself.

```yaml
root:
  par:
    - map: {over: "{{args.services}}", as: s, cap: 2,
            do: {agent: {agent: sonnet, prompt: "Build {{s.id}}."}, id: build, say: "Build {{s.id}}."}}
      id: images
      say: Build one image per service, two at a time.
    - agent: {agent: sonnet, prompt: "Write the mock against {{images.base.build.tag}}."}
      after: [images.base]
      say: Write the mock once the base image is built.
  say: Images and the mock.
```

An item's key is its `id` when it has one, else its position, so the
second node above waits on the item `base` with `after: [images.base]` and
reads its result as `images.base.build`. `after` also takes a node's id (it
is done) or `node.name`, an early output that node gives with
`workflow_output` while it keeps working; a key of that name in its result
meets it too. `{{node.name}}` reads the early output whatever the result
holds, so the lint refuses a result schema with a property of an awaited
output's name. A `{{name}}` must name a node
that finishes before this one: earlier in a `seq`, or named in `after`.
The lint refuses an `after` that could never be met.

## Commands, checks and pauses

```yaml
- sh: "gh pr merge {{impl.pr}} --squash"
  id: merge
  say: "Merge PR {{impl.pr}}."
- check: {sh: "test -f build/done", every: 1m}
  deadline: 2h
  say: Wait for the build to leave its marker.
- wait: 20m
  say: Give the deploy twenty minutes.
```

`sh: "gh pr merge {{impl.pr}} --squash"` runs a command once, under bash, in
the directory you armed the workflow from. Exit 0 is done; anything else
fails the step with its code and the last line it printed to stderr. Its
value is `{code, out, err}` (the last 4000 characters of each), so a later
step reads `{{merge.out}}`. A value reaches a command, or an sh probe,
through its environment, never through its text: each `{{...}}` becomes a
variable, so `{{impl.pr}}` and `"PR {{impl.pr}}"` both get the exact value and
nothing in it runs. The lint refuses one inside single quotes or a quoted
heredoc, where it would stay literal, and one in arithmetic (`((...))`,
`$((...))`, `[[ ]]`, `let`, a subscript) or where bash reads a variable's name,
since bash runs a `$(...)` found there; never use a value in arithmetic, even
copied into a variable first. Only a session with full
permission can arm a workflow with a command, and the card shows every
command in full before the person approves.

`check:` waits until a probe passes: `{sh: cmd, every: 1m}` passes on exit 0
and runs again every `every` (30s by default, at least 5s); `{expr: "..."}`
is read once and passes when the expression is true, or fails the check, since
the values it reads never change; `{judge: {agent, prompt, inputs}}` asks
an agent once for a yes or a no, with each of `inputs` (references such as
`impl` or `review.verdict`) given to it as JSON. A judge counts in the same
limits as an agent node. Without a deadline above it, a check gives up after
an hour and a command after 30 minutes. `wait: 20m` pauses on wall time.

## Branch and repeat

```yaml
- if: {when: {expr: "review.approved"},
       then: {sh: "gh pr merge {{impl.pr}}", say: Merge it.},
       else: {agent: {agent: sonnet, prompt: "Fix what the review found."}, say: Fix it.}}
  say: Merge if the review approved, else fix.
- loop: {max: 5, until: {judge: {agent: opus, prompt: "Is the fix right?", inputs: [fix]}},
         do: {agent: {agent: sonnet, prompt: "Fix the next problem."}, id: fix, say: Fix one problem.}}
  id: review
  say: Fix until a judge agrees, at most five times.
```

`if: {when: probe, then: node, else: node}` takes one branch; `else` is
optional, and a step outside a branch cannot read a step inside it. `loop:
{do: node, max: 5, until: probe}` runs `do` up to `max` rounds and stops
when `until` passes; running out of rounds fails it. Without `until` it
runs `max` rounds. Round 2's `fix` is `review.2.fix`, and after the loop
`{{review.fix.pr}}` reads the last round's.

## Expressions

A `{{...}}` and an `expr` probe hold one expression: references
(`impl.pr`, `args.issue`, `m.0.one`), numbers, 'strings', true, false, null,
`== != < > <= >=`, `and`, `or`, `not` and parentheses. `or` gives its first
true value, so `{{args.every or '20m'}}` is a default. It replaces any false
value, not only a missing one: 0, "", false, [] and null all give way to it. Nothing else: no
arithmetic, no calls. A value that is not a string is written as JSON.

## Deadlines and retries

```yaml
root:
  seq:
    - {sh: "make test", retries: 2, say: "Run the tests, up to three times."}
  deadline: 1h
  say: Test it within the hour.
```

`deadline: 2h` on any step, and on the workflow, bounds it: past it, its
running agents are interrupted and kept for you to read, its commands are
killed, and the step fails. A deadline on a group bounds everything in it.
`retries: 2` on an agent, sh or check runs it again when it fails, with a
fresh session for an agent. A reference with no value is never retried,
and a command cut by a server restart is never run again on its own.

## Blocks

```yaml
blocks:
  land:
    seq:
      - agent: {agent: sonnet, prompt: "Implement {{x.title}}.", result: {$ref: "#/defs/pr"}}
        id: impl
        say: "Implement {{x.title}}."
      - sh: "gh pr merge {{impl.pr}} --squash"
        say: "Merge PR {{impl.pr}}."
    say: Implement and merge.
defs:
  pr: {type: object, properties: {pr: {type: integer}}, required: [pr]}
```

`blocks: {land: node}` names a sub-tree; `ref: land` uses it, and its ids
belong to the map item, loop round or top level it is used in, so a block
is used at most once in each. `id`, `say`, `after`, `deadline` and
`retries` on the `ref` override the block's own. `defs: {pr: schema}` names
a result schema; `result: {$ref: "#/defs/pr"}` uses it.

## After a restart

When the server stops and starts again during a run:

- an agent or a judge goes on in its session, and the step ends when it reports;
- a command is not run again: the step fails with "cut by restart", and you decide whether to rerun or skip it;
- a check's command probe runs again, and its deadline still counts from its first start;
- an `expr` probe is evaluated again;
- a wait ends at the time it was going to end;
- an if or a loop never asks a probe again once its answer is logged, and a loop keeps its round;
- a deadline that passed while the server was down fires at once;
- a retried step keeps its count of attempts;
- a failure, a gate, a question or a proposed change stays open, and you are told of it again only if you never were;
- a `session: main` step stays yours until you report it.

## How many at once

A node waits, shown as queued, until it has room in every bound that
applies. The workflow's `limits`, such as `{total: 4, opus: 2}`, bound this
run: `total` counts all its agents together, and a number under an agent's
name counts that agent alone. The server's `workflows: {max_agents: ...}` in
`.aegis.yaml` has the same shape and bounds every workflow together, 4 in
total by default. A session a node spawns, or one spawned under it, counts
in both as an agent of its own alias while the run lives; `session_spawn`
refuses it with an error naming the run when it does not fit, and the node
goes on without it. Last comes the quota the server keeps for its owner. An
agent without a number of its own is bound only by the totals, and a `cap`
above `limits.total` is refused.

## Arm it

`workflow_lint` checks a definition and names each error with its YAML path.
`workflow_arm` with the definition and `args` checks it again and draws it as
a card under your call. Nothing runs until the person presses Approve and
run there. You cannot approve it yourself. The card docks beside the
transcript as a sent file or a page does, and stays live while the run goes
on.

## While it runs

Each agent node is a quiet session that nobody can type into while the run
lives: a person's message or interrupt, a peer's handoff, a schedule, a page's
submit and a session handoff are all refused with an error
naming the run. Its own monitors and queue tasks still reach it. It ends by calling `workflow_result` with a value matching the node's
`result` schema and a one-sentence `summary`; a turn that ends without one
is nudged once, then fails.

You are woken by a message headed `> from workflow:<id>` for each thing the
run waits on you for: a failed step, a gate with `by: main`, a worker's
question, and the person's answer to a change you proposed. Each one is told
once, also across a restart. You get one more message when the run ends,
headed `done`, `failed` or `stopped`. `workflow_status` shows the tree, the
story and what the run waits on at any time; `workflow_stop` stops the run
and unlocks its sessions, or, with a node, stops only that step, which then
waits for your answer.

## When a step fails

```yaml
id: deploy
title: Deploy
say: Build, then deploy.
deadline: 1h
root:
  par:
    - {sh: "make build", id: build, retries: 1, say: Build it.}
    - {sh: "make docs", id: docs, say: Build the docs.}
  say: Build the code and the docs.
```

A step that fails once its `retries` are spent does not end the run. Its
branch waits, the other branches go on, and a failed branch keeps its slot
of a `cap`. You get a message headed `> from workflow:<id> · failed · <step>`
naming the step, its error, its attempt and, for an agent, its session to
read with `peer_read`. It ends with the `workflow_answer` call that answers
it, whose choice is one of three:

- "retry" runs the step again; a group runs again whole;
- "skip" goes on without it, and `value`, checked against its result schema, stands in for its result where later steps read it;
- "fail" ends the run, with `text` as the reason the story keeps.

Change a step before retrying it with `workflow_edit`, below. The workflow's
own `deadline` ends the run without asking, and so does a run whose main
agent's session has closed.

## Gates

```yaml
id: release
title: Release
say: Build the packages, ask, then publish them.
root:
  seq:
    - {sh: "make dist && ls dist", id: dist, say: Build the packages.}
    - gate: {by: person, ask: "Publish these packages?", show: [dist.out]}
      id: ok
      say: Ask the person before publishing.
    - agent: {agent: sonnet, prompt: "Publish dist/. The person said: {{ok.value}}"}
      say: Publish the packages.
  say: Release.
```

`gate: {by, ask, show}` holds its branch until someone answers `ask`. With
`by: person`, a card in your transcript shows the question and each value in
`show` as JSON, with an answer box, Continue and Stop; no agent can answer
it, you included. With `by: main` the question comes to you, and you answer
with `workflow_answer`: choice "continue" with an optional `value`, or
"abort". You may ask the person first, in your own words. Later steps read
the answer as `{{ok.value}}`. A no, the person's Stop or your "abort", stops
the run at the gate.

Only the person makes a person's gate count as passed. `workflow_stop` does
not take a gate, and when a person's gate fails (its deadline, a deadline
around it, a question that did not render) the step, or the group around
it, takes only "retry", which asks the person again, or "fail": never
"skip".

## Questions and notes

A node that cannot go on without an answer calls `workflow_ask` and ends its
turn. The question comes to you, the main agent, headed `ask · q1`, and never
to a person. Answer it with `workflow_answer` and `text`; the text is the
node's next message, and the node waits meanwhile without being nudged.

```text
workflow_answer(run="deploy-3f2a1c", to="q1", text="Use the staging database.")
workflow_note(run="deploy-3f2a1c", text="Skipped the docs: they did not change.")
```

`workflow_note` writes one line into the run's story, the part of the card
the person reads as news. Say there why you retried, skipped or changed
something.

## Change a running workflow

```yaml
id: deploy
title: Deploy
say: Build, then deploy.
deadline: 1h
root:
  seq:
    - {sh: "make build", id: build, retries: 1, say: Build it.}
    - {sh: "make smoke", id: smoke, say: Smoke-test the build.}
    - {sh: "make deploy", id: ship, say: Deploy it.}
  say: Build, smoke-test and deploy.
```

`workflow_edit` with the run and the whole definition, with the same ids,
proposes a change, here a smoke test before the deploy. A step that started
stays as it is: one that ran keeps its result, one that is running must be
stopped first with `workflow_stop` and its node, and a started map, loop or
if keeps its items, rounds and probe. A failed or stopped step may change,
and applying the change runs it again. A new step goes after the steps of
its seq that started, and a seq that has begun keeps the order of the steps
it had. A step that has not started may move to another group; the card
shows the move as its own line.

The person gets a card with the change in the steps' sentences and every
command it adds, with Apply change and Keep the plan; no agent can apply it.
The run goes on meanwhile, but the steps the change touches wait, and so
does the step right after one it adds, and a run whose steps are all done
waits for the answer before it ends. You get a message headed `edit · v2`
saying which the person chose. Give every step of a workflow you may change
an `id`: a generated id moves when a step is added before it, and the edit
is then refused.

## Arm it again

After a stop or a failure, `workflow_arm` with `run` arms the same run
again, after a new approval on a new card. What finished keeps its result;
every step that did not runs again. It keeps its args. A definition given
with `run` may change only what `workflow_edit` may.

## A step you run yourself

```yaml
id: rollout
title: Roll out
say: Deploy, let me look at the dashboard, then announce it.
root:
  seq:
    - {sh: "make deploy", id: ship, say: Deploy it.}
    - agent:
        session: main
        prompt: "Look at the deploy dashboard and say whether it is green."
        result: {type: object, properties: {green: {type: boolean}}, required: [green]}
      id: look
      deadline: 30m
      say: Check the dashboard myself.
    - agent: {agent: sonnet, prompt: "Announce the deploy. Green: {{look.green}}."}
      say: Announce the deploy.
  say: Deploy, check, announce.
```

`session: main` on an agent node gives the step to you: its prompt arrives
as a message headed `step <id>`, and you end it with `workflow_result`, as a
node does. It takes no `agent` and no `cwd`. Your turns are also the
person's, so nothing nudges you or fails you for a turn without a result;
give the step a `deadline`. Steps like it run one at a time in a run, and
stopping the run never interrupts you.

## Inside a workflow

If your first message is headed `> from workflow:`, you are a node. Do the
task, call `workflow_result` once, and end your turn. If it is headed
`> from workflow:… · judge`, you are a judge: call `workflow_result` with
`{pass, why}` and change nothing. If your first
message names outputs other steps wait on, call `workflow_output` with each
one as soon as it is ready, then go on. If you cannot go on without an
answer, call `workflow_ask` with your question and end your turn: it reaches
the main agent, never a person, and the answer arrives as your next message.
Neither you nor a session you spawn can arm a workflow.
