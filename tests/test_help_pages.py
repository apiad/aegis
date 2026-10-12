"""The help pages against the code, in both directions (#339).

Every agent tool, aegis slash command, CLI subcommand and config key appears
on a page, written the one way the pages name it; every tool, command and
subcommand a page names exists. The rosters come from the code as built: the
app's registry, ``commands.AEGIS``, the Typer app and the config models. This
is the gate; ``.rift.yaml`` asks most of the same questions faster while a page
is being written. Neither reads the prose: a page that names only real tools
can still describe one wrongly, which the smoke tour checks."""

import re
import subprocess
import zipfile
from pathlib import Path

import pytest
import typer.main

from aegis import cli
from aegis.app import App
from aegis.commands import AEGIS
from aegis.config import AgentDoc, ConfigDoc, QueueDoc
from aegis import helppages
from aegis.helppages import HELP_DIR, KINDS, Index, Page, load
from aegis.ops import Caller, OpError
from aegis.roots import make_roots

FIRST_PAGES = {
    "getting-started",
    "sessions",
    "monitors",
    "queues",
    "handoffs",
    "artifacts",
    "links",
    "config",
    "commands",
    "writing-help-pages",
}
# A backticked word with an underscore and nothing else is a tool name; a
# parameter is written with its value (`timeout_s=600`), a key with its colon.
TOOL = re.compile(r"`([a-z]+(?:_[a-z]+)+)`")
COMMAND = re.compile(r"`/([a-z][a-z-]*)[` ]")
PROGRAM = re.compile(r"`(aegis(?: [^`]*)?)`")


@pytest.fixture(scope="module")
def pages():
    return {p.id: (HELP_DIR / f"{p.id}.md").read_text() for p in load()}


@pytest.fixture(scope="module")
def registry(tmp_path_factory):
    return App(make_roots(tmp_path_factory.mktemp("help"), None)).registry


def where(pages: dict[str, str], pattern: re.Pattern) -> dict[str, list[str]]:
    """Each name the pattern finds, with the pages it is on."""
    found: dict[str, list[str]] = {}
    for page, text in pages.items():
        for name in pattern.findall(text):
            found.setdefault(name, []).append(page)
    return found


def test_the_first_pages_are_there_with_title_kind_and_when(pages):
    assert FIRST_PAGES <= set(pages)
    for p in load():
        assert re.fullmatch(r"[a-z0-9]+(-[a-z0-9]+)*", p.id), p.id
        assert p.kind in KINDS and p.title and p.when, p.id
        assert "\n" not in p.when, p.id
        assert p.sections[0].text, f"{p.id}.md opens with no paragraph"
        fenced = False
        for line in p.body.splitlines():
            fenced ^= line.startswith("```")
            assert fenced or not line.startswith("# "), f"{p.id}.md: {line!r}"


def test_every_op_name_is_lowercase_segments_joined_by_dots(registry):
    """The reverse tool rule maps monitor_start back to monitor.start, which is
    right only while no segment has an underscore."""
    bad = [n for n in registry.names() if not re.fullmatch(r"[a-z]+(\.[a-z]+)*", n)]
    assert not bad, bad


def test_every_agent_tool_is_named_on_a_page(registry, pages):
    text = "\n".join(pages.values())
    missing = [
        op.tool_name for op in registry.agent_ops() if f"`{op.tool_name}`" not in text
    ]
    assert not missing, f"no help page names {missing}"


def test_every_tool_a_page_names_exists(registry, pages):
    tools = {op.tool_name for op in registry.agent_ops()}
    unknown = {n: p for n, p in where(pages, TOOL).items() if n not in tools}
    assert not unknown, f"no agent tool is named {unknown}"


def test_every_aegis_command_is_named_on_a_page(pages):
    text = "\n".join(pages.values())
    missing = [c for c in AEGIS if not re.search(rf"`/{re.escape(c)}[` ]", text)]
    assert not missing, f"no help page names /{missing}"


def test_every_command_a_page_names_exists(pages):
    unknown = {n: p for n, p in where(pages, COMMAND).items() if n not in AEGIS}
    assert not unknown, f"aegis has no command {unknown}"


def _subcommands() -> list[str]:
    """Every path through the CLI: ``link``, ``link add``, ``usage repo``."""
    out: list[str] = []

    def walk(group, prefix: str) -> None:
        for name, cmd in sorted(group.commands.items()):
            out.append(f"{prefix}{name}")
            if hasattr(cmd, "commands"):
                walk(cmd, f"{prefix}{name} ")

    walk(typer.main.get_command(cli.app), "")
    return out


