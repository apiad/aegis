"""`_serve` resolves from the roots it is handed, not from the process cwd.

The second test asserts on AST calls rather than on the substring
``"Path.cwd()"`` the plan sketched: a text scan is fooled by a line break
(``Path.cwd(\\n)`` slips past — verified by mutation) and fires on a mere
mention in a comment. ``tests/test_no_cwd_regression`` already owns that
check; this reuses its helper rather than growing a third copy.
"""
import ast
import inspect
import textwrap

from tests.test_no_cwd_regression import _cwd_calls


def test_serve_takes_roots_and_not_local_root():
    from aegis.cli import _serve
    params = inspect.signature(_serve).parameters
    assert "roots" in params, "_serve must take AegisRoots"
    assert "local_root" not in params, (
        "local_root is superseded by roots.harness_cwd; keeping both "
        "reintroduces the ambiguity this stage removes")


def test_serve_has_no_cwd_calls():
    """_serve held eight Path.cwd() calls (cli.py:401,404,405,410,450,455,
    463,481) while accepting a local_root it used once."""
    from aegis.cli import _serve
    # getsource returns the function indented at its module position, which
    # ast.parse rejects; dedent first.
    tree = ast.parse(textwrap.dedent(inspect.getsource(_serve)))
    offenders = [f"_serve:+{line}: {what}" for line, what in _cwd_calls(tree)]
    assert not offenders, "cwd resolution in _serve:\n" + "\n".join(offenders)
