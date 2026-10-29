---
title: Writing help pages
kind: howto
when: adding or updating a help page after changing a tool, a command, the CLI or the config
hints:
  - Every help page lists hints like this one in its front matter. #the-format
---

The help pages are Markdown files shipped inside aegis, one per topic, in
`src/aegis/help/`. Agents search them with `help_search` and read them with
`help_read`; people will read them in the browser. They are written by hand,
by the agent that changes the feature, in the same pull request. A change a
person or an agent will notice is not done until its page says how to use it.

## The format

A page's file name is its id: `monitors.md` is the page monitors. It opens
with four keys:

```yaml
---
title: Monitors
kind: howto
when: waiting on a build, a test run, CI or other sessions without polling
hints:
  - Agents wait on builds and CI with a monitor that wakes them. #wait-on-a-process
---
```

- `title:` the page's name. Write no `#` heading in the body; the title is
  the page's heading.
- `kind:` concept (what something is and how its parts fit), howto (how to
  do one job) or tutorial (steps to follow, which a guide can walk a person
  through).
- `when:` one line saying when to read the page. Search counts its words
  twice and the title's three times, apart from the section's own text, so
  these two lines decide which page answers: phrase them the way someone
  would ask.
- `hints:` one to three items, each one sentence of at most 90 characters
  inviting the person to try something on the page. The new tab shows them
  one at a time, and a click opens the page, or the section a trailing
  `#slug` names. A page with no hints, more than three, a longer one or a
  `#slug` that is not one of its sections does not load.

Open with a paragraph that says what the page covers, then one `## ` heading
per section. A section is addressed `page#slug`, the heading in lowercase
with every run of other characters turned into one hyphen, so two headings on
a page must not slug alike. Keep a section to one idea: search returns
sections, and an agent reads the one it was pointed to. `help_read` also
opens a `### ` heading inside a section by its slug, from the heading to the
next `### `; search never returns one.

A tutorial's sections are numbered steps. Each says what to do and what the
person should see. A step that starts an agent, spends quota or reaches
another server says so in its first line, so the guide asks first. No check
reads this; read your steps for it.

## How a page names things

The checks read names, so each kind of name is written one way:

- an agent tool as an agent calls it, in backticks: `monitor_start`. A
  backticked word with an underscore and nothing else is always read as a
  tool;
- a parameter with an underscore, with its value: `timeout_s=600`, and a
  configuration key with its colon: `max_parallel:`;
- an aegis slash command with its slash, in backticks: `/spawn`;
- a harness's own command in plain text with the harness named, as in
  Claude Code's /compact, never in backticks, because it is not aegis's;
- a subcommand of the `aegis` program in backticks, starting with aegis:
  `aegis link add`, with its arguments in angle brackets;
- an error code in plain text, such as not_resumable;
- a file in this repository by its path from the root, in backticks, only on
  pages about working on aegis itself, since a person using aegis has no
  checkout.

## The checks

Two checks compare the pages with the code, in both directions. Every agent
tool and slash command must appear on a page, every `aegis` subcommand on the
commands page and every configuration key on the config page; every tool,
command, subcommand and repository path a page names must exist. A third
check holds search to the questions it must answer.

- `make lint-docs` runs rift on the rules in `.rift.yaml`; it is quick, so
  run it while you write. It is the only check of repository paths.
- `tests/test_help_pages.py` asks the rest of the same questions of the live
  registry, the commands, the command-line program and the configuration
  models, adds the configuration keys and the frontmatter rules, and runs in
  `make test` and in CI.
- `tests/test_help_index.py` holds a table of questions an agent would ask.
  A question with one answer must find that page, or that section, first; a
  question two pages answer must find one of them in its first three
  results. A page that stops answering one of them fails it; fix the page's
  `when:` line or its wording, and add a question when you add a page.

Neither check reads the prose. A page can name only real tools and still
describe one wrongly, so read the code you describe, and check every number
and default against it.

## Try it as an agent would

After editing, search for the questions the page should answer and read what
comes back:

```bash
uv run python -c "from aegis.helppages import Index, load; print(*Index(load()).search('wait for CI without polling'), sep='\n')"
```
