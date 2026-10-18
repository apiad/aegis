"""``aegis map`` on a small git repository built per test, through the CLI."""

import os
import subprocess
from pathlib import Path

import pytest
from typer.testing import CliRunner

from aegis.cli import app
from aegis.codemap.index import Index

FILES = {
    "pkg/__init__.py": '"""The package."""\n',
    "pkg/store.py": '''"""Where records live."""


class Store:
    """Keeps records in memory."""

    def put(self, key, value):
        self.data[key] = value

    def get(self, key):
        """One record."""
        return self.data[key]


def helper(x):
    return x + 1
''',
    "pkg/api.py": '''"""The API over the store."""
from pkg.store import Store, helper


def save(key, value):
    s = Store()
    s.put(key, helper(value))


def handle(request):
    save(request.key, request.value)
''',
    "pkg/other.py": """def helper(y):
    return y


def close():
    return helper(2)
""",
    "web/app.ts": """// The web entry point.
import { render } from "./view";

export function main(): void {
  render("hello");
}
""",
    "web/view.ts": """export function render(text: string): void {
  console.log(text);
}
""",
}


@pytest.fixture
def repo(tmp_path, monkeypatch):
    root = tmp_path / "repo"
    for rel, text in FILES.items():
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_text(text)
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    subprocess.run(["git", "-C", str(root), "add", "."], check=True)
    # The map's cache only: the grammars stay in the user's cache, downloaded once.
    monkeypatch.setattr("aegis.codemap.cli.cache_dir", lambda: tmp_path / "cache")
    return root


def run(repo: Path, *args: str) -> str:
    r = CliRunner().invoke(app, ["map", *args, "--repo", str(repo)])
    assert r.exit_code == 0, r.output
    return r.stdout


def test_repo_lists_folders_and_module_docs(repo):
    out = run(repo, "repo", "pkg")
    assert "store.py  1 class 1 function 2 method  — Where records live." in out
    assert "api.py  2 function  — The API over the store." in out


def test_outline_nests_methods_under_their_class(repo):
    out = run(repo, "outline", "pkg/store.py")
    assert "4: class Store  — Keeps records in memory." in out
    assert "  10: def get(self, key)  — One record." in out


def test_callers_marks_an_import_confirmed_edge_sure(repo):
    out = run(repo, "callers", "pkg/store.py:helper")
    assert "L1 pkg/api.py: save:7\n" in out
    # other.py calls its own helper, which is not this one
    assert "other.py" not in out


def test_callers_of_an_ambiguous_name_are_marked_unsure(repo):
    out = run(repo, "callers", "Store.put")
    assert "pkg/api.py: save:7" in out
    assert "<module>" not in out


def test_impact_follows_sure_edges_to_the_next_level(repo):
    out = run(repo, "impact", "pkg/store.py:helper")
    # the import line is a use too, at module level
    assert "L1 pkg/api.py: <module>:2, save:7" in out
    assert "L2 pkg/api.py: handle:11" in out
    assert "= 3 uses in 1 files" in out


def test_callees_and_show(repo):
    assert "pkg/store.py:Store.put" in run(repo, "callees", "save")
    assert "return self.data[key]" in run(repo, "show", "Store.get")
    assert "pkg/other.py:helper" in run(repo, "find", "helper")


def test_typescript_resolves_across_files(repo):
    out = run(repo, "callers", "render")
    assert "L1 web/app.ts: main:5" in out


def test_an_edited_file_is_reparsed_and_the_rest_is_not(repo, tmp_path):
    cache = tmp_path / "cache"
    assert Index(repo, cache).stats.parsed == len(FILES)
    (repo / "pkg/other.py").write_text("def helper(y):\n    return y\n")
    st = (repo / "pkg/other.py").stat()
    os.utime(repo / "pkg/other.py", ns=(st.st_atime_ns, st.st_mtime_ns + 10**9))
    ix = Index(repo, cache)
    assert ix.stats.parsed == 1
    assert "pkg/other.py:close" not in ix.defs


def test_outside_a_git_repository_it_says_so(tmp_path):
    r = CliRunner().invoke(app, ["map", "repo", "--repo", str(tmp_path)])
    assert r.exit_code == 1
    assert "not inside a git repository" in r.output
