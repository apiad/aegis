"""Builtin slash commands, one module per command family. Importing this
package imports every submodule for its registration side-effects, so
``from aegis.commands import builtins`` (at the bottom of the commands
package) wires up the whole builtin set."""
from aegis.commands.builtins import core as _core  # noqa: F401
from aegis.commands.builtins import coordination as _coordination  # noqa: F401
