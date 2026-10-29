"""The help index (helppages.py): parsing a page, BM25 over its sections, and
reading a page or a section. The last two tests are the ranking table:
questions an agent would ask, and the page or section each must find first, or,
where two pages both answer it, in its first three results."""

import re

import pytest

from aegis.helppages import Index, load, parse, slug

PAGE = """\
---
title: Monitors
kind: howto
when: waiting on a build or CI without polling
hints:
  - Wait on a build without polling: a monitor wakes you. #wait-on-a-process
  - A monitor can wait for other sessions.
---

A monitor waits so you do not have to.

## Wait on a process

Call monitor_start with a done condition.

```bash
## not a heading inside a fence
```

## Wait on other sessions

monitor_sessions waits for handles.
"""

OTHER = """\
---
title: Queues
kind: howto
when: handing tasks to workers
hints:
  - Hand tasks to workers.
---

A queue runs tasks in workers, and a task can wait for a build too.
"""


def test_a_heading_slugs_to_lowercase_words_joined_by_hyphens():
    assert slug("Wait on CI") == "wait-on-ci"
    assert slug("  What's new in 2.6.0?  ") == "what-s-new-in-2-6-0"


def test_a_page_is_its_frontmatter_and_its_sections():
    p = parse("monitors", PAGE)
    assert (p.id, p.title, p.kind, p.when) == (
        "monitors",
        "Monitors",
        "howto",
        "waiting on a build or CI without polling",
    )
    assert [(s.slug, s.heading) for s in p.sections] == [
        ("", "Monitors"),
        ("wait-on-a-process", "Wait on a process"),
        ("wait-on-other-sessions", "Wait on other sessions"),
    ]
    assert p.sections[0].text == "A monitor waits so you do not have to."
    assert "## not a heading inside a fence" in p.sections[1].text


@pytest.mark.parametrize("key", ["title", "kind", "when"])
def test_a_page_missing_a_key_is_refused_by_file_name(key):
    text = "\n".join(ln for ln in PAGE.splitlines() if not ln.startswith(key + ":"))
    with pytest.raises(ValueError, match=rf"monitors\.md: .*{key}"):
        parse("monitors", text + "\n")


def test_an_unknown_kind_and_a_missing_frontmatter_are_refused():
    with pytest.raises(ValueError, match="kind 'guide' is not one of"):
        parse("x", PAGE.replace("kind: howto", "kind: guide"))
    with pytest.raises(ValueError, match=r"x\.md: no frontmatter"):
        parse("x", "# Title\n\nbody\n")


def test_two_sections_that_slug_alike_are_refused():
    with pytest.raises(ValueError, match="two sections slug to wait-on-ci"):
        parse("x", PAGE + "\n## Wait on CI\n\na\n\n## Wait on CI!\n\nb\n")


def test_hints_parse_with_their_refs():
    assert parse("monitors", PAGE).hints == (
        (
            "Wait on a build without polling: a monitor wakes you.",
            "monitors#wait-on-a-process",
        ),
        ("A monitor can wait for other sessions.", "monitors"),
    )


def _hints(*items: str) -> str:
    lines = PAGE.split("hints:\n", 1)
    rest = lines[1].split("---\n", 1)[1]
    return lines[0] + "hints:\n" + "".join(f"  - {i}\n" for i in items) + "---\n" + rest


@pytest.mark.parametrize(
    "items, words",
    [
        ((), "1 to 3 items, has 0"),
        (("a", "b", "c", "d"), "1 to 3 items, has 4"),
        (("x" * 91,), "91 characters"),
        (("Wait. #no-such-section",), "#no-such-section"),
    ],
)
def test_a_bad_hints_list_is_refused_by_file_name(items, words):
    with pytest.raises(ValueError, match=rf"monitors\.md: .*{re.escape(words)}"):
        parse("monitors", _hints(*items))


def test_search_ranks_the_page_whose_title_and_when_match():
    index = Index([parse("monitors", PAGE), parse("queues", OTHER)])
    hits = index.search("wait for a build", limit=5)
    assert hits[0].ref.startswith("monitors")
    assert {h.ref for h in hits} >= {"monitors#wait-on-a-process", "queues"}
    first = index.search("monitor_sessions handles")[0]
    assert (first.ref, first.heading) == (
        "monitors#wait-on-other-sessions",
        "Wait on other sessions",
    )
    assert first.when == "waiting on a build or CI without polling"
    assert "monitor_sessions" in first.snippet


