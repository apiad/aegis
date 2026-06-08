"""Uninstall hook for socks-proxy: remove the conf file."""
from __future__ import annotations

from aegis.plugins.install_context import InstallContext


def uninstall(ctx: InstallContext) -> None:
    conf = ctx.aegis_dir / "socks-proxy.conf"
    if not conf.exists():
        return
    if ctx._yes or ctx.confirm(f"Delete {conf}?", default=True):
        conf.unlink()
        if ctx.console is not None:
            ctx.console.print(f"[green]removed[/] {conf}")
    elif ctx.console is not None:
        ctx.console.print(
            f"[yellow]kept[/] {conf} — the pre_spawn hook is gone with "
            "the plugin, so the conf is now inert."
        )
