# Native lovelaice agent (VS3 — full native toolset) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: superpowers:executing-plans. Steps use checkbox (`- [ ]`) syntax.

**Goal:** The default `lovelaice-acp` coding agent gets a full basic toolset on par with gemini/opencode: `read`, `bash` (existing) + **`write`, `edit`, `glob`, `list_dir`** — with ACP `kind`s and the cwd path guard.

**Architecture:** Add `@tool`-wrapped coding-host tool modules that delegate to the already-robust `lovelaice.tools.files` / `lovelaice.tools.search` logic (`write` creates parent dirs; `edit` errors on 0/>1 matches; `glob` honors `.gitignore`). Wire them into `create_coding_agent` with the right `kind`/`title_template`; extend `path_guard` to cover `list_dir`. No subagents, no skills.

**Tech Stack:** lovelaice `agent/` + `coding/` + `tools/`, `lingo.tools.tool`.

## Global Constraints

- Delegate to existing `lovelaice.tools.files`/`search` logic — don't reimplement (DRY); those functions already handle parent-dir creation, unambiguous edit, gitignore.
- `@tool` derives the tool name from `__name__`; use `list_dir` (not `list`, which shadows the builtin).
- ACP kinds: write/edit → `edit`; glob/list_dir → `search`.
- `path_guard` gates `read/write/edit`; extend to `list_dir`. `glob` uses `pattern` (rooted at cwd), no guard.
- Ship lovelaice **2.9.0**; aegis floor → `>=2.9,<3`.
- Real-model probe before release (VS1/VS2 lesson). Tests inline.

## File Structure
- Create `src/lovelaice/coding/tools/write.py`, `edit.py`, `glob.py`, `list_dir.py` — `@tool` wrappers delegating to the lib.
- Modify `src/lovelaice/coding/hooks.py` — add `list_dir` to `path_guard`.
- Modify `src/lovelaice/coding/host.py` — wire the four tools + update `CODING_PREAMBLE`.
- Create `tests/coding/test_full_toolset.py`.
- Modify `pyproject.toml` + `CHANGELOG.md` (2.9.0). aegis `pyproject.toml` floor bump.

## Task 1: coding-host tool wrappers

**Files:** Create the four `coding/tools/*.py`; Test `tests/coding/test_full_toolset.py`.

**Interfaces:**
- Produces `write(path, content)`, `edit(path, old, new)`, `glob(pattern)`, `list_dir(path=".")` — each a `lingo` Tool (via `@tool`) delegating to the lib.

- [ ] **Step 1: Failing test**

```python
# tests/coding/test_full_toolset.py
import pytest
from lovelaice.coding.tools.write import write
from lovelaice.coding.tools.edit import edit
from lovelaice.coding.tools.glob import glob
from lovelaice.coding.tools.list_dir import list_dir


@pytest.mark.asyncio
async def test_write_edit_glob_list(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    await write.run(path="sub/a.txt", content="hello world")
    assert (tmp_path / "sub/a.txt").read_text() == "hello world"
    await edit.run(path="sub/a.txt", old="world", new="there")
    assert (tmp_path / "sub/a.txt").read_text() == "hello there"
    names = await list_dir.run(path="sub")
    assert "a.txt" in names
    matches = await glob.run(pattern="sub/*.txt")
    assert "sub/a.txt" in matches
    # tool names are the model-facing identifiers
    assert {write.name, edit.name, glob.name, list_dir.name} == {
        "write", "edit", "glob", "list_dir"}
```

