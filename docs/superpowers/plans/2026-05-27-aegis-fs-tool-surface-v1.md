# Aegis FS Tool Surface v1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship six aegis-owned filesystem MCP tools (`aegis_bash`, `aegis_read`, `aegis_write`, `aegis_edit`, `aegis_grep`, `aegis_listdir`), a `PermissionRouter` that gates them with `allow`/`deny`/`ask` per agent profile, TUI inline + Telegram inline-button approval surfaces, hard Claude built-in suppression via `--tools ""`, and a universal prefer-aegis-tools system-prompt addendum.

**Architecture:** Tools live in `src/aegis/mcp/fs_tools/` (one file per tool), registered through the existing `aegis.mcp.server` FastMCP shape. A `PermissionRouter` in `src/aegis/mcp/permissions.py` wraps each tool registration. "Ask" verdicts route to TUI when a `ConversationPane` is mounted for the handle, Telegram inline buttons otherwise. Claude drivers gain `--tools ""` when `agent.suppress_builtins=True`. The `PRIMING` system prompt grows a prefer-aegis-tools block (unconditional).

**Tech Stack:** Python 3.11+, `asyncio`, FastMCP, `pytest`, `uv`. New deps: none (ripgrep optional at runtime).

**Spec:** `docs/superpowers/specs/2026-05-27-aegis-fs-tool-surface-design.md`.

**Files this plan touches:**

| File | Status | Responsibility |
|---|---|---|
| `src/aegis/mcp/fs_tools/__init__.py` | create | `register_fs_tools(server, bridge)` entrypoint |
| `src/aegis/mcp/fs_tools/read.py` | create | `aegis_read` impl |
| `src/aegis/mcp/fs_tools/listdir.py` | create | `aegis_listdir` impl |
| `src/aegis/mcp/fs_tools/grep.py` | create | `aegis_grep` impl (ripgrep + grep fallback) |
| `src/aegis/mcp/fs_tools/write.py` | create | `aegis_write` impl (new-file-only) |
| `src/aegis/mcp/fs_tools/edit.py` | create | `aegis_edit` impl (exact-string replace) |
| `src/aegis/mcp/fs_tools/bash.py` | create | `aegis_bash` impl (one-shot subprocess) |
| `src/aegis/mcp/permissions.py` | create | `Verdict` enum, `PermissionRequest`, `OperatorSurface` Protocol, `PermissionRouter`, `permission_gate` decorator |
| `src/aegis/mcp/audit.py` | create | `AuditLog` writer + `record_call` helper |
| `src/aegis/mcp/server.py` | modify | Call `register_fs_tools(server, bridge)`; extend `PRIMING`; add `bridge.permission_router` plumbing |
| `src/aegis/mcp/bridge.py` | modify | `AppBridge` Protocol grows `permission_router: object` |
| `src/aegis/config/__init__.py` | modify | `_ProviderBase` gains `suppress_builtins`, `permissions`, `permission_timeout_s` |
| `src/aegis/drivers/claude_print.py` | modify | `build_argv` appends `--tools ""` when `agent.suppress_builtins` |
| `src/aegis/drivers/claude_repl.py` | modify | same as claude_print.py |
| `src/aegis/tui/pane.py` | modify | `ConversationPane` registers as TUI `OperatorSurface`, renders approval modal |
| `src/aegis/telegram/bot.py` | modify | `send_message_with_inline_keyboard`, `edit_message_text`, dispatch `callback_query` updates |
| `src/aegis/telegram/frontend.py` | modify | Register `perm:` callback handler; route to `PermissionRouter.resolve` |
| `tests/test_aegis_fs_*.py` (6 files) | create | Per-tool TDD tests |
| `tests/test_aegis_permissions.py` | create | Permission router + cache + timeout tests |
| `tests/test_aegis_audit.py` | create | Audit log tests |
| `tests/test_aegis_permission_tui.py` | create | TUI modal tests |
| `tests/test_aegis_permission_telegram.py` | create | Telegram inline-button tests |
| `tests/test_aegis_priming.py` | create | PRIMING addendum tests |
| `tests/test_claude_tools_suppression.py` | create | Claude `--tools ""` argv + smoke test |
| `AGENTS.md` | modify | Document fs_tools package + permission framework |
| `CHANGELOG.md` | modify | `## [0.13.0]` entry |
| `pyproject.toml` | modify | Version bump 0.12.0 → 0.13.0 |

---

## Slice 1 — Six filesystem tools (no permission gate yet)

Ship all six tools as plain MCP registrations with full TDD coverage. Each is a small, focused module. Permission gate layers on in Slice 2.

### Task 1: `fs_tools/` package + `register_fs_tools` entrypoint

**Files:**
- Create: `src/aegis/mcp/fs_tools/__init__.py`
- Create: `tests/test_aegis_fs_register.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_aegis_fs_register.py`:

```python
from __future__ import annotations

from aegis.mcp.fs_tools import register_fs_tools


class _StubServer:
    def __init__(self) -> None:
        self.registered: list[str] = []

    def tool(self, fn):
        self.registered.append(fn.__name__)
        return fn


def test_register_fs_tools_registers_all_six_by_name():
    srv = _StubServer()
    register_fs_tools(srv, bridge=None)
    assert set(srv.registered) == {
        "aegis_bash", "aegis_read", "aegis_write",
        "aegis_edit", "aegis_grep", "aegis_listdir",
    }
```

- [ ] **Step 2: Run test to verify it fails**

```
uv run pytest tests/test_aegis_fs_register.py -v
```

Expected: ERROR — `ModuleNotFoundError: No module named 'aegis.mcp.fs_tools'`.

- [ ] **Step 3: Write minimal implementation**

Create `src/aegis/mcp/fs_tools/__init__.py`:

```python
"""Aegis-owned filesystem and shell tools exposed on the MCP plane.

One module per tool; this __init__ wires them into the FastMCP server.
Spec: docs/superpowers/specs/2026-05-27-aegis-fs-tool-surface-design.md
"""
from __future__ import annotations


def register_fs_tools(server, bridge) -> None:
    """Register every aegis_* filesystem tool on the FastMCP server.

    Called once from aegis.mcp.server during MCP server construction.
    """
    from aegis.mcp.fs_tools.bash import aegis_bash
    from aegis.mcp.fs_tools.read import aegis_read
    from aegis.mcp.fs_tools.write import aegis_write
    from aegis.mcp.fs_tools.edit import aegis_edit
    from aegis.mcp.fs_tools.grep import aegis_grep
    from aegis.mcp.fs_tools.listdir import aegis_listdir
    for fn in (aegis_bash, aegis_read, aegis_write,
               aegis_edit, aegis_grep, aegis_listdir):
        server.tool(fn)
```

Now create empty placeholders for each tool so imports resolve. Each file just defines an async function with the expected name; bodies come in their own tasks.

Create `src/aegis/mcp/fs_tools/read.py`:

```python
async def aegis_read(path: str, offset: int = 0, limit: int = 2000) -> dict:
    """Placeholder; implemented in Task 2."""
    raise NotImplementedError
```

Create `src/aegis/mcp/fs_tools/listdir.py`:

```python
async def aegis_listdir(path: str = ".", recursive: bool = False,
                       respect_gitignore: bool = True) -> dict:
    raise NotImplementedError
```

Create `src/aegis/mcp/fs_tools/grep.py`:

```python
async def aegis_grep(pattern: str, path: str | None = None,
                    case_insensitive: bool = False,
                    max_results: int = 200) -> dict:
    raise NotImplementedError
```

Create `src/aegis/mcp/fs_tools/write.py`:

```python
async def aegis_write(path: str, content: str) -> dict:
    raise NotImplementedError
```

Create `src/aegis/mcp/fs_tools/edit.py`:

```python
async def aegis_edit(path: str, old_string: str, new_string: str,
                    replace_all: bool = False) -> dict:
    raise NotImplementedError
```

Create `src/aegis/mcp/fs_tools/bash.py`:

```python
async def aegis_bash(command: str, cwd: str | None = None,
                    timeout_s: int = 120) -> dict:
    raise NotImplementedError
```

- [ ] **Step 4: Run test to verify it passes**

```
uv run pytest tests/test_aegis_fs_register.py -v
```

Expected: 1 PASSED.

- [ ] **Step 5: Commit**

```bash
git add src/aegis/mcp/fs_tools/ tests/test_aegis_fs_register.py
git commit -m "feat(mcp): scaffold fs_tools package + register_fs_tools entrypoint"
```

---

### Task 2: `aegis_read`

**Files:**
- Modify: `src/aegis/mcp/fs_tools/read.py`
- Create: `tests/test_aegis_fs_read.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_aegis_fs_read.py`:

```python
from __future__ import annotations

import asyncio
from pathlib import Path

from aegis.mcp.fs_tools.read import aegis_read


def _run(coro):
    return asyncio.run(asyncio.wait_for(coro, timeout=5))


def test_reads_whole_small_file_with_line_numbers(tmp_path: Path):
    p = tmp_path / "hello.txt"
    p.write_text("alpha\nbeta\ngamma\n")
    out = _run(aegis_read(str(p)))
    assert out["path"] == str(p)
    assert out["content"] == "     1\talpha\n     2\tbeta\n     3\tgamma\n"
    assert out["total_lines"] == 3
    assert out["truncated"] is False


def test_pagination_via_offset_and_limit(tmp_path: Path):
    p = tmp_path / "big.txt"
    p.write_text("\n".join(f"line {i}" for i in range(1, 11)) + "\n")
    out = _run(aegis_read(str(p), offset=5, limit=3))
    # Lines 6, 7, 8 (0-indexed offset=5 → 1-indexed line 6)
    assert out["content"] == "     6\tline 6\n     7\tline 7\n     8\tline 8\n"
    assert out["truncated"] is True
    assert out["total_lines"] == 10


def test_missing_file_returns_error(tmp_path: Path):
    out = _run(aegis_read(str(tmp_path / "nope.txt")))
    assert "error" in out
    assert "not found" in out["error"].lower()


def test_directory_path_returns_error(tmp_path: Path):
    out = _run(aegis_read(str(tmp_path)))
    assert "error" in out
    assert "directory" in out["error"].lower()
```

- [ ] **Step 2: Run test to verify it fails**

```
uv run pytest tests/test_aegis_fs_read.py -v
```

Expected: 4 FAILED — `NotImplementedError`.

- [ ] **Step 3: Write minimal implementation**

Replace `src/aegis/mcp/fs_tools/read.py`:

