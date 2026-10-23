from datetime import date

import pytest

from aegis.knowledge.items import check_name, dump_memory, parse_memory, parse_skill
from aegis.ops import OpError

MEM = """---
name: deploy-destination
description: Check where a service runs before touching a server
type: feedback
lifetime: temporary
expires: 2026-12-01
paths: [repos/bot/*]
---
Body with [[other-item]].
"""


@pytest.mark.parametrize(
    "bad", ["../x", "Deploy Notes", "", "-lead", "a" * 65, "UPPER"]
)
def test_unsafe_names_are_refused(bad):
    with pytest.raises(OpError) as e:
        check_name(bad)
    assert e.value.code == "bad_name"


def test_a_memory_round_trips():
    it = parse_memory(MEM, "deploy-destination")
    assert it.kind == "memory" and it.status == "approved"
    assert it.lifetime == "temporary" and it.expires == date(2026, 12, 1)
    assert it.paths == ["repos/bot/*"] and "[[other-item]]" in it.body
    assert parse_memory(dump_memory(it), it.name) == it


def test_a_skill_keeps_its_aegis_block():
    it = parse_skill(
        "---\nname: release\ndescription: Cut a release\naegis:\n  scripts: [bump.py]\n---\n1. go\n",
        "release",
    )
    assert it.kind == "skill" and it.meta["aegis"]["scripts"] == ["bump.py"]


def test_broken_frontmatter_raises_value_error():
    with pytest.raises(ValueError):
        parse_memory("---\nname: [unclosed\n---\nx", "x")


def test_a_file_without_frontmatter_raises_value_error():
    with pytest.raises(ValueError):
        parse_memory("just text\n", "x")
