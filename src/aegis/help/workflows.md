---
title: Workflows
kind: howto
when: work that needs several agents coordinated, checked or gated, which the person should approve as a plan before it runs
hints:
  - Coordinate several agents with a workflow you approve as a plan before it runs. #write-one
  - Type /loop with a prompt to repeat it until the agent says it is done. #repeat-a-prompt-with-loop
  - Save a workflow and run it later with /workflow. #save-a-workflow
---

A workflow is a plan the server runs. You write it as a YAML tree in which
every node says, in one sentence for the person, what it does; the person
reads that tree as the plan and approves it; then aegis runs it and tells
you when it ends. Use `plan_update` for work you do yourself, even when it
hands a few tasks to a queue in order. Use a workflow when the work needs
coordination. For the common shapes, copy a recipe from
[Workflow recipes](workflow-recipes): each one lints as it stands.

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
runs every kind in the spec. `args`, when you give one, is a JSON Schema of
the same subset as a `result`, plus default for a value left out; the lint
checks it, and each default against its own schema. A default goes only on
a property of `args` itself, never on args as a whole, on a field inside a
property, or in a def; `required` names only properties there are.

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
An `sh` step, or a `check` whose probe is `sh`, can carry `progress:` beside
it: a command that prints a number from 0 to 100, as a monitor's does. The
card draws it as a bar on the step's line in Now and on its row, so the person
can tell a step nearly done from one that is stuck. A check reads it after each
probe that does not pass, and an `sh` every 15 seconds while its command runs.
The last line of its output is the number; anything else keeps the last
reading and never fails the step. It takes values as any command does, the
person approves it with the rest, and a restart loses the reading until the
next one.

```yaml
- check: {sh: "test -f build/done", every: 1m}
  progress: "ls build/*.o | wc -l | awk '{print int($1 * 100 / 120)}'"
  deadline: 2h
  say: Wait for the build, counting its objects.
```

A wait whose length is filled in at run time can carry `between: [1m, 1h]`
beside it, and the length is clamped into those bounds, so a value an agent
gives never waits longer than the definition allows.

## Branch and repeat

```yaml
- if: {when: {expr: "review.approved"},
       then: {sh: "gh pr merge {{impl.pr}}", say: Merge it.},
       else: {agent: {agent: sonnet, prompt: "Fix what the review found."}, say: Fix it.}}
  say: Merge if the review approved, else fix.
- loop: {max: 5, until: {judge: {agent: opus, prompt: "Is the fix right?", inputs: [fix]}},
         at_max: done,
         do: {agent: {agent: sonnet, prompt: "Fix the next problem."}, id: fix, say: Fix one problem.}}
  id: review
  say: Fix until a judge agrees, at most five times.
```

`if: {when: probe, then: node, else: node}` takes one branch; `else` is
optional, and a step outside a branch cannot read a step inside it. `loop:
{do: node, max: 5, until: probe}` runs `do` up to `max` rounds and stops
when `until` passes; running out of rounds fails it, unless its at_max is
done, as here, and then it ends done. Without `until` it runs `max` rounds. Round 2's `fix` is `review.2.fix`, and after the loop
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
A step or group that has finished, or that was skipped, never expires: its
deadline stops counting once it is done.
`retries: 2` on an agent, sh or check runs it again when it fails, with a
fresh session for an agent. A reference with no value is never retried,
and a command cut by a server restart is never run again on its own. An
if's deadline counts from when it starts asking its probe.

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
- an if still asking its command probe asks it again, and its deadline counts from its first start;
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
on. Pass `dock` to place it as you arm, with the values `artifact_send`
takes; a bad `dock` arms nothing, and one that no longer fits once the run
exists comes back as `"docked": false` with the reason. `layout_read` lists
the card as kind workflow, with its title and run. Arming again before
approval keeps the spot: the new card takes the docked one's pane. If the
person presses Reject, nothing runs and you get a message headed
`rejected`: ask them what to change, then arm a new version. When they
reject a run you armed again, the steps it finished are kept, and
`workflow_arm` with `run` arms it again once more.

## While it runs

Each agent node is a quiet session that nobody can type into while the run
lives: a person's message or interrupt, a peer's handoff, a schedule, a page's
submit and a session handoff are all refused with an error
naming the run, and so are stopping, closing, configuring and renaming it,
by a person or an agent. Its own monitors and queue tasks still reach it. It ends by calling `workflow_result` with a value matching the node's
`result` schema and a one-sentence `summary`; a turn that ends without one
is nudged once, then fails.

