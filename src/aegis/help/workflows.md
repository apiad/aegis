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
runs `seq` and `agent`; the others in the spec come next.

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
when the run ends. `workflow_status` shows the tree at any time, and
`workflow_stop` stops the run and unlocks its sessions.

## Inside a workflow

If your first message is headed `> from workflow:`, you are a node. Do the
task, call `workflow_result` once, and end your turn. Nobody answers
questions here, and neither you nor a session you spawn can arm a workflow.
