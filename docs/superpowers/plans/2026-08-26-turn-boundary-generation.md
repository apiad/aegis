# Turn-Boundary Generation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fire one structured-generation call at the turn boundary to (a) decide whether an armed `/loop` continues, from facts rather than the agent's self-report, and (b) leave a one-line recap of what the turn did.

**Architecture:** A shared `TurnDigest` collects what a turn actually *did* — commits, files written, plan movement — and feeds three consumers that differ only in window budget, schema and prompt: the loop judge (blocking, once per iteration), the recap (detached, once per productive turn), and `/btw` (unchanged budget, now facts-aware). All ride the existing `HarnessDriver.generate_detailed` seam and are best-effort by contract: every failure returns an empty result, never an exception.

**Tech Stack:** Python 3.13+, pydantic, asyncio, pytest. `uv run` for everything.

**Spec:** `docs/superpowers/specs/2026-08-26-aegis-turn-boundary-generation-design.md`

## Global Constraints

- **TDD.** Failing test first, minimal implementation, commit per logical unit.
- **Best-effort by contract.** No generation path may raise into a turn. Failures return an empty/None result. Follow `titlegen.suggest_title`'s shape.
- **Off-host means no git.** A session whose `place` is not local must never have its paths resolved against the local disk. Same rule as `Claim.host` and `render_shared.file_target`.
- **Tests:** `uv run python -m pytest -q -m "not live"`. Never `-k "not live"` — it matches `live` as a substring and eats unrelated names.
- **Never `git add -A`.** Stage the explicit paths the task touched. This is a shared checkout.
- **English** for all code, comments, identifiers, and commit messages.
- **Measured baseline** (zion, 2026-08-26, haiku): one-shot at `cwd=Workspace` = 21,445 input tokens, ~7s, $0.045 cold. The prompt cache warms **only on an identical prompt**, so a per-turn feature never amortizes.

---

### Task 1: Shed the project-instruction prefix from every one-shot

The one-shot loads the cwd's `CLAUDE.md` and skills — measured ~15k tokens it cannot use, since it is handed its window explicitly. This task is a prerequisite: it cuts `/btw` and `titlegen` today, and it is what makes a per-turn recap affordable at all.

**Files:**
- Modify: `src/aegis/drivers/claude.py:310-338` (`_oneshot_argv`)
- Test: `tests/test_oneshot_argv.py` (create)

**Interfaces:**
- Consumes: nothing.
- Produces: no signature change. `ClaudeDriver._oneshot_argv(agent, schema, instructions, *, system="") -> list[str]` gains two argv entries.

- [ ] **Step 1: Write the failing test**

```python
"""The one-shot must not load the project's instructions.

Measured 2026-08-26 on zion: a 16-char prompt costs 20,611 input tokens at
cwd=Workspace and 6,319 in an empty dir. The difference is CLAUDE.md and
skills — which a generation call cannot use, because it is handed its
window explicitly.
"""
import json

from aegis.config import Agent
from aegis.drivers.claude import ClaudeDriver
from pydantic import BaseModel


class _Schema(BaseModel):
    line: str


def _argv():
    d = ClaudeDriver()
    agent = Agent(harness="claude-code", model="haiku")
    return d._oneshot_argv(agent, _Schema, ["hello"])


def test_oneshot_loads_no_setting_sources():
    argv = _argv()
    assert "--setting-sources" in argv
    # The value must be empty: user/project/local are exactly the three
    # sources that pull in CLAUDE.md, skills and plugins.
    assert argv[argv.index("--setting-sources") + 1] == ""


def test_oneshot_still_sheds_tools_and_mcp():
    """Regression guard: the new flag must not displace the old ones."""
    argv = _argv()
    assert argv[argv.index("--tools") + 1] == ""
    assert "--strict-mcp-config" in argv
    assert json.loads(argv[argv.index("--mcp-config") + 1]) == {
        "mcpServers": {}}


def test_oneshot_does_not_use_exclude_dynamic():
    """Measured: it moves tokens without removing any (21,445 either way)."""
    argv = _argv()
    assert "--exclude-dynamic-system-prompt-sections" not in argv
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run python -m pytest tests/test_oneshot_argv.py -v`
Expected: `test_oneshot_loads_no_setting_sources` FAILS with `assert '--setting-sources' in argv`. The other two PASS.

- [ ] **Step 3: Write minimal implementation**

In `src/aegis/drivers/claude.py`, replace the `_oneshot_argv` docstring's stale measurement and add the flag:

```python
    def _oneshot_argv(self, agent: Agent, schema, instructions: list[str],
                      *, system: str = "") -> list[str]:
        """A `claude` invocation that generates rather than agents.

        Every flag here is load-shedding. Measured on zion (claude 2.1.220,
        haiku, same window and question):

            default `claude -p`         21.9s  $0.0633  53,593 in  refusal
            --system-prompt --tools ""   8.5s  $0.0044   2,361 in  correct

        The default run does not merely cost more — it goes *agentic*,
        tries to read files that a side note has no business reading, and
        then answers "I cannot verify". ``--tools ""`` is what removes the
        tool schemas (the bulk of those 53k input tokens) and with them the
        urge to use them; ``--system-prompt`` replaces claude's agentic
        default rather than appending to it, which
        ``--append-system-prompt`` cannot do.

        **Re-measured 2026-08-26, and the $0.0044 above is a WARM price.**
        A cold call in this repo costs 21,445 input tokens / ~7s / $0.0448,
        because the run still loads the cwd's CLAUDE.md, skills and
        plugins. The prompt cache warms only on an *identical* prompt, and
        no real caller repeats one — so the cold number is the steady
        state, not the exception. ``--setting-sources ""`` sheds exactly
        those three sources: 21,445 -> 7,749 input tokens, a 64% cut, with
        no behavioural change, because a generation call is handed its
        window explicitly and has no business reading the project's
        instructions.

        ``--exclude-dynamic-system-prompt-sections`` was measured too and
        is deliberately absent: it relocates the per-machine sections
        without removing them (21,445 either way).
        """
        return [
            "claude", "-p", "\n\n".join(instructions),
            "--model", agent.model,
            "--output-format", "json",
            "--json-schema", json.dumps(schema.model_json_schema()),
            "--system-prompt", system or _ONESHOT_SYSTEM,
            "--tools", "",
            "--setting-sources", "",
            "--mcp-config", json.dumps({"mcpServers": {}}),
            "--strict-mcp-config",
        ]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run python -m pytest tests/test_oneshot_argv.py -v`
Expected: 3 passed.

- [ ] **Step 5: Verify against the real CLI (this is the point of the task)**

Run:

```bash
cd /home/apiad/Workspace/repos/aegis && uv run python - <<'PY'
import asyncio, json, subprocess, time
from pydantic import BaseModel
from aegis.config import Agent
from aegis.drivers.claude import ClaudeDriver

class Line(BaseModel):
    line: str

async def main():
    d, agent = ClaudeDriver(), Agent(harness="claude-code", model="haiku")
    argv = d._oneshot_argv(agent, Line, ["Say ok in one line."])
    t0 = time.monotonic()
    p = await asyncio.create_subprocess_exec(
        *argv, cwd="/home/apiad/Workspace", stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    out, err = await p.communicate()
    env = json.loads(out)
    u = env.get("usage") or {}
    total = (u.get("input_tokens", 0) + u.get("cache_read_input_tokens", 0)
             + u.get("cache_creation_input_tokens", 0))
    print(f"total_in={total:,}  wall={time.monotonic()-t0:.1f}s  "
          f"${env.get('total_cost_usd', 0):.5f}")
asyncio.run(main())
PY
```

Expected: `total_in` around **7,700**, not ~21,000. If it still reads ~21,000 the flag is not taking effect — check `claude --help | grep -A2 setting-sources` for a signature change before proceeding, because every later cost claim in this plan rests on this number.

- [ ] **Step 6: Commit**

```bash
git add tests/test_oneshot_argv.py src/aegis/drivers/claude.py
git commit -m "perf(oneshot): stop loading project instructions into generation calls

Measured on zion 2026-08-26: a one-shot at cwd=Workspace burned 21,445
input tokens, ~15k of which is CLAUDE.md + skills the call cannot use —
it is handed its window explicitly. --setting-sources \"\" cuts the
prefix 64% (21,445 -> 7,749) with no behavioural change.

Also corrects the docstring: its \$0.0044 figure is a WARM price. The
cache warms only on an identical prompt, so no real caller amortizes and
the cold number is the steady state."
```

---

### Task 2: Digest models and pure renderer

**Files:**
- Create: `src/aegis/digest/__init__.py`
- Create: `src/aegis/digest/models.py`
- Create: `src/aegis/digest/render.py`
- Test: `tests/test_digest_render.py`

**Interfaces:**
- Consumes: nothing.
- Produces:
  - `CommitLine(sha: str, subject: str)`
  - `RepoDelta(name: str, host: str = "local", commits: tuple[CommitLine, ...] = (), files_written: int = 0, available: bool = True)`
  - `TurnFacts(repos: tuple[RepoDelta, ...] = (), plan_done_delta: int = 0, plan_done: int = 0, plan_total: int = 0, assistant_tail: str = "", duration_s: float = 0.0, error: str = "")`
  - `TurnFacts.moved -> bool`
  - `render_facts(facts: TurnFacts) -> str`

- [ ] **Step 1: Write the failing test**

```python
"""The digest's pure half: what a turn did, and how it reads in a prompt."""
from aegis.digest.models import CommitLine, RepoDelta, TurnFacts
from aegis.digest.render import render_facts


def _commit(sha="abc1234", subject="feat: a thing"):
    return CommitLine(sha=sha, subject=subject)


def test_moved_is_false_for_a_read_only_turn():
    """The gate the recap hangs on. A turn of questions moved nothing."""
    assert TurnFacts().moved is False
    assert TurnFacts(assistant_tail="I read the file.").moved is False


def test_moved_is_true_on_a_commit():
    facts = TurnFacts(repos=(RepoDelta(name="aegis",
                                       commits=(_commit(),)),))
    assert facts.moved is True


def test_moved_is_true_on_a_write_with_no_commit():
    facts = TurnFacts(repos=(RepoDelta(name="aegis", files_written=3),))
    assert facts.moved is True


def test_moved_is_true_on_plan_progress_alone():
    """A turn that finished a task without touching a tracked repo."""
    assert TurnFacts(plan_done_delta=1, plan_done=3, plan_total=5).moved


def test_moved_is_false_when_the_digest_failed():
    """An errored digest must not read as movement — that would make every
    broken collection fire a recap."""
    facts = TurnFacts(repos=(RepoDelta(name="aegis", files_written=3),),
                      error="git exploded")
    assert facts.moved is False


def test_render_names_commits_and_counts():
    facts = TurnFacts(
        repos=(RepoDelta(name="aegis", files_written=2,
                         commits=(_commit("51430de", "docs(spec): the spec"),
                                  _commit("dae9d19", "docs: identity"))),),
        plan_done_delta=1, plan_done=2, plan_total=5)
    out = render_facts(facts)
    assert "aegis" in out
    assert "51430de" in out and "docs(spec): the spec" in out
    assert "2 files" in out
    assert "2/5" in out


def test_render_of_nothing_is_explicit_not_empty():
    """An empty string would read as 'facts unavailable'. It is not the
    same claim as 'this turn changed nothing', and the judge acts on the
    difference."""
    out = render_facts(TurnFacts())
    assert out.strip()
    assert "no commits" in out.lower() or "nothing" in out.lower()


def test_render_says_so_when_a_repo_is_off_host():
    facts = TurnFacts(repos=(RepoDelta(name="app", host="vps",
                                       available=False),))
    out = render_facts(facts)
    assert "vps" in out
    assert "not inspected" in out.lower() or "unavailable" in out.lower()


def test_render_surfaces_the_error():
    out = render_facts(TurnFacts(error="git not found"))
    assert "git not found" in out
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run python -m pytest tests/test_digest_render.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'aegis.digest'`