- [ ] **Step 2: Run → fail** (modules don't exist). `cd repos/lovelaice && uv run python -m pytest tests/coding/test_full_toolset.py -v`

- [ ] **Step 3: Implement** each wrapper, e.g. `coding/tools/write.py`:

```python
"""Coding host: write tool (delegates to lovelaice.tools.files.write)."""
from lingo.tools import tool
from lovelaice.tools import files


@tool
async def write(path: str, content: str) -> str:
    """Write content to a file, overwriting if it exists; creates parent
    directories as needed. For surgical in-file changes prefer `edit`."""
    return await files.write(path, content)
```

`edit.py` → delegates to `files.edit(path, old, new)` with a docstring describing the unique-match requirement. `list_dir.py` → `@tool async def list_dir(path: str = ".") -> list[str]: return await files.list_(path)`. `glob.py` → `@tool async def glob(pattern: str) -> list[str]: return await search.glob(pattern)`. Copy the good docstrings from the lib functions so the model gets strong descriptions.

- [ ] **Step 4: Run → pass.**
- [ ] **Step 5: Commit** — `feat(coding): write/edit/glob/list_dir tool wrappers`

## Task 2: path_guard covers list_dir + wire into host

**Files:** Modify `coding/hooks.py`, `coding/host.py`; Test `tests/coding/test_full_toolset.py`.

**Interfaces:**
- `path_guard` gates `("read","write","edit","list_dir")`.
- `create_coding_agent` registers the four tools with kinds/titles.

- [ ] **Step 1: Failing test** — append:

```python
def test_host_wires_full_toolset(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-test")
    from lovelaice.coding.host import create_coding_agent
    agent = create_coding_agent(
        model="fake/model", session_path=tmp_path / "s.jsonl", cwd=str(tmp_path))
    names = {t.name for t in agent.harness.tools.all()}
    assert {"read", "bash", "write", "edit", "glob", "list_dir"} <= names


def test_path_guard_blocks_list_dir_outside_cwd():
    from lovelaice.coding.hooks import path_guard
    class Call:
        name = "list_dir"
        arguments = {"path": "/etc"}
    assert path_guard(Call(), cwd="/tmp") is not None
```

- [ ] **Step 2: Run → fail.**
- [ ] **Step 3: Implement:**
  - `hooks.py`: `if call.name not in ("read", "write", "edit", "list_dir"): return None`. For `list_dir` the arg is `path` — already handled by the shared body.
  - `host.py`: import the four tools; add to the `tools` list:
    ```python
    AgentTool(inner=write_tool, kind="edit", title_template="Writing {path}"),
    AgentTool(inner=edit_tool, kind="edit", title_template="Editing {path}"),
    AgentTool(inner=glob_tool, kind="search", title_template="Globbing {pattern}"),
    AgentTool(inner=list_dir_tool, kind="search", title_template="Listing {path}"),
    ```
  - Update `CODING_PREAMBLE`: "You can read, write, and edit files, glob and list directories, and run bash commands. Prefer reading before writing; prefer `edit` for surgical changes."

- [ ] **Step 4: Run → pass** (+ existing coding/acp suites green).
- [ ] **Step 5: Commit** — `feat(coding): wire full toolset + guard list_dir + preamble`

## Task 3: real-model probe + release 2.9.0

- [ ] **Step 1:** `.playground` probe (or reuse `probe.py` idea): real haiku model, cwd a tmp dir; prompt "create a file notes.md with 'hi', then edit 'hi' to 'bye', then list the dir". Assert `write`/`edit`/`list_dir` tool_uses fire and the file ends as `bye`. (Install local editable into aegis or run via lovelaice env.)
- [ ] **Step 2:** Bump `version = "2.9.0"`; CHANGELOG § 2.9.0 (full native toolset). Full suite green.
- [ ] **Step 3:** Commit, push, `gh release create v2.9.0 …` → OIDC publish; verify PyPI == 2.9.0.

## Task 4: aegis bump

- [ ] **Step 1:** aegis `pyproject.toml` → `lovelaice>=2.9,<3`; `uv lock && uv sync`.
- [ ] **Step 2:** Re-run `tests/test_lovelaice_live.py` + `tests/test_lovelaice_mcp_live.py` green against 2.9.0.
- [ ] **Step 3:** Commit + push.

## Self-Review
**Spec coverage (Part 2):** ✅ write/edit/glob/list wired (T1–T2); ACP kinds (T2); guards (T2); preamble (T2); no subagents/skills. Robustness (parent-dir/unambiguous-edit/gitignore) inherited from the lib. **Deferred:** output caps on glob/list_dir (lib tools are shared; cap all consumers separately if a real blowup appears).
**Type consistency:** tool names `write/edit/glob/list_dir` consistent across wrappers (T1), guard (T2), host wiring (T2). `create_coding_agent` unchanged signature (extra_tools from VS2 preserved).