When the person approves, you get a message headed `approved` that says
where the run next stops: its first gate or `session: main` step not yet
done, or else its end. Answer it with `turn_end(attention="review")` and a
short message telling the person the run is going, that they can write to
you meanwhile to steer it or ask about it without stopping it, and when it
next stops. A run approved by its launch gets no such message: the
`workflow_arm` result carries the same line as `next`.

Once approved, your card shows waiting on `workflow <run>` until the run
ends, between steps too; approving answers your needs_you. A review or done
turn during the run goes back to waiting. You get no idle nudge, and your
process may park: the run's messages restart it.

You are woken by a message headed `> from workflow:<id>` for each thing the
run waits on you for: a failed step, a gate with `by: main`, a worker's
question, and the person's answer to a change you proposed. You are also
told, with nothing to answer, when a gate with `by: person` opens (headed
`gate`) and when the person continues at it (headed `passed`), so you can
tell the person their card waits and need no monitor to see it. Each one is
told once, also across a restart. You get one more message when the run ends,
headed `done`, `failed` or `stopped`, or `rejected` when the person rejects
the plan before it starts. `workflow_status` shows the tree, the
story and what the run waits on at any time; `workflow_stop` stops the run
and unlocks its sessions, or, with a node, stops only that step, which then
waits for your answer.

## What the person sees

The run's args sit at the top of the card, each folded to its name and a
short preview (`3 items`, an object's keys, a string's opening words) until
the person opens it; a list or an object opens as coloured JSON. A short
value shows whole on its line.

The card's header counts the steps done, failed and the questions open over
a progress bar; before approval it says how many steps there are. Under the
bar it says how long the run has gone and what its steps' sessions have cost.
While it runs it adds an estimate of how long is left, from the pace so far,
and of the total cost, from what each finished step cost. While the
run goes on, Now lists each working step, with its session or its command
and how long it has run, then up to three steps that come next. Under it the
story tells what happened, and under that the plan, one row per step. A map
of more than six items shows its count line and folds its items until the
person opens it; an item that failed, waits for a change or holds an open
gate for the person stays shown. A loop past six rounds shows only its
current one. The story's lines for those items fold into one line with the
same toggle; a failure, a question and a note stay shown.

The card opens full screen, docks and moves to another browser window as a
page does. A step's session has no tab while the run lives: its handle on
the card and on the Fleet's card opens the transcript read-only, and the
session gets its tab when the step fails or the run ends.

A session on a linked server shows its run's cards too, and Approve and run,
a gate's answer, a proposed change and a step's `see` chip all act on the
server the run lives on.

## Link a step to the plan

```yaml
id: slices
title: Slices
say: Implement each slice of the plan.
root:
  map:
    over: [ingest, export]
    as: s
    do:
      agent: {agent: sonnet, prompt: "Implement the {{s}} slice of docs/plan.html."}
      id: impl
      see: "docs/plan.html#slices"
      say: Implement the {{s}} slice.
  id: each
  say: Every slice.
```

`see` on any step is a path to a file, relative to the run's folder or
absolute, with an optional `#fragment` or `:line`. It is fixed when the
person approves the run: the lint refuses a `{{...}}` in it, so no value
filled in later, such as a step's result, chooses the file, and it refuses
a URL. The person's card shows it as a chip that opens the file in the
viewer: an HTML page at its fragment, a text file at its line.

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
read with `peer_read`; a command that failed or ran past its deadline adds
the last 50 lines of its output, which `workflow_status` shows too as the
step's `tail`. It ends with the `workflow_answer` call that answers
it, whose choice is one of these:

- "retry" runs the step again; a group runs again whole;
- "skip" goes on without it, and `value`, checked against its result schema, stands in for its result where later steps read it;
- "more", only for a loop whose `until` did not pass in its rounds, runs one round more and goes on from there; `value` takes how many rounds more;
- "fail" ends the run, with `text` as the reason the story keeps.

