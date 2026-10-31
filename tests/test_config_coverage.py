"""Every key .aegis.yaml takes has a place in the Settings form (#527).

The keys come from the readers' own declarations (TOP_KEYS, AGENT_KEYS, the
blocks' defaults), so a key added to a reader fails these tests until the
form's document carries it and the page has a control for it. A new top-level
block fails ``reader_keys`` until its keys are listed here.
"""

import re
from pathlib import Path

from ruamel.yaml import YAML

from aegis import config
from aegis.config import doc_from, validate, write
from aegis.net.config import KEYS as NET_KEYS
from aegis.term_profiles import PROFILE_KEYS

JS = Path(__file__).parents[1] / "src" / "aegis" / "client" / "js" / "settings.js"

# Spelled another way in the file, edited by the control of the key it names.
ALIASES = {"agents.*.provider": "agents.*.harness"}
# Keys the form leaves out on purpose. multi_user changes what the host must
# provide (a service user, capabilities) and applies at the next start, so a
# click in the browser could leave a server that refuses to boot. It is set by
# hand or by the setup page (#548).
HOST_ONLY = {"multi_user.env_file"}

# A file that sets every key, none to its default.
FULL = """\
default_agent: opus
exit_via: far
agents:
  opus: {exit: "off", harness: claude-code, model: opus, effort: high, permission: full, priming: Be brief., keep_harness_tools: [Agent]}
  quick: {harness: claude-code, models: [haiku, sonnet], effort: low, permission: read}
queues:
  general: {agent: opus, max_parallel: 3, quiet: false}
recap: {agent: quick}
btw: {agent: opus}
context: {handoff_at: 200000, cold_after: 1800, cold_min: 50000}
session_memory_max: 3G
terminal_memory_max: 1G
terminals:
  api: {cmd: uvicorn app:app, cwd: app, restart: on-failure, env: {PORT: "8000"}, access: {ops: read}}
park: {claude-code: 1200, opencode: 600, codex: 900}
network:
  enabled: false
  interval: 30
  trace_interval: 600
  speed_interval: 0
  speed_bytes: 500000
  timeout: 5
  speed_timeout: 20
  anchors: ["9.9.9.9:443"]
host: {hide_mounts: [/srv/scratch], add_mounts: [/tank]}
workflows: {max_agents: {total: 6, opus: 3}}
usage:
  owners: {aegis: acme-co}
  owner_names: {acme-co: Acme}
  subscriptions: {a1b2: {name: claude-max, fee: 200, renews: 14}}
harness_versions: {person: warn, agent: "off", startup: "off", accepted: {opencode: ["2.0.33"]}}
nudges: {silent_s: false, plan_min: 20}
knowledge: {embeddings: minishlab/potion-multilingual-128M, know_how: false}
notices:
  disk: {on: 80, critical: 95, clear: 70}
  memory: {on: 85, critical: 92, clear: 80, psi_full: 10}
  cpu: {on: 60, clear: 40}
  quota: false
  status: {lines: 3}
  monitors: {stall_min: 20, broken_checks: 5, overdue_min: 15, wake: false}
  plan: {stale_min: 5}
  queues: false
  held: false
"""


def reader_keys() -> list[str]:
    """Every key path the readers take; ``*`` stands for a name."""
    notices = []
    for kind, default in config._notices_defaults().items():
        if isinstance(default, dict) and default:
            notices += [f"{kind}.{k}" for k in default]
        else:
            notices.append(kind)  # only on or off
    blocks = {
        "agents": [f"*.{k}" for k in config.AGENT_KEYS],
        "queues": [f"*.{k}" for k in config.QUEUE_KEYS],
        "terminals": [f"*.{k}" for k in PROFILE_KEYS],
        "park": ["*"],  # a harness's name
        "workflows": ["max_agents"],
        "usage": list(config.USAGE_DEFAULTS),
        "harness_versions": list(config.HV_DEFAULTS),
        "context": list(config.CONTEXT_DEFAULTS),
        "nudges": list(config.NUDGES_DEFAULTS),
        "knowledge": list(config.KNOWLEDGE_DEFAULTS),
        "network": list(NET_KEYS),
        "host": ["hide_mounts", "add_mounts"],
        "notices": notices,
        "recap": ["agent"],
        "btw": ["agent"],
        "multi_user": ["env_file"],
    }
    scalars = {"default_agent", "exit_via", "session_memory_max", "terminal_memory_max"}
    out = []
    for top in config.TOP_KEYS:
        if top in scalars:
            out.append(top)
            continue
        assert top in blocks, f"{top}: a block this test does not know; list its keys"
        out += [f"{top}.{k}" for k in blocks[top]]
    return [k for k in out if k not in HOST_ONLY]


def paths(node: object, prefix: str = "") -> set[str]:
    """The key paths a parsed file sets, a name under a named block as ``*``."""
    if not isinstance(node, dict):
        return {prefix}
    out = set()
    for k, v in node.items():
        named = prefix in ("agents", "queues", "terminals", "park")
        p = f"{prefix}.{'*' if named else k}" if prefix else str(k)
        leaf = p in (
            "workflows.max_agents",
            "harness_versions.accepted",
            "usage.owners",
            "usage.owner_names",
            "usage.subscriptions",
        ) or (p.startswith("terminals.*.") and p.count(".") == 2)
        out |= {p} if leaf else paths(v, p)
    return out


def safe(text: str) -> dict:
    return YAML(typ="safe").load(text)


def test_the_full_file_sets_every_key():
    assert set(reader_keys()) - set(ALIASES) - paths(safe(FULL)) == set()


def test_the_form_document_carries_every_key(tmp_path):
    """Written from nothing, the form's document of FULL is FULL."""
    doc = doc_from(safe(FULL))
    assert validate(doc) == []
    p = tmp_path / ".aegis.yaml"
    assert write(p, doc, None) == []
    assert safe(p.read_text()) == safe(FULL)


def test_every_key_has_a_control_in_settings(tmp_path):
    """A key is drawn from the wire's schema, or named in settings.js."""
    (tmp_path / ".aegis.yaml").write_text(FULL)
    schema = {f["key"] for f in config.Config(tmp_path).current().wire()["schema"]}
    # Each word of each string literal: a control may name two keys.
    named = {
        w for lit in re.findall(r'"([^"\n]*)"', JS.read_text()) for w in lit.split()
    }
    missing = [k for k in reader_keys() if ALIASES.get(k, k) not in schema | named]
    assert missing == []
