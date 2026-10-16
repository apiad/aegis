---
title: Workflow recipes
kind: howto
when: writing a workflow for a common shape by copying one: implement and review, merge on green CI, one agent per item, an early output, a nightly run, polling, a judge's check
---

Each recipe is a whole workflow that lints as it stands. Copy the closest
one, change its prompts, and use the agent names in your .aegis.yaml where it
says sonnet and opus. [Workflows](workflows) explains every field. Every step
has an id, so a change to a running copy keeps them, and every value reaches
a command through its environment: write `{{...}}` in a command as a bare
word, never inside quotes. Only a session with full permission arms a
workflow with commands.

## Implement, review and fix until approved

```yaml
id: build-and-review
title: Implement a change and review it until approved
say: One agent implements the change and opens a PR, the tests run, and a second agent reviews it until it approves.
args:
  type: object
  properties:
    task: {type: string, description: "What to build, in the person's words."}
  required: [task]
defs:
  work: {type: object, properties: {pr: {type: integer}, worktree: {type: string}}, required: [pr, worktree]}
  verdict: {type: object, properties: {approved: {type: boolean}, notes: {type: string}}, required: [approved, notes]}
root:
  seq:
    - agent: {agent: sonnet, result: {$ref: "#/defs/work"},
              prompt: "In a new git worktree, implement this with tests and open a PR: {{args.task}}"}
      id: impl
      say: "Implement it and open a PR: {{args.task}}"
    - sh: "cd {{impl.worktree}} && make test"
      id: tests
      retries: 1
      say: Run the tests in its worktree.
    - loop:
        max: 3
        until: {expr: "review.approved"}
        do:
          seq:
            - agent: {agent: opus, result: {$ref: "#/defs/verdict"},
                      prompt: "Review PR {{impl.pr}} against this task, harshly: {{args.task}}"}
              id: review
              say: Have a second agent review the PR.
            - if:
                when: {expr: "not review.approved"}
                then:
                  agent: {session: impl, prompt: "The review asks for changes: {{review.notes}} Fix them, run the tests and push."}
                  id: fix
                  say: Fix what the review found, in the session that wrote it.
              id: unless-approved
              say: Unless it approved, fix it.
          id: round
          say: One review.
      id: reviews
      say: Review and fix, at most three times.
  id: all
  say: Implement, test, review.
```

The fix runs in the session that wrote the change, with everything it
already read. Three reviews that never approve fail the loop, and the
failure comes to you. To merge as well, put the next recipe's steps after
these in the same `seq`, reading `{{impl.pr}}` where it reads `{{args.pr}}`.

## Merge on green CI, after the person agrees

```yaml
id: merge-on-green
title: Merge a PR on green CI, after the person agrees
say: Wait for every CI check on the PR to pass, fixing it when one fails, then ask the person and merge.
args:
  type: object
  properties:
    pr: {type: integer, description: The pull request to merge.}
  required: [pr]
root:
  seq:
    - loop:
        max: 3
        until: {sh: "gh pr checks {{args.pr}} --json bucket -q 'length > 0 and all(.[]; .bucket == \"pass\")' | grep -qx true"}
        do:
          seq:
            - wait: 1m
              id: settle
              say: Give CI a moment to start on the latest push.
            - check: {sh: "gh pr checks {{args.pr}} --json bucket -q 'length > 0 and all(.[]; .bucket != \"pending\")' | grep -qx true", every: 1m}
              id: finished
              deadline: 1h
              retries: 1
              say: Wait until every CI check has finished, and at least one ran.
            - if:
                when: {sh: "gh pr checks {{args.pr}} --json bucket -q 'any(.[]; .bucket == \"fail\")' | grep -qx true"}
                then:
                  agent: {agent: sonnet, prompt: "CI failed on PR {{args.pr}}. Read the failed logs, fix the cause and push."}
                  id: fix
                  say: Fix what made CI fail and push.
              id: if-red
              say: If a check failed, fix it.
          id: round
          say: One CI run.
      id: ci
      say: Wait for green CI, fixing it at most three times.
    - gate: {by: person, ask: "CI is green on this PR. Merge it?", show: [args.pr]}
      id: go
      say: Ask the person before merging.
    - sh: "gh pr merge {{args.pr}} --squash --delete-branch"
      id: merge
      say: Merge the PR.
  id: all
  say: Green CI, the person's go, then the merge.
```

`length > 0` makes "green" mean at least one check ran: a PR whose checks
have not started yet is not green. The wait at the start of each round keeps
a fix's push from being judged by the run before it.

## One step per item, a few at a time

```yaml
id: per-item
title: One agent per item, two at a time, each result judged
say: For each item, two at a time, an agent does the work and a judge checks it.
args:
  type: object
  properties:
    items: {type: array, items: {type: string}, description: One line per piece of work.}
  required: [items]
limits: {total: 4}
root:
  map:
    over: "{{args.items}}"
    as: it
    cap: 2
    do:
      seq:
        - agent: {agent: sonnet, prompt: "Do this and report what you changed: {{it}}",
                  result: {type: object, properties: {changed: {type: string}}, required: [changed]}}
          id: work
          say: "Do it: {{it}}"
        - check: {judge: {agent: opus, inputs: [work],
                          prompt: "Does this change do what was asked, and nothing else? Asked: {{it}}"}}
          id: verify
          say: "Have a judge check the work on: {{it}}"
      id: one
      say: "{{it}}: do it, then check it."
  id: each
  say: Every item, two at a time.
```

