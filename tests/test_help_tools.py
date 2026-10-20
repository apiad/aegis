"""help_search and help_read through the real /mcp, called by the fake claude
with its own token, and the primer and meta that point agents at them (#339)."""

import json

from aegis.mcp import PRIMER

from .test_agents import CONFIG, World, mcp, turn


def test_the_primer_tells_an_agent_to_search_the_help_first():
    assert "call help_search" in PRIMER
    assert "how to do something in aegis" in PRIMER


async def test_an_agent_searches_and_reads_the_help_through_mcp(tmp_path, fake_claude):
    (tmp_path / ".aegis.yaml").write_text(CONFIG)
    w = await World(tmp_path, fake_claude).start()
    try:
        # A read session: both tools only read, so it has them too.
        r = await w.app.registry.call("session.spawn", {"agent": "reviewer"})
        s = w.session(r["log_id"])
        said = await turn(s, mcp("help_search", query="wait for CI without polling"))
        hits = json.loads(said.removeprefix("mcp ok: "))["hits"]
        assert hits and set(hits[0]) == {"ref", "heading", "when", "snippet"}
        assert hits[0]["ref"].split("#")[0] == "monitors", hits

        said = await turn(s, mcp("help_search", query="keep a page in view"))
        hits = json.loads(said.removeprefix("mcp ok: "))["hits"]
        assert hits[0]["ref"] == "artifacts#pages-that-stay-in-view", hits

        said = await turn(s, mcp("help_read", page="monitors#wait-on-ci"))
        assert said.startswith("mcp ok: ## Wait on CI\n"), said

        said = await turn(s, mcp("help_read", page="monitor"))
        assert said.startswith("mcp error: unknown_page: no help page 'monitor'")
        assert "monitors" in said

        said = await turn(s, mcp("help_read", page="monitors", section="nope"))
        assert said.startswith("mcp error: unknown_section: monitors has no section")

        said = await turn(s, mcp("help_search", query="zebra xylophone"))
        empty = json.loads(said.removeprefix("mcp ok: "))
        assert empty["hits"] == [] and {"monitors", "getting-started"} <= {
            p["id"] for p in empty["pages"]
        }

        said = await turn(s, mcp("meta"))
        assert said.rstrip().endswith("help_search finds how to use any of these.")
        assert "call help_search" in said
    finally:
        await w.stop()


def test_the_primer_names_the_feedback_turn_and_offers_to_file():
    assert (
        "`> from feedback · …` when the person asks you to report something "
        "about aegis" in PRIMER
    )
    assert "Offer once per problem and never stop work to" in PRIMER
    assert "follow help_read feedback" in PRIMER