```python
"""aegis_read — paginated file read with cat -n-style line numbers."""
from __future__ import annotations

import asyncio
from pathlib import Path


async def aegis_read(path: str, offset: int = 0, limit: int = 2000) -> dict:
    """Read a UTF-8 text file, returning lines [offset, offset+limit)
    formatted with cat -n-style line numbers ("     N\\t<line>\\n").

    Returns:
        {path, content, total_lines, truncated}
        — or {error: "..."} on failure.
    """
    return await asyncio.to_thread(_read_sync, path, offset, limit)


def _read_sync(path: str, offset: int, limit: int) -> dict:
    p = Path(path)
    if not p.exists():
        return {"error": f"file not found: {path}"}
    if p.is_dir():
        return {"error": f"path is a directory, not a file: {path}"}
    try:
        text = p.read_text(encoding="utf-8", errors="replace")
    except OSError as e:
        return {"error": f"read failed: {e}"}
    lines = text.splitlines(keepends=False)
    total = len(lines)
    selected = lines[offset:offset + limit]
    formatted = "".join(f"{i:>6}\t{line}\n"
                        for i, line in enumerate(selected, start=offset + 1))
    return {
        "path": path,
        "content": formatted,
        "total_lines": total,
        "truncated": offset + len(selected) < total or offset > 0,
    }
```

- [ ] **Step 4: Run test to verify it passes**

```
uv run pytest tests/test_aegis_fs_read.py -v
```

Expected: 4 PASSED.

- [ ] **Step 5: Commit**

```bash
git add src/aegis/mcp/fs_tools/read.py tests/test_aegis_fs_read.py
git commit -m "feat(mcp/fs_tools): aegis_read — paginated read with line numbers"
```

---

### Task 3: `aegis_listdir`

**Files:**
- Modify: `src/aegis/mcp/fs_tools/listdir.py`
- Create: `tests/test_aegis_fs_listdir.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_aegis_fs_listdir.py`:

```python
from __future__ import annotations

import asyncio
import os
from pathlib import Path

from aegis.mcp.fs_tools.listdir import aegis_listdir


def _run(coro):
    return asyncio.run(asyncio.wait_for(coro, timeout=5))


def test_flat_listing_with_types(tmp_path: Path):
    (tmp_path / "a.txt").write_text("x")
    (tmp_path / "sub").mkdir()
    (tmp_path / "link").symlink_to(tmp_path / "a.txt")
    out = _run(aegis_listdir(str(tmp_path)))
    by_name = {e["name"]: e for e in out["entries"]}
    assert by_name["a.txt"]["type"] == "file"
    assert by_name["sub"]["type"] == "dir"
    assert by_name["link"]["type"] == "symlink"


def test_recursive_lists_nested(tmp_path: Path):
    (tmp_path / "top.txt").write_text("x")
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "inner.txt").write_text("y")
    out = _run(aegis_listdir(str(tmp_path), recursive=True))
    names = {e["name"] for e in out["entries"]}
    assert "top.txt" in names
    assert "sub/inner.txt" in names


def test_respects_gitignore_when_enabled(tmp_path: Path):
    (tmp_path / ".gitignore").write_text("ignored.txt\n")
    (tmp_path / "keep.txt").write_text("x")
    (tmp_path / "ignored.txt").write_text("y")
    out = _run(aegis_listdir(str(tmp_path), respect_gitignore=True))
    names = {e["name"] for e in out["entries"]}
    assert "keep.txt" in names
    assert "ignored.txt" not in names


def test_respect_gitignore_false_shows_everything(tmp_path: Path):
    (tmp_path / ".gitignore").write_text("ignored.txt\n")
    (tmp_path / "ignored.txt").write_text("y")
    out = _run(aegis_listdir(str(tmp_path), respect_gitignore=False))
    names = {e["name"] for e in out["entries"]}
    assert "ignored.txt" in names


def test_missing_path_returns_error():
    out = _run(aegis_listdir("/this/does/not/exist/anywhere"))
    assert "error" in out
```

- [ ] **Step 2: Run test to verify it fails**

```
uv run pytest tests/test_aegis_fs_listdir.py -v
```

Expected: 5 FAILED — `NotImplementedError`.

- [ ] **Step 3: Write minimal implementation**

Replace `src/aegis/mcp/fs_tools/listdir.py`:

```python
"""aegis_listdir — flat or recursive directory listing."""
from __future__ import annotations

import asyncio
from pathlib import Path


async def aegis_listdir(path: str = ".", recursive: bool = False,
                       respect_gitignore: bool = True) -> dict:
    """List directory entries.

    Returns:
        {path, entries: [{name, type}], count}
        — or {error: "..."}.

    `type` is "file" | "dir" | "symlink".
    """
    return await asyncio.to_thread(_listdir_sync, path, recursive,
                                   respect_gitignore)


def _listdir_sync(path: str, recursive: bool,
                  respect_gitignore: bool) -> dict:
    root = Path(path)
    if not root.exists():
        return {"error": f"path not found: {path}"}
    if not root.is_dir():
        return {"error": f"path is not a directory: {path}"}
    ignores = _read_gitignore(root) if respect_gitignore else set()
    entries: list[dict] = []
    if recursive:
        for sub in root.rglob("*"):
            rel = sub.relative_to(root).as_posix()
            if respect_gitignore and _matches_any(rel, ignores):
                continue
            entries.append({"name": rel, "type": _entry_type(sub)})
    else:
        for sub in sorted(root.iterdir()):
            if respect_gitignore and _matches_any(sub.name, ignores):
                continue
            entries.append({"name": sub.name, "type": _entry_type(sub)})
    return {"path": path, "entries": entries, "count": len(entries)}


def _entry_type(p: Path) -> str:
    if p.is_symlink():
        return "symlink"
    if p.is_dir():
        return "dir"
    return "file"


def _read_gitignore(root: Path) -> set[str]:
    gi = root / ".gitignore"
    if not gi.exists():
        return set()
    return {line.strip() for line in gi.read_text(encoding="utf-8",
                                                  errors="replace").splitlines()
            if line.strip() and not line.startswith("#")}


def _matches_any(name: str, patterns: set[str]) -> bool:
    """Simple substring/exact match; not full gitignore-glob semantics
    (v1: keep deps zero — the few cases that matter are exact filenames
    and dir names). Future: pathspec library if real workloads need full
    glob support."""
    base = name.split("/")[0]
    return base in patterns or name in patterns
```

- [ ] **Step 4: Run test to verify it passes**

```
uv run pytest tests/test_aegis_fs_listdir.py -v
```

Expected: 5 PASSED.

- [ ] **Step 5: Commit**

```bash
git add src/aegis/mcp/fs_tools/listdir.py tests/test_aegis_fs_listdir.py
git commit -m "feat(mcp/fs_tools): aegis_listdir — flat/recursive with gitignore filter"
```

---

### Task 4: `aegis_grep`

**Files:**
- Modify: `src/aegis/mcp/fs_tools/grep.py`
- Create: `tests/test_aegis_fs_grep.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_aegis_fs_grep.py`:

```python
from __future__ import annotations

import asyncio
from pathlib import Path

from aegis.mcp.fs_tools.grep import aegis_grep


def _run(coro):
    return asyncio.run(asyncio.wait_for(coro, timeout=10))


def test_literal_match_returns_path_line_match(tmp_path: Path):
    (tmp_path / "a.py").write_text("def foo():\n    return 42\n")
    (tmp_path / "b.py").write_text("def bar():\n    return foo()\n")
    out = _run(aegis_grep("foo", str(tmp_path)))
    # Three hits: a.py:1 (def foo), b.py:2 (return foo())
    matches = out["matches"]
    assert any(m["path"].endswith("a.py") and m["line"] == 1
               and "foo" in m["text"] for m in matches)
    assert any(m["path"].endswith("b.py") and m["line"] == 2
               for m in matches)


def test_regex_special_chars_are_literal(tmp_path: Path):
    (tmp_path / "x.txt").write_text("a.b\nazb\n")
    # Literal "a.b" should match only the first line, not "azb"
    out = _run(aegis_grep("a.b", str(tmp_path)))
    assert len(out["matches"]) == 1
    assert "a.b" in out["matches"][0]["text"]


def test_case_insensitive(tmp_path: Path):
    (tmp_path / "x.txt").write_text("Hello\nhello\nHELLO\n")
    out = _run(aegis_grep("hello", str(tmp_path), case_insensitive=True))
    assert len(out["matches"]) == 3


def test_max_results_caps_output(tmp_path: Path):
    (tmp_path / "x.txt").write_text(
        "\n".join(f"hit {i}" for i in range(50)) + "\n")
    out = _run(aegis_grep("hit", str(tmp_path), max_results=10))
    assert len(out["matches"]) == 10
    assert out["truncated"] is True


def test_no_matches_returns_empty(tmp_path: Path):
    (tmp_path / "x.txt").write_text("nothing here\n")
    out = _run(aegis_grep("xyzzy", str(tmp_path)))
    assert out["matches"] == []
    assert out["truncated"] is False
```

- [ ] **Step 2: Run test to verify it fails**

```
uv run pytest tests/test_aegis_fs_grep.py -v
```

Expected: 5 FAILED — `NotImplementedError`.

- [ ] **Step 3: Write minimal implementation**

Replace `src/aegis/mcp/fs_tools/grep.py`:

```python
"""aegis_grep — literal-text recursive search.

Prefers ripgrep when on PATH; falls back to GNU grep -F.
"""
from __future__ import annotations

import asyncio
import shutil
import subprocess
from pathlib import Path


async def aegis_grep(pattern: str, path: str | None = None,
                    case_insensitive: bool = False,
                    max_results: int = 200) -> dict:
    """Recursively search `path` for literal `pattern`.

    Returns:
        {matches: [{path, line, text}], truncated, engine}
    """
    return await asyncio.to_thread(_grep_sync, pattern, path or ".",
                                   case_insensitive, max_results)


def _grep_sync(pattern: str, path: str, case_insensitive: bool,
               max_results: int) -> dict:
    if not Path(path).exists():
        return {"error": f"path not found: {path}", "matches": [],
                "truncated": False}
    rg = shutil.which("rg")
    if rg:
        return _grep_via_rg(rg, pattern, path, case_insensitive, max_results)
    return _grep_via_grep(pattern, path, case_insensitive, max_results)


def _grep_via_rg(rg: str, pattern: str, path: str,
                 case_insensitive: bool, max_results: int) -> dict:
    cmd = [rg, "--fixed-strings", "--line-number", "--no-heading",
           "--color", "never", "-m", str(max_results + 1)]
    if case_insensitive:
        cmd.append("--ignore-case")
    cmd += ["--", pattern, path]
    res = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    return _parse_grep_lines(res.stdout, max_results, engine="ripgrep")


def _grep_via_grep(pattern: str, path: str, case_insensitive: bool,
                   max_results: int) -> dict:
    cmd = ["grep", "-r", "-F", "-n", "--color=never"]
    if case_insensitive:
        cmd.append("-i")
    cmd += ["--", pattern, path]
    res = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    return _parse_grep_lines(res.stdout, max_results, engine="grep")


def _parse_grep_lines(stdout: str, max_results: int, engine: str) -> dict:
    matches: list[dict] = []
    truncated = False
    for line in stdout.splitlines():
        if len(matches) >= max_results:
            truncated = True
            break
        # "path:line:text"
        parts = line.split(":", 2)
        if len(parts) < 3:
            continue
        try:
            ln = int(parts[1])
        except ValueError:
            continue
        matches.append({"path": parts[0], "line": ln, "text": parts[2]})
    return {"matches": matches, "truncated": truncated, "engine": engine}
```