`cap: 2` runs two items at once; each item goes through both of its steps
on its own, so a judged item does not wait for the others. Judges count in
`limits` like any agent.

## Wait for another branch's early output

```yaml
id: api-and-client
title: Build an API and its client side by side
say: One agent builds the API and gives out its schema early; a second writes the client as soon as the schema exists.
root:
  par:
    - agent: {agent: sonnet, result: {type: object, properties: {pr: {type: integer}}, required: [pr]},
              prompt: "Build the export API. Once its OpenAPI schema is settled, give its path as the output schema, then finish the API."}
      id: api
      say: Build the export API, giving out its schema first.
    - agent: {agent: sonnet, prompt: "Write the export client against the schema at {{api.schema}}."}
      id: client
      after: [api.schema]
      say: Write the client once the API's schema is out.
  id: both
  say: The API and its client at once.
```

`after: [api.schema]` waits for the output named schema, which the API's
agent gives with `workflow_output` while it keeps working; the client
starts then, not when the whole API is done.

## A saved workflow every night

```yaml
id: nightly-deps
title: Update the dependencies every night
say: Update the lockfiles on a branch, open a PR, wait for CI, and leave the merge to the person.
root:
  seq:
    - agent: {agent: sonnet, result: {type: object, properties: {pr: {type: integer}}, required: [pr]},
              prompt: "On a new branch, update the lockfiles, run the tests, and open a PR. Report its number."}
      id: update
      say: Update the lockfiles and open a PR.
    - check: {sh: "gh pr checks {{update.pr}} --json bucket -q 'length > 0 and all(.[]; .bucket == \"pass\")' | grep -qx true", every: 5m}
      id: green
      deadline: 2h
      say: Wait until every CI check on the PR passes.
    - gate: {by: person, ask: "The dependency update is green. Merge it?", show: [update.pr]}
      id: go
      say: Ask the person, who may answer in the morning.
    - sh: "gh pr merge {{update.pr}} --squash --delete-branch"
      id: merge
      say: Merge the update.
  id: all
  say: Tonight's dependency update.
```

Save it, then give it a clock:

```text
workflow_save(definition="<the workflow above>")
schedule_create(name="nightly-deps", clock={"cron": "0 3 * * *"},
                fire={"agent": "sonnet"}, prompt="/workflow nightly-deps")
```

`schedule_create` from an agent makes a paused schedule, with a card under
the call; it runs once the person presses Unpause there. Each night at 3 it
starts a session, which gets the launch turn and arms the workflow with no
click. A gate for the person waits without a timer, so the merge waits for
the morning.

## Poll until something is true

```yaml
id: after-deploy
title: Smoke-test the site once the deploy is live
say: Wait until the site answers with the new version, then smoke-test it.
args:
  type: object
  properties:
    version: {type: string, description: The version the deploy ships.}
  required: [version]
root:
  seq:
    - check: {sh: "curl -fsS https://example.com/version | grep -qxF {{args.version}}", every: 2m}
      id: live
      deadline: 3h
      say: "Wait until the site answers with version {{args.version}}."
    - agent: {agent: sonnet, prompt: "Smoke-test https://example.com: sign in, export a report, and say what broke."}
      id: smoke
      say: Smoke-test the live site.
  id: all
  say: Wait for the deploy, then test it.
```

A check costs no agent while it waits: use one whenever a command can tell.
When only an agent can tell, because each round means reading something and
judging it, the person types a `/loop` line instead, such as:

```text
/loop look at the error dashboard and say when the 500s are gone every 10m
```

## Let a judge decide before going on

```yaml
id: release-notes
title: Write the release notes, judge them, then publish
say: An agent drafts the notes, a judge checks them against the merged PRs, and they are published only if it agrees.
args:
  type: object
  properties:
    tag: {type: string, description: "The tag to release, such as v2.4.0."}
  required: [tag]
root:
  seq:
    - agent: {agent: sonnet, result: {type: object, properties: {path: {type: string}}, required: [path]},
              prompt: "Write release notes for {{args.tag}} from the PRs merged since the last tag, into notes.md."}
      id: notes
      say: "Draft the release notes for {{args.tag}}."
    - check: {judge: {agent: opus, inputs: [notes],
                      prompt: "Read the notes file. Does it name every PR merged since the last tag, in words a user understands?"}}
      id: judged
      say: Have a judge check the notes against the merged PRs.
    - sh: "gh release create {{args.tag}} --notes-file {{notes.path}}"
      id: publish
      say: "Publish release {{args.tag}}."
  id: all
  say: Notes, judged, then published.
```

A judge answers yes or no once. A no fails the check and comes to you:
answer retry to ask it again, or change the notes step first with
`workflow_edit`. To redraft until a judge agrees, put the judge in a loop's
until; [Workflows](workflows) shows how.