def test_search_honours_the_limit_and_finds_nothing_for_unknown_words():
    index = Index([parse("monitors", PAGE), parse("queues", OTHER)])
    assert len(index.search("wait", limit=1)) == 1
    assert index.search("zebra xylophone") == []
    assert index.search("?!") == []  # no word at all: nothing, not an error


def test_read_returns_a_page_a_section_or_a_ref():
    index = Index([parse("monitors", PAGE)])
    assert index.read("monitors").startswith("# Monitors\n\nA monitor waits")
    one = index.read("monitors", "wait-on-a-process")
    assert one.startswith("## Wait on a process\n\nCall monitor_start")
    assert "monitor_sessions" not in one
    assert index.read("monitors#wait-on-a-process") == one
    assert index.read("monitors", "Wait on a process") == one
    assert index.read("monitors#wait-on-other-sessions", "wait-on-a-process") == one


def test_an_unknown_page_names_the_three_closest():
    index = Index([parse(i, PAGE) for i in ("monitors", "queues", "links", "config")])
    with pytest.raises(KeyError) as e:
        index.read("monitor")
    msg = e.value.args[0]
    assert msg.startswith("no help page 'monitor'; the closest are monitors, ")
    assert len(msg.split("closest are ")[1].split(", ")) == 3


def test_read_opens_a_subsection_by_its_slug_or_heading():
    """#760: a guide named a ### heading and got unknown_section."""
    text = PAGE + (
        "\n## Skills\n\nSkills intro.\n\n### Skills as commands\n\nType /name.\n\n"
        "```text\n### not a heading\n```\n\n### Writing one\n\nWrite it.\n"
    )
    index = Index([parse("knowledge", text)])
    one = index.read("knowledge", "skills-as-commands")
    assert (
        one
        == "### Skills as commands\n\nType /name.\n\n```text\n### not a heading\n```\n"
    )
    assert index.read("knowledge#Skills as commands") == one
    assert index.read("knowledge", "writing-one") == "### Writing one\n\nWrite it.\n"
    assert index.read("knowledge", "skills").startswith("## Skills\n\nSkills intro.")
    with pytest.raises(KeyError):
        index.read("knowledge", "not-a-heading")


def test_the_shipped_knowledge_page_opens_skills_as_commands():
    got = Index(load()).read("knowledge", "Skills as commands")
    assert got.startswith("### Skills as commands\n")


def test_an_unknown_section_lists_the_page_sections():
    index = Index([parse("monitors", PAGE)])
    with pytest.raises(KeyError, match="its sections are wait-on-a-process, "):
        index.read("monitors", "nope")


def test_the_listing_is_by_kind_then_title():
    pages = [
        parse("b", OTHER),
        parse("a", PAGE.replace("kind: howto", "kind: tutorial")),
        parse("c", OTHER.replace("kind: howto", "kind: concept")),
    ]
    assert [p["id"] for p in Index(pages).listing()] == ["c", "b", "a"]
    assert set(Index(pages).listing()[0]) == {"id", "title", "kind", "when"}