- [ ] **Step 3: Write minimal implementation**

`src/aegis/digest/__init__.py`:

```python
"""What a turn actually did — the half no transcript window contains.

`/btw`'s window carries what was *said*. The loop judge and the recap both
need what was *done*: commits, files written, plan movement. That is the
whole reason this is one shared collector rather than two private helpers.

Best-effort by contract, like ``titlegen``: any failure yields a
``TurnFacts`` with ``error`` set. A summary must never be able to disturb
the conversation it summarises.
"""
from aegis.digest.models import CommitLine, RepoDelta, TurnFacts
from aegis.digest.render import render_facts

__all__ = ["CommitLine", "RepoDelta", "TurnFacts", "render_facts"]
```

`src/aegis/digest/models.py`:

```python
"""The digest's data, frozen and pure."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class CommitLine:
    """One commit, as `git log --oneline` gives it."""
    sha: str
    subject: str


@dataclass(frozen=True)
class RepoDelta:
    """What one turn did to one repo.

    ``available`` is False for an off-host repo. The same path names a
    different tree on another machine, so probing it locally returns a
    silently wrong answer rather than an error — the rule ``Claim.host``
    and ``render_shared.file_target`` already follow.
    """
    name: str
    host: str = "local"
    commits: tuple[CommitLine, ...] = ()
    files_written: int = 0
    available: bool = True


@dataclass(frozen=True)
class TurnFacts:
    """One turn's substrate movement."""
    repos: tuple[RepoDelta, ...] = ()
    plan_done_delta: int = 0
    plan_done: int = 0
    plan_total: int = 0
    assistant_tail: str = ""
    duration_s: float = 0.0
    error: str = ""

    @property
    def moved(self) -> bool:
        """Did the substrate change?

        The recap's gate. An errored digest is NOT movement: we did not
        observe stillness, we failed to look, and firing on that would
        make every broken collection produce a recap.
        """
        if self.error:
            return False
        if self.plan_done_delta > 0:
            return True
        return any(r.commits or r.files_written for r in self.repos)
```

`src/aegis/digest/render.py`:

```python
"""TurnFacts as the text block a prompt embeds. Pure."""
from __future__ import annotations

from aegis.digest.models import TurnFacts

MAX_COMMITS_SHOWN = 10


def render_facts(facts: TurnFacts) -> str:
    """A compact, honest block. Never empty.

    Emptiness would read as "facts unavailable", which is a different
    claim from "this turn changed nothing" — and the judge acts on the
    difference between a still turn and an unobserved one.
    """
    lines: list[str] = ["--- what this turn did ---"]
    if facts.error:
        lines.append(f"(facts incomplete: {facts.error})")
    for repo in facts.repos:
        if not repo.available:
            lines.append(f"{repo.name}@{repo.host}: not inspected "
                         f"(off-host)")
            continue
        head = f"{repo.name}: {len(repo.commits)} commit" \
               f"{'' if len(repo.commits) == 1 else 's'}"
        if repo.files_written:
            head += (f", {repo.files_written} file"
                     f"{'' if repo.files_written == 1 else 's'} written")
        lines.append(head)
        for c in repo.commits[:MAX_COMMITS_SHOWN]:
            lines.append(f"  {c.sha} {c.subject}")
        if len(repo.commits) > MAX_COMMITS_SHOWN:
            lines.append(f"  … and {len(repo.commits) - MAX_COMMITS_SHOWN}"
                         f" more")
    if facts.plan_total:
        lines.append(f"plan: {facts.plan_done}/{facts.plan_total} done "
                     f"(+{facts.plan_done_delta} this turn)")
    if len(lines) == 1:
        lines.append("no commits, no files written, no plan movement")
    lines.append("--- end ---")
    return "\n".join(lines)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run python -m pytest tests/test_digest_render.py -v`
Expected: 9 passed.

- [ ] **Step 5: Commit**

```bash
git add src/aegis/digest/__init__.py src/aegis/digest/models.py \
        src/aegis/digest/render.py tests/test_digest_render.py
git commit -m "feat(digest): TurnFacts and its pure renderer

The shared half of the loop judge and the recap: what a turn DID, which
no transcript window contains. moved() is the recap's gate — and an
errored digest is deliberately not movement, since we failed to look
rather than observed stillness."
```

---

### Task 3: Collect the digest from git

**Files:**
- Create: `src/aegis/digest/collect.py`
- Test: `tests/test_digest_collect.py`

**Interfaces:**
- Consumes: `CommitLine`, `RepoDelta` from Task 2.
- Produces:
  - `read_head(root: Path) -> str` — the current commit sha, or `""`.
  - `commits_since(root: Path, base: str, *, max_count: int = 50) -> tuple[CommitLine, ...]`
  - `class DigestCollector` with:
    - `note_write(root: Path, host: str = "local") -> None` — lazily captures the base `HEAD` the first time a repo is written to this turn.
    - `reset() -> None`
    - `build(*, plan_done: int, plan_total: int, plan_done_at_start: int, assistant_tail: str, duration_s: float) -> TurnFacts` (async)

**Why a lazy base:** at turn start we do not know which repos a turn will touch, and `git log --since=<time>` would attribute a peer's commit in a shared checkout to us. Capturing `HEAD` at the first *recorded write* is precise and cheap. Known limitation, documented in the module: a turn that commits without using a write tool (pure Bash) contributes no commits, because `write_target` deliberately excludes Bash.

- [ ] **Step 1: Write the failing test**

```python
"""Digest collection against a real git repo. Not mocks — the thing under
test is whether we read git correctly."""
import subprocess
from pathlib import Path

import pytest

from aegis.digest.collect import DigestCollector, commits_since, read_head


def _git(root: Path, *args: str) -> str:
    return subprocess.run(("git", *args), cwd=root, check=True,
                          capture_output=True, text=True).stdout.strip()


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    root = tmp_path / "demo"
    root.mkdir()
    _git(root, "init", "-q")
    _git(root, "config", "user.email", "t@example.com")
    _git(root, "config", "user.name", "Test")
    (root / "a.txt").write_text("one\n")
    _git(root, "add", "a.txt")
    _git(root, "commit", "-q", "-m", "initial")
    return root


def _commit(root: Path, name: str, msg: str) -> None:
    (root / name).write_text("x\n")
    _git(root, "add", name)
    _git(root, "commit", "-q", "-m", msg)


def test_read_head_returns_a_sha(repo):
    assert len(read_head(repo)) >= 7


def test_read_head_of_a_non_repo_is_empty(tmp_path):
    assert read_head(tmp_path / "nope") == ""


def test_commits_since_lists_only_new_ones(repo):
    base = read_head(repo)
    _commit(repo, "b.txt", "feat: b")
    _commit(repo, "c.txt", "fix: c")
    got = commits_since(repo, base)
    assert [c.subject for c in got] == ["fix: c", "feat: b"]
    assert all(len(c.sha) >= 7 for c in got)


def test_commits_since_is_empty_when_nothing_landed(repo):
    assert commits_since(repo, read_head(repo)) == ()


def test_commits_since_survives_a_bogus_base(repo):
    """Never raise into a turn."""
    assert commits_since(repo, "notasha") == ()


@pytest.mark.asyncio
async def test_collector_reports_commits_made_after_the_first_write(repo):
    c = DigestCollector()
    c.note_write(repo)                       # base captured here
    _commit(repo, "b.txt", "feat: b")
    facts = await c.build(plan_done=0, plan_total=0, plan_done_at_start=0,
                          assistant_tail="done", duration_s=1.0)
    assert len(facts.repos) == 1
    delta = facts.repos[0]
    assert delta.name == "demo"
    assert [x.subject for x in delta.commits] == ["feat: b"]
    assert delta.files_written == 1
    assert facts.moved is True


@pytest.mark.asyncio
async def test_collector_counts_writes_without_commits(repo):
    c = DigestCollector()
    c.note_write(repo)
    c.note_write(repo)
    facts = await c.build(plan_done=0, plan_total=0, plan_done_at_start=0,
                          assistant_tail="", duration_s=0.5)
    assert facts.repos[0].files_written == 2
    assert facts.repos[0].commits == ()
    assert facts.moved is True


@pytest.mark.asyncio
async def test_collector_never_probes_an_off_host_repo(repo):
    """The identically-named local path is a different tree there."""
    c = DigestCollector()
    c.note_write(repo, host="vps")
    _commit(repo, "b.txt", "feat: b")
    facts = await c.build(plan_done=0, plan_total=0, plan_done_at_start=0,
                          assistant_tail="", duration_s=0.1)
    delta = facts.repos[0]
    assert delta.host == "vps"
    assert delta.available is False
    assert delta.commits == ()


@pytest.mark.asyncio
async def test_collector_computes_the_plan_delta(repo):
    c = DigestCollector()
    facts = await c.build(plan_done=4, plan_total=6, plan_done_at_start=2,
                          assistant_tail="", duration_s=0.1)
    assert facts.plan_done_delta == 2
    assert facts.plan_done == 4 and facts.plan_total == 6
    assert facts.moved is True


@pytest.mark.asyncio
async def test_reset_forgets_the_previous_turn(repo):
    c = DigestCollector()
    c.note_write(repo)
    c.reset()
    facts = await c.build(plan_done=0, plan_total=0, plan_done_at_start=0,
                          assistant_tail="", duration_s=0.1)
    assert facts.repos == ()
    assert facts.moved is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run python -m pytest tests/test_digest_collect.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'aegis.digest.collect'`

- [ ] **Step 3: Write minimal implementation**

`src/aegis/digest/collect.py`:

```python
"""Reading what a turn did off git. The impure half.

**Why the base HEAD is captured lazily.** At turn start we do not know
which repos a turn will touch. The obvious alternative — `git log --since
=<turn start>` — misattributes a peer's commit in a shared checkout, and
this workspace is one. So the base is read the first time a write to that
repo is *recorded*, which is precise and costs one `rev-parse`.

Known limitation, stated rather than hidden: a turn that commits without
using a write tool (pure Bash) contributes no commits, because
``repos.writes.write_target`` deliberately excludes Bash.
"""
from __future__ import annotations

import asyncio
import subprocess
from pathlib import Path

from aegis.digest.models import CommitLine, RepoDelta, TurnFacts

MAX_COMMITS = 50
_TIMEOUT_S = 5


def _git(root: Path, *args: str) -> str:
    """Run git, or return "". Never raises — this runs inside a turn."""
    try:
        proc = subprocess.run(
            ("git", *args), cwd=root, capture_output=True, text=True,
            timeout=_TIMEOUT_S)
    except (OSError, subprocess.SubprocessError):
        return ""
    return proc.stdout.strip() if proc.returncode == 0 else ""


def read_head(root: Path) -> str:
    """The current commit sha, or "" if this is not a readable repo."""
    return _git(root, "rev-parse", "HEAD")


def commits_since(root: Path, base: str, *,
                  max_count: int = MAX_COMMITS) -> tuple[CommitLine, ...]:
    """Commits landed since ``base``, newest first. () on any failure."""
    if not base:
        return ()
    out = _git(root, "log", "--oneline", "--no-decorate",
               f"--max-count={max_count}", f"{base}..HEAD")
    if not out:
        return ()
    lines = []
    for raw in out.splitlines():
        sha, _, subject = raw.partition(" ")
        if sha:
            lines.append(CommitLine(sha=sha, subject=subject.strip()))
    return tuple(lines)


class _Tracked:
    __slots__ = ("root", "host", "base", "writes")

    def __init__(self, root: Path, host: str) -> None:
        self.root = root
        self.host = host
        # Off-host: never resolve against the local disk.
        self.base = read_head(root) if host == "local" else ""
        self.writes = 0


class DigestCollector:
    """Per-session, reset at each turn start."""

    def __init__(self) -> None:
        self._tracked: dict[tuple[str, str], _Tracked] = {}

    def reset(self) -> None:
        self._tracked.clear()

    def note_write(self, root: Path | str, host: str = "local") -> None:
        """Record a write, capturing the repo's base HEAD the first time."""
        root = Path(root)
        key = (host, str(root))
        entry = self._tracked.get(key)
        if entry is None:
            entry = self._tracked[key] = _Tracked(root, host)
        entry.writes += 1

    async def build(self, *, plan_done: int, plan_total: int,
                    plan_done_at_start: int, assistant_tail: str,
                    duration_s: float) -> TurnFacts:
        """Diff every tracked repo. Git runs off the event loop."""
        try:
            deltas = await asyncio.to_thread(self._diff_all)
        except Exception as e:                                # noqa: BLE001
            return TurnFacts(assistant_tail=assistant_tail,
                             duration_s=duration_s,
                             error=f"{type(e).__name__}: {e}")
        return TurnFacts(
            repos=deltas,
            plan_done=plan_done,
            plan_total=plan_total,
            plan_done_delta=max(0, plan_done - plan_done_at_start),
            assistant_tail=assistant_tail,
            duration_s=duration_s)

    def _diff_all(self) -> tuple[RepoDelta, ...]:
        out = []
        for entry in self._tracked.values():
            local = entry.host == "local"
            out.append(RepoDelta(
                name=entry.root.name,
                host=entry.host,
                commits=(commits_since(entry.root, entry.base)
                         if local else ()),
                files_written=entry.writes,
                available=local))
        return tuple(out)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run python -m pytest tests/test_digest_collect.py -v`
Expected: 10 passed.

- [ ] **Step 5: Mutation-check the off-host rule**

The off-host guard is the one rule here that fails silently if broken. Prove the test can fail: temporarily change `_Tracked.__init__` to `self.base = read_head(root)` unconditionally and `_diff_all` to always pass `commits_since(...)`, then run:

Run: `uv run python -m pytest tests/test_digest_collect.py::test_collector_never_probes_an_off_host_repo -v`
Expected: **FAIL**. Revert the mutation and confirm it passes again. A guard whose test cannot fail is worth less than none.

- [ ] **Step 6: Commit**

```bash
git add src/aegis/digest/collect.py tests/test_digest_collect.py
git commit -m "feat(digest): collect commits and writes off git

Base HEAD is captured lazily at the first recorded write, not at turn
start: we do not know which repos a turn will touch, and --since
misattributes a peer's commit in a shared checkout. Off-host repos are
listed but never probed."
```

---

### Task 4: Wire the digest into AgentSession

**Files:**
- Modify: `src/aegis/core/session.py` (`__init__`, `_fire_event`, `_run_turn`)
- Test: `tests/test_digest_session.py`

**Interfaces:**
- Consumes: `DigestCollector` from Task 3.
- Produces: `AgentSession.digest: DigestCollector`, `AgentSession.last_facts: TurnFacts | None`, and an `on_facts: Callable[[AgentSession, TurnFacts], None] | None` observer fired once per completed turn.

- [ ] **Step 1: Write the failing test**

```python
"""The digest must ride the real turn path — and the recording hook must
be somewhere replay does not reach."""
import pytest

from aegis.digest.models import TurnFacts


def test_session_exposes_a_digest_collector(make_session):
    """`make_session` is the existing fixture in tests/conftest.py."""
    s = make_session()
    assert s.digest is not None
    assert s.last_facts is None


@pytest.mark.asyncio
async def test_a_turn_produces_facts_and_fires_the_observer(make_session):
    seen = []
    s = make_session()
    s.on_facts = lambda _s, f: seen.append(f)
    await s.send("hello")
    assert isinstance(s.last_facts, TurnFacts)
    assert len(seen) == 1
    assert seen[0] is s.last_facts


@pytest.mark.asyncio
async def test_writes_during_a_turn_reach_the_collector(make_session,
                                                        tmp_path):
    """A Write tool_use must be recorded, via the same write_target path
    the repo tracker already uses."""
    from aegis.events import ToolUse
    s = make_session()
    target = tmp_path / "f.py"
    target.write_text("x")
    s._fire_event(ToolUse(name="Write", tool_call_id="t1",
                          raw_input={"file_path": str(target)}))
    assert s.digest._tracked


@pytest.mark.asyncio
async def test_each_turn_starts_from_a_clean_digest(make_session, tmp_path):
    from aegis.events import ToolUse
    s = make_session()
    target = tmp_path / "f.py"
    target.write_text("x")
    s._fire_event(ToolUse(name="Write", tool_call_id="t1",
                          raw_input={"file_path": str(target)}))
    await s.send("go")
    first = s.last_facts
    await s.send("again")
    assert first is not s.last_facts
    assert s.last_facts.repos == ()


@pytest.mark.asyncio
async def test_the_tail_excludes_subagent_narration(make_session):
    """A subagent's narration is not this turn's answer. The queue's
    result capture had to learn this the hard way.

    The events must be EMITTED BY THE FAKE HARNESS during the turn —
    `own_text_parts` fills inside `_run_turn`'s event loop, so firing them
    via `_fire_event` would pass against a broken implementation. Follow
    however the existing session tests script their fake harness's event
    stream (see the fixtures in tests/conftest.py) and script this one to
    yield, in order:

        AssistantText(text="I dispatched a subagent.")
        AssistantText(text="SUBAGENT-CHATTER", parent_tool_use_id="task-1")
        AssistantText(text=" Done.")
        Result(duration_ms=1)
    """
    s = make_session(script=[...])       # per the fixture's own API
    await s.send("go")
    assert "SUBAGENT-CHATTER" not in s.last_facts.assistant_tail
    assert "I dispatched a subagent." in s.last_facts.assistant_tail
    assert "Done." in s.last_facts.assistant_tail


@pytest.mark.asyncio
async def test_a_broken_collector_does_not_break_the_turn(make_session,
                                                          monkeypatch):
    """Best-effort by contract: the turn completes regardless."""
    s = make_session()

    async def boom(**_kw):
        raise RuntimeError("nope")

    monkeypatch.setattr(s.digest, "build", boom)
    await s.send("hello")          # must not raise
    assert s.last_facts is not None
    assert s.last_facts.error
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run python -m pytest tests/test_digest_session.py -v`
Expected: FAIL with `AttributeError: 'AgentSession' object has no attribute 'digest'`

If `make_session` does not exist in `tests/conftest.py`, read the fixtures the existing `tests/test_session*.py` files use and follow that pattern instead — do not invent a new harness.

- [ ] **Step 3: Write minimal implementation**

In `src/aegis/core/session.py`:

Add the import beside the existing `from aegis.repos.writes import write_target` (line 15):

```python
from aegis.digest.collect import DigestCollector
from aegis.digest.models import TurnFacts
```

In `__init__`, beside `self.repo_tracker = repo_tracker` (line 103):

```python
        # What this turn actually did — commits, writes, plan movement.
        # Shared by the loop judge, the recap and /btw. Reset per turn.
        self.digest = DigestCollector()
        self.last_facts: TurnFacts | None = None
        # Fired once per completed turn with that turn's facts.
        self.on_facts = None
```

In `_fire_event` (line 621), beside the existing `write_target` handling, record into the digest as well. Find where the repo tracker is fed and add the sibling call:

```python
            # The digest rides the SAME hook as the repo tracker, and for
            # the same reason: `_fire_event` is not on the replay walk, so
            # a resumed session does not re-collect its whole history as
            # one turn's worth of work.
            self.digest.note_write(target, host=host)
```

In `_run_turn`, reset at the start of execution. Immediately after `self._unsolicited = False  # a real, prompted turn` (line 482):

```python
        self.digest.reset()
        turn_started = self._now()
        plan_done_at_start = self.plan_state().done
```

(Task 6 adds a `self._cancel_recap()` call beside `self.digest.reset()`. Do not add it here — the method does not exist yet and this task must leave the suite green on its own.)

The digest's assistant tail must exclude subagent narration. `_run_turn`'s existing `assistant_text_parts` (line ~543) deliberately collects everything for `PostTurnEvent`; do **not** change that — it is a different consumer with its own contract. Add a sibling list instead, in the same `isinstance(ev, AssistantText)` branch:

```python
                if isinstance(ev, AssistantText):
                    assistant_text_parts.append(ev.text)
                    # A subagent's narration is not this turn's answer.
                    # `capture_next_reply` has said so since @peer shipped,
                    # and the queue's result capture had to learn it the
                    # hard way. Parented events are skipped, not treated as
                    # ending the run — doing the latter truncates the
                    # agent's own message whenever a subagent speaks
                    # mid-stream.
                    if getattr(ev, "parent_tool_use_id", None) is None:
                        own_text_parts.append(ev.text)
```

Declare `own_text_parts: list[str] = []` beside `assistant_text_parts` at the top of section 3.

And build the facts in section 4, replacing the existing post-turn hook block so the facts are computed before the observers and before `_chain_if_pending`:

```python
        # 4. Turn facts — best-effort, never raises into the turn.
        try:
            facts = await self.digest.build(
                plan_done=self.plan_state().done,
                plan_total=self.plan_state().total,
                plan_done_at_start=plan_done_at_start,
                assistant_tail="".join(own_text_parts),
                duration_s=max(0.0, self._now() - turn_started))
        except Exception as e:                                # noqa: BLE001
            facts = TurnFacts(error=f"{type(e).__name__}: {e}")
        self.last_facts = facts
        if self.on_facts is not None:
            try:
                self.on_facts(self, facts)
            except Exception:                                 # noqa: BLE001
                log.exception("on_facts observer raised")

        # 5. Post-turn hooks (fire-and-forget)
        post_ev = PostTurnEvent(
            session=ctx.session,
            user_message=text,
            assistant_message="".join(assistant_text_parts),
            project_root=self.project_root,
        )
        asyncio.create_task(
            run_observer_hooks(
                post_ev, _HOOK_REG["post_turn"], state_dir=self.state_dir
            )
        )

        self._chain_if_pending()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run python -m pytest tests/test_digest_session.py -v`
Expected: 5 passed.

- [ ] **Step 5: Run the blast-radius suite**

Run: `uv run python -m pytest tests/ -q -m "not live" -x`
Expected: all pass. `_run_turn` is the hottest path in the codebase; a red run here is a regression to fix, not noise to re-roll.

- [ ] **Step 6: Commit**

```bash
git add src/aegis/core/session.py tests/test_digest_session.py
git commit -m "feat(digest): collect turn facts on the real turn path

Recorded from _fire_event — the same hook the repo tracker uses, and for
the same reason: replay does not call it, so a resumed session does not
re-collect its whole history as one turn's work."
```

---

### Task 5: The recap generator

**Files:**
- Create: `src/aegis/recap/__init__.py`
- Test: `tests/test_recap_generate.py`

**Interfaces:**
- Consumes: `TurnFacts`, `render_facts` (Task 2); `generation_agent` (`btw/__init__.py:62`); `assemble` (`btw/window.py:125`).
- Produces:
  - `TurnRecap(BaseModel)` with `line: str`
  - `SessionRecap(BaseModel)` with `building: str`, `done: str`, `remaining: str`
  - `Recap` frozen dataclass: `line: str = ""`, `building/done/remaining: str = ""`, `header/model: str = ""`, `duration_ms: int = 0`, `cost_usd: float = 0.0`, `ok: bool = False`, `error: str = ""`, plus `footer` and `text` properties.
  - `async recap_turn(*, replay, facts, driver, agent, cwd) -> Recap`
  - `async recap_session(*, replay, facts, driver, agent, cwd) -> Recap`
  - `async recap_for(*, state_dir, log_id, facts, agent, agents, cwd, session_scope: bool) -> Recap`

