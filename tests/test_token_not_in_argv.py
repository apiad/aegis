"""A session's aegis token never sits in a harness's argv: /proc/<pid>/cmdline
is readable by every user on the host, and in multi-user mode another person
could act as that session (#548). It travels in the environment, which only
the process's own user can read; Claude Code expands ${VAR} in --mcp-config."""

from dataclasses import replace

from aegis.claude.harness import ClaudeSession
from aegis.harness import TOKEN_VAR

from .test_replaced_tools import launch

SECRET = "tok-not-in-argv-1234"


def test_the_claude_argv_names_the_variable_not_the_token(tmp_path):
    lch = replace(launch(tmp_path, ("http://127.0.0.1:1/mcp", SECRET)), log_id="L1")
    proc = ClaudeSession("claude", lch)._proc
    assert not any(SECRET in a for a in proc.argv)
    assert "${" + TOKEN_VAR + "}" in " ".join(proc.argv)
    assert proc.env is not None and proc.env[TOKEN_VAR] == SECRET
