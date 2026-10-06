"""Example plugin command. Drop this file into ``.aegis/plugins/`` (or any
``plugin_dirs:`` entry) to register ``/hello`` as a plugin control command."""
from aegis.commands import command, CommandResult
from aegis.commands.args import Arg, ArgSpec


@command(summary="greet someone", usage="/hello <name>",
         spec=ArgSpec(positionals=(Arg("name", required=False),)))
async def hello(ctx, args):
    who = args.get("name") or "world"
    return CommandResult(True, f"hello {who}")