`workflow_answer` and `workflow_stop` reply with the run's status, the state
of the step you named and what the run still waits on; the whole tree is
`workflow_status`'s.

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
`show` as JSON, with an answer box, Continue, Stop and Auto; no agent can
answer it, you included. Auto is the person handing the gate to you: it
becomes a `by: main` gate, now and each later time the same step asks (each
item of a map, each round of a loop, each retry), and you are woken to
answer it. Other items of a map already waiting stay the person's until
they press Auto on one of them, which hands the step over. When the person
tells you in chat that you may pass their gates yourself, ask them to press
Auto once on the card: you cannot do it for them. With `by: main` the question comes to you, and you answer
with `workflow_answer`: choice "continue" with an optional `value`, or
"abort". You may ask the person first, in your own words. Later steps read
the answer as `{{ok.value}}`. A no, the person's Stop or your "abort", stops
the run at the gate.

Only the person makes a person's gate count as passed, until they hand it
to you with Auto. `workflow_stop` does
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

After a stop or a failure, or after the person rejects arming it again,
`workflow_arm` with `run` arms the same run again, after a new approval on
a new card. What finished keeps its result;
every step that did not runs again. It keeps its args. A definition given
with `run` may change only what `workflow_edit` may.

## Save a workflow

`workflow_save` keeps a workflow in the project, as a file named after its
id in the .aegis/workflows folder beside the server's .aegis.yaml, so it lives
in git with the rest of the configuration. It writes the ids aegis generated
for the steps into the file, so a later edit of a run keeps them. A second
save with the same id replaces the file. Saving needs no approval, because
running a saved workflow does.

`workflow_list` lists the project's workflows and the built-in ones, each
with its title, args schema, version, who saved it and when, and what is
wrong with it when it does not lint. `workflow_delete` removes one from the
project. The built-in ids are reserved: no project workflow takes one, and
none is deleted.

In Settings → Workflows a person sees every saved workflow with its steps,
exports one as a YAML file, and imports one, which is linted first and
saved as theirs.

`workflow_arm` with `id` in place of `definition` arms a saved workflow,
with your args, and the person approves it on its card as any other.

## Run a saved workflow

To run a saved workflow, a person types `/workflow <id> <text>` in your
session, or `/workflow` alone to pick one from the menu. You then get one
inbox turn, headed `> from workflow:<id> · launch`, that quotes their line,
names the saved workflow and its args schema, and gives you a token. Make
the args from their text and from what you know, and call `workflow_arm`
with `id`, `token` and `args`, nothing else. The run starts at once, with no
card to approve: their line was the approval. The args must match the
schema, and a key it does not name is refused at any depth; a default is
filled in for a key you leave out. If a required value cannot be made from
the text, ask the person instead.

The token works once, only in your session, and only for that saved
workflow as it was when the line was typed: saving it again in between changes
nothing. An arm refused for its args or its lint keeps the token, so fix the
args and arm again. A token lasts 24 hours, and a server restart voids it;
then ask the person to type the line again. A session runs one workflow at a
time: while one runs, the line is refused before you hear of it, and the
person stops the run from its card first.

## Repeat a prompt with /loop

`/loop <prompt>` repeats a prompt every few minutes, or at your own pace,
until you say it is done. It is `/workflow loop <prompt>`: it runs the
built-in workflow `loop`, which repeats the person's prompt in your own
session, round after round, on a server timer that survives a restart. It takes the
place of the harness's own /loop. Its definition:

```yaml
id: loop
title: Repeat a prompt until it says done
say: Run the prompt round after round, until the agent says it is done or the rounds run out.
args:
  type: object
  properties:
    prompt:
      type: string
      description: What to do each round, in the person's words without the interval.
    every:
      type: string
      description: A fixed wait between rounds, such as 5m or 1h30m, when the person gave one. Without it the agent picks each wait.
    max:
      type: integer
      default: 20
      minimum: 1
      maximum: 100
      description: The most rounds, from 1 to 100.
  required: [prompt]
root:
  loop:
    max: "{{args.max}}"
    until: {expr: "tick.done"}
    at_max: done
    do:
      seq:
        - agent:
            session: main
            prompt: "{{args.prompt}}\n\nReport done: true when there is nothing left to do. Without a fixed interval, give next: how long to wait before the next round, from 1m to 1h."
            result:
              type: object
              properties:
                done: {type: boolean}
                next: {type: string}
              required: [done]
          id: tick
          say: "Do it: {{args.prompt}}"
          deadline: 2h
        - if:
            when: {expr: "not tick.done"}
            then:
              if:
                when: {expr: "args.every == null"}
                then:
                  wait: "{{tick.next or '20m'}}"
                  between: [1m, 1h]
                  id: pause
                  say: Wait as the agent asked, 1m to 1h.
                else:
                  wait: "{{args.every}}"
                  between: [1m, 1d]
                  id: interval
                  say: "Wait {{args.every}}."
              id: how-long
              say: Wait.
          id: unless-done
          say: Unless it is done, wait.
      id: round
      say: One round.
  id: rounds
  say: "Up to {{args.max}} rounds."
```

