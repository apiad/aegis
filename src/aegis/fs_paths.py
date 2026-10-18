"""Where a path a person's browser names may lead (the Files view, #458).

Every fs.* operation and the /fs ticket route resolve through here, and nothing
else builds a path from a client string. A path is relative to the config root,
names no "..", resolves (symlinks followed) inside the root, and is never inside
the state root (it holds the admin token and live stores) or a .git directory
(editing a repository's internals by hand only breaks it)."""

from pathlib import Path

from .ops import OpError


def resolve(root: Path, state: Path, rel: str, *, must_exist: bool = True) -> Path:
    """The real path ``rel`` names under ``root``. With ``must_exist`` false
    the path may be new, but its folder must exist and pass the same rules."""
    parts = Path(rel).parts
    if not rel or "\x00" in rel or rel.startswith("/") or ".." in parts:
        raise OpError("outside_root", f"{rel!r} is not a path under the root")
    if ".git" in parts:
        raise OpError("forbidden", f"{rel} is inside a .git directory")
    real_root = root.resolve()
    path = root / rel
    # A dangling symlink counts: its target is checked, never written through.
    exists = path.exists() or path.is_symlink()
    if not exists and not path.parent.is_dir():
        raise OpError("not_found", f"no folder {Path(rel).parent}")
    real = (path if exists else path.parent).resolve()
    if not real.is_relative_to(real_root):
        raise OpError("outside_root", f"{rel} leads outside the root")
    if (
        real.is_relative_to(state.resolve())
        or ".git" in real.relative_to(real_root).parts
    ):
        raise OpError("forbidden", f"{rel} is aegis's own state or a .git directory")
    if must_exist and not (exists and real.exists()):
        raise OpError("not_found", f"no file {rel}")
    return real if exists else real / path.name
