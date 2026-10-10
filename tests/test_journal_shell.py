from aegis.journal.shell import (
    commit_dirs,
    commits,
    leading_cd,
    pull_request,
    workdir,
    writes,
)


def test_a_commit_line_gives_branch_hash_and_subject():
    out = "[main 1a2b3c4] fix(x): a thing\n 2 files changed, 3 insertions(+)"
    assert commits("git commit -m 'fix'", out) == [
        ("main", "1a2b3c4", "fix(x): a thing")
    ]


def test_a_root_commit_and_a_detached_head_are_read():
    assert commits("git commit -m a", "[topic (root-commit) abcdef0] first") == [
        ("topic", "abcdef0", "first")
    ]
    assert commits("git commit -m a", "[detached HEAD abcdef1] wip") == [
        ("detached HEAD", "abcdef1", "wip")
    ]


def test_output_shaped_like_a_commit_counts_only_when_git_ran():
    assert commits("echo '[main 1a2b3c4] x'", "[main 1a2b3c4] x") == []


def test_workdir_follows_a_leading_cd_and_git_dash_c():
    assert workdir("# Commit the fix\ncd /r/a && git commit -m x", "/w") == "/r/a"
    assert workdir("git -C ../b commit -m x", "/w/a") == "/w/b"
    assert workdir("git commit -m x", "/w") == "/w"


def test_writes_finds_redirects_tee_sed_mv_and_cp():
    assert writes("echo hi > out.txt && sed -i 's/a/b/' f.py | tee log.txt", "/w") == [
        "/w/f.py",
        "/w/log.txt",
        "/w/out.txt",
    ]
    assert writes("mv a.txt b/ ; cp x /abs/y", "/w") == ["/abs/y", "/w/b"]


def test_writes_ignores_quotes_fd_redirects_heredoc_bodies_and_dev():
    assert writes("python -c 'print(1 > 0)' 2>&1 > /dev/null", "/w") == []
    assert writes("cat > notes.md <<'EOF'\nx > y\nEOF", "/w") == ["/w/notes.md"]
    assert writes("echo 'unclosed", "/w") == []


def test_pull_requests_opened_and_merged():
    assert (
        pull_request(
            'gh pr create --title "Add x" --body y', "https://github.com/a/b/pull/12\n"
        )
        == "opened https://github.com/a/b/pull/12 · Add x"
    )
    assert (
        pull_request(
            "gh pr merge 12 --squash", "✓ Squashed and merged pull request #12"
        )
        == "merged #12"
    )
    assert pull_request("gh pr view 12", "anything") is None


def test_degenerate_inputs_never_raise():
    """Malformed commands should yield nothing, never an error."""
    # Redirect-only lines should not raise IndexError
    assert writes("echo a >\n>\n", "/w") == []
    # Lone redirect operator
    assert writes(">", "/w") == []
    # Unclosed quote
    assert commits("echo 'unclosed", "") == []
    # Empty string
    assert commits("", "") == []
    # Only comment
    assert workdir("# just a comment", "/w") == "/w"
    assert writes("# just a comment", "/w") == []
    # All functions should handle these gracefully
    assert pull_request("", "") is None


def test_workdir_git_c_not_confused_with_other_options():
    """git commit --amend -C HEAD should not treat HEAD as a directory."""
    assert workdir("git commit --amend -C HEAD", "/w") == "/w"
    # But git -C should still work
    assert workdir("git -C ../b status", "/w/a") == "/w/b"
    # Mixed: leading options then -C flag
    assert workdir("git -c user.name=x -C /tmp status", "/w") == "/tmp"


def test_writes_preserves_heredoc_marker_line_redirect():
    """Heredoc marker line should be parsed to detect its redirect or pipe."""
    assert writes("cat <<EOF > a.txt\nx\nEOF", "/w") == ["/w/a.txt"]
    assert writes("cat <<EOF | tee b.txt\nx\nEOF", "/w") == ["/w/b.txt"]
    # Heredoc with quote marker
    assert writes("cat <<'EOF' > c.txt\nx > y\nEOF", "/w") == ["/w/c.txt"]


def test_writes_strips_leading_paren_and_env_vars():
    """Leading parens and VAR=value should be skipped."""
    assert writes("(cd /x && echo a > b.txt)", "/w") == ["/x/b.txt"]
    assert writes("FOO=1 echo x > y.txt", "/w") == ["/w/y.txt"]
    # VAR=value assignments should be skipped before commands
    assert writes("VAR=val1 VAR2=val2 echo hi > z.txt", "/w") == ["/w/z.txt"]


def test_workdir_git_c_probes():
    """Ensure -C is only a git option when among git's leading options."""
    # -C is a git option
    assert workdir("git -C repo commit -m x", "/w") == "/w/repo"
    assert workdir("git -C src/foo status", "/w") == "/w/src/foo"
    # -C is NOT a git option (it's a subcommand option)
    assert workdir("git commit -C /abc", "/w") == "/w"
    assert workdir("git status -C /tmp", "/w") == "/w"


def test_workdir_git_c_stacking():
    """Multiple -C flags stack: each is resolved relative to previous."""
    assert workdir("git -c user.name=x -C ../b commit", "/w/a") == "/w/b"
    assert workdir("git -C a -C b status", "/w") == "/w/a/b"


def test_writes_with_env_vars_and_commands():
    """Commands with leading VAR= should work correctly."""
    # FOO=1 tee x should detect x as a tee argument
    assert writes("FOO=1 tee x", "/w") == ["/w/x"]


HEREDOC_COMMIT = (
    "cd /r/x && git add a.py && git commit -m \"$(cat <<'EOF'\n"
    'feat: y (#1)\n\nCo-Authored-By: someone\nEOF\n)"'
)


def test_a_heredoc_commit_message_keeps_the_cd_before_it():
    """Claude's standard commit form: the line holding the cd also opens a quote
    that only the heredoc's closing line ends."""
    assert workdir(HEREDOC_COMMIT, "/w") == "/r/x"
    assert commit_dirs(HEREDOC_COMMIT, "/w", 1) == ["/r/x"]
    assert writes('cd /r/x && echo "a\nb" > out.txt', "/w") == ["/r/x/out.txt"]


def test_each_commit_gets_the_directory_of_its_own_git_command():
    cmd = "git -C a commit -m x && git -C b commit -m y"
    assert commit_dirs(cmd, "/w", 2) == ["/w/a", "/w/b"]
    # Counts that do not pair: the last commit command's directory.
    assert commit_dirs(cmd, "/w", 3) == ["/w/b"] * 3
    # No commit command at all (a script committed): the first git's directory.
    assert commit_dirs("git -C c status && ./release.sh", "/w", 1) == ["/w/c"]


def test_leading_cd_is_where_the_shell_stays():
    assert leading_cd("cd /r/x && make test", "/w") == "/r/x"
    assert leading_cd("cd sub", "/w") == "/w/sub"
    assert leading_cd("cd a; cd b && ls", "/w") == "/w/a/b"
    assert leading_cd("make && cd x", "/w") is None
    assert leading_cd("(cd x && make)", "/w") is None
    assert leading_cd("cd $W && ls", "/w") is None
    assert leading_cd(HEREDOC_COMMIT, "/w") == "/r/x"


def test_writes_skip_targets_named_by_a_variable():
    cmd = 'echo x > $f; echo y >> "$W/progress.md"; echo z > ok.txt; tee "${D}/a"'
    assert writes(cmd, "/w") == ["/w/ok.txt"]
