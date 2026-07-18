"""Aegis workflow catalog.

Importing a seed workflow registers it with the workflow runtime, after
which it's invocable via ``aegis_run_workflow``. The submodule imports
below trigger the ``@workflow`` decorators without rebinding the
submodule names at package level — so ``aegis.workflows.review_branch``
remains the module, not the function (important for ``monkeypatch``).
"""
from aegis.workflows import (  # noqa: F401
    brainstorm_to_spec,
    dynamic,
    execute_plan,
    review_branch,
    tdd_cycle,
)
