"""Values reach a command through its environment, never through its text:
each {{expr}} becomes a reference to AEGIS_V<n>, and the lint refuses the
places where bash would read a value as code or keep it literal."""

import json
import os
import subprocess

import pytest

from aegis.workflow_lang import parse, shell_command

PAYLOADS = ["$(touch M)", "';touch M;'", "a[$(touch M)]", "`touch M`"]
# Every byte bash could misread: quotes, a newline, $, backslashes.
EXACT = "x \"q\" 'q' \\ \\\\ $HOME ${HOME} $(touch M) `touch M`\n';touch M;' a[$(touch M)] *"


def lint(cmd: str, probe: str = "") -> list[str]:
    c = json.dumps(cmd)
    node = {
        "": f"{{sh: {c}, say: Run.}}",
        "check": f"{{check: {{sh: {c}}}, say: Probe.}}",
        "if": f"{{if: {{when: {{sh: {c}}}, then: {{wait: 1s, say: T.}}}}, say: If.}}",
        "until": f"{{loop: {{max: 2, until: {{sh: {c}}}, do: {{wait: 1s, say: T.}}}}, say: L.}}",
    }[probe]
    text = f"id: q\ntitle: Q\nsay: Q.\nargs: {{type: object}}\nroot: {node}\n"
    return [f"{e.path}: {e.message}" for e in parse(text, set())[1]]


def run(cmd: str, value: str, tmp_path) -> str:
    text, env = shell_command(cmd, {"args": {"v": value}})
    assert "{{" not in text and value not in text or value == "", text
    p = subprocess.run(
        ["bash", "-c", text],
        cwd=tmp_path,
        env={**os.environ, **env},
        capture_output=True,
        text=True,
    )
    assert not (tmp_path / "M").exists(), (cmd, value, text)
    return p.stdout


# A form a person may write, with what bash prints for a value v.
ALLOWED = [
    ("printf %s {{args.v}}", lambda v: v),
    ('printf %s "{{args.v}}"', lambda v: v),
    (
        'printf %s "a $HOME-less {{args.v}} b"',
        lambda v: f"a {os.environ['HOME']}-less {v} b",
    ),
    ("printf %s `printf %s {{args.v}}`", None),  # unquoted: word-split, still inert
    ('printf %s "`printf %s {{args.v}}`"', lambda v: v.rstrip("\n")),
    ('printf %s "$(printf %s {{args.v}})"', lambda v: v.rstrip("\n")),
    ("printf %s $(printf %s {{args.v}})", None),
    ("cat <<EOF\n{{args.v}}\nEOF", lambda v: v + "\n"),
    ("cat <<-EOF\n\t{{args.v}}\n\tEOF", lambda v: v + "\n"),
    ("printf %s ${X:-{{args.v}}}", lambda v: v),
    ('printf %s "${X:-{{args.v}}}"', lambda v: v),
    ("printf %s ${X:+x}${Y-{{args.v}}}", lambda v: v),
    ("cat <<< {{args.v}}", lambda v: v + "\n"),
    ('echo $((1<<2)) >/dev/null\nprintf %s "{{args.v}}"', lambda v: v),
    ('echo $((1<<"2")) >/dev/null\nprintf %s "{{args.v}}"', lambda v: v),
    ("(true)# it's\nprintf %s {{args.v}}", lambda v: v),
    ("echo x >/dev/null # it's\nprintf %s {{args.v}}", lambda v: v),
    ("test {{args.v}} = {{args.v}} && printf %s {{args.v}}", lambda v: v),
    ("[ {{args.v}} -eq 0 ] 2>/dev/null; printf %s {{args.v}}", lambda v: v),
    ('export X={{args.v}} && printf %s "$X"', lambda v: v),
    ("printf %s {{args.v}} # {{args.v}} in a comment", lambda v: v),
]


@pytest.mark.parametrize("cmd,want", ALLOWED, ids=[c for c, _ in ALLOWED])
@pytest.mark.parametrize("value", [*PAYLOADS, EXACT])
def test_an_allowed_form_gets_the_exact_value_and_runs_nothing(
    cmd, want, value, tmp_path
):
    assert lint(cmd) == [], cmd
    out = run(cmd, value, tmp_path)
    if want is not None:
        assert out == want(value), (cmd, out)