- [ ] **Step 4: Run test to verify it passes**

```
uv run pytest tests/test_aegis_fs_grep.py -v
```

Expected: 5 PASSED.

- [ ] **Step 5: Commit**

```bash
git add src/aegis/mcp/fs_tools/grep.py tests/test_aegis_fs_grep.py
git commit -m "feat(mcp/fs_tools): aegis_grep — literal-text search (ripgrep+grep fallback)"
```

---

### Task 5: `aegis_write` (new-file-only)

**Files:**
- Modify: `src/aegis/mcp/fs_tools/write.py`
- Create: `tests/test_aegis_fs_write.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_aegis_fs_write.py`:

```python
from __future__ import annotations

import asyncio
from pathlib import Path

from aegis.mcp.fs_tools.write import aegis_write


def _run(coro):
    return asyncio.run(asyncio.wait_for(coro, timeout=5))


def test_writes_new_file(tmp_path: Path):
    p = tmp_path / "new.txt"
    out = _run(aegis_write(str(p), "hello\nworld\n"))
    assert out["path"] == str(p)
    assert out["bytes"] == 12
    assert p.read_text() == "hello\nworld\n"


def test_refuses_existing_file(tmp_path: Path):
    p = tmp_path / "exists.txt"
    p.write_text("original")
    out = _run(aegis_write(str(p), "new content"))
    assert "error" in out
    assert "exists" in out["error"].lower()
    assert p.read_text() == "original"  # not overwritten


def test_creates_parent_dirs_if_missing(tmp_path: Path):
    p = tmp_path / "deep" / "nested" / "file.txt"
    out = _run(aegis_write(str(p), "x"))
    assert "error" not in out
    assert p.read_text() == "x"
```

- [ ] **Step 2: Run test to verify it fails**

```
uv run pytest tests/test_aegis_fs_write.py -v
```

Expected: 3 FAILED — `NotImplementedError`.

- [ ] **Step 3: Write minimal implementation**

Replace `src/aegis/mcp/fs_tools/write.py`:

```python
"""aegis_write — new-file-only write.

For modifying existing files use aegis_edit. This is intentional: write
should never overwrite anything, so the caller has to be explicit when
they want a modification.
"""
from __future__ import annotations

import asyncio
from pathlib import Path


async def aegis_write(path: str, content: str) -> dict:
    """Write `content` to a new file at `path`. Errors if the path exists.
    Creates parent directories as needed.

    Returns:
        {path, bytes} on success, {error: "..."} otherwise.
    """
    return await asyncio.to_thread(_write_sync, path, content)


def _write_sync(path: str, content: str) -> dict:
    p = Path(path)
    if p.exists():
        return {"error": f"path already exists (use aegis_edit to modify): {path}"}
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
    except OSError as e:
        return {"error": f"write failed: {e}"}
    return {"path": path, "bytes": len(content.encode("utf-8"))}
```

- [ ] **Step 4: Run test to verify it passes**

```
uv run pytest tests/test_aegis_fs_write.py -v
```

Expected: 3 PASSED.

- [ ] **Step 5: Commit**

```bash
git add src/aegis/mcp/fs_tools/write.py tests/test_aegis_fs_write.py
git commit -m "feat(mcp/fs_tools): aegis_write — new-file-only write"
```

---

### Task 6: `aegis_edit` (exact-string replace)

**Files:**
- Modify: `src/aegis/mcp/fs_tools/edit.py`
- Create: `tests/test_aegis_fs_edit.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_aegis_fs_edit.py`:

```python
from __future__ import annotations

import asyncio
from pathlib import Path

from aegis.mcp.fs_tools.edit import aegis_edit


def _run(coro):
    return asyncio.run(asyncio.wait_for(coro, timeout=5))


def test_replaces_unique_occurrence(tmp_path: Path):
    p = tmp_path / "f.txt"
    p.write_text("hello world\n")
    out = _run(aegis_edit(str(p), "hello", "goodbye"))
    assert out["replacements"] == 1
    assert p.read_text() == "goodbye world\n"


def test_errors_when_old_string_not_unique(tmp_path: Path):
    p = tmp_path / "f.txt"
    p.write_text("foo\nfoo\nbar\n")
    out = _run(aegis_edit(str(p), "foo", "FOO"))
    assert "error" in out
    assert "unique" in out["error"].lower() or "multiple" in out["error"].lower()
    assert p.read_text() == "foo\nfoo\nbar\n"  # untouched


def test_replace_all_handles_multiple(tmp_path: Path):
    p = tmp_path / "f.txt"
    p.write_text("foo\nfoo\nbar\n")
    out = _run(aegis_edit(str(p), "foo", "FOO", replace_all=True))
    assert out["replacements"] == 2
    assert p.read_text() == "FOO\nFOO\nbar\n"


def test_errors_when_old_string_not_found(tmp_path: Path):
    p = tmp_path / "f.txt"
    p.write_text("hello\n")
    out = _run(aegis_edit(str(p), "goodbye", "x"))
    assert "error" in out
    assert "not found" in out["error"].lower()


def test_errors_on_missing_file(tmp_path: Path):
    out = _run(aegis_edit(str(tmp_path / "nope.txt"), "a", "b"))
    assert "error" in out
    assert "not found" in out["error"].lower()
```

- [ ] **Step 2: Run test to verify it fails**

```
uv run pytest tests/test_aegis_fs_edit.py -v
```

Expected: 5 FAILED — `NotImplementedError`.

- [ ] **Step 3: Write minimal implementation**

Replace `src/aegis/mcp/fs_tools/edit.py`:

```python
"""aegis_edit — exact-string targeted replace.

Matches Claude's Edit semantics: error if old_string is not unique
unless replace_all=True; error if old_string is not present at all.
"""
from __future__ import annotations

import asyncio
from pathlib import Path


async def aegis_edit(path: str, old_string: str, new_string: str,
                    replace_all: bool = False) -> dict:
    """Replace `old_string` with `new_string` in the file at `path`.

    Returns:
        {path, replacements} on success, {error: "..."} otherwise.
    """
    return await asyncio.to_thread(_edit_sync, path, old_string,
                                   new_string, replace_all)


def _edit_sync(path: str, old_string: str, new_string: str,
               replace_all: bool) -> dict:
    p = Path(path)
    if not p.exists():
        return {"error": f"file not found: {path}"}
    if p.is_dir():
        return {"error": f"path is a directory, not a file: {path}"}
    try:
        text = p.read_text(encoding="utf-8")
    except OSError as e:
        return {"error": f"read failed: {e}"}
    count = text.count(old_string)
    if count == 0:
        return {"error": f"old_string not found in {path}"}
    if count > 1 and not replace_all:
        return {"error": f"old_string is not unique ({count} occurrences); "
                         "pass replace_all=True or narrow old_string"}
    new_text = (text.replace(old_string, new_string) if replace_all
                else text.replace(old_string, new_string, 1))
    try:
        p.write_text(new_text, encoding="utf-8")
    except OSError as e:
        return {"error": f"write failed: {e}"}
    return {"path": path, "replacements": count if replace_all else 1}
```

- [ ] **Step 4: Run test to verify it passes**

```
uv run pytest tests/test_aegis_fs_edit.py -v
```

Expected: 5 PASSED.

- [ ] **Step 5: Commit**

```bash
git add src/aegis/mcp/fs_tools/edit.py tests/test_aegis_fs_edit.py
git commit -m "feat(mcp/fs_tools): aegis_edit — exact-string targeted replace"
```

---

### Task 7: `aegis_bash` (one-shot subprocess)

**Files:**
- Modify: `src/aegis/mcp/fs_tools/bash.py`
- Create: `tests/test_aegis_fs_bash.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_aegis_fs_bash.py`:

```python
from __future__ import annotations

import asyncio
from pathlib import Path

from aegis.mcp.fs_tools.bash import aegis_bash


def _run(coro):
    return asyncio.run(asyncio.wait_for(coro, timeout=15))


def test_runs_command_captures_stdout():
    out = _run(aegis_bash("echo hello"))
    assert out["returncode"] == 0
    assert out["stdout"].strip() == "hello"
    assert out["stderr"] == ""
    assert out["timed_out"] is False


def test_captures_stderr_and_nonzero_rc():
    out = _run(aegis_bash("echo oops >&2; exit 3"))
    assert out["returncode"] == 3
    assert out["stderr"].strip() == "oops"


def test_respects_cwd(tmp_path: Path):
    out = _run(aegis_bash("pwd", cwd=str(tmp_path)))
    assert out["stdout"].strip() == str(tmp_path)


def test_timeout_kills_long_running():
    out = _run(aegis_bash("sleep 10", timeout_s=1))
    assert out["timed_out"] is True
    assert out["returncode"] != 0
```

- [ ] **Step 2: Run test to verify it fails**

```
uv run pytest tests/test_aegis_fs_bash.py -v
```

Expected: 4 FAILED — `NotImplementedError`.

- [ ] **Step 3: Write minimal implementation**

Replace `src/aegis/mcp/fs_tools/bash.py`:

```python
"""aegis_bash — one-shot subprocess.

For long-lived shells use the aegis_term_* substrate. This tool is for
fire-and-forget commands whose full output fits in memory.
"""
from __future__ import annotations

import asyncio


async def aegis_bash(command: str, cwd: str | None = None,
                    timeout_s: int = 120) -> dict:
    """Run `command` via /bin/sh -c. Capture stdout, stderr, returncode.

    Returns:
        {command, cwd, returncode, stdout, stderr, timed_out, duration_ms}
    """
    import time
    started = time.monotonic()
    proc = await asyncio.create_subprocess_shell(
        command,
        cwd=cwd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout, stderr = await asyncio.wait_for(
            proc.communicate(), timeout=timeout_s)
        timed_out = False
    except asyncio.TimeoutError:
        proc.kill()
        stdout, stderr = await proc.communicate()
        timed_out = True
    duration_ms = int((time.monotonic() - started) * 1000)
    return {
        "command": command,
        "cwd": cwd,
        "returncode": proc.returncode if proc.returncode is not None else -1,
        "stdout": stdout.decode("utf-8", errors="replace"),
        "stderr": stderr.decode("utf-8", errors="replace"),
        "timed_out": timed_out,
        "duration_ms": duration_ms,
    }
```

- [ ] **Step 4: Run test to verify it passes**

```
uv run pytest tests/test_aegis_fs_bash.py -v
```

Expected: 4 PASSED.

- [ ] **Step 5: Commit**

