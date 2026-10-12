"""The help index (helppages.py): parsing a page, BM25 over its sections, and
reading a page or a section. The last two tests are the ranking table:
questions an agent would ask, and the page or section each must find first, or,
where two pages both answer it, in its first three results."""

import pytest

from aegis.helppages import Index, load, parse, slug

PAGE = """\
---
title: Monitors
kind: howto
when: waiting on a build or CI without polling
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
    ("wait for CI without polling", "monitors#wait-on-ci"),
    ("how do I wait until a GitHub Actions run finishes", "monitors#wait-on-ci"),
    ("wait until other sessions finish", "monitors#wait-on-other-sessions"),
    ("show the person a page they can click", "artifacts"),
    ("send the person a screenshot", "artifacts#send-a-file-instead"),
    ("run a batch of tasks in parallel workers", "queues"),
    ("cancel a task I enqueued", "queues#check-cancel-and-resume"),
    ("give another session an instruction", "handoffs#hand-work-over"),
    ("read what another agent is doing", "handoffs"),
    ("link another aegis server", "links"),
    ("add an agent to .aegis.yaml", "config#add-an-agent"),
    ("install aegis on my phone home screen", "phone"),
    ("turn on push notifications for this device", "phone#push-notifications"),
    ("what does read permission allow", "sessions#permission"),
    ("rename my session", "sessions#rename-it"),
    ("run aegis in the background", "commands"),
    ("first time using aegis", "getting-started"),
    ("update a help page after changing a tool", "writing-help-pages"),
]

# Questions two pages answer: one of them must be in the first three.
EITHER = [
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
