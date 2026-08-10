# F3 sidebar REPOS section — implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [x]`) syntax for tracking.

**Status:** complete 2026-08-10 — all seven tasks landed, full fast suite green (3104 passed).

**Goal:** A `REPOS` section in the `F3` sidebar listing every git repo a live
agent has written to, with branch, dirty count, ahead/behind, and a per-pane
mark distinguishing this agent from peers.

**Spec:** `docs/superpowers/specs/2026-08-10-aegis-sidebar-repos-section-design.md`

**Tech Stack:** Python 3.13+, `uv`, pytest, Textual 8.x, Rich.

## Global Constraints

- TDD: failing test first, minimal implementation, commit per logical unit.
- `uv run python -m pytest -q -m "not live"`. Never `-k "not live"` — it
  matches `live` as a substring and eats unrelated names.
- English for code, comments, identifiers, error strings, commit messages.
- Conventional commits, scope `repos` (or `sidebar` for TUI wiring).
- All width arithmetic measures **cells** (`rich.cells.cell_len`), never `len()`.
- New tests: `tests/test_repos_*.py`.
- Nothing in `repos/render.py` imports Textual.

---

## Task 1 — repo root resolution

- [x] `tests/test_repos_probe.py::test_find_root_*` — a file inside a temp
      repo resolves to its root; a nested repo resolves to the *nearest*
      root, not the outer one; a path outside any repo returns `None`; a
      `.git` **file** carrying `gitdir:` resolves through the pointer.
- [x] `src/aegis/repos/probe.py::find_repo_root(path) -> Path | None`.
- [x] Commit.

## Task 2 — the git probe

- [x] `tests/test_repos_probe.py::test_probe_*` against a **real** temp repo
      built in a fixture: `init`, commit, dirty a file, clone to make a local
      upstream and commit ahead of it, detach `HEAD`. Assert branch, `dirty`,
      `ahead`/`behind`, and the detached flag. Not mocked.
- [x] `probe_repo(root) -> RepoState` — one
      `git status --porcelain=v2 --branch`, parsed for `branch.head`,
      `branch.ab`, and the entry lines.
- [x] `read_head_branch(root) -> str` — `.git/HEAD` fallback, no subprocess.
- [x] Non-zero exit / timeout / missing `git` → branch-only state, `stale`.
- [x] Commit.

## Task 3 — models + tracker

- [x] `tests/test_repos_tracker.py` — `record` adds a writer and orders by
      recency; a second handle on the same repo yields two writers;
      `snapshot(for_handle)` marks `mine` correctly; `drop` removes a handle
      and removes the row only when it was the last writer; a path outside
      any repo records nothing.
- [x] `src/aegis/repos/models.py` — `RepoState`, `RepoView`.
- [x] `src/aegis/repos/tracker.py` — `RepoTracker` with `record` / `drop` /
      `snapshot` / `subscribe`, and the ~5s TTL cache driving `probe_repo`
      off-thread. Notify subscribers when a probe lands.
- [x] Commit.

## Task 4 — the pure renderer

- [x] `tests/test_repos_render.py` — section order of rows (recency); the
      `●` vs `·` mark; amber on a multi-writer row; tier selection at 26 vs
      60 columns; **tier 5 truncates a long repo name rather than dropping
      the row**; empty list returns `None`; a `stale` row renders dim; a
      remote-host row shows `name@host` with no counts.
- [x] `src/aegis/repos/render.py::render_repos(views, palette, width)`.
- [x] Commit.

## Task 5 — record from the session

- [x] Test: an `AgentSession` fed a `ToolUse` for `Write` / `Edit` /
      `NotebookEdit` records the path against its handle; a `Read` records
      nothing; a `Bash` records nothing; no tracker attached is a no-op.
- [x] `AgentSession` takes an optional `repo_tracker` and calls
      `record(self.handle, file_path)` on write tools only.
- [x] Commit.

## Task 6 — sidebar wiring

- [x] Test: `SidebarModel.repos` renders through `render_sidebar` in the
      right position (between `MONITORS` and `SYSTEM`), and the section is
      absent when the list is empty.
- [x] `repos` field on `SidebarModel`; `_repos()` composer in
      `tui/sidebar.py`; `_sidebar_model()` fills it from the app tracker.
- [x] `AegisApp` owns one `RepoTracker`; panes subscribe in `on_mount` and
      release in `on_unmount` alongside `_digest` / `_monitor_manager`;
      `drop(handle)` on session close.
- [x] Commit.

## Task 7 — integration + mutation check

- [x] Test: open the sidebar in a running app with a real temp repo written
      to, assert the row is *actually rendered* — not that the model field is
      populated.
- [x] Mutation-check the empty case: break the `None`-on-empty rule, confirm
      the test goes red, restore.
- [x] Full fast suite green: `uv run python -m pytest -q -m "not live"`.
- [x] `TASKS.md`: record the web-client half as debt, alongside the existing
      sidebar and live-task-list entries.
- [x] `AGENTS.md`: one `src/aegis/repos/` entry in the Layout section.
- [x] `CHANGELOG.md` entry. Commit.
