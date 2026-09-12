"""``python -m aegis``.

The console script at ``pyproject.toml:70`` is the same entry point, but
the daemon is autostarted with ``sys.executable`` -- which always exists --
rather than with a console script that may not be on the PATH of a uvx or
bare-venv invocation. Without this module that autostart fails with
``No module named aegis.__main__`` on a stderr pointed at /dev/null.
"""
from aegis.cli import main

main()
