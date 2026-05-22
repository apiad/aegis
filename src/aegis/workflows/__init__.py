"""Aegis workflow catalog.

Importing a workflow from this package registers it with the workflow
runtime, after which it's invocable via ``aegis_run_workflow``::

    from aegis.workflows import brainstorm_to_spec
    # Now aegis_run_workflow(name="brainstorm_to_spec", ...) works.
"""
from aegis.workflows.brainstorm_to_spec import brainstorm_to_spec
from aegis.workflows.execute_plan import execute_plan
from aegis.workflows.review_branch import review_branch
from aegis.workflows.tdd_cycle import tdd_cycle

__all__ = [
    "brainstorm_to_spec",
    "execute_plan",
    "review_branch",
    "tdd_cycle",
]
