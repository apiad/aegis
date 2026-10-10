from pathlib import Path

import pytest

from aegis.agents import PERMISSION_ORDER
from aegis.claude.process import PERMISSION_MODE
from aegis.mcp import HEADER
from aegis.opencode.config import catalog_from, child_config, rules, split_model


@pytest.mark.parametrize("permission", list(PERMISSION_MODE))
def test_no_permission_ever_asks(permission):
    r = rules(permission)
    assert "ask" not in r.values()
    assert (r["aegis_*"], r["question"], r["doom_loop"]) == ("allow", "deny", "deny")


def test_each_permission_denies_strictly_less_than_the_one_below_it():
    denied = [{k for k, v in rules(p).items() if v == "deny"} for p in PERMISSION_ORDER]
    for lower, higher in zip(denied, denied[1:]):
        assert higher < lower
    assert {"edit", "bash", "task", "external_directory"} <= denied[0]
    assert "edit" not in denied[1] and "bash" in denied[1]
    assert denied[3] == {"question", "doom_loop"}


def test_the_child_config_carries_the_token_and_the_rules():
    c = child_config(("http://127.0.0.1:9/mcp", "tok"), "read")
    assert c["mcp"]["aegis"] == {
        "type": "remote",
        "url": "http://127.0.0.1:9/mcp",
        "headers": {HEADER: "tok"},
        "oauth": False,
        "enabled": True,
    }
    assert c["permission"] == rules("read")
    assert "mcp" not in child_config(None, "full")


def test_a_model_splits_at_the_first_slash():
    assert split_model("opencode-go/deepseek-v4-pro") == {
        "providerID": "opencode-go",
        "modelID": "deepseek-v4-pro",
    }
    assert split_model("openrouter/qwen/qwen3-32b")["modelID"] == "qwen/qwen3-32b"


def test_the_catalog_lists_models_with_variants_and_windows_and_commands():
    cat = catalog_from(
        [
            {
                "name": "hello",
                "description": "Greet someone. More.",
                "source": "command",
            },
            {"name": "unslop", "description": "Cut AI tells.", "source": "skill"},
            {"description": "nameless"},
        ],
        {
            "providers": [
                {
                    "id": "go",
                    "models": {
                        "pro": {
                            "name": "Pro",
                            "limit": {"context": 1000000},
                            "variants": {"high": {}, "max": {}},
                        },
                        "plain": {"name": "Plain", "limit": {"context": 200000}},
                    },
                }
            ]
        },
    )
    assert [(c["name"], c["source"], c["doc"]) for c in cat.commands] == [
        ("hello", "opencode", "Greet someone."),
        ("unslop", "skill", "Cut AI tells."),
    ]
    pro = cat.model("go/pro")
    assert (pro.label, pro.efforts, pro.window) == ("Pro", ("high", "max"), 1000000)
    assert cat.model("go/plain").efforts == ()


def test_a_free_model_costs_nothing_calls_tools_and_writes_text():
    def model(cost, toolcall=True, text=True):
        return {
            "name": "m",
            "cost": cost,
            "capabilities": {"toolcall": toolcall, "output": {"text": text}},
        }

    cat = catalog_from(
        [],
        {
            "providers": [
                {
                    "id": "p",
                    "models": {
                        "paid": model({"input": 1, "output": 2}),
                        "video": model(
                            {"input": 0, "output": 0}, toolcall=False, text=False
                        ),
                        "free": model({"input": 0, "output": 0}),
                        "unpriced": {"name": "u"},
                    },
                }
            ]
        },
    )
    assert {m.value: m.free for m in cat.models} == {
        "p/paid": False,
        "p/video": False,
        "p/free": True,
        "p/unpriced": False,
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
    assert cfg["permission"]["external_directory"]["/s/inbox/l/*"] == "allow"
