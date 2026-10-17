"""The fixed text every aegis session receives, held to a byte budget.

The primer and aegis's tool list ride on every API request a session makes,
so a paragraph added to either is paid on every turn of every session. The
budgets are the sizes on 2026-10-17 plus about 5%; the token bench
(``make bench-tokens``) shows whether a raise bought anything.
"""

import json

from aegis.app import App
from aegis.mcp import PRIMER, SHELL_FORM, _schema
from aegis.roots import make_roots

# Raising a budget is a deliberate edit: say in the PR what the bytes buy.
PRIMER_BUDGET = 11_200  # PRIMER rendered with sample values
NAMES_BUDGET = 1_500  # "mcp__aegis__<tool>" per agent op, one per line
# turn_end + plan_update: doc + SHELL_FORM + json.dumps(_schema(op))
EAGER_BUDGET = 2_750


def agent_ops(tmp_path):
    (tmp_path / ".aegis.yaml").write_text("agents: {}\n")
    return App(make_roots(tmp_path, None), claude_bin="true").registry.agent_ops()


def test_the_primer_fits_its_budget(tmp_path):
    text = PRIMER.format(
        handle="sample-handle",
        server="local",
        tools="mcp__aegis__*",
        root=str(tmp_path),
    )
    size = len(text.encode())
    assert size <= PRIMER_BUDGET, f"primer is {size} B, budget {PRIMER_BUDGET} B"


def test_the_tool_names_fit_their_budget(tmp_path):
    names = "".join(f"mcp__aegis__{op.tool_name}\n" for op in agent_ops(tmp_path))
    size = len(names.encode())
    assert size <= NAMES_BUDGET, f"tool names are {size} B, budget {NAMES_BUDGET} B"


def test_the_schemas_every_session_loads_fit_their_budget(tmp_path):
    eager = [
        op for op in agent_ops(tmp_path) if op.tool_name in ("turn_end", "plan_update")
    ]
    assert len(eager) == 2
    size = sum(
        len(
            (
                (op.doc or op.name)
                + SHELL_FORM.format(name=op.tool_name)
                + json.dumps(_schema(op))
            ).encode()
        )
        for op in eager
    )
    assert size <= EAGER_BUDGET, (
        f"turn_end and plan_update are {size} B, budget {EAGER_BUDGET} B"
    )
