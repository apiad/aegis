"""fs_paths.resolve: the one gate between a browser's path and the disk."""

from pathlib import Path

import pytest

from aegis.fs_paths import resolve
from aegis.ops import OpError


@pytest.fixture
def tree(tmp_path: Path) -> tuple[Path, Path, Path]:
    root = tmp_path / "root"
    state = root / ".aegis" / "state"
    state.mkdir(parents=True)
    (state / "token").write_text("secret")
    (root / "src").mkdir()
    (root / "src" / "a.py").write_text("x = 1\n")
    (root / "repo" / ".git").mkdir(parents=True)
    (root / "repo" / ".git" / "config").write_text("[core]\n")
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.txt").write_text("no")
    return root, state, outside


def code(root: Path, state: Path, rel: str, **kw) -> str:
    with pytest.raises(OpError) as e:
        resolve(root, state, rel, **kw)
    return e.value.code


def test_a_path_inside_resolves(tree):
    root, state, _ = tree
    assert resolve(root, state, "src/a.py") == (root / "src" / "a.py").resolve()


@pytest.mark.parametrize("rel", ["..", "src/../../x", "/etc/passwd", "", "a\x00b"])
def test_escapes_are_outside_root(tree, rel):
    root, state, _ = tree
    assert code(root, state, rel) == "outside_root"


def test_a_symlink_out_of_the_root_is_outside_root(tree):
    root, state, outside = tree
    (root / "link").symlink_to(outside / "secret.txt")
    (root / "dirlink").symlink_to(outside)
    assert code(root, state, "link") == "outside_root"
    assert code(root, state, "dirlink/secret.txt") == "outside_root"
    assert code(root, state, "dirlink/new.txt", must_exist=False) == "outside_root"


def test_a_dangling_symlink_out_of_the_root_is_outside_root(tree):
    root, state, outside = tree
    (root / "dangling").symlink_to(outside / "missing.txt")
    assert code(root, state, "dangling", must_exist=False) == "outside_root"


def test_the_state_root_is_forbidden(tree):
    root, state, _ = tree
    assert code(root, state, ".aegis/state/token") == "forbidden"
    assert code(root, state, ".aegis/state/new", must_exist=False) == "forbidden"


def test_a_symlink_into_the_state_root_is_forbidden(tree):
    root, state, _ = tree
    (root / "tok").symlink_to(state / "token")
    assert code(root, state, "tok") == "forbidden"


def test_a_git_directory_is_forbidden(tree):
    root, state, _ = tree
    assert code(root, state, "repo/.git/config") == "forbidden"
    (root / "gitlink").symlink_to(root / "repo" / ".git")
    assert code(root, state, "gitlink/config") == "forbidden"


def test_a_new_file_needs_an_existing_folder_inside(tree):
    root, state, _ = tree
    assert (
        resolve(root, state, "src/new.py", must_exist=False)
        == (root / "src").resolve() / "new.py"
    )
    assert code(root, state, "nowhere/new.py", must_exist=False) == "not_found"
    assert code(root, state, "src/missing.py") == "not_found"
