import asyncio

import pytest

from aegis.commands.expand import expand, ExpandError


async def _fake_shell(cmd, cwd):
    return f"[ran: {cmd}]"


def _run(coro):
    return asyncio.run(coro)


def test_positional_and_arguments_substitution(tmp_path):
    out = _run(expand("hi $1 and $2 — all: $ARGUMENTS",
                      "alpha beta", tmp_path, _fake_shell))
    assert out == "hi alpha and beta — all: alpha beta"


def test_missing_positional_is_empty(tmp_path):
    out = _run(expand("[$1][$2]", "only", tmp_path, _fake_shell))
    assert out == "[only][]"


def test_arguments_is_raw_verbatim(tmp_path):
    out = _run(expand("$ARGUMENTS", 'a "b c" d', tmp_path, _fake_shell))
    assert out == 'a "b c" d'          # raw, quotes preserved
    out2 = _run(expand("$1|$2|$3", 'a "b c" d', tmp_path, _fake_shell))
    assert out2 == "a|b c|d"           # $1..$3 shlex-split


def test_file_include(tmp_path):
    (tmp_path / "note.md").write_text("FILE BODY", encoding="utf-8")
    out = _run(expand("before @note.md after", "", tmp_path, _fake_shell))
    assert out == "before FILE BODY after"


def test_missing_file_raises(tmp_path):
    with pytest.raises(ExpandError):
        _run(expand("@nope.md", "", tmp_path, _fake_shell))


def test_shell_embed(tmp_path):
    out = _run(expand("log:\n!`git log`", "", tmp_path, _fake_shell))
    assert out == "log:\n[ran: git log]"


def test_args_first_reach_shell(tmp_path):
    out = _run(expand("!`echo $1`", "hello", tmp_path, _fake_shell))
    assert out == "[ran: echo hello]"   # $1 substituted before the runner sees it
