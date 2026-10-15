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
runs `seq`, `par`, `map` and `agent`; the others in the spec come next.

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
is nudged once, then fails the run. You are not woken for each node: you get
one message, headed `> from workflow:<id> · done`, `failed` or `stopped`,
when the run ends. A failure ends the run and stops the nodes still running
beside it. `workflow_status` shows the tree at any time, and
`workflow_stop` stops the run and unlocks its sessions.

## Inside a workflow

If your first message is headed `> from workflow:`, you are a node. Do the
task, call `workflow_result` once, and end your turn. If your first
message names outputs other steps wait on, call `workflow_output` with each
one as soon as it is ready, then go on. Nobody answers
questions here, and neither you nor a session you spawn can arm a workflow.