def test_every_cli_subcommand_is_on_the_commands_page(pages):
    text = pages["commands"]
    missing = [
        s for s in _subcommands() if not re.search(rf"`aegis {re.escape(s)}[` ]", text)
    ]
    assert not missing, f"the commands page has no `aegis {missing}`"


def test_every_cli_subcommand_a_page_names_exists(pages):
    root = typer.main.get_command(cli.app)
    unknown: dict[str, list[str]] = {}
    for span, on in where(pages, PROGRAM).items():
        cmd = root
        for word in span.split()[1:]:
            if not re.fullmatch(r"[a-z][a-z-]*", word):
                break  # an option or an <argument>: the path is over
            subs = getattr(cmd, "commands", None)
            if subs is None:
                break  # a leaf: the rest are its arguments
            if word not in subs:
                unknown[span] = on
                break
            cmd = subs[word]
    assert not unknown, f"aegis has no such subcommand: {unknown}"


def test_every_config_key_is_on_the_config_page(pages):
    """A key is written with its colon. ``name`` is each agent's and queue's
    key in its map, not a key inside it."""
    keys = {
        k
        for model in (ConfigDoc, AgentDoc, QueueDoc)
        for k in model.model_fields
        if k != "name"
    }
    missing = sorted(k for k in keys if f"`{k}:`" not in pages["config"])
    assert not missing, f"the config page does not name {missing}"


@pytest.mark.slow
def test_the_wheel_ships_every_help_page(tmp_path):
    """The index reads the pages from the installed package, so a wheel without
    them is a server whose help tools answer nothing."""
    root = Path(__file__).parent.parent
    subprocess.run(
        ["uv", "build", "--wheel", "-q", "-o", str(tmp_path)], cwd=root, check=True
    )
    (wheel,) = tmp_path.glob("*.whl")
    names = set(zipfile.ZipFile(wheel).namelist())
    assert {f"aegis/help/{p.id}.md" for p in load()} <= names


async def test_the_help_tab_lists_every_page_and_agents_cannot_ask(
    tmp_path, fake_claude, fake_opencode
):
    """help.pages is the Help tab's list: what the index lists, for a person
    only. An agent asks help_search, which answers with the same list when
    nothing matches."""
    (tmp_path / ".aegis.yaml").write_text(
        "default_agent: opus\nagents:\n"
        "  opus: {harness: claude-code, model: opus, effort: high, permission: full}\n"
    )
    app = App(
        make_roots(tmp_path, tmp_path),
        claude_bin=fake_claude,
        opencode_bin=fake_opencode,
    )
    assert await app.registry.call("help.pages", {}) == Index(load()).listing()
    with pytest.raises(OpError) as e:
        await app.registry.call("help.pages", {}, Caller("agent", log_id="x"))
    assert e.value.code == "not_for_agents"


REPO = Path(__file__).resolve().parents[1]
FRAGMENT_HELP = re.compile(r"^help: ([a-z0-9-]+)(?:#([a-z0-9-]+))?$")


def fragment_problems(directory: Path, pages: dict[str, Page]) -> list[str]:
    """Each added or changed fragment whose last line does not name a real help
    page and section, as "<file>: <why>"."""
    out = []
    found = [*directory.glob("*.added.md"), *directory.glob("*.changed.md")]
    for f in sorted(found, key=lambda p: p.name):
        lines = f.read_text().strip().splitlines()
        m = FRAGMENT_HELP.match(lines[-1]) if lines else None
        if m is None:
            out.append(f"{f.name}: the last line is not 'help: page[#section]'")
        elif m[1] not in pages:
            out.append(f"{f.name}: no help page {m[1]!r}")
        elif m[2] and m[2] not in {s.slug for s in pages[m[1]].sections}:
            out.append(f"{f.name}: page {m[1]!r} has no section {m[2]!r}")
    return out


def test_every_feature_fragment_names_a_help_page_that_exists():
    pages = {p.id: p for p in helppages.load()}
    assert fragment_problems(REPO / "changelog.d", pages) == []


def test_the_fragment_check_fails_on_a_missing_line_page_or_section(tmp_path):
    pages = {p.id: p for p in helppages.load()}
    (tmp_path / "1-ok.added.md").write_text("- **A.**\nhelp: transcript#tool-rows\n")
    (tmp_path / "2-none.changed.md").write_text("- **B.**\n")
    (tmp_path / "3-page.added.md").write_text("- **C.**\nhelp: no-such-page\n")
    (tmp_path / "4-sec.added.md").write_text(
        "- **D.**\nhelp: transcript#no-such-section\n"
    )
    (tmp_path / "5-fix.fixed.md").write_text("- **E.**\n")
    names = [p.split(":")[0] for p in fragment_problems(tmp_path, pages)]
    assert names == ["2-none.changed.md", "3-page.added.md", "4-sec.added.md"]