@pytest.mark.parametrize("probe", ["check", "if", "until"])
def test_a_probe_lints_like_a_command(probe):
    assert lint('test -n "{{args.v}}"', probe) == []
    assert "sits inside single quotes" in lint("test -n '{{args.v}}'", probe)[0]


REFUSED = [
    ("echo '{{args.v}}'", "single quotes"),
    ("echo 'a' '{{args.v}}'", "single quotes"),
    ("echo $'\\' {{args.v}} '", "a $'...' string"),
    ("echo $'{{args.v}}'", "a $'...' string"),
    ("(true)# '\necho '{{args.v}}'", "single quotes"),
    ("echo x >/dev/null # it's\necho '{{args.v}}'", "single quotes"),
    ("echo x;# it's\necho '{{args.v}}'", "single quotes"),
    ("cat <<'EOF'\n{{args.v}}\nEOF", "a quoted heredoc"),
    ("cat <<\\EOF\n{{args.v}}\nEOF", "a quoted heredoc"),
    ("(( {{args.v}} )); echo ok", "arithmetic"),
    ("echo $(( {{args.v}} + 1 ))", "arithmetic"),
    ('echo $(( "{{args.v}}" ))', "arithmetic"),
    ("echo $[ {{args.v}} ]", "arithmetic"),
    ("for ((i=0; i<{{args.v}}; i++)); do :; done", "arithmetic"),
    ("[[ {{args.v}} -eq 0 ]] && echo zero", "a [[ ... ]] test"),
    ('[[ "{{args.v}}" == x ]]', "a [[ ... ]] test"),
    ("let x={{args.v}}", "a let or declare -i command"),
    ("declare -i n; n={{args.v}}", "a let or declare -i command"),
    ("typeset -i n={{args.v}}", "a let or declare -i command"),
    ("f() { local -n r={{args.v}}; }", "a let or declare -i command"),
    ("a[{{args.v}}]=1", "an array subscript"),
    ("echo ${a[{{args.v}}]}", "a ${...} expansion"),
    ("echo ${x:{{args.v}}}", "a ${...} expansion"),
    ("echo ${x:0:{{args.v}}}", "a ${...} expansion"),
    ("test -v {{args.v}}", "a variable's name"),
    ("[ -v {{args.v}} ]", "a variable's name"),
    ("printf -v {{args.v}} x", "a variable's name"),
    ("read {{args.v}} <<< x", "a variable's name"),
    ("declare {{args.v}}=1", "a variable's name"),
]


@pytest.mark.parametrize("cmd,where", REFUSED, ids=[c for c, _ in REFUSED])
def test_a_template_where_bash_reads_code_or_a_literal_is_refused(cmd, where):
    got = lint(cmd)
    assert got and all(f"sits inside {where}" in e for e in got), got


@pytest.mark.parametrize("cmd,where", REFUSED, ids=[c for c, _ in REFUSED])
def test_a_refused_quoting_form_still_runs_nothing(cmd, where, tmp_path):
    # Safe by construction: outside arithmetic and names, a misread context
    # gives a wrong literal at worst, never a run command.
    if where in ("single quotes", "a $'...' string", "a quoted heredoc"):
        for value in PAYLOADS:
            run(cmd, value, tmp_path)


def test_the_environment_carries_each_occurrence():
    text, env = shell_command(
        'a {{args.v}} "b {{args.n}}" {{args.v}}', {"args": {"v": "x y", "n": 3}}
    )
    assert text == 'a "$AEGIS_V1" "b ${AEGIS_V2}" "$AEGIS_V3"'
    assert env == {"AEGIS_V1": "x y", "AEGIS_V2": "3", "AEGIS_V3": "x y"}


def test_a_value_that_comes_to_null_has_no_value():
    with pytest.raises(KeyError, match="has no value"):
        shell_command("echo {{args.v}}", {"args": {}})
