from pathlib import Path

from aegis.cost.locality import (
    classify,
    cwd_inside,
    module_of,
    repo_roots,
    repos_mentioned,
    session_share,
)


def test_cwd_substring_does_not_count_as_the_repo(tmp_path):
    """Trap 1. `"aegis" in cwd` matched /tmp/aegis-shot-a1b2 and .aegis/state
    and put 1,679 sessions on aegis where the real number was 394."""
    repo = tmp_path / "repos" / "aegis"
    repo.mkdir(parents=True)
    roots = repo_roots(repo)

    assert cwd_inside(str(repo), roots) is True
    assert cwd_inside(str(repo / "src" / "aegis"), roots) is True
    assert cwd_inside(str(tmp_path / "repos" / "aegis-wt-slice2"), roots) is True

    assert cwd_inside("/tmp/aegis-shot-a1b2", roots) is False
    assert cwd_inside(str(tmp_path / ".aegis" / "state"), roots) is False
    assert cwd_inside(str(tmp_path / "repos" / "aegisx"), roots) is False
    assert cwd_inside(None, roots) is False


def test_worktree_mentions_fold_onto_the_repo():
    text = "editing repos/aegis-wt-slice2/src/x.py and repos/une-tools/app.py"
    assert repos_mentioned(text, fold="aegis") == {"aegis", "une-tools"}
    assert repos_mentioned(text) == {"aegis", "une-tools"}


def test_share_is_the_record_ratio_when_cwd_is_elsewhere():
    roots = ("/nowhere", "/nowhere-wt-")
    assert session_share("aegis", {"aegis": 3, "other": 1}, "/home/x", roots) == 0.75
    assert session_share("aegis", {"other": 4}, "/home/x", roots) == 0.0
    assert session_share("aegis", {}, "/home/x", roots) == 0.0


def test_share_is_whole_when_the_session_works_inside_the_repo():
    roots = ("/w/repos/aegis", "/w/repos/aegis-wt-")
    assert session_share("aegis", {"other": 9}, "/w/repos/aegis/src", roots) == 1.0


def test_module_splits_monorepo_containers_but_not_ordinary_dirs():
    assert module_of("src/aegis/cli.py") == "src"
    assert module_of("apps/web/main.ts") == "apps/web"
    assert module_of("apps") == "(root)"
    assert module_of("apps/web") == "apps/*"
    assert module_of("README.md") == "(root)"


def test_classify_separates_binaries_from_text():
    assert classify("docs/report.pdf") == "binary"
    assert classify("docs/report.md") == "prose"
    assert classify("src/main.py") == "code"
    assert classify("data/rows.csv") == "data"


def test_repo_roots_resolves_symlinks_and_trailing_slashes(tmp_path):
    real = tmp_path / "real" / "aegis"
    real.mkdir(parents=True)
    link = tmp_path / "link"
    link.symlink_to(tmp_path / "real")

    assert repo_roots(link / "aegis") == repo_roots(real)
    assert repo_roots(Path(str(real) + "/")) == repo_roots(real)
    assert cwd_inside(str(real / "src"), repo_roots(link / "aegis")) is True