# Questions with one answer, and the page, or the page#section, that must be the
# first hit. A ranking change, or a page edit, that pushes it down fails here:
# an agent reads the first hit, so second is already a wrong answer.
FIRST = [
    (
        "review a change with a second agent until it approves",
        "workflow-recipes#implement-review-and-fix-until-approved",
    ),
    (
        "merge a pull request when CI is green",
        "workflow-recipes#merge-on-green-ci-after-the-person-agrees",
    ),
    (
        "run one agent per item two at a time",
        "workflow-recipes#one-step-per-item-a-few-at-a-time",
    ),
    (
        "start a step when another step gives an early output",
        "workflow-recipes#wait-for-another-branch-s-early-output",
    ),
    ("run a workflow every night", "workflow-recipes#a-saved-workflow-every-night"),
    ("poll until a deploy is live", "workflow-recipes#poll-until-something-is-true"),
    ("give the person a password I generated", "secrets#send-a-new-password"),
    (
        "wait for CI after re-running a failed job",
        "monitors#wait-on-ci-again-after-a-re-run-or-a-push",
    ),
    ("what does this icon mean", "icons"),
    ("which icon shows the effort level", "icons#session-facts"),
    ("let several people use one server as their own linux users", "multi-user"),
    ("add a person to a multi-user server", "multi-user#add-a-person"),
    ("members cannot see each other's sessions", "multi-user"),
    ("move my claude code skills and cron jobs into aegis", "setup"),
    ("merge duplicate memories and retire unused skills", "consolidate"),
    (
        "suggest a config change the person applies from a card",
        "config#suggested-changes-applied-from-a-card",
    ),
    ("ask the person for an API token", "secrets"),
    ("claude keeps logging me out", "claude-sign-in"),
    ("a claude session failed to sign in with a 401", "claude-sign-in"),
    (
        "have a judge agent check before publishing",
        "workflow-recipes#let-a-judge-decide-before-going-on",
    ),
    ("ask an agent a question from the help tab", "sessions#from-the-help-tab"),
    ("give me a guided tour of a help page", "sessions#from-the-help-tab"),
    ("wait for CI without polling", "monitors#wait-on-ci"),
    ("how do I wait until a GitHub Actions run finishes", "monitors#wait-on-ci"),
    ("wait until other sessions finish", "monitors#wait-on-other-sessions"),
    ("show the person a page they can click", "artifacts"),
    ("send the person a screenshot", "artifacts#send-a-file-instead"),
    ("keep a page in view", "artifacts#pages-that-stay-in-view"),
    ("dock a file beside the transcript", "transcript#the-canvas"),
    ("edit a file on the server in the browser", "files"),
    ("my save says the file changed on disk", "files#when-the-file-changed-on-disk"),
    (
        "pop a page out to a second browser window",
        "transcript#a-canvas-across-browser-windows",
    ),
    ("run a batch of tasks in parallel workers", "queues"),
    ("cancel a task I enqueued", "queues#check-cancel-and-resume"),
    ("give another session an instruction", "handoffs#hand-work-over"),
    ("read what another agent is doing", "handoffs"),
    ("link another aegis server", "links"),
    ("add an agent to .aegis.yaml", "config#add-an-agent"),
    ("install aegis on my phone home screen", "phone"),
    ("turn on push notifications for this device", "phone#push-notifications"),
    ("see docked panes as tabs on a phone", "phone#the-canvas-on-a-phone"),
    ("what does read permission allow", "sessions#permission"),
    ("rename my session", "sessions#rename-it"),
    ("find every caller of a function", "code-map#callers-and-impact"),
    ("what would break if I change this function", "code-map#callers-and-impact"),
    ("understand the layout of an unfamiliar repository", "code-map"),
    ("run aegis in the background", "commands"),
    ("first time using aegis", "getting-started"),
    ("learn aegis from my first session to a queue of workers", "getting-started"),
    ("update a help page after changing a tool", "writing-help-pages"),
    ("repeat a prompt every few minutes", "workflows#repeat-a-prompt-with-loop"),
    ("run a saved workflow", "workflows#run-a-saved-workflow"),
    ("make a schedule run a workflow", "schedules#fire-a-workflow"),
    ("call aegis tools from bash in a loop", "commands#tools-from-an-agent-s-shell"),
    ("pipe a tool result into another command", "commands#tools-from-an-agent-s-shell"),
]

# Questions two pages answer: one of them must be in the first three.
EITHER = [
    ("step by step tutorial for linked servers", {"tutorial-links", "links"}),
    (
        "learn the canvas and docked panes step by step",
        {"tutorial-layouts", "transcript"},
    ),
    ("try aegis on my phone step by step", {"tutorial-phone", "phone"}),
    ("tutorial for running a server for other people", {"tutorial-server", "tokens"}),
    ("send work to another server", {"handoffs", "links"}),
    ("make the agent run on haiku", {"sessions", "config"}),
    ("reopen an archived session", {"sessions", "getting-started"}),
    ("make a token for a colleague", {"commands", "tokens"}),
]


@pytest.mark.parametrize("question,want", FIRST)
def test_a_question_with_one_answer_finds_it_first(question, want):
    hits = Index(load()).search(question, limit=3)
    found = [h.ref if "#" in want else h.ref.split("#")[0] for h in hits]
    assert found[:1] == [want], f"{question!r} found {[h.ref for h in hits]}"


@pytest.mark.parametrize("question,pages", EITHER)
def test_a_question_two_pages_answer_finds_one_in_the_first_three(question, pages):
    hits = Index(load()).search(question, limit=3)
    found = [h.ref.split("#")[0] for h in hits]
    assert pages & set(found), f"{question!r} found {[h.ref for h in hits]}"


def test_getting_started_comes_first_and_its_next_names_every_other_tutorial():
    index = Index(load())
    tutorials = [p["id"] for p in index.listing() if p["kind"] == "tutorial"]
    assert tutorials[0] == "getting-started"
    nxt = next(s for s in index.pages["getting-started"].sections if s.slug == "next")
    linked = re.findall(r"\]\(([a-z0-9-]+)\)", nxt.text)
    assert sorted(linked) == sorted(tutorials[1:])
