---
when: a PR is ready for Alex, or a release is about to be cut — offering Alex the guided smoke test, running the build under test beside his aegis with a tour agent inside it, and what to do with what the tour finds
---

# The guided smoke test

AGENTS.md makes Alex's own hands on the build the last gate. This is how that
happens: the build under test runs beside his everyday aegis, and an agent
inside it walks him through what changed, simplest first, asking after each
step whether it works. The first run, on 2026-10-09 before the 2.4 release,
found four problems that 1,000 green tests had not (#241, #242, #243, #244).

## 1. Offer it

When a PR is ready, and before every release, ask Alex whether he wants the
smoke test now. Do nothing on a no. A yes covers the steps below.

## 2. Start the build under test beside his aegis

His aegis serves :8742, and the session you are in runs on it: never stop or
restart it. Run the build in its own root and port, detached so it outlives
your session:

```bash
mkdir -p ~/aegis-smoke && cp ~/Workspace/.aegis.yaml ~/aegis-smoke/
fuser 8791/tcp || echo free          # another agent's aegis-dev may hold a port
cd ~/aegis-smoke && AEGIS_REF=<branch|main> ~/Workspace/bin/aegis-dev \
  serve -d --window --port 8791 --root ~/aegis-smoke
```

`-d` returns once the port listens and prints the URL, the pid and the log
path; `--window` opens the app window on his desktop. Without `-d` the server
dies with the shell that started it, which on 2026-10-09 was a resumed
session. Note the commit `aegis-dev` prints: the tour names it.

## 3. Write the tour

List what changed: on a PR its own `changelog.d/` fragments, before a release
every fragment since the last tag (`git describe --tags --abbrev=0 origin/main`).
Order them by how much the person has to do, from looking at something to
coordinating several sessions. Each step says what changed in a sentence or
two and exactly what to do or look at.

The prompt's rules, which the first tour showed work:

- talk to Alex in his language, short messages;
- keep the tour as the plan (`plan_update`), one step per turn, and end each
  turn with `turn_end(attention="needs_you", replies=["funciona", "no funciona"])`;
- when something fails, keep Alex's words, do not debug, move on;
- ask before anything that spends quota (an OpenCode session) or reaches
  another server;
- for a CLI feature, run the build under test's CLI
  (`uvx --from git+https://github.com/apiad/aegis@<commit> aegis …`), not the
  `aegis` on PATH, which is the release;
- end with one line per step and Alex's words for each failure, and file no
  issue without a yes.

Make the features exercise each other: the tour's own plan tests the plan,
an artifact can be the form where Alex reviews the steps so far.

**Links need the new version on both ends.** A release older than links
refuses the link's socket with a bare 403 (#244). To test links, start a
second `aegis-dev` of the same ref on another port and root, and link the two;
never link to the aegis on :8742.

## 4. Create the tour agent

```bash
uv run python scripts/smoke.py spawn --port 8791 --root ~/aegis-smoke --prompt-file ~/aegis-smoke/tour.md
```

It creates an `opus` session in the build under test the way a browser does,
so the tab appears in Alex's window. Then wait until it has asked its first
question, with a monitor whose `done` is
`uv run python scripts/smoke.py read --asked <transcript>`. That reads
aegis's own `turn_end` record. Grepping the transcript for `turn_end` passes
at once, because the prompt that asks for it is in the transcript too.

## 5. Read the result, then fix or file

When Alex says the tour is over:

```bash
uv run python scripts/smoke.py read ~/aegis-smoke/.aegis/state/transcripts/<log_id>.jsonl
```

prints the conversation: his answers, the agent's steps, tool errors. Find the
cause of each problem before acting on it; one of the first run's four was
the tour's design, not aegis.

- **On a PR:** fix what is easy in the same PR, and file the rest.
- **Before a release:** file each problem as an issue, with Alex's words and
  the cause, unless the fix on main is trivial.

Ask him whether to stop the build under test (`kill <pid>` from step 2); it
holds the port and a window until then.