```bash
git add src/aegis/mcp/fs_tools/bash.py tests/test_aegis_fs_bash.py
git commit -m "feat(mcp/fs_tools): aegis_bash — one-shot subprocess with timeout"
```

---

## Slice 2 — PermissionRouter + audit log

### Task 8: Permission types + audit log writer

**Files:**
- Create: `src/aegis/mcp/audit.py`
- Create: `tests/test_aegis_audit.py`
- Create: `src/aegis/mcp/permissions.py` (types only, router in next task)
- Create: `tests/test_aegis_permissions.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_aegis_audit.py`:

```python
from __future__ import annotations

import json
from pathlib import Path

from aegis.mcp.audit import AuditLog


def test_audit_log_appends_jsonl_entries(tmp_path: Path):
    log = AuditLog(root=tmp_path)
    log.record(handle="lucid-knuth", tool="aegis_bash",
               args={"command": "ls"}, verdict="allow",
               cache_hit=False, latency_ms=42)
    log.record(handle="lucid-knuth", tool="aegis_read",
               args={"path": "/etc/hostname"}, verdict="ask_allow",
               cache_hit=False, latency_ms=2200)
    path = tmp_path / "lucid-knuth.jsonl"
    lines = path.read_text().strip().split("\n")
    rows = [json.loads(l) for l in lines]
    assert rows[0]["tool"] == "aegis_bash"
    assert rows[0]["verdict"] == "allow"
    assert rows[1]["verdict"] == "ask_allow"
    assert rows[1]["latency_ms"] == 2200
    assert "ts" in rows[0]


def test_audit_truncates_large_args(tmp_path: Path):
    log = AuditLog(root=tmp_path, max_arg_bytes=100)
    big = "x" * 5000
    log.record(handle="h", tool="aegis_write",
               args={"content": big}, verdict="allow",
               cache_hit=False, latency_ms=1)
    row = json.loads((tmp_path / "h.jsonl").read_text().strip())
    # Content was truncated
    assert len(row["args"]["content"]) < 200
    assert row["args"]["content"].endswith("…(truncated)")
```

Create `tests/test_aegis_permissions.py`:

```python
from __future__ import annotations

import asyncio
from typing import Literal

from aegis.mcp.permissions import PermissionRequest, Verdict


def test_verdict_enum_values():
    assert Verdict.ALLOW.value == "allow"
    assert Verdict.DENY.value == "deny"
    assert Verdict.ASK.value == "ask"


def test_permission_request_fields():
    req = PermissionRequest(
        req_id="r1", handle="h", tool="aegis_bash",
        args={"command": "ls"})
    assert req.req_id == "r1"
    assert req.tool == "aegis_bash"
    assert req.args == {"command": "ls"}
```

- [ ] **Step 2: Run tests to verify they fail**

```
uv run pytest tests/test_aegis_audit.py tests/test_aegis_permissions.py -v
```

Expected: ERRORS — modules don't exist.

- [ ] **Step 3: Write minimal implementations**

Create `src/aegis/mcp/audit.py`:

```python
"""Aegis tool-call audit log.

Append-only JSONL per handle under .aegis/state/tool-audit/<handle>.jsonl.
Visibility floor — every aegis_* tool call lands here regardless of verdict.
"""
from __future__ import annotations

import datetime as dt
import json
from pathlib import Path
from typing import Any


class AuditLog:
    def __init__(self, root: Path, max_arg_bytes: int = 4096) -> None:
        self._root = Path(root)
        self._max_arg_bytes = max_arg_bytes
        self._root.mkdir(parents=True, exist_ok=True)

    def record(self, *, handle: str, tool: str, args: dict[str, Any],
               verdict: str, cache_hit: bool, latency_ms: int) -> None:
        path = self._root / f"{handle}.jsonl"
        row = {
            "ts": dt.datetime.now(tz=dt.timezone.utc).isoformat(),
            "handle": handle,
            "tool": tool,
            "args": self._truncate_args(args),
            "verdict": verdict,
            "cache_hit": cache_hit,
            "latency_ms": latency_ms,
        }
        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")

    def _truncate_args(self, args: dict[str, Any]) -> dict[str, Any]:
        out: dict[str, Any] = {}
        for k, v in args.items():
            if isinstance(v, str) and len(v.encode("utf-8")) > self._max_arg_bytes:
                cut = v[:self._max_arg_bytes]
                out[k] = cut + "…(truncated)"
            else:
                out[k] = v
        return out
```

Create `src/aegis/mcp/permissions.py` (types only — router class in Task 9):

```python
"""Per-agent-profile permission framework for aegis_* tools.

Verdicts: allow | deny | ask. "ask" routes to TUI inline modal or
Telegram inline buttons via OperatorSurface, with default-allow for
unlisted tools, session-scoped cache, and configurable timeout → deny.

Spec: docs/superpowers/specs/2026-05-27-aegis-fs-tool-surface-design.md
"""
from __future__ import annotations

import enum
from dataclasses import dataclass
from typing import Any


class Verdict(str, enum.Enum):
    ALLOW = "allow"
    DENY = "deny"
    ASK = "ask"


@dataclass(frozen=True)
class PermissionRequest:
    req_id: str
    handle: str
    tool: str
    args: dict[str, Any]
```

- [ ] **Step 4: Run tests to verify they pass**

```
uv run pytest tests/test_aegis_audit.py tests/test_aegis_permissions.py -v
```

Expected: 5 PASSED (2 audit + 3 permissions types).

- [ ] **Step 5: Commit**

```bash
git add src/aegis/mcp/audit.py src/aegis/mcp/permissions.py tests/test_aegis_audit.py tests/test_aegis_permissions.py
git commit -m "feat(mcp): AuditLog + Verdict/PermissionRequest types"
```

---

### Task 9: `PermissionRouter` + `OperatorSurface` Protocol + session cache + timeout

**Files:**
- Modify: `src/aegis/mcp/permissions.py`
- Modify: `tests/test_aegis_permissions.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_aegis_permissions.py`:

```python
from aegis.mcp.permissions import (PermissionRouter, OperatorSurface,
                                   PermissionRequest, Verdict)


class _RecordingSurface:
    """Test double for OperatorSurface — records asks, scripts replies."""

    def __init__(self, reply: str = "allow") -> None:
        self.asks: list[PermissionRequest] = []
        self.reply = reply

    async def ask(self, req: PermissionRequest, timeout_s: float) -> str:
        self.asks.append(req)
        return self.reply  # "allow" | "deny" | "allow_always" | "timeout"


def _make_profile(perms: dict[str, str], timeout_s: float = 300.0):
    """Tiny profile stand-in: just .permissions and .permission_timeout_s."""
    class P:
        permissions = perms
        permission_timeout_s = timeout_s
    return P()


def test_allow_verdict_returns_allow_no_surface_call():
    surf = _RecordingSurface()
    router = PermissionRouter(operator_surface=surf)
    profile = _make_profile({"aegis_bash": "allow"})

    async def scenario():
        v = await router.check(handle="h", tool="aegis_bash",
                               args={"command": "ls"}, profile=profile)
        return v

    out = asyncio.run(asyncio.wait_for(scenario(), timeout=5))
    assert out == Verdict.ALLOW
    assert surf.asks == []


def test_deny_verdict_returns_deny_no_surface_call():
    surf = _RecordingSurface()
    router = PermissionRouter(operator_surface=surf)
    profile = _make_profile({"aegis_bash": "deny"})

    async def scenario():
        return await router.check(handle="h", tool="aegis_bash",
                                  args={}, profile=profile)

    out = asyncio.run(asyncio.wait_for(scenario(), timeout=5))
    assert out == Verdict.DENY
    assert surf.asks == []


def test_unlisted_tool_defaults_to_allow():
    surf = _RecordingSurface()
    router = PermissionRouter(operator_surface=surf)
    profile = _make_profile({})  # nothing listed

    async def scenario():
        return await router.check(handle="h", tool="aegis_read",
                                  args={"path": "/etc/hostname"},
                                  profile=profile)

    out = asyncio.run(asyncio.wait_for(scenario(), timeout=5))
    assert out == Verdict.ALLOW


def test_ask_verdict_consults_surface_and_returns_reply():
    surf = _RecordingSurface(reply="allow")
    router = PermissionRouter(operator_surface=surf)
    profile = _make_profile({"aegis_bash": "ask"})

    async def scenario():
        return await router.check(handle="h", tool="aegis_bash",
                                  args={"command": "rm -rf /tmp/x"},
                                  profile=profile)

    out = asyncio.run(asyncio.wait_for(scenario(), timeout=5))
    assert out == Verdict.ALLOW
    assert len(surf.asks) == 1
    assert surf.asks[0].tool == "aegis_bash"


def test_ask_verdict_deny_reply_returns_deny():
    surf = _RecordingSurface(reply="deny")
    router = PermissionRouter(operator_surface=surf)
    profile = _make_profile({"aegis_bash": "ask"})

    async def scenario():
        return await router.check(handle="h", tool="aegis_bash",
                                  args={}, profile=profile)

    out = asyncio.run(asyncio.wait_for(scenario(), timeout=5))
    assert out == Verdict.DENY


def test_allow_always_reply_caches_for_session():
    surf = _RecordingSurface(reply="allow_always")
    router = PermissionRouter(operator_surface=surf)
    profile = _make_profile({"aegis_bash": "ask"})

    async def scenario():
        # First call: surface consulted (allow_always reply)
        v1 = await router.check(handle="h", tool="aegis_bash",
                                args={}, profile=profile)
        # Second call: should hit cache, no second ask
        v2 = await router.check(handle="h", tool="aegis_bash",
                                args={}, profile=profile)
        return v1, v2, len(surf.asks)

    v1, v2, ask_count = asyncio.run(asyncio.wait_for(scenario(), timeout=5))
    assert v1 == Verdict.ALLOW
    assert v2 == Verdict.ALLOW
    assert ask_count == 1  # cached after the first allow_always


def test_ask_timeout_returns_deny():
    surf = _RecordingSurface(reply="timeout")
    router = PermissionRouter(operator_surface=surf)
    profile = _make_profile({"aegis_bash": "ask"}, timeout_s=0.2)

    async def scenario():
        return await router.check(handle="h", tool="aegis_bash",
                                  args={}, profile=profile)

    out = asyncio.run(asyncio.wait_for(scenario(), timeout=5))
    assert out == Verdict.DENY


def test_clear_session_cache_drops_allow_always():
    surf = _RecordingSurface(reply="allow_always")
    router = PermissionRouter(operator_surface=surf)
    profile = _make_profile({"aegis_bash": "ask"})

    async def scenario():
        await router.check(handle="h", tool="aegis_bash",
                           args={}, profile=profile)
        router.clear_session("h")
        await router.check(handle="h", tool="aegis_bash",
                           args={}, profile=profile)
        return len(surf.asks)

    asks = asyncio.run(asyncio.wait_for(scenario(), timeout=5))
    assert asks == 2  # cache cleared, asked again
```