**Window budgets** (from the spec's table): turn recap = `max_turns=1, budget_tokens=2_000, item_chars=200`; session recap = `assemble()` defaults.

- [ ] **Step 1: Write the failing test**

```python
"""The recap's generation half, against a fake driver."""
import pytest

from aegis.digest.models import CommitLine, RepoDelta, TurnFacts
from aegis.drivers.oneshot import Generation
from aegis.recap import Recap, SessionRecap, TurnRecap, recap_session, \
    recap_turn


class FakeDriver:
    supports_oneshot = True

    def __init__(self, value=None, raises=False):
        self.value, self.raises = value, raises
        self.calls = []

    async def generate_detailed(self, agent, cwd, schema, *instructions):
        self.calls.append((schema, instructions))
        if self.raises:
            raise RuntimeError("driver exploded")
        return Generation(value=self.value, model="haiku",
                          duration_ms=1200, cost_usd=0.02)


class FakeReplay:
    events = []


FACTS = TurnFacts(repos=(RepoDelta(name="aegis", files_written=2,
                                   commits=(CommitLine("51430de",
                                                       "docs: spec"),)),))


@pytest.mark.asyncio
async def test_turn_recap_returns_the_line():
    d = FakeDriver(TurnRecap(line="Wrote the spec; 1 commit."))
    got = await recap_turn(replay=FakeReplay(), facts=FACTS, driver=d,
                           agent=object(), cwd=".")
    assert got.ok is True
    assert got.line == "Wrote the spec; 1 commit."
    assert got.text == "Wrote the spec; 1 commit."


@pytest.mark.asyncio
async def test_turn_recap_asks_for_the_turn_schema():
    d = FakeDriver(TurnRecap(line="x"))
    await recap_turn(replay=FakeReplay(), facts=FACTS, driver=d,
                     agent=object(), cwd=".")
    schema, _ = d.calls[0]
    assert schema is TurnRecap


@pytest.mark.asyncio
async def test_the_facts_are_in_the_prompt():
    """The whole reason the recap can say '1 commit' at all."""
    d = FakeDriver(TurnRecap(line="x"))
    await recap_turn(replay=FakeReplay(), facts=FACTS, driver=d,
                     agent=object(), cwd=".")
    _, instructions = d.calls[0]
    assert any("51430de" in part for part in instructions)


@pytest.mark.asyncio
async def test_session_recap_returns_the_block():
    d = FakeDriver(SessionRecap(building="the judge", done="the spec",
                                remaining="the wiring"))
    got = await recap_session(replay=FakeReplay(), facts=FACTS, driver=d,
                              agent=object(), cwd=".")
    assert got.ok is True
    assert got.building == "the judge"
    assert "the judge" in got.text and "the wiring" in got.text
    assert got.line == ""


@pytest.mark.asyncio
async def test_a_raising_driver_returns_a_failed_recap():
    """Best-effort by contract — a recap must never disturb the turn."""
    got = await recap_turn(replay=FakeReplay(), facts=FACTS,
                           driver=FakeDriver(raises=True), agent=object(),
                           cwd=".")
    assert got.ok is False
    assert "RuntimeError" in got.error
    assert got.line == ""


@pytest.mark.asyncio
async def test_an_unparseable_payload_returns_a_failed_recap():
    got = await recap_turn(replay=FakeReplay(), facts=FACTS,
                           driver=FakeDriver(value=None), agent=object(),
                           cwd=".")
    assert got.ok is False
    assert got.error


def test_footer_carries_the_price():
    r = Recap(line="x", model="haiku", duration_ms=1200, cost_usd=0.02,
              ok=True)
    assert "haiku" in r.footer and "1.2s" in r.footer
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run python -m pytest tests/test_recap_generate.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'aegis.recap'`

- [ ] **Step 3: Write minimal implementation**

`src/aegis/recap/__init__.py`:

```python
"""`/recap` — where this turn, or this session, actually stands.

Two schemas rather than one with optional fields: the automatic recap is
one line about a turn, `/recap` is a short block about a session, and a
schema serving two masters degrades both.

Gating lives in the caller (``aegis.recap.gate``), not here — this module
only knows how to ask. Best-effort by contract, like ``titlegen``.
"""
from __future__ import annotations

from dataclasses import dataclass

from pydantic import BaseModel, Field

from aegis.btw.window import assemble
from aegis.digest.models import TurnFacts
from aegis.digest.render import render_facts

TURN_WINDOW = dict(max_turns=1, budget_tokens=2_000, item_chars=200)


class TurnRecap(BaseModel):
    line: str = Field(description="ONE line, past tense, concrete. Name "
                                  "files and counts. No preamble.")


class SessionRecap(BaseModel):
    building: str = Field(description="what the session is working toward")
    done: str = Field(description="what has actually landed")
    remaining: str = Field(description="what is left")


@dataclass(frozen=True)
class Recap:
    """One recap, and what it cost."""
    line: str = ""
    building: str = ""
    done: str = ""
    remaining: str = ""
    header: str = ""
    model: str = ""
    duration_ms: int = 0
    cost_usd: float = 0.0
    ok: bool = False
    error: str = ""

    @property
    def text(self) -> str:
        if self.line:
            return self.line
        if not (self.building or self.done or self.remaining):
            return ""
        return "\n".join(x for x in (
            f"building: {self.building}" if self.building else "",
            f"done: {self.done}" if self.done else "",
            f"remaining: {self.remaining}" if self.remaining else "",
        ) if x)

    @property
    def footer(self) -> str:
        bits = [b for b in (
            self.model,
            f"{self.duration_ms / 1000:.1f}s" if self.duration_ms else "",
            f"${self.cost_usd:.4f}" if self.cost_usd else "",
            self.header,
        ) if b]
        return " · ".join(bits)


_TURN_SYSTEM = (
    "You write a single line saying what a coding agent's last turn did. "
    "Past tense, concrete, no preamble, no praise. Prefer the FACTS block "
    "over the agent's own narration — the agent describes what it meant "
    "to do; the facts say what landed. Name files and counts."
)

_SESSION_SYSTEM = (
    "You summarize where a coding session stands, for an operator "
    "returning to it. Three short fields: what is being built, what has "
    "landed, what is left. Prefer the FACTS block over the agent's own "
    "narration. No preamble, no praise."
)


async def _one(schema, system, *, replay, facts, driver, agent, cwd,
               window_opts) -> Recap:
    window = assemble(replay, **window_opts)
    try:
        gen = await driver.generate_detailed(
            agent, cwd, schema, system,
            f"--- conversation ({window.header or 'no turns yet'}) ---\n"
            f"{window.text}\n--- end ---",
            render_facts(facts))
    except Exception as e:                                    # noqa: BLE001
        return Recap(header=window.header,
                     error=f"{type(e).__name__}: {e}")
    if gen.value is None:
        return Recap(header=window.header, model=gen.model,
                     duration_ms=gen.duration_ms, cost_usd=gen.cost_usd,
                     error="the model returned nothing usable")
    v = gen.value
    return Recap(
        line=getattr(v, "line", ""),
        building=getattr(v, "building", ""),
        done=getattr(v, "done", ""),
        remaining=getattr(v, "remaining", ""),
        header=window.header, model=gen.model,
        duration_ms=gen.duration_ms, cost_usd=gen.cost_usd, ok=True)


async def recap_turn(*, replay, facts: TurnFacts, driver, agent,
                     cwd: str) -> Recap:
    """One line about the turn that just ended."""
    return await _one(TurnRecap, _TURN_SYSTEM, replay=replay, facts=facts,
                      driver=driver, agent=agent, cwd=cwd,
                      window_opts=TURN_WINDOW)


async def recap_session(*, replay, facts: TurnFacts, driver, agent,
                        cwd: str) -> Recap:
    """A short block about where the session stands."""
    return await _one(SessionRecap, _SESSION_SYSTEM, replay=replay,
                      facts=facts, driver=driver, agent=agent, cwd=cwd,
                      window_opts={})


async def recap_for(*, state_dir, log_id: str, facts: TurnFacts, agent,
                    agents: dict, cwd: str,
                    session_scope: bool) -> Recap:
    """Resolve driver + billing profile + transcript, then ask.

    Mirrors ``btw.side_note_for`` exactly, including reading the log off
    the event loop — a 24MB transcript takes 0.65s warm, far too much to
    spend on the UI thread.
    """
    import asyncio

    from aegis.btw import generation_agent
    from aegis.drivers import get_driver
    from aegis.state.session_log import replay_events

    gen_agent, _unset = generation_agent(agent, agents)
    try:
        driver = get_driver(gen_agent.harness)
    except KeyError:
        return Recap(error=f"unknown harness: {gen_agent.harness!r}")
    if not getattr(driver, "supports_oneshot", False):
        return Recap(error=f"the {gen_agent.harness} driver cannot do "
                            f"one-shot generation")
    try:
        replay = await asyncio.to_thread(replay_events, state_dir, log_id)
    except Exception as e:                                    # noqa: BLE001
        return Recap(error=f"could not read the transcript: {e}")
    fn = recap_session if session_scope else recap_turn
    return await fn(replay=replay, facts=facts, driver=driver,
                    agent=gen_agent, cwd=cwd)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run python -m pytest tests/test_recap_generate.py -v`
Expected: 7 passed.

- [ ] **Step 5: Commit**

```bash
git add src/aegis/recap/__init__.py tests/test_recap_generate.py
git commit -m "feat(recap): the generation half, turn- and session-scoped

Two schemas rather than one with optional fields — the auto recap is one
line about a turn, /recap is a block about a session, and a schema
serving two masters degrades both. The facts block is what lets a recap
say '1 commit' at all."
```

---

### Task 6: The movement gate and the detached auto-fire

The measured 7s latency floor is why this is detached: a 7-second stall after every productive turn is not payable, and shedding tokens does not move it.

**Files:**
- Create: `src/aegis/recap/gate.py`
- Modify: `src/aegis/core/session.py`
- Modify: `src/aegis/config/yaml_loader.py` (add `recap: bool = True`)
- Test: `tests/test_recap_gate.py`

**Interfaces:**
- Consumes: `TurnFacts.moved` (Task 2), `AgentSession.on_facts` (Task 4), `recap_for` (Task 5).
- Produces: `should_recap(facts: TurnFacts, *, last_line: str, enabled: bool) -> bool`; `AgentSession.on_recap: Callable[[AgentSession, Recap], None] | None`.

- [ ] **Step 1: Write the failing test**

```python
"""The gate is the cost control and the noise control at once.

anthropics/claude-code#56346: gating on turn count produces 10+ identical
recaps in a row. Gate on substrate movement instead.
"""
import pytest

from aegis.digest.models import CommitLine, RepoDelta, TurnFacts
from aegis.recap.gate import should_recap

MOVED = TurnFacts(repos=(RepoDelta(name="aegis", files_written=1,
                                   commits=(CommitLine("a1", "feat: x"),)),))
STILL = TurnFacts(assistant_tail="Here is what that function does.")


def test_a_moving_turn_recaps():
    assert should_recap(MOVED, last_line="", enabled=True) is True


def test_a_read_only_turn_does_not():
    assert should_recap(STILL, last_line="", enabled=True) is False


def test_a_disabled_recap_never_fires():
    assert should_recap(MOVED, last_line="", enabled=False) is False


def test_an_errored_digest_does_not_fire():
    facts = TurnFacts(repos=MOVED.repos, error="git exploded")
    assert should_recap(facts, last_line="", enabled=True) is False


@pytest.mark.asyncio
async def test_the_auto_recap_does_not_block_the_turn(make_session,
                                                      monkeypatch):
    """The measured 7s floor is why this is detached. If the recap were
    awaited, this turn would take at least as long as the sleep."""
    import asyncio
    import time

    from aegis.recap import Recap

    async def slow(**_kw):
        await asyncio.sleep(0.5)
        return Recap(line="done", ok=True)

    monkeypatch.setattr("aegis.core.session.recap_for", slow)
    s = make_session()
    s.recap_enabled = True
    t0 = time.monotonic()
    await s.send("hello")
    assert time.monotonic() - t0 < 0.4    # the turn did not wait


@pytest.mark.asyncio
async def test_a_new_turn_cancels_an_in_flight_recap(make_session,
                                                     monkeypatch):
    """A late recap describes a transcript that has moved on."""
    import asyncio

    from aegis.recap import Recap

    seen = []

    async def slow(**_kw):
        await asyncio.sleep(5)
        return Recap(line="late", ok=True)

    monkeypatch.setattr("aegis.core.session.recap_for", slow)
    s = make_session()
    s.recap_enabled = True
    s.on_recap = lambda _s, r: seen.append(r)
    await s.send("one")
    await s.send("two")
    await asyncio.sleep(0.05)
    assert seen == []
    assert s._recap_task is None or s._recap_task.cancelled() \
        or not s._recap_task.done() or s._recap_task.result() is None


@pytest.mark.asyncio
async def test_the_recap_never_reaches_the_agent(make_session,
                                                 monkeypatch):
    """It is for the operator. Feeding it back would make every turn open
    by reading a summary of itself — and worse, a logged recap enters the
    window the NEXT recap assembles, so summaries compound.

    Assert on the substrate (what the harness was sent, what the log
    holds), not on a rendered string: a source-grep would pin the text and
    survive the bug.
    """
    import asyncio

    from aegis.recap import Recap

    async def fast(**_kw):
        return Recap(line="RECAP-SENTINEL-XYZ", ok=True)

    monkeypatch.setattr("aegis.core.session.recap_for", fast)
    s = make_session()
    s.recap_enabled = True
    await s.send("one")
    await asyncio.sleep(0.05)
    await s.send("two")

    sent = getattr(s._session, "sent", None)
    assert sent is not None, "fake harness must record what it was sent"
    assert not any("RECAP-SENTINEL-XYZ" in m for m in sent)
```

If the fake harness used by `make_session` does not record what it was sent, add that recording to the fake rather than weakening the assertion — the whole point is to check the substrate.

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run python -m pytest tests/test_recap_gate.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'aegis.recap.gate'`

- [ ] **Step 3: Write minimal implementation**

`src/aegis/recap/gate.py`:

```python
"""When a recap is worth firing.

Claude Code gates its recap on turn count (>=3 turns, "never twice in a
row") and anthropics/claude-code#56346 reports the predictable result:
in a conversation of questions and reads, 10+ identical recaps accumulate.

Gate on substrate movement instead. Ten identical recaps would require ten
turns that each changed something and each changed it the same way.
"""
from __future__ import annotations

from aegis.digest.models import TurnFacts


def should_recap(facts: TurnFacts, *, last_line: str,
                 enabled: bool) -> bool:
    """True when this turn earned a recap.

    ``last_line`` is the previous recap's text — reserved for the identity
    guard applied after generation, since we cannot know the new line
    before we ask for it.
    """
    if not enabled:
        return False
    return facts.moved
```

In `src/aegis/core/session.py`, add the import:

```python
from aegis.recap import Recap, recap_for
from aegis.recap.gate import should_recap
```

In `__init__`, beside the digest fields from Task 4:

```python
        # The auto recap. Detached on purpose: measured 2026-08-26, a
        # one-shot costs ~7s wall REGARDLESS of prefix size, and a 7s
        # stall after every productive turn is not payable.
        self.recap_enabled = True
        self.on_recap = None
        self._recap_task: asyncio.Task | None = None
        self._last_recap_line = ""
```

Add the method:

```python
    def _maybe_recap(self, facts) -> None:
        """Fire a recap without making the turn wait for it."""
        if not should_recap(facts, last_line=self._last_recap_line,
                            enabled=self.recap_enabled):
            return
        self._cancel_recap()
        self._recap_task = asyncio.create_task(self._run_recap(facts))

    def _cancel_recap(self) -> None:
        """Drop an in-flight recap. A late one describes a transcript that
        has already moved on, which is worse than none."""
        if self._recap_task is not None and not self._recap_task.done():
            self._recap_task.cancel()
        self._recap_task = None

    async def _run_recap(self, facts) -> None:
        try:
            recap = await recap_for(
                state_dir=self.state_dir, log_id=self.log_id, facts=facts,
                agent=self.agent, agents=self._agents, cwd=self.cwd,
                session_scope=False)
        except asyncio.CancelledError:
            raise
        except Exception:                                     # noqa: BLE001
            log.exception("recap failed")
            return
        if not recap.ok or not recap.line:
            return
        # The identity guard: a line identical to the last one is noise,
        # which is the #56346 failure arriving by another road.
        if recap.line.strip() == self._last_recap_line.strip():
            return
        self._last_recap_line = recap.line
        if self.on_recap is not None:
            try:
                self.on_recap(self, recap)
            except Exception:                                 # noqa: BLE001
                log.exception("on_recap observer raised")
```

Call `self._maybe_recap(facts)` in `_run_turn` immediately after the `on_facts` observer block from Task 4, and call `self._cancel_recap()` at the top of `_run_turn` beside `self.digest.reset()`.

**`AgentSession` does not carry an agents map today** — both bridge implementations pass their own (`tui/app.py:1729` uses `agents=self._agents`, `core/manager.py:346` likewise). So thread one in exactly the way `repo_tracker` was: an optional constructor kwarg `agents: dict | None = None`, stored as `self._agents`, with `_maybe_recap` returning early when it is `None`. The cwd is already on the session as `self.project_root` — pass `cwd=str(self.project_root)`, matching what both bridges pass to `side_note_for`.

In `src/aegis/config/yaml_loader.py`, beside `text_generation` (line 75), add `recap: bool = True` to the config dataclass and `recap=raw.get("recap", True)` to the constructor call around line 262.

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run python -m pytest tests/test_recap_gate.py -v`
Expected: 6 passed.

- [ ] **Step 5: Mutation-check the gate**

Per the spec: a gate that cannot fail is worth less than none. Temporarily change `should_recap`'s last line to `return True`, then run:

Run: `uv run python -m pytest tests/test_recap_gate.py::test_a_read_only_turn_does_not -v`
Expected: **FAIL**. Revert and confirm it passes.

- [ ] **Step 6: Commit**

```bash
git add src/aegis/recap/gate.py src/aegis/core/session.py \
        src/aegis/config/yaml_loader.py tests/test_recap_gate.py
git commit -m "feat(recap): movement gate and detached auto-fire

Gates on substrate movement, not turn count — the latter is what makes
claude-code#56346 accumulate ten identical recaps. Detached because the
measured ~7s one-shot latency is flat in prefix size, so no amount of
token shedding makes a blocking recap payable."
```

---

### Task 7: `/recap` across the four bridge surfaces

`side_note` exists on four surfaces and `recap` needs all four. This is not boilerplate: `read_peer` carries a test asserting both bridges take the same window knobs precisely because a signature that drifts on one bridge breaks that frontend and no other.

**Files:**
- Modify: `src/aegis/mcp/bridge.py:112` (Protocol)
- Modify: `src/aegis/core/manager.py:335`
- Modify: `src/aegis/tui/app.py:1716`
- Modify: `src/aegis/tui/remote_manager.py:255`
- Modify: `src/aegis/commands/builtins/core.py` (register `/recap`)
- Modify: `src/aegis/render.py` (add `render_recap`)
- Modify: `src/aegis/tui/pane.py:1484` (apply the effect)
- Test: `tests/test_recap_command.py`

**Interfaces:**
- Consumes: `recap_for` (Task 5), `Recap` (Task 5).
- Produces: `AppBridge.recap(handle: str, *, session_scope: bool = True) -> Recap` on all four surfaces; command `/recap`; `render_recap(recap, colors) -> Panel`.

- [ ] **Step 1: Write the failing test**

```python
"""/recap, and the four-surface rule."""
import inspect

import pytest

from aegis.recap import Recap


@pytest.mark.asyncio
async def test_recap_command_renders_the_block():
    from aegis.commands import dispatch

    class Bridge:
        async def recap(self, handle, *, session_scope=True):
            assert session_scope is True
            return Recap(building="the judge", done="the spec",
                         remaining="the wiring", ok=True, model="haiku")

    res = await dispatch("/recap", Bridge(), "agent-1")
    assert res.ok is True
    assert "the judge" in res.title or "the judge" in res.body
    assert res.effect["kind"] == "recap"
    assert isinstance(res.effect["recap"], dict)   # JSON-safe for the web


@pytest.mark.asyncio
async def test_a_failed_recap_is_an_error_result():
    from aegis.commands import dispatch

    class Bridge:
        async def recap(self, handle, *, session_scope=True):
            return Recap(error="no transcript")

    res = await dispatch("/recap", Bridge(), "agent-1")
    assert res.ok is False
    assert "no transcript" in res.body


def test_every_bridge_takes_the_same_recap_signature():
    """A signature that drifts on one bridge breaks that frontend and no
    other — the hardest kind of bug to see. Same guard read_peer carries."""
    from aegis.core.manager import SessionManager
    from aegis.mcp.bridge import AppBridge
    from aegis.tui.app import AegisApp
    from aegis.tui.remote_manager import RemoteSessionManager

    want = inspect.signature(AppBridge.recap)
    for impl in (SessionManager, AegisApp, RemoteSessionManager):
        assert inspect.signature(impl.recap) == want, impl.__name__
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run python -m pytest tests/test_recap_command.py -v`
Expected: FAIL — `/recap` is not a registered command.

- [ ] **Step 3: Write minimal implementation**

Add to `src/aegis/mcp/bridge.py`, beside `side_note`:

```python
    async def recap(self, handle: str, *, session_scope: bool = True):
        """Where this session stands, in one call. See aegis.recap."""
        ...
```

`src/aegis/core/manager.py` and `src/aegis/tui/app.py` — mirror their own `side_note` bodies exactly, swapping `side_note_for` for `recap_for` and passing `facts=session.last_facts or TurnFacts()` and `session_scope=session_scope`.

`src/aegis/tui/remote_manager.py`, beside its `side_note`:

```python
    async def recap(self, handle: str, *, session_scope: bool = True):
        raise RemoteUnsupportedError(f"recap: {_MSG}")
```

In `src/aegis/commands/builtins/core.py`, beside `_btw`:

```python
async def _recap(ctx: CommandContext, args) -> CommandResult:
    """Where this session stands — building / done / remaining.

    More verbose than the automatic one-liner on purpose: the automatic
    one re-orients you after a turn, this one audits a two-hour session
    before you open a PR.
    """
    recap = await ctx.bridge.recap(ctx.handle, session_scope=True)
    if not recap.ok:
        return CommandResult(False, "recap failed",
                             recap.error or "no answer")
    from dataclasses import asdict
    # asdict, not the dataclass: the web seam ships `effect` straight out
    # as JSON, and a dataclass there would break /recap on the web client
    # only — the same trap /btw already documented.
    return CommandResult(True, recap.text, recap.footer,
                         effect={"kind": "recap",
                                 "recap": asdict(recap)})
```

Register it in the same table `/btw` is registered in, with no arguments.

In `src/aegis/render.py`, beside `render_side_note`:

```python
def render_recap(recap, colors) -> Panel:
    """Visible block for a recap.

    Transient by design, exactly as a side note is: this block lands in
    the pane's ``_history`` and is **never appended to the session log**.
    That is the mechanism behind "the recap never enters the agent's
    context" — and it is also what stops recaps compounding, since a
    logged recap would enter the window the *next* recap assembles and
    every summary after it would be summarizing its own summaries.

    Markdown on the ok path only, for the reason ``render_side_note``
    gives: the text is model prose, but an error is aegis speaking a fixed
    sentence and keeps its ``colors.error`` tint.
    """
    tint = colors.error if not recap.ok else colors.accent
    parts: list[RenderableType] = [
        Text("recap", style=f"bold italic {tint}")]
    if recap.ok:
        parts.append(Markdown(recap.text))
    else:
        parts.append(Text(recap.error or "no answer", style=tint))
    if recap.footer:
        parts.append(Text(recap.footer, style=colors.muted))
    return _aside(parts, colors)
```

In `src/aegis/tui/pane.py:1484`, beside the `side_note` branch, add a `recap` branch calling `render_recap` and `self._put_block(...)`. Wire `on_recap` on the session to the same `_put_block` path so the automatic recap renders through one surface, not two.

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run python -m pytest tests/test_recap_command.py -v`
Expected: 3 passed.

- [ ] **Step 5: Verify in the real TUI**

Per `CLAUDE.md` §5, exercise the surface the way a user reaches it — a passing test is not the same artifact as a rendered block.

Run `uv run aegis` in a real terminal, let one agent take a turn that writes a file, and confirm: (a) a recap line appears after that turn, (b) `/recap` prints the three-field block, (c) a turn that only answers a question prints nothing.

- [ ] **Step 6: Commit**

```bash
git add src/aegis/mcp/bridge.py src/aegis/core/manager.py \
        src/aegis/tui/app.py src/aegis/tui/remote_manager.py \
        src/aegis/commands/builtins/core.py src/aegis/render.py \
        src/aegis/tui/pane.py tests/test_recap_command.py
git commit -m "feat(recap): /recap across all four bridge surfaces

Signature-parity test included: a bridge signature that drifts breaks
that frontend and no other, which is why read_peer already carries one."
```

---

### Task 8: The loop judge generator

**Files:**
- Create: `src/aegis/core/loop_judge.py`
- Modify: `src/aegis/config/yaml_loader.py` (add `loop_judge: bool = True`)
- Test: `tests/test_loop_judge.py`

**Interfaces:**
- Consumes: `TurnFacts`, `render_facts` (Task 2); `assemble` (`btw/window.py`); `generation_agent` (`btw/__init__.py:62`).
- Produces:
  - `LoopVerdict(BaseModel)`: `verdict: Literal["continue", "done", "stuck"]`, `reason: str`, `addendum: str = ""`
  - `Judgement` frozen dataclass: `verdict: str = "continue"`, `reason/addendum/model: str = ""`, `duration_ms: int`, `cost_usd: float`, `ok: bool`, `error: str`
  - `async judge(*, instruction, iteration, max_iterations, facts, still_streak, advisory, replay, driver, agent, cwd) -> Judgement`
  - `async judge_for(*, state_dir, log_id, instruction, iteration, max_iterations, facts, still_streak, advisory, agent, agents, cwd) -> Judgement`

**Window budget:** `max_turns=2, budget_tokens=4_000, item_chars=300`.

- [ ] **Step 1: Write the failing test**

```python
"""The judge. Its failure mode must be CONTINUE — a failed API call must
never silently end a night of work."""
import pytest

from aegis.core.loop_judge import Judgement, LoopVerdict, judge
from aegis.digest.models import CommitLine, RepoDelta, TurnFacts
from aegis.drivers.oneshot import Generation


class FakeDriver:
    supports_oneshot = True

    def __init__(self, value=None, raises=False):
        self.value, self.raises = value, raises
        self.calls = []

    async def generate_detailed(self, agent, cwd, schema, *instructions):
        self.calls.append((schema, instructions))
        if self.raises:
            raise RuntimeError("driver exploded")
        return Generation(value=self.value, model="haiku",
                          duration_ms=900, cost_usd=0.02)


class FakeReplay:
    events = []


MOVED = TurnFacts(repos=(RepoDelta(name="aegis", files_written=2,
                                   commits=(CommitLine("a1", "feat: x"),)),))


async def _judge(driver, **over):
    kw = dict(instruction="wire the recap end to end", iteration=3,
              max_iterations=20, facts=MOVED, still_streak=0, advisory="",
              replay=FakeReplay(), driver=driver, agent=object(), cwd=".")
    kw.update(over)
    return await judge(**kw)


@pytest.mark.asyncio
async def test_a_continue_verdict_carries_its_addendum():
    d = FakeDriver(LoopVerdict(verdict="continue", reason="UI unbuilt",
                               addendum="the model tab is still missing"))
    got = await _judge(d)
    assert got.ok and got.verdict == "continue"
    assert got.addendum == "the model tab is still missing"


@pytest.mark.asyncio
async def test_a_done_verdict_is_returned_as_done():
    d = FakeDriver(LoopVerdict(verdict="done", reason="all wired"))
    assert (await _judge(d)).verdict == "done"


@pytest.mark.asyncio
async def test_a_raising_driver_continues():
    """The cap bounds runaway; a failed call must not end the loop."""
    got = await _judge(FakeDriver(raises=True))
    assert got.verdict == "continue"
    assert got.ok is False
    assert got.addendum == ""


@pytest.mark.asyncio
async def test_an_unparseable_payload_continues():
    got = await _judge(FakeDriver(value=None))
    assert got.verdict == "continue"
    assert got.ok is False


@pytest.mark.asyncio
async def test_an_unknown_verdict_string_continues():
    """A model that invents a fourth verdict must not stop the loop."""
    got = await _judge(FakeDriver(LoopVerdict(verdict="maybe", reason="?")))
    assert got.verdict == "continue"


@pytest.mark.asyncio
async def test_the_prompt_carries_the_facts_and_the_iteration():
    d = FakeDriver(LoopVerdict(verdict="continue", reason="x"))
    await _judge(d)
    joined = "\n".join(d.calls[0][1])
    assert "a1" in joined                     # the commit
    assert "3" in joined and "20" in joined   # iteration N/max


@pytest.mark.asyncio
async def test_the_still_streak_is_stated_not_inferred():
    d = FakeDriver(LoopVerdict(verdict="stuck", reason="no movement"))
    got = await _judge(d, still_streak=3)
    assert "3" in "\n".join(d.calls[0][1])
    assert got.verdict == "stuck"


@pytest.mark.asyncio
async def test_the_agents_stop_request_is_presented_as_a_claim():
    """Advisory, not authoritative — the burn this whole feature exists
    for was an agent stopping at iteration 1 of 20."""
    d = FakeDriver(LoopVerdict(verdict="continue", reason="UI unbuilt"))
    await _judge(d, advisory="I wired both rails and verified them")
    joined = "\n".join(d.calls[0][1])
    assert "I wired both rails" in joined
    assert "claim" in joined.lower() or "asked to stop" in joined.lower()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run python -m pytest tests/test_loop_judge.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'aegis.core.loop_judge'`

- [ ] **Step 3: Write minimal implementation**

`src/aegis/core/loop_judge.py`:

```python
"""Whether an armed `/loop` continues — decided from facts, not from the
agent's self-report.

The agent deciding whether the agent is done is the agent grading its own
homework, from inside the tunnel it has been in for N turns. On
2026-07-30 that cost a whole night of autonomy: a loop reaped at
iteration 1 of 20 with the model-manager UI, the download/load API and
the docs unbuilt. openai/codex#27352 is the same failure in another
harness.

**Best-effort, and its failure mode is CONTINUE.** The iteration cap is
what bounds runaway; a failed API call must never silently end a night of
work.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel, Field

from aegis.btw.window import assemble
from aegis.digest.models import TurnFacts
from aegis.digest.render import render_facts

JUDGE_WINDOW = dict(max_turns=2, budget_tokens=4_000, item_chars=300)

VERDICTS = ("continue", "done", "stuck")


class LoopVerdict(BaseModel):
    verdict: Literal["continue", "done", "stuck"]
    reason: str = Field(description="one sentence, addressed to the "
                                    "operator")
    addendum: str = Field(default="", description="only when continuing: "
                                                  "what REMAINS and what "
                                                  "not to redo. Never "
                                                  "restate the goal.")


@dataclass(frozen=True)
class Judgement:
    verdict: str = "continue"
    reason: str = ""
    addendum: str = ""
    model: str = ""
    duration_ms: int = 0
    cost_usd: float = 0.0
    ok: bool = False
    error: str = ""


_SYSTEM = (
    "You decide whether a coding agent's looping instruction is finished. "
    "You are OUTSIDE the work, which is the point: the agent has been in "
    "this task for many turns and consistently underestimates what "
    "remains.\n\n"
    "Read the instruction at its WIDEST sensible reading. 'Wire it up' "
    "means the system works end to end and the operator can USE it — not "
    "that one component's wires are connected. Infrastructure with no "
    "user-visible surface is half a job.\n\n"
    "Weigh the FACTS over the agent's narration: the agent says what it "
    "meant to do; the facts say what landed.\n\n"
    "verdict=done only if an operator could sit down and use the result. "
    "verdict=stuck if turns are passing with nothing landing. "
    "Otherwise continue, and put what REMAINS in addendum — never restate "
    "the goal, which is delivered verbatim anyway."
)


def _clean(raw: LoopVerdict) -> Judgement:
    verdict = raw.verdict if raw.verdict in VERDICTS else "continue"
    return Judgement(verdict=verdict, reason=raw.reason,
                     addendum=raw.addendum if verdict == "continue" else "")


async def judge(*, instruction: str, iteration: int, max_iterations: int,
                facts: TurnFacts, still_streak: int, advisory: str,
                replay, driver, agent, cwd: str) -> Judgement:
    """One call. Any failure returns a continuing Judgement."""
    window = assemble(replay, **JUDGE_WINDOW)
    state = [f"The looping instruction, verbatim: {instruction}",
             f"This is iteration {iteration} of {max_iterations}.",
             f"Consecutive turns with no commits, no files written and no "
             f"plan movement: {still_streak}."]
    if advisory:
        # Presented as a CLAIM, never as an instruction. This is the whole
        # inversion: aegis_loop_stop is advisory now.
        state.append(f"The agent asked to stop. Its claim, which you are "
                     f"free to reject: {advisory}")
    try:
        gen = await driver.generate_detailed(
            agent, cwd, LoopVerdict, _SYSTEM,
            f"--- conversation ({window.header or 'no turns yet'}) ---\n"
            f"{window.text}\n--- end ---",
            render_facts(facts),
            "\n".join(state))
    except Exception as e:                                    # noqa: BLE001
        return Judgement(error=f"{type(e).__name__}: {e}")
    if gen.value is None:
        return Judgement(model=gen.model, duration_ms=gen.duration_ms,
                         cost_usd=gen.cost_usd,
                         error="the model returned nothing usable")
    out = _clean(gen.value)
    return Judgement(verdict=out.verdict, reason=out.reason,
                     addendum=out.addendum, model=gen.model,
                     duration_ms=gen.duration_ms, cost_usd=gen.cost_usd,
                     ok=True)


async def judge_for(*, state_dir, log_id: str, instruction: str,
                    iteration: int, max_iterations: int, facts: TurnFacts,
                    still_streak: int, advisory: str, agent, agents: dict,
                    cwd: str) -> Judgement:
    """Resolve driver + billing profile + transcript, then judge."""
    import asyncio

    from aegis.btw import generation_agent
    from aegis.drivers import get_driver
    from aegis.state.session_log import replay_events

    gen_agent, _unset = generation_agent(agent, agents)
    try:
        driver = get_driver(gen_agent.harness)
    except KeyError:
        return Judgement(error=f"unknown harness: {gen_agent.harness!r}")
    if not getattr(driver, "supports_oneshot", False):
        return Judgement(error=f"the {gen_agent.harness} driver cannot do "
                                f"one-shot generation")
    try:
        replay = await asyncio.to_thread(replay_events, state_dir, log_id)
    except Exception as e:                                    # noqa: BLE001
        return Judgement(error=f"could not read the transcript: {e}")
    return await judge(instruction=instruction, iteration=iteration,
                       max_iterations=max_iterations, facts=facts,
                       still_streak=still_streak, advisory=advisory,
                       replay=replay, driver=driver, agent=gen_agent,
                       cwd=cwd)
```

Add `loop_judge: bool = True` to the config dataclass in `src/aegis/config/yaml_loader.py` beside `recap` from Task 6, and `loop_judge=raw.get("loop_judge", True)` to the constructor call.

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run python -m pytest tests/test_loop_judge.py -v`
Expected: 8 passed.

- [ ] **Step 5: Commit**

```bash
git add src/aegis/core/loop_judge.py src/aegis/config/yaml_loader.py \
        tests/test_loop_judge.py
git commit -m "feat(loop): the judge — a verdict from facts, not self-report

Failure mode is CONTINUE by design: the iteration cap bounds runaway, and
a failed API call must never silently end a night of work. An invented
fourth verdict is also treated as continue."
```

---

### Task 9: Wire the judge in; remove the coda

**Files:**
- Modify: `src/aegis/core/loop.py` (delete `_CODA`, change `render`, add the still-streak ring)
- Modify: `src/aegis/core/session.py:769` (`_chain_if_pending`'s loop tier, `stop_loop`)
- Modify: `src/aegis/queue/loop.py` (`LoopService.stop` → advisory)
- Test: `tests/test_loop_judge_wiring.py`
- Check: `tests/` for existing tests asserting the coda's text; update them, do not delete them.

**Interfaces:**
- Consumes: `judge_for`, `Judgement` (Task 8); `AgentSession.last_facts` (Task 4).
- Produces: `LoopState.render(addendum: str = "") -> str`; `LoopState.note(facts) -> None`; `LoopState.still_streak: int`; `LoopState.advisory: str`; `AgentSession.stop_loop(reason, *, advisory=False) -> bool`.

- [ ] **Step 1: Write the failing test**

```python
"""The judge replaces the coda as the thing that ends a loop."""
import pytest

from aegis.core.loop import LoopState
from aegis.core.loop_judge import Judgement
from aegis.digest.models import CommitLine, RepoDelta, TurnFacts

MOVED = TurnFacts(repos=(RepoDelta(name="a", commits=(CommitLine("a1",
                                                                 "x"),)),))
STILL = TurnFacts(assistant_tail="thinking")


def test_render_is_the_instruction_verbatim():
    """The coda is gone. Verbatim matters: the previous turn may have
    ended somewhere unhelpful."""
    s = LoopState(text="keep going")
    assert s.render() == "keep going"
    assert "aegis_loop_stop" not in s.render()


def test_render_appends_the_addendum_without_replacing_the_goal():
    s = LoopState(text="keep going")
    out = s.render(addendum="the UI is still missing")
    assert out.startswith("keep going")
    assert "the UI is still missing" in out


def test_the_still_streak_counts_consecutive_motionless_turns():
    s = LoopState(text="x")
    assert s.still_streak == 0
    s.note(STILL)
    s.note(STILL)
    assert s.still_streak == 2
    s.note(MOVED)
    assert s.still_streak == 0


@pytest.mark.asyncio
async def test_a_done_verdict_stops_the_loop(make_session, monkeypatch):
    async def verdict(**_kw):
        return Judgement(verdict="done", reason="all wired", ok=True)

    monkeypatch.setattr("aegis.core.session.judge_for", verdict)
    s = make_session()
    s.arm_loop("keep going", 20)
    await s.settle()          # let the loop tier run
    assert s.loop_status() is None


@pytest.mark.asyncio
async def test_a_done_verdict_does_not_consume_an_iteration(make_session,
                                                            monkeypatch):
    seen = {}

    async def verdict(**kw):
        seen["iteration"] = kw["iteration"]
        return Judgement(verdict="done", reason="done", ok=True)

    monkeypatch.setattr("aegis.core.session.judge_for", verdict)
    s = make_session()
    s.arm_loop("keep going", 20)
    await s.settle()
    assert seen["iteration"] == 1


@pytest.mark.asyncio
async def test_a_failed_judge_continues(make_session, monkeypatch):
    async def verdict(**_kw):
        return Judgement(verdict="continue", ok=False, error="boom")

    monkeypatch.setattr("aegis.core.session.judge_for", verdict)
    s = make_session()
    s.arm_loop("keep going", 3)
    await s.settle()
    assert s.loop_status() is not None


@pytest.mark.asyncio
async def test_the_first_delivery_is_not_judged(make_session, monkeypatch):
    """arm_loop chains immediately when idle; there is no turn to judge."""
    calls = []

    async def verdict(**kw):
        calls.append(kw)
        return Judgement(verdict="continue", ok=True)

    monkeypatch.setattr("aegis.core.session.judge_for", verdict)
    s = make_session()
    s.arm_loop("keep going", 20)
    await s.settle_one_turn()
    assert calls == []


@pytest.mark.asyncio
async def test_an_exhausted_loop_is_not_judged(make_session, monkeypatch):
    """No point paying for a verdict on a loop that is stopping anyway."""
    calls = []

    async def verdict(**kw):
        calls.append(kw)
        return Judgement(verdict="continue", ok=True)

    monkeypatch.setattr("aegis.core.session.judge_for", verdict)
    s = make_session()
    s.arm_loop("keep going", 1)
    await s.settle()
    assert calls == []
    assert s.loop_status() is None


@pytest.mark.asyncio
async def test_loop_stop_is_advisory_and_does_not_reap(make_session):
    """The inversion: the tool records a claim, the judge decides."""
    s = make_session()
    s.arm_loop("keep going", 20)
    s.stop_loop("I think I'm done", advisory=True)
    assert s.loop_status() is not None
    assert s._loop.advisory == "I think I'm done"


@pytest.mark.asyncio
async def test_the_operator_can_still_stop_a_loop_outright(make_session):
    s = make_session()
    s.arm_loop("keep going", 20)
    s.stop_loop("operator stopped it")
    assert s.loop_status() is None
```

The `settle` / `settle_one_turn` helpers may not exist. Read how the existing loop tests in `tests/` drive `_chain_if_pending` to a quiescent point and reuse that mechanism rather than inventing one.

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run python -m pytest tests/test_loop_judge_wiring.py -v`
Expected: FAIL — `LoopState.render()` requires a `handle` argument.

- [ ] **Step 3: Write minimal implementation**

Rewrite `src/aegis/core/loop.py`'s `_CODA` and `LoopState`:

```python
STILL_STREAK_CAP = 10


@dataclass
class LoopState:
    """One armed loop. ``iteration`` counts deliveries and is incremented
    as the turn is dispatched, so the Nth delivery reads ``iteration
    N/max``."""

    text: str
    iteration: int = 0
    max_iterations: int = DEFAULT_MAX_ITERATIONS
    # Consecutive turns that changed nothing. The judge is TOLD this
    # rather than left to infer it: the count is a fact, and inference is
    # what the judge exists to remove.
    still_streak: int = 0
    # What the agent said when it called aegis_loop_stop. Advisory since
    # the judge outranks it; cleared once the judge has seen it.
    advisory: str = ""

    def exhausted(self) -> bool:
        return self.iteration >= self.max_iterations

    def note(self, facts) -> None:
        """Fold one turn's facts into the still-streak."""
        if getattr(facts, "moved", False):
            self.still_streak = 0
        else:
            self.still_streak = min(self.still_streak + 1, STILL_STREAK_CAP)

    def render(self, addendum: str = "") -> str:
        """The body delivered to the agent.

        The instruction is VERBATIM — the previous turn may have ended
        somewhere unhelpful, and the instruction has to be present in the
        turn that acts on it. The judge's addendum is appended, never
        substituted: a judge-authored replacement is the ideal vector for
        reading a looping instruction more narrowly each iteration.

        The old stop-coda is gone. The agent no longer decides.
        """
        if not addendum:
            return self.text
        return f"{self.text}\n\nStill outstanding: {addendum}"

    def status(self) -> dict:
        return {"text": self.text, "iteration": self.iteration,
                "max_iterations": self.max_iterations,
                "still_streak": self.still_streak}
```

In `src/aegis/core/session.py`, make `stop_loop` advisory-capable:

```python
    def stop_loop(self, reason: str = "stopped", *,
                  advisory: bool = False) -> bool:
        """Reap the loop, or record a request to.

        ``advisory=True`` is what ``aegis_loop_stop`` now does: the agent
        states a claim and the judge decides. Leaving the tool
        authoritative would leave the 2026-07-30 burn exactly where it is
        — a loop reaped at iteration 1 of 20 with the user-visible half
        unbuilt.
        """
        if self._loop is None:
            return False
        if advisory:
            self._loop.advisory = reason
            self._emit_loop("stop requested")
            return True
        self._loop = None
        self._emit_loop(reason)
        return True
```

Replace the loop tier at line 769 with a dispatch through the judge:

```python
        if self._loop is not None and self._unsolicited_hold == 0:
            if self._loop.exhausted():
                self.stop_loop(
                    f"capped at {self._loop.max_iterations} iterations "
                    f"— the judge did not stop it")
            else:
                self._emit_state(AgentState.working, finished=False)
                self.metrics.start_turn(self._now())
                self._task = asyncio.create_task(self._judged_loop_turn())
                return
```

And add:

```python
    async def _judged_loop_turn(self) -> None:
        """Ask the judge, then deliver — or stop.

        Emits `working` before the call (done by the caller) so the
        session does not flicker idle while the judge thinks. The FIRST
        delivery is never judged: arm_loop chains immediately when idle,
        so no turn has yet run under the instruction.
        """
        loop = self._loop
        if loop is None:
            return
        addendum = ""
        if loop.iteration > 0 and self.loop_judge_enabled:
            loop.note(self.last_facts)
            verdict = await judge_for(
                state_dir=self.state_dir, log_id=self.log_id,
                instruction=loop.text, iteration=loop.iteration,
                max_iterations=loop.max_iterations,
                facts=self.last_facts or TurnFacts(),
                still_streak=loop.still_streak, advisory=loop.advisory,
                agent=self.agent, agents=self._agents, cwd=self.cwd)
            loop.advisory = ""       # consumed; the judge has seen it
            if verdict.verdict in ("done", "stuck"):
                self.stop_loop(f"{verdict.verdict}: {verdict.reason}")
                self._emit_state(AgentState.ready, finished=True)
                self._chain_if_pending()
                return
            addendum = verdict.addendum
        loop.iteration += 1
        msg = InboxMessage(
            sender=sender_loop(loop.iteration, loop.max_iterations),
            timestamp=now_iso(),
            body=loop.render(addendum))
        self._emit_dispatch([msg])
        self._emit_loop("fired")
        await self._run_turn(_render_batch([msg]))
```

Add `self.loop_judge_enabled = True` in `__init__` beside `recap_enabled`, and the `judge_for` / `Judgement` imports at the top.

In `src/aegis/queue/loop.py`, make `LoopService.stop` advisory:

```python
    def stop(self, *, from_handle: str, reason: str = "stopped") -> dict:
        """Record the agent's request to stop. The judge decides.

        The MCP tool keeps its identity gating; what changed is that a
        call is now a claim rather than a reaping.
        """
        session = self._session_for(from_handle)
        if session is None:
            return {"error": f"no live session for handle {from_handle!r}"}
        noted = session.stop_loop(reason, advisory=True)
        return {"noted": noted, "reason": reason,
                "note": "recorded — the loop judge decides whether the "
                        "loop ends"}
```

Update `aegis_loop_stop`'s docstring in `src/aegis/mcp/server.py:1469` to say the call is advisory, and update `mcp/server.py`'s BRIEFING text where it describes `aegis_loop_stop` as the thing that reaps a loop.

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run python -m pytest tests/test_loop_judge_wiring.py -v`
Expected: 10 passed.

- [ ] **Step 5: Fix the tests the coda's removal breaks**

Run: `uv run python -m pytest tests/ -q -m "not live"`

Expected: failures in existing loop tests asserting the coda's text or `render(handle)`'s signature. **Update them to the new contract; do not delete them.** Each one is a paid-for assertion about loop behaviour.

- [ ] **Step 6: Commit**

```bash
git add src/aegis/core/loop.py src/aegis/core/session.py \
        src/aegis/queue/loop.py src/aegis/mcp/server.py tests/
git commit -m "feat(loop): the judge decides, aegis_loop_stop advises

Deletes the per-iteration stop coda: the agent no longer decides whether
the agent is done. aegis_loop_stop keeps its identity gating but now
records a claim the judge is free to reject — which is the only shape
that addresses the 2026-07-30 burn (reaped at iteration 1 of 20 with the
user-visible half unbuilt).

The first delivery and an exhausted loop are never judged."
```

---

### Task 10: `/btw` gains the facts, and one live round-trip

**Files:**
- Modify: `src/aegis/btw/__init__.py` (`side_note`, `side_note_for`)
- Modify: `src/aegis/core/manager.py`, `src/aegis/tui/app.py` (pass `facts`)
- Test: `tests/test_btw_facts.py`
- Test: `tests/test_turn_generation_live.py`

**Interfaces:**
- Consumes: `TurnFacts`, `render_facts` (Task 2).
- Produces: `side_note(prompt, *, replay, driver, agent, cwd, facts=None, **window_opts)`; `side_note_for(..., facts=None)`.

- [ ] **Step 1: Write the failing test**

```python
"""/btw sees what the session DID, not only what it said."""
import pytest

from aegis.btw import BtwAnswer, side_note
from aegis.digest.models import CommitLine, RepoDelta, TurnFacts
from aegis.drivers.oneshot import Generation


class FakeDriver:
    supports_oneshot = True

    def __init__(self):
        self.calls = []

    async def generate_detailed(self, agent, cwd, schema, *instructions):
        self.calls.append(instructions)
        return Generation(value=BtwAnswer(answer="yes"), model="haiku",
                          duration_ms=10, cost_usd=0.001)


class FakeReplay:
    events = []


FACTS = TurnFacts(repos=(RepoDelta(name="aegis",
                                   commits=(CommitLine("51430de",
                                                       "docs: spec"),)),))


@pytest.mark.asyncio
async def test_btw_prompt_carries_the_facts():
    """'did that commit land?' is answerable now."""
    d = FakeDriver()
    await side_note("did it land?", replay=FakeReplay(), driver=d,
                    agent=object(), cwd=".", facts=FACTS)
    assert any("51430de" in part for part in d.calls[0])


@pytest.mark.asyncio
async def test_btw_still_works_without_facts():
    """Back-compat: every existing caller passes none."""
    d = FakeDriver()
    note = await side_note("hi", replay=FakeReplay(), driver=d,
                           agent=object(), cwd=".")
    assert note.ok is True
```

And the live test:

```python
"""One real round-trip per generator. Behind the `live` marker.

Run with: uv run python -m pytest tests/test_turn_generation_live.py -v
(the fast suite uses -m "not live"; never -k, which eats substrings)
"""
import shutil

import pytest

from aegis.config import Agent
from aegis.core.loop_judge import judge
from aegis.digest.models import CommitLine, RepoDelta, TurnFacts
from aegis.drivers import get_driver
from aegis.recap import recap_turn

pytestmark = pytest.mark.live

FACTS = TurnFacts(repos=(RepoDelta(name="aegis", files_written=2,
                                   commits=(CommitLine("51430de",
                                                       "docs: the spec"),)),))


class Replay:
    from aegis.events import AssistantText
    events = [AssistantText(text="I wrote the spec and committed it.")]


@pytest.fixture
def driver_and_agent():
    if not shutil.which("claude"):
        pytest.skip("claude not on PATH")
    return get_driver("claude-code"), Agent(harness="claude-code",
                                            model="haiku")


@pytest.mark.asyncio
async def test_a_real_turn_recap_comes_back_as_one_line(driver_and_agent):
    driver, agent = driver_and_agent
    got = await recap_turn(replay=Replay(), facts=FACTS, driver=driver,
                           agent=agent, cwd=".")
    assert got.ok, got.error
    assert got.line.strip()
    assert "\n" not in got.line.strip()


@pytest.mark.asyncio
async def test_a_real_judge_returns_a_known_verdict(driver_and_agent):
    driver, agent = driver_and_agent
    got = await judge(instruction="write and commit the spec", iteration=2,
                      max_iterations=20, facts=FACTS, still_streak=0,
                      advisory="", replay=Replay(), driver=driver,
                      agent=agent, cwd=".")
    assert got.ok, got.error
    assert got.verdict in ("continue", "done", "stuck")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run python -m pytest tests/test_btw_facts.py -v`
Expected: FAIL — `side_note() got an unexpected keyword argument 'facts'`

- [ ] **Step 3: Write minimal implementation**

In `src/aegis/btw/__init__.py`, add the parameter and the third instruction part:

```python
async def side_note(prompt: str, *, replay, driver, agent, cwd: str,
                    facts=None, **window_opts) -> SideNote:
    """Answer ``prompt`` from ``replay``'s tail, in one call.

    ``facts`` is the turn's ``TurnFacts``, when the caller has them. The
    window carries what was *said*; the facts carry what was *done*, and
    "did that commit land?" is only answerable from the second. Costs
    ~120 tokens against a 7,749-token prefix — inside the noise.
    """
    from aegis.digest.render import render_facts

    window = assemble(replay, **window_opts)
    parts = [
        _PREAMBLE.format(header=window.header or "no turns yet"),
        f"--- conversation ---\n{window.text}\n--- end ---",
    ]
    if facts is not None:
        parts.append(render_facts(facts))
    parts.append(f"The operator's side question: {prompt}")
    try:
        gen = await driver.generate_detailed(agent, cwd, BtwAnswer, *parts)
    except Exception as e:                                    # noqa: BLE001
        return SideNote(header=window.header,
                        error=f"{type(e).__name__}: {e}")
    ...  # the rest of the body is unchanged
```

Thread `facts=` through `side_note_for` (defaulting to `None`) and pass `session.last_facts` from `core/manager.py:335` and `tui/app.py:1716`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run python -m pytest tests/test_btw_facts.py -v`
Expected: 2 passed.

Run: `uv run python -m pytest tests/test_turn_generation_live.py -v`
Expected: 2 passed (or skipped if `claude` is off PATH). A live failure here is a real failure — these hit the real CLI.

- [ ] **Step 5: Run the full suite**

Run: `uv run python -m pytest tests/ -q -m "not live"`
Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add src/aegis/btw/__init__.py src/aegis/core/manager.py \
        src/aegis/tui/app.py tests/test_btw_facts.py \
        tests/test_turn_generation_live.py
git commit -m "feat(btw): side notes see what the session did, not only said

The point of making the digest a shared component rather than two private
helpers: 'did that commit land?' was previously answerable only from
whatever git calls survived the 500-char item clip."
```

---

### Task 11: Document the two features

**Files:**
- Modify: `AGENTS.md` (layout section — add `digest/`, `recap/`, `loop_judge.py`)
- Modify: `CHANGELOG.md`
- Modify: `docs/superpowers/specs/2026-08-26-aegis-turn-boundary-generation-design.md` (status header)
- Modify: `docs/superpowers/plans/2026-08-26-turn-boundary-generation.md` (check off tasks)

- [ ] **Step 1: Update AGENTS.md**

Add entries in the `## Layout` list, following the house style there (what it is, plus the rules a contributor would otherwise break):

- `src/aegis/digest/` — what a turn did. Note the two paid-for rules: the base HEAD is captured **lazily at the first recorded write** (at turn start we do not know which repos a turn will touch, and `--since` misattributes a peer's commit in a shared checkout); and `moved` is False on an errored digest, because we failed to look rather than observed stillness.
- `src/aegis/recap/` — the one-liner and `/recap`. Note that it gates on **movement, not turn count** (claude-code#56346), and that it is **detached** because the measured one-shot latency is ~7s flat in prefix size.
- `src/aegis/core/loop_judge.py` — note that its failure mode is **continue**, that the first delivery and an exhausted loop are never judged, and that `aegis_loop_stop` is now advisory.

Also correct the one-shot cost note if AGENTS.md carries one: the `$0.0044` figure is a warm price.

- [ ] **Step 2: Update CHANGELOG.md**

Add under the unreleased version, following the file's existing format:

```markdown
### Features

- **Loop judge.** An armed `/loop` now ends on a verdict from the turn's
  facts — commits, files written, plan movement — rather than the agent's
  own `aegis_loop_stop` call, which becomes advisory. Adds a `stuck`
  verdict for a loop that spins.
- **Recap.** A one-line summary after any turn that moved the substrate,
  and `/recap` for a building/done/remaining block about the session.
  Gated on movement rather than turn count, and never fed back into the
  agent's context.
- **`/btw` sees the facts too**, so "did that commit land?" is answerable.

### Performance

- One-shot generation no longer loads the project's `CLAUDE.md`, skills
  and plugins: 21,445 -> 7,749 input tokens, a 64% cut for `/btw`,
  `titlegen` and the new generators alike.
```

- [ ] **Step 3: Flip the spec's status header**

Change it to:

```markdown
> **Status:** implemented 2026-08-26. Plan:
> `docs/superpowers/plans/2026-08-26-turn-boundary-generation.md`.
```

- [ ] **Step 4: Check off every completed task in this plan**

Stale status headers mislead the next `/workon`. Flip them in the same commit batch as the work.

- [ ] **Step 5: Commit**

```bash
git add AGENTS.md CHANGELOG.md docs/superpowers/specs/2026-08-26-aegis-turn-boundary-generation-design.md docs/superpowers/plans/2026-08-26-turn-boundary-generation.md
git commit -m "docs: record the loop judge, the recap, and the one-shot cut"
```

---

## Verification checklist

Before calling this done, confirm each — and note which artifact each check actually touches:

- [ ] `uv run python -m pytest -q -m "not live"` is green (not a subset).
- [ ] `uv run python -m pytest tests/test_turn_generation_live.py -v` passes against the real CLI.
- [ ] The real TUI shows a recap line after a writing turn, nothing after a read-only turn, and a block on `/recap`. **Exercised in a terminal, not inferred from a test.**
- [ ] A real `/loop` on a trivial instruction stops on a `done` verdict, and the transcript states the reason.
- [ ] A one-shot at `cwd=/home/apiad/Workspace` reports ~7,700 input tokens, not ~21,000 (Task 1, Step 5).
- [ ] Both mutation checks were performed and both failed as expected before reverting (Task 3 Step 5, Task 6 Step 5).
