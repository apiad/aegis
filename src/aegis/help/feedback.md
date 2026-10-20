---
title: Sending feedback about aegis
kind: howto
when: reporting a bug, asking for a feature or pointing out a docs gap in aegis itself, or after /feedback
---

This page is how an agent turns a person's report about aegis into a GitHub
issue a maintainer can act on. The person types `/feedback` with a few words,
or none, or agrees when you offer to file something aegis got wrong. You pick
the kind, check that it is new, ask only what is missing, redact, show a
draft, and file it once they say yes. Nothing is published before that yes:
the repository is public and the issue goes out under the person's name.

## Is it aegis

This is for aegis itself: its tools, commands, help pages, browser view and
server. A fault in the project the session works on is not feedback. A fault
in the harness goes to that harness's own tracker:

- Claude Code: https://github.com/anthropics/claude-code/issues (or its own
  /feedback command, run outside aegis);
- OpenCode: https://github.com/anomalyco/opencode/issues;
- Codex: https://github.com/openai/codex/issues.

When you cannot tell whether aegis or the harness is at fault, file it here
and say so in the issue.

## Pick the kind

- **bug**: aegis did something wrong, crashed, or contradicted its help page.
- **feature**: aegis cannot do something the person wants.
- **docs**: a help page is wrong, missing, or did not answer the question.
- **question**: the person does not know which of the three it is.

Tell the person which kind you picked and why, in one line. They can
correct you.

## Check before you ask

1. Run `help_search` for the behaviour. If aegis already does what a feature
   request asks, the answer is the help section, not an issue.
2. For a bug, find the cause when you can. `aegis --version` gives the
   version. Inside aegis's environment,
   `python -c "import aegis; print(aegis.__file__)"` gives the installed
   source, and `aegis map` reads it. Name the file and line in the issue.
3. Search for a duplicate in open and closed issues, as in
   [File it](#file-it). A clear match turns the draft into a comment on
   that issue.

## What each kind needs

Each kind has a GitHub issue form. Each field below has a label, which is
the heading in the issue body, and an id, which a prefilled link uses. The
starred ones are required.

```text
bug       What happened* (what-happened) · What you expected* (expected) · Steps to reproduce* (steps) · Version* (version) · Evidence (evidence) · Context (context)
feature   The problem* (problem) · What you propose* (proposal) · What you do today (workaround) · Version* (version) · Context (context)
docs      Page or search* (page) · What is wrong or missing* (wrong-or-missing) · Suggested text (suggested) · Version* (version)
question  Question* (question) · What you tried (tried) · Version* (version)
```

What a maintainer needs from each field:

- **What happened**: the observed behaviour, with the exact error text.
  Good: "monitor_start returned `progress must be a command` although I
  passed null". Weak: "monitors are broken".
- **Steps to reproduce**: numbered steps from a fresh session, or a command
  block. Good: three steps ending in the error. Weak: "it happens sometimes".
- **The problem**: the moment it was wanted and what the person was doing.
  Good: "watching three queue workers, I could not tell which ran on the
  small model". Weak: "better queue cards".
- **What you propose**: what aegis should do, seen from the person's side,
  not how to build it.
- **Page or search**: the page id and section (queues#cancel-a-task), or the
  search that found nothing.
- **Version**: the aegis version, the harness and its version, the OS, and
  the browser when the browser view is involved. The /feedback turn gives
  you most of it.
- **Evidence**: logs, a transcript excerpt, a screenshot. See
  [Transcripts and examples](#transcripts-and-examples).

The lint workflow on the repository checks every feedback issue against this
list. It reads the text block above, so keep a field's label here the same
as in the form.

## How much to ask

Fill every field the conversation already answers. A bug you hit yourself in
this session usually needs no questions. Ask only for what is left, one
question per message, about a concrete moment ("what had you clicked just
before it froze?") rather than a category. Stop asking when a maintainer
could act on the issue. If the person says "just file it", file what you
have and leave optional fields out.

## Redact

Before you show the draft, remove:

- session handles, server and host names, user names and emails;
- home paths (keep the path from the repository root);
- tokens, keys and anything shaped like one;
- whatever about the person's own project the issue does not need.

Under the draft, say in one line what you removed.

## Show the draft

Show the title and the body exactly as they will be filed, in one fenced
block, with each field as a `### Label` heading in the order above. Then end
your turn with `turn_end`, attention needs_you, and the replies "file it" and
"change something". Any reply other than a yes is an edit: show the new draft
and wait again.

## File it

The repository is the one in the `issues:` line of the /feedback turn. Each
kind has a label: bug is bug, feature is enhancement, docs is documentation,
question is question.

**Search for duplicates.** With `gh` logged in (`gh auth status` exits 0):

```bash
gh issue list -R <owner>/<repo> --state all --search "<a few words>"
```

Without it, GitHub's public search allows ten requests a minute:

```bash
curl -s "https://api.github.com/search/issues?q=repo:<owner>/<repo>+<words>"
```

**With `gh` logged in**, write the body to a file, with the kind marker as
its last line, and file it:

```bash
gh issue create -R <owner>/<repo> --title "<title>" --body-file body.md --label <label>
```

```text
<!-- aegis-feedback kind=feature -->
```

GitHub drops the label when the person cannot triage the repository. The
marker is how the lint workflow knows the kind then.

**Without `gh`**, give the person a link that opens the issue form with
every field filled:

```text
https://github.com/<owner>/<repo>/issues/new?template=<kind>.yml&title=<title>&<id>=<value>&...
```

URL-encode each value. GitHub refuses a link that is too long, so keep it
under about 6,000 characters: leave Evidence out of the link and give it to
the person as a file to paste or drop into that field. Tell them that nothing
is filed until they press Submit.

**A duplicate** gets a comment instead, with the person's case, version and
evidence, approved the same way:

```bash
gh issue comment <number> -R <owner>/<repo> --body-file comment.md
```

## Transcripts and examples

Attach nothing from the transcript unless the person agrees. When an excerpt
would help, offer it in the draft with one line saying what it shows ("the
12 lines where the monitor fired twice"), and leave it out until they say yes.

An excerpt holds only the turns that show the problem, redacted as above,
under 200 lines, inside a collapsed block in Evidence:

```text
<details><summary>transcript excerpt</summary>

...

</details>
```

Never attach a whole session log: it holds every tool output of the session.
A larger example, such as a config file, a workflow or a screenshot, goes
into a file in the session's working directory, and the person drops it into
the issue's text box, since GitHub takes attachments only in its web form.
Do not use a gist: a secret gist is still public to anyone with the link.

## After filing

Give the person the issue's URL. When you filed with `gh`, the lint workflow
comments within a minute or two if something is missing. Wait for it with
`monitor_start`, with a done condition on
`gh issue view <number> -R <owner>/<repo> --comments`, then fix what it lists
with `gh issue edit`. Show the person the change first if it adds content. A
link the person submitted is theirs to follow up.