From the launch turn, put the work in `prompt` without the interval, and
the interval in `every` when the person gave one, such as `5m` for
"check the deploy every 5m", from 1m to 1d; `max` is the most rounds, 20 unless they said
otherwise, and at most 100. Each round arrives as a step in your inbox: do the work, then call
`workflow_result` with `done: true` when nothing is left to do, which ends
the loop at once. Without `every`, give `next`, the wait before the next
round, from 1m to 1h (a `next` outside that is clamped into it); without
either it waits 20 minutes. The loop ends done
when a round says so or the rounds run out, and stops when the person
presses Stop on the card or asks you to stop, which you do with
`workflow_stop`. A round left unanswered for two hours fails like any step.

## Fire one from a schedule

A schedule whose prompt is a `/workflow <id> <text>` or `/loop <text>`
line fires that saved workflow. At each fire, the session it reaches gets
the same launch turn under the schedule's header, with its own token, and
the run starts with no click: the person's approval of the schedule covers
its runs. Nobody may be watching, so when a required value cannot be made
from the text, end your turn with `turn_end` asking the person, and the
schedule fires again at its next tick.

A schedule pins the version of the workflow it fires when it is created, or
changed to fire another workflow; any other change to the schedule keeps the
version you approved. When the workflow changes, by a save, a delete or an edit of its
file, the schedule pauses for the person's approval, and its card shows what
changed in the steps since the version they approved. Unpause on that card
sends the version it showed; if the workflow changed again while they read,
the server refuses and the card shows the newer one. The card and the
Schedules view show the workflow's steps, or the change, and every shell
command and probe it runs without asking. No agent can fire such a schedule
on demand: its clock fires it, or a person does. The schedule's run stays
live while its workflow runs, so the overlap rule waits for the workflow, and
a fire into a session already running a workflow waits until that run ends.

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
give the step a `deadline`. Such a step never waits for room, since you hold
no slot: on parallel branches several can be open at once, and then each
`workflow_result` or `workflow_output` passes `node`, the step id its header
names. Stopping the run never interrupts you.

## Continue a step's session

```yaml
id: review
title: Review
say: Implement it, then fix what the tests say in the same session.
root:
  seq:
    - agent: {agent: sonnet, prompt: "Implement the export command."}
      id: impl
      say: Implement it.
    - sh: "make test"
      id: tests
      say: Run the tests.
    - agent:
        session: impl
        prompt: "The tests said: {{tests.out}}. Fix what failed."
      id: fix
      say: Fix it in the same session.
  say: Implement, test, fix.
```

`session: impl` runs the step in the session the earlier agent step `impl`
spawned, with everything that session already read. It takes no `agent` and
no `cwd`. A chain resolves to the first session: a step with `session: fix`
here would run in impl's too. The lint refuses a name that is not an earlier
agent step with a session of its own; a step that may start before that one
finishes (put it later in a seq, or name it in `after`); two steps in one
session that may run at the same time; and a step inside a map that the
session's step is not inside, since every item would use it at once. A
loop may reuse a session, one round after another. The session takes no new
place in the pool. It stays open while a step may still run in it, every
round of a loop included, and closes once each step that uses it is done,
skipped or in a branch of an `if` not taken. Two steps in the `then` and
`else` branches of one `if` may both use it, since only one runs. A step that fails in it gets its tab, and a retry
prompts the same session again.

## Inside a workflow

If your first message is headed `> from workflow:`, you are a node. Do the
task, call `workflow_result` once, and end your turn. If it is headed
`> from workflow:… · judge`, you are a judge: call `workflow_result` with
`{pass, why}` and change nothing. If your first
message names outputs other steps wait on, call `workflow_output` with each
one as soon as it is ready, then go on. If you cannot go on without an
answer, call `workflow_ask` with your question and end your turn: it reaches
the main agent, never a person, and the answer arrives as your next message.
If a later message is headed `> from workflow:… · node <id>` and says it is
the same workflow, a later step runs in your session: do it, and report it
with `workflow_result` as before.
Neither you nor a session you spawn can arm a workflow.
