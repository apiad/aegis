"""Sample items for the knowledge tests; names from the fictional team."""

from aegis.knowledge.items import Item


def mem(name, description="a fact", body="", **meta):
    return Item("memory", name, description, body or f"About {name}.\n", meta)


def skill(name, description="a procedure", body="", **meta):
    return Item("skill", name, description, body or f"1. do {name}\n", meta)