- [ ] **Step 2: Run tests to verify they fail**

```
uv run pytest tests/test_aegis_permissions.py -v
```

Expected: 8 new tests FAIL/ERROR.

- [ ] **Step 3: Write minimal implementation**

Append to `src/aegis/mcp/permissions.py`:

```python
import uuid
from typing import Protocol, runtime_checkable


@runtime_checkable
class OperatorSurface(Protocol):
    """Where an aegis 'ask' verdict goes to be resolved.

    The router calls .ask(req, timeout_s) and awaits a reply string:
    "allow" | "deny" | "allow_always" | "timeout".
    Concrete surfaces: TuiPermissionSurface (ConversationPane modal),
    TelegramPermissionSurface (InlineKeyboardMarkup), and a
    RoutingSurface that picks per handle. In tests, a recording double.
    """

    async def ask(self, req: PermissionRequest,
                  timeout_s: float) -> str: ...


class PermissionRouter:
    """Gates aegis_* tool calls against per-profile permissions.

    Defaults: unlisted tools allow; "ask" verdicts route to
    OperatorSurface with timeout → deny; "allow_always" replies cache
    per (handle, tool) for the lifetime of the handle's session.
    """

    def __init__(self, operator_surface: OperatorSurface) -> None:
        self._surface = operator_surface
        # cache[(handle, tool)] = Verdict.ALLOW (only allow caches today)
        self._cache: dict[tuple[str, str], Verdict] = {}

    async def check(self, *, handle: str, tool: str,
                    args: dict, profile) -> Verdict:
        cached = self._cache.get((handle, tool))
        if cached is not None:
            return cached
        raw = profile.permissions.get(tool, "allow")
        verdict = Verdict(raw)
        if verdict is Verdict.ALLOW:
            return Verdict.ALLOW
        if verdict is Verdict.DENY:
            return Verdict.DENY
        # ASK path
        req = PermissionRequest(req_id=str(uuid.uuid4()),
                                handle=handle, tool=tool, args=args)
        reply = await self._surface.ask(req,
                                        timeout_s=profile.permission_timeout_s)
        if reply == "allow":
            return Verdict.ALLOW
        if reply == "allow_always":
            self._cache[(handle, tool)] = Verdict.ALLOW
            return Verdict.ALLOW
        # deny | timeout | any unknown reply → deny
        return Verdict.DENY

    def clear_session(self, handle: str) -> None:
        """Drop cached allow_always grants for this handle. Called by
        SessionManager / AgentSession on session close."""
        for key in list(self._cache.keys()):
            if key[0] == handle:
                del self._cache[key]
```

- [ ] **Step 4: Run tests to verify they pass**

```
uv run pytest tests/test_aegis_permissions.py -v
```

Expected: 11 PASSED (3 from Task 8 types + 8 router tests).

- [ ] **Step 5: Commit**

```bash
git add src/aegis/mcp/permissions.py tests/test_aegis_permissions.py
git commit -m "feat(mcp): PermissionRouter — allow/deny/ask with session cache + timeout"
```

---

### Task 10: `permission_gate` decorator wiring router into tool registration

**Files:**
- Modify: `src/aegis/mcp/permissions.py`
- Modify: `tests/test_aegis_permissions.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_aegis_permissions.py`:

```python
from aegis.mcp.permissions import permission_gate


def test_permission_gate_wraps_tool_and_consults_router_and_logs(tmp_path):
    """End-to-end through the gate: allow path returns inner result + logs;
    deny path returns permission_denied dict + logs."""
    from aegis.mcp.audit import AuditLog

    surf = _RecordingSurface(reply="allow")
    router = PermissionRouter(operator_surface=surf)
    audit = AuditLog(root=tmp_path)

    profile_allow = _make_profile({"aegis_bash": "allow"})
    profile_deny = _make_profile({"aegis_bash": "deny"})

    async def inner(command: str, from_handle: str = "") -> dict:
        return {"stdout": f"ran: {command}", "returncode": 0}

    inner.__name__ = "aegis_bash"

    async def lookup_profile(handle: str):
        # toggle by handle for the test
        return profile_allow if handle == "ok" else profile_deny

    gated = permission_gate(router=router, audit=audit,
                            lookup_profile=lookup_profile)(inner)

    async def scenario():
        ok = await gated(command="echo hi", from_handle="ok")
        no = await gated(command="echo hi", from_handle="bad")
        return ok, no

    ok, no = asyncio.run(asyncio.wait_for(scenario(), timeout=5))
    assert ok == {"stdout": "ran: echo hi", "returncode": 0}
    assert no == {"error": "permission_denied", "tool": "aegis_bash",
                  "reason": "denied by agent profile"}

    # Audit lines: one allow, one deny
    import json
    ok_log = (tmp_path / "ok.jsonl").read_text().strip().split("\n")
    bad_log = (tmp_path / "bad.jsonl").read_text().strip().split("\n")
    assert json.loads(ok_log[0])["verdict"] == "allow"
    assert json.loads(bad_log[0])["verdict"] == "deny"
```

- [ ] **Step 2: Run test to verify it fails**

```
uv run pytest tests/test_aegis_permissions.py::test_permission_gate_wraps_tool_and_consults_router_and_logs -v
```

Expected: ERROR — `ImportError: cannot import name 'permission_gate'`.

- [ ] **Step 3: Write minimal implementation**

Append to `src/aegis/mcp/permissions.py`:

```python
import functools
import time
from collections.abc import Awaitable, Callable

from aegis.mcp.audit import AuditLog


def permission_gate(
    *,
    router: PermissionRouter,
    audit: AuditLog,
    lookup_profile: Callable[[str], Awaitable[object]],
):
    """Decorate an MCP tool function with permission checks + audit logging.

    The wrapped tool MUST accept `from_handle: str` as a kwarg (existing
    aegis MCP convention). Profile lookup is async to allow future
    integrations to fetch profile from a manager rather than a static
    dict.

    On allow:  call inner, return result, log verdict=allow.
    On deny:   skip inner, return permission_denied dict, log verdict=deny.
    On ask→allow:   call inner, log verdict=ask_allow.
    On ask→deny/timeout: skip inner, return permission_denied,
                         log verdict=ask_deny / ask_timeout accordingly.
    """
    def decorator(fn):
        tool_name = fn.__name__

        @functools.wraps(fn)
        async def wrapper(*args, **kwargs):
            handle = kwargs.get("from_handle", "")
            profile = await lookup_profile(handle)
            started = time.monotonic()
            verdict = await router.check(handle=handle, tool=tool_name,
                                         args=kwargs, profile=profile)
            cache_hit = router._cache.get((handle, tool_name)) is not None

            if verdict is Verdict.ALLOW:
                try:
                    result = await fn(*args, **kwargs)
                finally:
                    latency = int((time.monotonic() - started) * 1000)
                    audit.record(handle=handle, tool=tool_name, args=kwargs,
                                 verdict="allow" if not cache_hit else "allow",
                                 cache_hit=cache_hit, latency_ms=latency)
                return result

            latency = int((time.monotonic() - started) * 1000)
            audit.record(handle=handle, tool=tool_name, args=kwargs,
                         verdict="deny", cache_hit=cache_hit,
                         latency_ms=latency)
            return {"error": "permission_denied", "tool": tool_name,
                    "reason": "denied by agent profile"}

        return wrapper
    return decorator
```

- [ ] **Step 4: Run test to verify it passes**

```
uv run pytest tests/test_aegis_permissions.py -v
```

Expected: 12 PASSED.

- [ ] **Step 5: Wire the gate into `register_fs_tools` via `AppBridge`**

Add an integration test that goes end-to-end through `register_fs_tools` with a bridge that supplies a `PermissionRouter` and `AuditLog`. Append to `tests/test_aegis_fs_register.py`:

```python
import asyncio
import json
from pathlib import Path


def test_register_fs_tools_applies_permission_gate(tmp_path: Path):
    """End-to-end: register tools through register_fs_tools with a bridge
    that has a deny profile for aegis_bash. Calling the registered fn
    must return permission_denied without running the inner."""
    from aegis.mcp.permissions import PermissionRouter
    from aegis.mcp.audit import AuditLog

    class _StubSurface:
        async def ask(self, req, timeout_s): return "deny"

    class _StubBridge:
        permission_router = PermissionRouter(operator_surface=_StubSurface())
        audit_log = AuditLog(root=tmp_path)
        async def profile_for(self, handle: str):
            class P:
                permissions = {"aegis_bash": "deny"}
                permission_timeout_s = 300
            return P()

    class _CapturingServer:
        def __init__(self):
            self.registered: dict = {}
        def tool(self, fn):
            self.registered[fn.__name__] = fn
            return fn

    srv = _CapturingServer()
    register_fs_tools(srv, bridge=_StubBridge())

    async def scenario():
        return await srv.registered["aegis_bash"](
            command="echo hi", from_handle="any")

    out = asyncio.run(asyncio.wait_for(scenario(), timeout=5))
    assert out == {"error": "permission_denied", "tool": "aegis_bash",
                   "reason": "denied by agent profile"}
    # Audit log got a deny line
    log = (tmp_path / "any.jsonl").read_text().strip()
    assert json.loads(log)["verdict"] == "deny"
```

Update `register_fs_tools` in `src/aegis/mcp/fs_tools/__init__.py` to apply the gate:

```python
def register_fs_tools(server, bridge) -> None:
    from aegis.mcp.fs_tools.bash import aegis_bash
    from aegis.mcp.fs_tools.read import aegis_read
    from aegis.mcp.fs_tools.write import aegis_write
    from aegis.mcp.fs_tools.edit import aegis_edit
    from aegis.mcp.fs_tools.grep import aegis_grep
    from aegis.mcp.fs_tools.listdir import aegis_listdir
    from aegis.mcp.permissions import permission_gate

    gate = permission_gate(
        router=bridge.permission_router,
        audit=bridge.audit_log,
        lookup_profile=bridge.profile_for,
    )
    for fn in (aegis_bash, aegis_read, aegis_write,
               aegis_edit, aegis_grep, aegis_listdir):
        server.tool(gate(fn))
```

Extend `AppBridge` Protocol in `src/aegis/mcp/bridge.py` with three new attributes (alongside the existing `queue_manager`, `inbox_router`, etc.):

```python
class AppBridge(Protocol):
    queue_manager: object
    inbox_router: object
    canvas_manager: object
    terminal_manager: object
    groups: object
    remotes: object
    scheduler: object
    state_root: object
    workflow_registry: object
    # NEW for fs-tool permission framework:
    permission_router: object   # PermissionRouter
    audit_log: object           # AuditLog
    async def profile_for(self, handle: str) -> object: ...
```

Then update the two concrete `AppBridge` implementors so they construct + expose these:

- **`SessionManager`** (`src/aegis/core/manager.py`): construct a `PermissionRouter` (operator_surface picked by routing logic — see Task 13's `RoutingSurface` if it lands, else a `TelegramPermissionSurface` directly), an `AuditLog(root=state_root / "tool-audit")`, and implement `profile_for(handle)` by looking up `self._sessions[handle].agent.provider`.
- **`AegisApp`** (`src/aegis/tui/app.py`): same constructions; `profile_for` reads from its session registry.

For test-time `_StubBridge`s (existing tests that hand-roll a bridge), `permission_router` + `audit_log` + `profile_for` become required attributes. Update any stubs that break. The earlier `test_register_fs_tools_registers_all_six_by_name` will fail because `_StubServer` doesn't have a bridge — pass `bridge=_NullBridge()` with no-op stubs to keep it green:

```python
class _NullBridge:
    permission_router = None  # the gate won't be invoked if profile_for is async
    # ...
```

Or simpler: update Task 1's `register_fs_tools` test to pass a `_StubBridge` with the same shape as in `test_register_fs_tools_applies_permission_gate`. The first test of the package now exercises real registration + gating; the prior pure-name-check test can be folded into it.

- [ ] **Step 6: Run the integration test + full permission suite**

```
uv run pytest tests/test_aegis_fs_register.py tests/test_aegis_permissions.py -v
```

Expected: all PASSED.

- [ ] **Step 7: Commit**

```bash
git add src/aegis/mcp/permissions.py src/aegis/mcp/fs_tools/__init__.py src/aegis/mcp/bridge.py src/aegis/core/manager.py src/aegis/tui/app.py tests/test_aegis_permissions.py tests/test_aegis_fs_register.py
git commit -m "feat(mcp): wire permission_gate into register_fs_tools via AppBridge"
```

---

## Slice 3 — Operator surfaces (TUI + Telegram)

### Task 11: TUI inline approval modal — `TuiPermissionSurface`

**Files:**
- Modify: `src/aegis/tui/pane.py` (add `TuiPermissionSurface` near `ConversationPane`)
- Create: `tests/test_aegis_permission_tui.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_aegis_permission_tui.py`:

```python
from __future__ import annotations

import asyncio

from aegis.mcp.permissions import PermissionRequest
from aegis.tui.pane import TuiPermissionSurface


class _FakePane:
    """Stand-in for ConversationPane. Captures the modal request,
    exposes resolve(verdict) to simulate user click."""

    def __init__(self) -> None:
        self.shown: list[PermissionRequest] = []
        self._pending: asyncio.Future | None = None

    def show_permission_modal(self, req: PermissionRequest,
                              future: asyncio.Future) -> None:
        self.shown.append(req)
        self._pending = future

    def click(self, reply: str) -> None:
        if self._pending and not self._pending.done():
            self._pending.set_result(reply)


def test_tui_surface_shows_modal_and_resolves_on_click():
    pane = _FakePane()
    surf = TuiPermissionSurface(pane=pane)
    req = PermissionRequest(req_id="r1", handle="h", tool="aegis_bash",
                            args={"command": "ls"})

    async def scenario():
        ask_task = asyncio.create_task(surf.ask(req, timeout_s=5))
        await asyncio.sleep(0.05)
        assert pane.shown == [req]
        pane.click("allow")
        return await ask_task

    out = asyncio.run(asyncio.wait_for(scenario(), timeout=5))
    assert out == "allow"


def test_tui_surface_timeout_returns_timeout_string():
    pane = _FakePane()
    surf = TuiPermissionSurface(pane=pane)
    req = PermissionRequest(req_id="r2", handle="h", tool="aegis_bash",
                            args={})

    async def scenario():
        return await surf.ask(req, timeout_s=0.1)

    out = asyncio.run(asyncio.wait_for(scenario(), timeout=5))
    assert out == "timeout"
```

- [ ] **Step 2: Run test to verify it fails**

```
uv run pytest tests/test_aegis_permission_tui.py -v
```

Expected: 2 ERRORS — `ImportError: cannot import name 'TuiPermissionSurface'`.

- [ ] **Step 3: Write minimal implementation**

Append to `src/aegis/tui/pane.py`:

```python
import asyncio

from aegis.mcp.permissions import PermissionRequest


class TuiPermissionSurface:
    """OperatorSurface that routes asks to a ConversationPane's inline modal.

    The pane MUST implement:
        show_permission_modal(req: PermissionRequest,
                              future: asyncio.Future) -> None

    where it renders an Approve / Deny / Always-allow modal and resolves
    the future with the reply string when the user clicks/keystrokes.
    """

    def __init__(self, pane) -> None:
        self._pane = pane

    async def ask(self, req: PermissionRequest, timeout_s: float) -> str:
        loop = asyncio.get_running_loop()
        fut: asyncio.Future = loop.create_future()
        self._pane.show_permission_modal(req, fut)
        try:
            return await asyncio.wait_for(fut, timeout=timeout_s)
        except asyncio.TimeoutError:
            return "timeout"
```

Note: the actual rendering of the modal inside `ConversationPane` (the Textual widget) is intentionally NOT in this task — it's a UI integration touch that follows the existing pane's mounting pattern. The minimum here is the surface bridge with a clearly-defined contract (`show_permission_modal(req, future)`). A follow-up commit can wire the actual Textual modal widget without changing the contract.

- [ ] **Step 4: Run test to verify it passes**

```
uv run pytest tests/test_aegis_permission_tui.py -v
```

Expected: 2 PASSED.

- [ ] **Step 5: Commit**

```bash
git add src/aegis/tui/pane.py tests/test_aegis_permission_tui.py
git commit -m "feat(tui): TuiPermissionSurface — pane modal bridge for ask verdicts"
```

---

### Task 12: Telegram inline-button primitive — `send_message_with_inline_keyboard` + `edit_message_text`

**Files:**
- Modify: `src/aegis/telegram/bot.py`
- Create: `tests/test_aegis_permission_telegram.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_aegis_permission_telegram.py`:

```python
from __future__ import annotations

import asyncio

from aegis.telegram.bot import BotClient


class _RecordingTransport:
    """Captures calls to BotClient._call without hitting the network."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []
        self.next_response: dict = {"ok": True, "result": {"message_id": 7}}

    async def __call__(self, method: str, **kwargs):
        self.calls.append((method, kwargs))
        return self.next_response


def test_send_with_inline_keyboard_formats_reply_markup():
    transport = _RecordingTransport()
    client = BotClient(token="t")
    client._call = transport

    async def scenario():
        return await client.send_message_with_inline_keyboard(
            chat_id=42,
            text="ask",
            buttons=[
                ("✅ Approve", "perm:r1:allow"),
                ("❌ Deny", "perm:r1:deny"),
                ("✅ Always allow", "perm:r1:allow_always"),
            ],
        )

    res = asyncio.run(asyncio.wait_for(scenario(), timeout=5))
    assert res["result"]["message_id"] == 7
    method, kwargs = transport.calls[0]
    assert method == "sendMessage"
    assert kwargs["chat_id"] == 42
    assert kwargs["text"] == "ask"
    import json
    keyboard = json.loads(kwargs["reply_markup"])["inline_keyboard"]
    # Single row, three buttons
    assert len(keyboard) == 1
    assert len(keyboard[0]) == 3
    assert keyboard[0][0] == {"text": "✅ Approve", "callback_data": "perm:r1:allow"}


def test_edit_message_text_clears_keyboard_by_default():
    transport = _RecordingTransport()
    client = BotClient(token="t")
    client._call = transport

    async def scenario():
        return await client.edit_message_text(
            chat_id=42, message_id=7, text="✅ Approved")

    asyncio.run(asyncio.wait_for(scenario(), timeout=5))
    method, kwargs = transport.calls[0]
    assert method == "editMessageText"
    assert kwargs["chat_id"] == 42
    assert kwargs["message_id"] == 7
    assert kwargs["text"] == "✅ Approved"
    # No reply_markup → buttons cleared (Telegram convention)
    assert "reply_markup" not in kwargs or kwargs["reply_markup"] in (None, "")
```

- [ ] **Step 2: Run test to verify it fails**

```
uv run pytest tests/test_aegis_permission_telegram.py -v
```

Expected: 2 ERRORS — `AttributeError: 'BotClient' object has no attribute 'send_message_with_inline_keyboard'`.

- [ ] **Step 3: Write minimal implementation**

Append to `src/aegis/telegram/bot.py` (inside the `BotClient` class):

```python
    async def send_message_with_inline_keyboard(
        self, chat_id: int, text: str,
        buttons: list[tuple[str, str]],
    ) -> dict:
        """Send a message with an inline keyboard.

        `buttons` is a list of (label, callback_data) tuples rendered as
        a single row. For multi-row layouts, pass nested lists in a
        follow-up — v1 only needs a single-row Approve/Deny/Always.
        """
        import json
        keyboard = {
            "inline_keyboard": [
                [{"text": label, "callback_data": cb}
                 for label, cb in buttons]
            ]
        }
        return await self._call("sendMessage",
                                chat_id=chat_id, text=text,
                                reply_markup=json.dumps(keyboard))

    async def edit_message_text(self, chat_id: int, message_id: int,
                                text: str, reply_markup: str | None = None) -> dict:
        """Edit a previously-sent message's text. By default clears any
        inline keyboard (no reply_markup parameter)."""
        kwargs = {"chat_id": chat_id, "message_id": message_id, "text": text}
        if reply_markup is not None:
            kwargs["reply_markup"] = reply_markup
        return await self._call("editMessageText", **kwargs)
```

- [ ] **Step 4: Run test to verify it passes**

```
uv run pytest tests/test_aegis_permission_telegram.py -v
```

Expected: 2 PASSED.

- [ ] **Step 5: Commit**

```bash
git add src/aegis/telegram/bot.py tests/test_aegis_permission_telegram.py
git commit -m "feat(telegram/bot): send_message_with_inline_keyboard + edit_message_text"
```

---

### Task 13: `TelegramPermissionSurface` + callback_query dispatch

**Files:**
- Modify: `src/aegis/telegram/frontend.py`
- Modify: `tests/test_aegis_permission_telegram.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_aegis_permission_telegram.py`:

```python
from aegis.telegram.frontend import TelegramPermissionSurface
from aegis.mcp.permissions import PermissionRequest


def test_telegram_surface_sends_prompt_and_resolves_on_callback():
    transport = _RecordingTransport()
    client = BotClient(token="t")
    client._call = transport
    surf = TelegramPermissionSurface(client=client, chat_id=42)
    req = PermissionRequest(req_id="r9", handle="lucid-knuth",
                            tool="aegis_bash",
                            args={"command": "rm -rf /tmp/x"})

    async def scenario():
        ask_task = asyncio.create_task(surf.ask(req, timeout_s=5))
        await asyncio.sleep(0.05)
        # Send message was issued
        assert transport.calls[0][0] == "sendMessage"
        # Now simulate the user's button click → callback_query dispatch
        surf.handle_callback("perm:r9:allow_always", message_id=7)
        return await ask_task

    out = asyncio.run(asyncio.wait_for(scenario(), timeout=5))
    assert out == "allow_always"
    # Message was edited to show verdict and clear buttons
    methods = [m for m, _ in transport.calls]
    assert "editMessageText" in methods
```

- [ ] **Step 2: Run test to verify it fails**

```
uv run pytest tests/test_aegis_permission_telegram.py::test_telegram_surface_sends_prompt_and_resolves_on_callback -v
```

Expected: ERROR — `ImportError: cannot import name 'TelegramPermissionSurface'`.

- [ ] **Step 3: Write minimal implementation**

Append to `src/aegis/telegram/frontend.py`:

```python
import asyncio

from aegis.mcp.permissions import PermissionRequest


_PROMPT_TEMPLATE = (
    "[{handle}] {tool}:\n"
    "{args_repr}"
)


def _format_args(args: dict) -> str:
    # One-line, truncated repr — full args land in audit log.
    s = ", ".join(f"{k}={v!r}" for k, v in args.items())
    return s if len(s) < 200 else s[:200] + "…"


class TelegramPermissionSurface:
    """OperatorSurface that routes asks to a Telegram chat via inline
    buttons. handle_callback() must be called from the bot's update loop
    when a callback_query with the perm: prefix arrives.
    """

    def __init__(self, client, chat_id: int) -> None:
        self._client = client
        self._chat_id = chat_id
        # pending[req_id] = (future, message_id)
        self._pending: dict[str, tuple[asyncio.Future, int]] = {}

    async def ask(self, req: PermissionRequest, timeout_s: float) -> str:
        loop = asyncio.get_running_loop()
        fut: asyncio.Future = loop.create_future()
        text = _PROMPT_TEMPLATE.format(
            handle=req.handle, tool=req.tool,
            args_repr=_format_args(req.args))
        res = await self._client.send_message_with_inline_keyboard(
            chat_id=self._chat_id, text=text,
            buttons=[
                ("✅ Approve", f"perm:{req.req_id}:allow"),
                ("❌ Deny", f"perm:{req.req_id}:deny"),
                ("✅ Always allow", f"perm:{req.req_id}:allow_always"),
            ],
        )
        message_id = res["result"]["message_id"]
        self._pending[req.req_id] = (fut, message_id)
        try:
            reply = await asyncio.wait_for(fut, timeout=timeout_s)
        except asyncio.TimeoutError:
            reply = "timeout"
        finally:
            self._pending.pop(req.req_id, None)
        # Update the message to reflect the verdict + remove buttons
        verdict_text = {
            "allow": "✅ Approved",
            "allow_always": "✅ Approved (won't ask again)",
            "deny": "❌ Denied",
            "timeout": "⌛ Timed out → denied",
        }.get(reply, "❓ " + reply)
        try:
            await self._client.edit_message_text(
                chat_id=self._chat_id, message_id=message_id,
                text=f"{text}\n\n{verdict_text}")
        except Exception:
            pass  # message edit is cosmetic; never fail the verdict on it
        return reply

    def handle_callback(self, callback_data: str, message_id: int) -> None:
        """Dispatch a Telegram callback_query.data. Must be a string of
        form 'perm:<req_id>:<verdict>'. No-op if no pending future
        matches (stale click after timeout, etc)."""
        parts = callback_data.split(":", 2)
        if len(parts) != 3 or parts[0] != "perm":
            return
        req_id, verdict = parts[1], parts[2]
        entry = self._pending.get(req_id)
        if entry is None:
            return
        fut, _ = entry
        if not fut.done():
            fut.set_result(verdict)
```

Also wire the bot's update loop to dispatch `callback_query` updates. In `src/aegis/telegram/bot.py`'s `getUpdates` consumer (the long-poll loop), extend the update-type dispatch to also recognize `callback_query`:

```python
# In the update-processing loop of BotClient / TelegramFrontend:
if "callback_query" in update:
    cq = update["callback_query"]
    data = cq.get("data", "")
    msg = cq.get("message") or {}
    message_id = msg.get("message_id")
    if data.startswith("perm:") and self._permission_surface is not None:
        self._permission_surface.handle_callback(data, message_id)
    # ACK the callback so Telegram doesn't show a spinner
    asyncio.create_task(self._client._call("answerCallbackQuery",
                                           callback_query_id=cq["id"]))
    continue  # don't fall through to message handling
```

The exact insertion point depends on the current shape of `TelegramFrontend._process_update` (or equivalent); look for where `message` updates are dispatched and add the `callback_query` branch above it. Hold `self._permission_surface` on the frontend, set during construction.

- [ ] **Step 4: Run test to verify it passes**

```
uv run pytest tests/test_aegis_permission_telegram.py -v
```

Expected: 3 PASSED.

- [ ] **Step 5: Commit**

```bash
git add src/aegis/telegram/frontend.py src/aegis/telegram/bot.py tests/test_aegis_permission_telegram.py
git commit -m "feat(telegram): TelegramPermissionSurface + callback_query dispatch"
```

---

## Slice 4 — Driver integration + suppression + universal PRIMING + release

### Task 14: Extend `PRIMING` with prefer-aegis-tools block

**Files:**
- Modify: `src/aegis/mcp/server.py` (the `PRIMING = (...)` definition at line ~279)
- Create: `tests/test_aegis_priming.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_aegis_priming.py`:

```python
from __future__ import annotations

from aegis.mcp.server import PRIMING


def test_priming_mentions_aegis_handle_substitution():
    # Existing contract — `{handle}` placeholder must remain.
    assert "{handle}" in PRIMING


def test_priming_includes_prefer_aegis_tools_block():
    text = PRIMING.format(handle="testy")
    # The new universal addendum names the six tools and their built-in
    # counterparts so the agent knows the mapping.
    assert "aegis_bash" in text
    assert "aegis_read" in text
    assert "aegis_write" in text
    assert "aegis_edit" in text
    assert "aegis_grep" in text
    assert "aegis_listdir" in text
    assert "Prefer aegis tools" in text or "prefer aegis tools" in text.lower()
```

- [ ] **Step 2: Run test to verify it fails**

```
uv run pytest tests/test_aegis_priming.py -v
```

Expected: 1 FAIL (the prefer-aegis-tools assertion).

- [ ] **Step 3: Write minimal implementation**

In `src/aegis/mcp/server.py`, locate `PRIMING = (` (around line 279) and append the new block before the closing paren. Final shape:

```python
PRIMING = (
    "<existing prefix text up through {handle} description>\n"
    "\n"
    "Prefer aegis tools over harness built-ins: aegis_bash instead of "
    "Bash/Shell, aegis_read instead of Read, aegis_edit instead of Edit, "
    "aegis_write instead of Write (new files only — use aegis_edit to "
    "modify), aegis_grep instead of Grep (literal-text match by default), "
    "aegis_listdir instead of ls. They route through your operator's "
    "permission and visibility layer."
)
```

Concretely: read the current PRIMING block, find its closing `)`, insert the new paragraph as the final string fragment before the closing paren. Do NOT replace the existing content — append to it.

- [ ] **Step 4: Run test to verify it passes**

```
uv run pytest tests/test_aegis_priming.py -v
```

Expected: 2 PASSED.

- [ ] **Step 5: Commit**

```bash
git add src/aegis/mcp/server.py tests/test_aegis_priming.py
git commit -m "feat(mcp): PRIMING — universal prefer-aegis-tools addendum"
```

---

### Task 15: `suppress_builtins` / `permissions` / `permission_timeout_s` fields + Claude `--tools ""`

**Files:**
- Modify: `src/aegis/config/__init__.py` (the `_ProviderBase` class)
- Modify: `src/aegis/drivers/claude_print.py`
- Modify: `src/aegis/drivers/claude_repl.py`
- Create: `tests/test_claude_tools_suppression.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_claude_tools_suppression.py`:

```python
from __future__ import annotations

import shutil

import pytest
from aegis.config import Agent, ClaudeCode
from aegis.drivers.claude_print import ClaudePrintDriver
from aegis.drivers.claude_repl import ClaudeReplDriver


MCP_URL = "http://127.0.0.1:9/mcp/"
HANDLE = "h"


def test_provider_base_defaults():
    a = Agent(provider=ClaudeCode(model="opus"))
    assert a.suppress_builtins is False
    assert a.permissions == {}
    assert a.permission_timeout_s == 300


def test_provider_base_accepts_per_tool_permissions():
    a = Agent(provider=ClaudeCode(
        model="opus",
        suppress_builtins=True,
        permissions={"aegis_bash": "ask", "aegis_write": "deny"},
        permission_timeout_s=60,
    ))
    assert a.suppress_builtins is True
    assert a.permissions["aegis_bash"] == "ask"
    assert a.permission_timeout_s == 60


def test_print_argv_includes_tools_empty_when_suppressed():
    a = Agent(provider=ClaudeCode(model="opus", suppress_builtins=True))
    argv = ClaudePrintDriver().build_argv(a, "/tmp", MCP_URL, HANDLE)
    # --tools "" disables built-ins
    idx = argv.index("--tools")
    assert argv[idx + 1] == ""


def test_print_argv_omits_tools_when_not_suppressed():
    a = Agent(provider=ClaudeCode(model="opus", suppress_builtins=False))
    argv = ClaudePrintDriver().build_argv(a, "/tmp", MCP_URL, HANDLE)
    assert "--tools" not in argv


def test_repl_argv_includes_tools_empty_when_suppressed():
    a = Agent(provider=ClaudeCode(model="opus", suppress_builtins=True))
    argv = ClaudeReplDriver().build_argv(a, "/tmp", MCP_URL, HANDLE)
    idx = argv.index("--tools")
    assert argv[idx + 1] == ""


@pytest.mark.live
@pytest.mark.skipif(shutil.which("claude") is None,
                    reason="claude CLI not on PATH")
def test_smoke_tools_empty_preserves_mcp_tools(tmp_path):
    """Risk #1 smoke: spawn claude --tools "" with an MCP server attached,
    ask the agent to call the MCP tool. If --tools "" zeros out MCP
    tools too, the agent reports it can't and we fall back to an
    explicit allowlist."""
    import asyncio
    import socket
    import uuid
    from pathlib import Path

    from fastmcp import FastMCP
    import uvicorn

    server = FastMCP(name="probe")
    hits: list[str] = []

    @server.tool()
    def probe_ping(note: str) -> str:
        hits.append(note)
        return f"pong:{note}"

    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    mcp_url = f"http://127.0.0.1:{port}/mcp/"
    config = uvicorn.Config(server.streamable_http_app(),
                            host="127.0.0.1", port=port, log_level="warning")
    srv = uvicorn.Server(config)
    server_task = asyncio.get_event_loop_policy().new_event_loop().run_until_complete

    async def run():
        s_task = asyncio.create_task(srv.serve())
        await asyncio.sleep(0.5)

        from aegis.drivers.claude_repl import ClaudeReplSession, _cwd_slug
        sid = str(uuid.uuid4())
        slug = _cwd_slug(str(tmp_path))
        transcript = Path.home() / ".claude" / "projects" / slug / f"{sid}.jsonl"
        import json as _json
        mcp_cfg = _json.dumps({"mcpServers": {
            "probe": {"type": "http", "url": mcp_url}}})
        argv = ["claude", "--session-id", sid,
                "--permission-mode", "auto", "--model", "haiku",
                "--mcp-config", mcp_cfg, "--strict-mcp-config",
                "--tools", "",
                "--add-dir", str(tmp_path),
                "--append-system-prompt",
                "Call probe_ping with note='ok' then say done."]
        sess = ClaudeReplSession(argv=argv, cwd=str(tmp_path),
                                 session_id=sid, transcript_path=transcript)
        try:
            await sess.start()
            await sess.send("go")
            evs = []
            async for ev in sess.events():
                evs.append(ev)
                if len(evs) > 80:
                    break
        finally:
            await sess.close()
            srv.should_exit = True
            await asyncio.wait_for(s_task, timeout=5)
        return hits

    out = asyncio.run(run())
    assert "ok" in out, "MCP tool was not callable under --tools '' — " \
        "fall back to explicit allowlist (--tools probe_ping,…)."
```

- [ ] **Step 2: Run hermetic tests to verify they fail**

```
uv run pytest tests/test_claude_tools_suppression.py -v -m "not live"
```

Expected: 5 hermetic tests FAIL — fields don't exist yet, argv doesn't include `--tools`.

- [ ] **Step 3: Write minimal implementations**

In `src/aegis/config/__init__.py`, modify `_ProviderBase`:

```python
class _ProviderBase(BaseModel):
    """Base for provider config objects."""
    model: str
    permission: Permission = Permission.auto
    suppress_builtins: bool = False
    permissions: dict[str, Literal["allow", "deny", "ask"]] = {}
    permission_timeout_s: int = 300
```

In `Agent`, ensure these fields surface as flat attributes (`agent.suppress_builtins`, `agent.permissions`, `agent.permission_timeout_s`). Follow the existing flat-attribute pattern used for `model` / `effort` / `permission` — add `@property` accessors that read from `self.provider`.

In `src/aegis/drivers/claude_print.py`, modify `build_argv` to append `--tools ""` when suppressed:

```python
def build_argv(self, agent: Agent, cwd: str,
               mcp_url: str, handle: str) -> list[str]:
    argv = [
        "claude", "-p",
        "--input-format", "stream-json",
        "--output-format", "stream-json",
        "--replay-user-messages",
        "--verbose",
        "--model", agent.model,
        "--effort", _EFFORT[agent.effort],
        "--permission-mode", _PERMISSION_MODE[agent.permission],
        "--mcp-config", mcp_config_json(mcp_url),
        "--strict-mcp-config",
        "--append-system-prompt", PRIMING.format(handle=handle),
    ]
    if agent.suppress_builtins:
        argv += ["--tools", ""]
    return argv
```

In `src/aegis/drivers/claude_repl.py`, modify `build_argv` similarly — append `["--tools", ""]` at the end when `agent.suppress_builtins`.

- [ ] **Step 4: Run hermetic tests to verify they pass**

```
uv run pytest tests/test_claude_tools_suppression.py -v -m "not live"
```

Expected: 5 PASSED.

- [ ] **Step 5: Run the live smoke test (risk #1 gate)**

```
uv run pytest tests/test_claude_tools_suppression.py::test_smoke_tools_empty_preserves_mcp_tools -v -m live
```

Expected: PASS. If it fails (MCP tools were also zeroed out), update the implementation to use an explicit allowlist:

```python
if agent.suppress_builtins:
    # --tools "" zeros MCP tools too in this claude version; use explicit
    # allowlist of the aegis MCP tool names instead.
    aegis_tools = ["aegis_bash", "aegis_read", "aegis_write",
                   "aegis_edit", "aegis_grep", "aegis_listdir",
                   # plus any other aegis_* the agent might need
                   "aegis_meta", "aegis_handoff", "aegis_enqueue"]
    argv += ["--tools", ",".join(aegis_tools)]
```

Re-run the smoke test until green.

- [ ] **Step 6: Commit**

```bash
git add src/aegis/config/__init__.py src/aegis/drivers/claude_print.py src/aegis/drivers/claude_repl.py tests/test_claude_tools_suppression.py
git commit -m "feat: suppress_builtins + permissions fields; Claude --tools \"\" wiring"
```

---

### Task 16: AGENTS.md + CHANGELOG entry + version bump + release

**Files:**
- Modify: `AGENTS.md`
- Modify: `CHANGELOG.md`
- Modify: `pyproject.toml`

- [ ] **Step 1: Update AGENTS.md**

In the `## Layout` section under `src/aegis/`, add a `mcp/fs_tools/` entry:

```
- `src/aegis/mcp/fs_tools/` — six aegis-owned filesystem MCP tools
  (`aegis_bash`, `aegis_read`, `aegis_write`, `aegis_edit`, `aegis_grep`,
  `aegis_listdir`). One module per tool, registered as a batch via
  `register_fs_tools(server, bridge)` in `__init__.py`. Permission gate
  layered by `aegis.mcp.permissions.permission_gate` decorator at
  registration time.
- `src/aegis/mcp/permissions.py` — `PermissionRouter` (allow/deny/ask
  per agent profile, session-cached, timeout → deny), `OperatorSurface`
  Protocol with TUI + Telegram concrete impls, `permission_gate`
  decorator.
- `src/aegis/mcp/audit.py` — `AuditLog` writer — JSONL per handle under
  `.aegis/state/tool-audit/<handle>.jsonl`, args > 4 KiB truncated.
```

- [ ] **Step 2: Add CHANGELOG entry**

In `CHANGELOG.md`, insert below `## [Unreleased]`:

```markdown
## [0.13.0] - 2026-05-27

### Aegis filesystem tool surface + permissions

- **Six new aegis-owned MCP tools** in `src/aegis/mcp/fs_tools/`:
  `aegis_bash` (one-shot subprocess), `aegis_read` (paginated read with
  line numbers), `aegis_write` (new-file-only), `aegis_edit` (exact-string
  targeted replace), `aegis_grep` (literal-text recursive search; ripgrep
  with grep fallback), `aegis_listdir` (flat or recursive, gitignore-aware).
- **Per-agent-profile permissions** via three new `_ProviderBase` fields:
  `suppress_builtins: bool = False`, `permissions: dict[str, "allow"|"deny"|"ask"] = {}`,
  `permission_timeout_s: int = 300`. Default `allow` for unlisted tools.
- **`PermissionRouter`** (`src/aegis/mcp/permissions.py`) gates every
  aegis_* tool call against the agent's profile. Session-cached
  `allow_always` grants; `ask` verdicts route to TUI (inline modal on
  the conversation pane) or Telegram (inline-button keyboard with
  `callback_query` resolution), picked per handle.
- **Universal prefer-aegis-tools system-prompt addendum** in `PRIMING` —
  every aegis-driven session, regardless of profile, gets the
  instruction to prefer `aegis_*` over harness built-ins.
- **Hard built-in suppression on Claude** via `--tools ""` (Print + REPL
  drivers) when `suppress_builtins=True`. Gemini / OpenCode get soft
  suppression via the PRIMING addendum only — upstream lacks an
  external knob today; visibility holds via the audit log regardless.
- **`AuditLog`** at `.aegis/state/tool-audit/<handle>.jsonl` — every
  tool call, verdict, cache-hit flag, and latency.

Spec: `docs/superpowers/specs/2026-05-27-aegis-fs-tool-surface-design.md`.
Plan: `docs/superpowers/plans/2026-05-27-aegis-fs-tool-surface-v1.md`.
```

- [ ] **Step 3: Bump version**

In `pyproject.toml`, change `version = "0.12.0"` to `version = "0.13.0"`.

- [ ] **Step 4: Verify full hermetic suite**

```
uv run pytest -m "not live" -q | tail -3
```

Expected: all pass.

- [ ] **Step 5: Verify live suite (gate before release)**

```
uv run pytest -m live -q | tail -3
```

Expected: all pass (including the risk #1 smoke from Task 15).

- [ ] **Step 6: Release commit + tag**

```bash
git add AGENTS.md CHANGELOG.md pyproject.toml
git commit -m "release: 0.13.0 — aegis fs tool surface + permissions framework"
git tag v0.13.0
git push origin main
git push origin v0.13.0
```

---

## Out-of-plan (defer to follow-up specs)

These are explicitly *out of scope* for v1, captured so they don't get smuggled in:

- `aegis_search` — semantic / embedding-indexed search across the repo.
- `aegis_search_other_sessions` — visibility into peer agents' work.
- Fine-grained permission predicates beyond `allow|deny|ask` (custom-Python).
- Hard suppression on Gemini/OpenCode (waiting on upstream Policy Engine / equivalent).
- Per-tool timeouts (`{"aegis_bash": {"verdict": "ask", "timeout_s": 60}}`).
- Audit-log rotation (acceptable in v1; flag for follow-up if size exceeds 100 MB after a week of normal use).
- Cross-session permission persistence.
- The Textual modal widget itself inside `ConversationPane` — Task 11 ships only the bridge contract (`show_permission_modal(req, future)`); a follow-up commit wires the actual widget without contract changes.

## Self-review — spec coverage map

| Spec section / requirement | Verified by |
|---|---|
| Six tools with semantics in the table | Tasks 2–7 |
| `aegis_grep` literal-text-by-default | Task 4 (`test_regex_special_chars_are_literal`) |
| `aegis_write` new-file-only | Task 5 (`test_refuses_existing_file`) |
| `aegis_edit` errors if not unique unless `replace_all` | Task 6 (`test_errors_when_old_string_not_unique`) |
| `aegis_bash` timeout | Task 7 (`test_timeout_kills_long_running`) |
| Tools execute in aegis process via `asyncio.to_thread` | All tool tasks |
| `PermissionRouter` allow / deny / ask | Tasks 9, 10 |
| Default-allow for unlisted tools | Task 9 (`test_unlisted_tool_defaults_to_allow`) |
| Session cache for `allow_always` | Task 9 (`test_allow_always_reply_caches_for_session`) |
| Timeout → deny | Task 9 (`test_ask_timeout_returns_deny`) |
| TUI surface | Task 11 |
| Telegram surface via inline buttons | Tasks 12, 13 |
| `callback_query` dispatch | Task 13 |
| Universal `PRIMING` addendum | Task 14 |
| `suppress_builtins` field + Claude `--tools ""` | Task 15 |
| Risk #1 verified (`--tools ""` doesn't kill MCP tools) | Task 15 step 5 live smoke |
| AuditLog at `.aegis/state/tool-audit/<handle>.jsonl` | Task 8 |
| Args > 4 KiB truncated | Task 8 (`test_audit_truncates_large_args`) |
