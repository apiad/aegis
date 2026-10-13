from pathlib import Path

import pytest

from aegis.agents import PERMISSION_ORDER
from aegis.claude.process import PERMISSION_MODE
from aegis.mcp import HEADER
from aegis.opencode.config import (
    catalog_from,
    child_config,
    rules,
    ruleset,
    split_model,
)


@pytest.mark.parametrize("permission", list(PERMISSION_MODE))
def test_no_permission_ever_asks(permission):
    r = rules(permission)
    assert "ask" not in r.values()
    assert (r["aegis_*"], r["question"], r["doom_loop"]) == ("allow", "deny", "deny")


def test_each_permission_denies_strictly_less_than_the_one_below_it():
    denied = [{k for k, v in rules(p).items() if v == "deny"} for p in PERMISSION_ORDER]
    for lower, higher in zip(denied, denied[1:]):
        assert higher < lower
    assert {"edit", "write", "shell", "subagent", "external_directory"} <= denied[0]
    assert "edit" not in denied[1] and "shell" in denied[1]
    assert denied[3] == {"question", "doom_loop"}


def test_the_child_config_carries_the_token_and_the_rules():
    c = child_config(("http://127.0.0.1:9/mcp", "tok"), "read")
    assert c["mcp"]["servers"]["aegis"] == {
        "type": "remote",
        "url": "http://127.0.0.1:9/mcp",
        "headers": {HEADER: "tok"},
        "oauth": False,
        "codemode": False,
    }
    assert c["permissions"] == ruleset("read")
    assert "mcp" not in child_config(None, "full")


def test_the_ruleset_puts_the_catch_all_first_so_every_deny_wins():
    rs = ruleset("write", (Path("/s/inbox/l"),))
    assert rs[0] == {"action": "*", "resource": "*", "effect": "allow"}
    assert {"action": "shell", "resource": "*", "effect": "deny"} in rs
    ext = [r for r in rs if r["action"] == "external_directory"]
    assert [r["resource"] for r in ext] == ["*", "/s/inbox/l/*"]


def test_a_model_splits_at_the_first_slash():
    assert split_model("opencode-go/deepseek-v4-pro") == {
        "providerID": "opencode-go",
        "id": "deepseek-v4-pro",
    }
    assert split_model("openrouter/qwen/qwen3-32b")["id"] == "qwen/qwen3-32b"


def model(mid, name="m", cost=None, tools=True, output=("text",), **kw):
    return {"id": mid, "providerID": "go", "name": name,
            "capabilities": {"tools": tools, "output": list(output)},
            **({"cost": [cost]} if cost is not None else {}), **kw}  # fmt: skip


def test_the_catalog_lists_models_with_variants_and_windows_and_commands():
    cat = catalog_from(
        [
            {"name": "hello", "description": "Greet someone. More."},
            {"description": "x"},
        ],
        [{"id": "unslop", "name": "Unslop", "description": "Cut AI tells."}],
        [
            model(
                "pro",
                "Pro",
                limit={"context": 1000000},
                variants=[{"id": "high"}, {"id": "max"}],
            ),  # fmt: skip
            model("plain", "Plain", limit={"context": 200000}),
            model("off", enabled=False),
        ],
    )
    assert [(c["name"], c["source"], c["doc"]) for c in cat.commands] == [
        ("hello", "opencode", "Greet someone."),
        ("unslop", "skill", "Cut AI tells."),
    ]
    pro = cat.model("go/pro")
    assert (pro.label, pro.efforts, pro.window) == ("Pro", ("high", "max"), 1000000)
    assert cat.model("go/plain").efforts == ()
    assert cat.model("go/off") is None


def test_a_free_model_costs_nothing_calls_tools_and_writes_text():
    cat = catalog_from([], [], [
        model("paid", cost={"input": 1, "output": 2}),
        model("video", cost={"input": 0, "output": 0}, tools=False, output=("video",)),
        model("free", cost={"input": 0, "output": 0}),
        model("unpriced"),
    ])  # fmt: skip
    assert {m.value: m.free for m in cat.models} == {
        "go/paid": False,
        "go/video": False,
        "go/free": True,
        "go/unpriced": False,
    }


@pytest.mark.parametrize("permission", ["read", "write", "auto"])
def test_the_inbox_is_the_one_outside_path_a_denying_permission_reads(permission):
    ext = rules(permission, (Path("/s/inbox/l"),))["external_directory"]
    assert ext == {"*": "deny", "/s/inbox/l/*": "allow"}
    assert list(ext) == ["*", "/s/inbox/l/*"]  # the specific rule last, so it wins


def test_full_permission_needs_no_inbox_rule():
    assert "external_directory" not in rules("full", (Path("/s/inbox/l"),))


def test_the_child_config_carries_the_inbox_rule():
    cfg = child_config(None, "write", (Path("/s/inbox/l"),))
    assert {
        "action": "external_directory",
        "resource": "/s/inbox/l/*",
        "effect": "allow",
    } in cfg["permissions"]
