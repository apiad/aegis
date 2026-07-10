# Aegis Claims Registry Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add `aegis_claim` / `aegis_release` / `aegis_claims` — an inter-agent file-claims registry with `shared`/`exclusive` intents, cheap prefix/set overlap detection, conflict surfacing for inbox negotiation, and dead-holder reaping.

**Architecture:** A new `src/aegis/locks/` package that mirrors `src/aegis/groups/` (models → resolver → registry → persistence → bridge → MCP). A `ClaimRegistry` holds live claims and applies the grant rule; a `live_handles` callable lets it drop claims whose holder session is gone. A `_LocksBridge` is attached as `self.locks` on both `AppBridge` implementers and consumed by three new MCP tools.

**Tech Stack:** Python 3.13+, `uv`, pytest (`uv run python -m pytest`), FastMCP.

## Global Constraints

- Python 3.13+.
- Package manager is `uv` (`uv run python -m pytest`), never bare pip.
- TDD: failing test first, minimal implementation, commit per logical unit.
- Fast hermetic suite: `uv run python -m pytest -q -m "not live"`. Never `-k "not live"`.
- Spec: `docs/superpowers/specs/2026-07-10-aegis-claims-registry-design.md`.
- Mirror the `src/aegis/groups/` package shape and the JSONL persistence conventions in `src/aegis/groups/persistence.py`.
- Use `new_ulid()` and `now_iso()` from `aegis.queue.schema` for ids/timestamps (already the substrate convention).
- Intent is exactly `"shared"` (default) or `"exclusive"`. `shared` default.
- Scope: new store, coexist with `bin/ws-lock` (do NOT touch it), per-host v1. No cross-host, no auto-notify, no glob∩glob math, no dashboard.
- Overlap is prefix-containment ∪ set-intersection over resolved concrete paths. Globs resolve to concrete paths at claim time.

---

### Task 1: `models.py` — `Claim` + overlap predicate

Pure data + the overlap math. No I/O.

**Files:**
- Create: `src/aegis/locks/__init__.py`
- Create: `src/aegis/locks/models.py`
- Test: `tests/test_locks_models.py`

**Interfaces:**
- Produces: `Claim(claim_id: str, handle: str, prefixes: frozenset[str], files: frozenset[str], intent: str, desc: str, since: str)`.
- Produces: `claims_overlap(a: Claim, b: Claim) -> bool`.
- Convention: a **prefix** always ends with `/` (a subtree); a **file** is an exact path with no trailing `/`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_locks_models.py`:

```python
from aegis.locks.models import Claim, claims_overlap


def _claim(prefixes=(), files=(), handle="a", intent="shared"):
    return Claim(claim_id="c-" + handle, handle=handle,
                 prefixes=frozenset(prefixes), files=frozenset(files),
                 intent=intent, desc="", since="2026-07-10T00:00:00Z")


def test_file_file_intersection_overlaps():
    a = _claim(files=["src/x.py"])
    b = _claim(files=["src/x.py"], handle="b")
    assert claims_overlap(a, b) is True


def test_disjoint_files_do_not_overlap():
    a = _claim(files=["src/x.py"])
    b = _claim(files=["src/y.py"], handle="b")
    assert claims_overlap(a, b) is False


def test_file_under_prefix_overlaps():
    a = _claim(prefixes=["src/aegis/tui/"])
    b = _claim(files=["src/aegis/tui/app.py"], handle="b")
    assert claims_overlap(a, b) is True


def test_prefix_under_prefix_overlaps():
    a = _claim(prefixes=["src/aegis/"])
    b = _claim(prefixes=["src/aegis/tui/"], handle="b")
    assert claims_overlap(a, b) is True


def test_sibling_prefixes_do_not_overlap():
    a = _claim(prefixes=["src/aegis/tui/"])
    b = _claim(prefixes=["src/aegis/mcp/"], handle="b")
    assert claims_overlap(a, b) is False


def test_prefix_boundary_is_slash_safe():
    # "src/aegisx/" must NOT be considered under "src/aegis/"
    a = _claim(prefixes=["src/aegis/"])
    b = _claim(prefixes=["src/aegisx/"], handle="b")
    assert claims_overlap(a, b) is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run python -m pytest tests/test_locks_models.py -v`
Expected: FAIL — `ModuleNotFoundError: aegis.locks.models`.

- [ ] **Step 3: Write the implementation**

Create `src/aegis/locks/__init__.py`:

```python
"""Inter-agent file-claims registry (aegis_claim / aegis_release / aegis_claims)."""
```

Create `src/aegis/locks/models.py`:

```python
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Claim:
    claim_id: str
    handle: str
    prefixes: frozenset[str]   # each ends with "/"
    files: frozenset[str]      # exact paths, no trailing "/"
    intent: str                # "shared" | "exclusive"
    desc: str
    since: str                 # ISO-8601


def _file_under_prefix(path: str, prefix: str) -> bool:
    # prefix always ends with "/"; the file is "under" it iff it starts with it.
    return path.startswith(prefix)


def claims_overlap(a: Claim, b: Claim) -> bool:
    # file ∩ file
    if a.files & b.files:
        return True
    # a file of one under a prefix of the other (both directions)
    for f in a.files:
        if any(_file_under_prefix(f, p) for p in b.prefixes):
            return True
    for f in b.files:
        if any(_file_under_prefix(f, p) for p in a.prefixes):
            return True
    # prefix under prefix (both directions); trailing "/" makes it slash-safe
    for pa in a.prefixes:
        for pb in b.prefixes:
            if pa.startswith(pb) or pb.startswith(pa):
                return True
    return False
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run python -m pytest tests/test_locks_models.py -q`
Expected: PASS (6 tests).

- [ ] **Step 5: Commit**

```bash
git add src/aegis/locks/__init__.py src/aegis/locks/models.py tests/test_locks_models.py
git commit -m "feat(locks): Claim model + prefix/set overlap predicate"
```

---

### Task 2: `resolver.py` — paths → (prefixes, files)

Split a raw `paths` list into normalized prefixes + concrete files, expanding globs against the tree.

**Files:**
- Create: `src/aegis/locks/resolver.py`
- Test: `tests/test_locks_resolver.py`

**Interfaces:**
- Produces: `resolve_paths(paths: list[str], root: Path) -> tuple[frozenset[str], frozenset[str]]` → `(prefixes, files)`. Trailing-`/` → prefix; glob chars (`* ? [`) → expanded against `root` (dirs become prefixes, files become files); everything else → a concrete file. All stored as posix paths relative to `root` when resolved from a glob; literal entries stored verbatim (already workspace-relative).

- [ ] **Step 1: Write the failing test**

Create `tests/test_locks_resolver.py`:

```python
from pathlib import Path

from aegis.locks.resolver import resolve_paths


def test_prefix_and_file_passthrough(tmp_path):
    prefixes, files = resolve_paths(
        ["src/aegis/tui/", "src/aegis/mcp/server.py"], tmp_path)
    assert prefixes == frozenset({"src/aegis/tui/"})
    assert files == frozenset({"src/aegis/mcp/server.py"})


def test_glob_expands_to_concrete_files(tmp_path):
    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg" / "a.py").write_text("")
    (tmp_path / "pkg" / "b.py").write_text("")
    (tmp_path / "pkg" / "c.txt").write_text("")
    prefixes, files = resolve_paths(["pkg/*.py"], tmp_path)
    assert files == frozenset({"pkg/a.py", "pkg/b.py"})
    assert prefixes == frozenset()


def test_blank_entries_ignored(tmp_path):
    prefixes, files = resolve_paths(["", "  ", "x.py"], tmp_path)
    assert files == frozenset({"x.py"})
    assert prefixes == frozenset()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run python -m pytest tests/test_locks_resolver.py -v`
Expected: FAIL — `ModuleNotFoundError: aegis.locks.resolver`.

- [ ] **Step 3: Write the implementation**

Create `src/aegis/locks/resolver.py`:

```python
from __future__ import annotations

from pathlib import Path

_GLOB_CHARS = "*?["


def resolve_paths(paths: list[str],
                  root: Path) -> tuple[frozenset[str], frozenset[str]]:
    prefixes: set[str] = set()
    files: set[str] = set()
    for raw in paths:
        p = raw.strip()
        if not p:
            continue
        if any(ch in p for ch in _GLOB_CHARS):
            for m in root.glob(p):
                rel = m.relative_to(root).as_posix()
                if m.is_dir():
                    prefixes.add(rel + "/")
                else:
                    files.add(rel)
            continue
        if p.endswith("/"):
            prefixes.add(p)
        else:
            files.add(p)
    return frozenset(prefixes), frozenset(files)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run python -m pytest tests/test_locks_resolver.py -q`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add src/aegis/locks/resolver.py tests/test_locks_resolver.py
git commit -m "feat(locks): resolve_paths — prefixes/files split with glob expansion"
```

---

### Task 3: `registry.py` — grant rule, release, reap, live-filtering

The heart: in-memory claims, the grant rule, and dead-holder reaping via a `live_handles` callable. No persistence yet (Task 4 adds it via an optional log).

**Files:**
- Create: `src/aegis/locks/registry.py`
- Test: `tests/test_locks_registry.py`

**Interfaces:**
- Consumes: `Claim`, `claims_overlap` (Task 1).
- Produces: `ClaimRegistry(live_handles: Callable[[], set[str]] | None = None, log=None)` with:
  - `claim(handle, prefixes, files, intent="shared", desc="") -> tuple[Claim, bool, list[Claim]]` → `(candidate, granted, overlaps)`. On `granted=False` the candidate is NOT stored.
  - `release(claim_id, handle) -> bool` (idempotent; foreign release no-ops).
  - `active() -> list[Claim]` (drops dead-holder claims first).
  - `reap(handle) -> None`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_locks_registry.py`:

```python
from aegis.locks.registry import ClaimRegistry


def _reg(live=("a", "b", "c")):
    live_set = set(live)
    return ClaimRegistry(live_handles=lambda: set(live_set))


def test_shared_over_shared_both_granted_and_surfaced():
    r = _reg()
    c1, g1, o1 = r.claim("a", ["src/x/"], [], intent="shared")
    assert g1 is True and o1 == []
    c2, g2, o2 = r.claim("b", ["src/x/"], [], intent="shared")
    assert g2 is True
    assert [c.handle for c in o2] == ["a"]      # sees the peer
    assert len(r.active()) == 2


def test_exclusive_over_existing_shared_denied():
    r = _reg()
    r.claim("a", ["src/x/"], [], intent="shared")
    c2, g2, o2 = r.claim("b", ["src/x/"], [], intent="exclusive")
    assert g2 is False
    assert [c.handle for c in o2] == ["a"]
    # denied claim was NOT recorded
    assert [c.handle for c in r.active()] == ["a"]


def test_shared_over_existing_exclusive_denied():
    r = _reg()
    r.claim("a", ["src/x/"], [], intent="exclusive")
    c2, g2, o2 = r.claim("b", ["src/x/"], [], intent="shared")
    assert g2 is False
    assert [c.handle for c in o2] == ["a"]


def test_exclusive_over_empty_granted():
    r = _reg()
    c1, g1, o1 = r.claim("a", [], ["src/x.py"], intent="exclusive")
    assert g1 is True and o1 == []


def test_release_is_idempotent_and_ownership_scoped():
    r = _reg()
    c1, _, _ = r.claim("a", ["src/x/"], [])
    assert r.release(c1.claim_id, "b") is False   # not the owner
    assert r.release(c1.claim_id, "a") is True
    assert r.release(c1.claim_id, "a") is False   # already gone
    assert r.active() == []


def test_dead_holder_claim_is_reaped_from_active():
    live = {"a"}
    r = ClaimRegistry(live_handles=lambda: set(live))
    r.claim("a", ["src/x/"], [], intent="exclusive")
    r.claim("gone", ["src/y/"], [], intent="exclusive")  # holder not live
    handles = {c.handle for c in r.active()}
    assert handles == {"a"}        # "gone" filtered out
    # and a new exclusive claim over src/y/ now succeeds
    _, g, _ = r.claim("a", ["src/y/"], [], intent="exclusive")
    assert g is True
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run python -m pytest tests/test_locks_registry.py -v`
Expected: FAIL — `ModuleNotFoundError: aegis.locks.registry`.

- [ ] **Step 3: Write the implementation**

Create `src/aegis/locks/registry.py`:

```python
from __future__ import annotations

from typing import Callable

from aegis.locks.models import Claim, claims_overlap
from aegis.queue.schema import new_ulid, now_iso


class ClaimRegistry:
    def __init__(self,
                 live_handles: Callable[[], set[str]] | None = None,
                 log=None) -> None:
        self._claims: dict[str, Claim] = {}
        self._live = live_handles or (lambda: set())
        self._log = log

    def _prune_dead(self) -> None:
        live = self._live()
        dead = [cid for cid, c in self._claims.items() if c.handle not in live]
        for cid in dead:
            c = self._claims.pop(cid)
            if self._log is not None:
                self._log.write(self._log.reaped(cid, c.handle, now_iso()))

    def claim(self, handle: str, prefixes, files,
              intent: str = "shared", desc: str = "") -> tuple[Claim, bool, list[Claim]]:
        self._prune_dead()
        candidate = Claim(claim_id=new_ulid(), handle=handle,
                          prefixes=frozenset(prefixes), files=frozenset(files),
                          intent=intent, desc=desc, since=now_iso())
        overlaps = [c for c in self._claims.values()
                    if c.handle != handle and claims_overlap(candidate, c)]
        if intent == "exclusive":
            granted = len(overlaps) == 0
        else:  # shared
            granted = not any(c.intent == "exclusive" for c in overlaps)
        if granted:
            self._claims[candidate.claim_id] = candidate
            if self._log is not None:
                self._log.write(self._log.claimed(candidate))
        return candidate, granted, overlaps

    def release(self, claim_id: str, handle: str) -> bool:
        c = self._claims.get(claim_id)
        if c is None or c.handle != handle:
            return False
        del self._claims[claim_id]
        if self._log is not None:
            self._log.write(self._log.released(claim_id, handle, now_iso()))
        return True

    def active(self) -> list[Claim]:
        self._prune_dead()
        return list(self._claims.values())

    def reap(self, handle: str) -> None:
        gone = [cid for cid, c in self._claims.items() if c.handle == handle]
        for cid in gone:
            self._claims.pop(cid)
            if self._log is not None:
                self._log.write(self._log.reaped(cid, handle, now_iso()))
```

Note: the `self._log.claimed/released/reaped(...)` record-builders are added in Task 4; with `log=None` (this task) they are never called, so these tests pass without the log module.

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run python -m pytest tests/test_locks_registry.py -q`
Expected: PASS (6 tests).

- [ ] **Step 5: Commit**

```bash
git add src/aegis/locks/registry.py tests/test_locks_registry.py
git commit -m "feat(locks): ClaimRegistry — grant rule, release, reap, live-filtering"
```

---

### Task 4: `persistence.py` — JSONL log + boot replay

Append-only lifecycle log under `<state_dir>/locks/claims.jsonl`, and a `replay()` that rebuilds the live claim set. Mirrors `src/aegis/groups/persistence.py`.

**Files:**
- Create: `src/aegis/locks/persistence.py`
- Modify: `src/aegis/locks/registry.py` (add `start()` boot-replay method)
- Test: `tests/test_locks_persistence.py`

**Interfaces:**
- Produces: `PersistedClaimLog(state_dir: Path)` with instance methods `write(record)`, `read() -> list[dict]`, and record-builders `claimed(claim) -> dict`, `released(claim_id, handle, at) -> dict`, `reaped(claim_id, handle, at) -> dict`, plus `replay() -> dict[str, Claim]` (folds records into the live set).
- Produces: `ClaimRegistry.start()` — loads `log.replay()` into `_claims`, then `_prune_dead()`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_locks_persistence.py`:

```python
from aegis.locks.persistence import PersistedClaimLog
from aegis.locks.registry import ClaimRegistry


def test_claim_then_reload_rebuilds_live_set(tmp_path):
    log = PersistedClaimLog(tmp_path)
    r = ClaimRegistry(live_handles=lambda: {"a", "b"}, log=log)
    r.claim("a", ["src/x/"], [], intent="exclusive")
    c2, _, _ = r.claim("b", ["src/y/"], [], intent="shared")
    r.release(c2.claim_id, "b")

    # fresh registry over the same log
    log2 = PersistedClaimLog(tmp_path)
    r2 = ClaimRegistry(live_handles=lambda: {"a", "b"}, log=log2)
    r2.start()
    handles = {c.handle for c in r2.active()}
    assert handles == {"a"}          # a's claim survives, b's was released


def test_replay_reaps_dead_holder(tmp_path):
    log = PersistedClaimLog(tmp_path)
    r = ClaimRegistry(live_handles=lambda: {"a"}, log=log)
    r.claim("a", ["src/x/"], [], intent="exclusive")
    r.claim("a", ["src/z/"], [], intent="exclusive")

    log2 = PersistedClaimLog(tmp_path)
    # on reboot, "a" is no longer live
    r2 = ClaimRegistry(live_handles=lambda: set(), log=log2)
    r2.start()
    assert r2.active() == []


def test_torn_trailing_line_tolerated(tmp_path):
    log = PersistedClaimLog(tmp_path)
    r = ClaimRegistry(live_handles=lambda: {"a"}, log=log)
    r.claim("a", ["src/x/"], [], intent="exclusive")
    # append a torn line
    p = log.path()
    with p.open("a") as f:
        f.write('{"kind": "claimed", "claim_id": "trunc')
    log2 = PersistedClaimLog(tmp_path)
    r2 = ClaimRegistry(live_handles=lambda: {"a"}, log=log2)
    r2.start()                       # must not raise
    assert len(r2.active()) == 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run python -m pytest tests/test_locks_persistence.py -v`
Expected: FAIL — `ModuleNotFoundError: aegis.locks.persistence`.

- [ ] **Step 3: Write the persistence module**

Create `src/aegis/locks/persistence.py`:

```python
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from aegis.locks.models import Claim


class PersistedClaimLog:
    def __init__(self, state_dir: Path) -> None:
        self._root = Path(state_dir) / "locks"
        self._root.mkdir(parents=True, exist_ok=True)

    def path(self) -> Path:
        return self._root / "claims.jsonl"

    # --- record builders -------------------------------------------------
    def claimed(self, claim: Claim) -> dict[str, Any]:
        return {"kind": "claimed", "claim_id": claim.claim_id,
                "handle": claim.handle, "prefixes": sorted(claim.prefixes),
                "files": sorted(claim.files), "intent": claim.intent,
                "desc": claim.desc, "since": claim.since}

    def released(self, claim_id: str, handle: str, at: str) -> dict[str, Any]:
        return {"kind": "released", "claim_id": claim_id,
                "handle": handle, "at": at}

    def reaped(self, claim_id: str, handle: str, at: str) -> dict[str, Any]:
        return {"kind": "reaped", "claim_id": claim_id,
                "handle": handle, "at": at}

    # --- io --------------------------------------------------------------
    def write(self, record: dict[str, Any]) -> None:
        with self.path().open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, separators=(",", ":")))
            f.write("\n")

    def read(self) -> list[dict[str, Any]]:
        p = self.path()
        if not p.is_file():
            return []
        out: list[dict[str, Any]] = []
        for line in p.read_text().splitlines():
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                continue
        return out

    def replay(self) -> dict[str, Claim]:
        live: dict[str, Claim] = {}
        for rec in self.read():
            kind = rec.get("kind")
            if kind == "claimed":
                cid = rec["claim_id"]
                live[cid] = Claim(
                    claim_id=cid, handle=rec["handle"],
                    prefixes=frozenset(rec.get("prefixes", [])),
                    files=frozenset(rec.get("files", [])),
                    intent=rec.get("intent", "shared"),
                    desc=rec.get("desc", ""), since=rec.get("since", ""))
            elif kind in ("released", "reaped"):
                live.pop(rec.get("claim_id"), None)
        return live
```

- [ ] **Step 4: Add `start()` to the registry**

Append to `ClaimRegistry` in `src/aegis/locks/registry.py`:

```python
    def start(self) -> None:
        """Boot replay: rebuild the live claim set from the log, then drop
        any claim whose holder is no longer a live session."""
        if self._log is not None:
            self._claims = dict(self._log.replay())
        self._prune_dead()
```

- [ ] **Step 5: Run test to verify it passes**

Run: `uv run python -m pytest tests/test_locks_persistence.py -q`
Expected: PASS (3 tests). Re-run Task 3's suite too: `uv run python -m pytest tests/test_locks_registry.py -q` → still PASS.

- [ ] **Step 6: Commit**

```bash
git add src/aegis/locks/persistence.py src/aegis/locks/registry.py tests/test_locks_persistence.py
git commit -m "feat(locks): JSONL persistence + boot replay with dead-holder reap"
```

---

### Task 5: `bridge.py` — `_LocksBridge` + wire into both AppBridge implementers

Add the bridge (path resolution + registry) and attach it as `self.locks` on `SessionManager` and `AegisApp`, and to the `AppBridge` protocol.

**Files:**
- Create: `src/aegis/locks/bridge.py`
- Modify: `src/aegis/mcp/bridge.py` (add `locks: object` to the `AppBridge` protocol attributes)
- Modify: `src/aegis/core/manager.py` (`__init__`: construct `self.locks`)
- Modify: `src/aegis/tui/app.py` (`__init__`: construct `self.locks`)
- Test: `tests/test_locks_bridge.py`

**Interfaces:**
- Consumes: `ClaimRegistry` (Tasks 3–4), `resolve_paths` (Task 2), `PersistedClaimLog` (Task 4).
- Produces: `make_locks_bridge(*, live_handles: Callable[[], set[str]], root_fn: Callable[[], Path], state_dir: Path | None = None) -> _LocksBridge`.
- Produces: `_LocksBridge` with `claim(handle, paths, intent="shared", desc="") -> tuple[Claim, bool, list[Claim]]`, `release(claim_id, handle) -> bool`, `active() -> list[Claim]`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_locks_bridge.py`:

```python
from pathlib import Path

from aegis.locks.bridge import make_locks_bridge


def test_bridge_resolves_and_applies_grant_rule(tmp_path):
    live = {"a", "b"}
    br = make_locks_bridge(live_handles=lambda: set(live),
                           root_fn=lambda: tmp_path)
    c1, g1, o1 = br.claim("a", ["src/tui/"], intent="exclusive")
    assert g1 is True
    c2, g2, o2 = br.claim("b", ["src/tui/app.py"], intent="shared")
    assert g2 is False                     # under a's exclusive prefix
    assert [c.handle for c in o2] == ["a"]
    assert br.release(c1.claim_id, "a") is True
    c3, g3, _ = br.claim("b", ["src/tui/app.py"], intent="shared")
    assert g3 is True                      # a released; now clear


def test_bridge_persists_when_state_dir_given(tmp_path):
    br = make_locks_bridge(live_handles=lambda: {"a"},
                           root_fn=lambda: tmp_path,
                           state_dir=tmp_path)
    br.claim("a", ["src/x/"], intent="exclusive")
    assert (tmp_path / "locks" / "claims.jsonl").is_file()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run python -m pytest tests/test_locks_bridge.py -v`
Expected: FAIL — `ModuleNotFoundError: aegis.locks.bridge`.

- [ ] **Step 3: Write the bridge**

Create `src/aegis/locks/bridge.py`:

```python
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from aegis.locks.persistence import PersistedClaimLog
from aegis.locks.registry import ClaimRegistry
from aegis.locks.resolver import resolve_paths


@dataclass
class _LocksBridge:
    registry: ClaimRegistry
    root_fn: Callable[[], Path]

    def claim(self, handle: str, paths: list[str],
              intent: str = "shared", desc: str = ""):
        prefixes, files = resolve_paths(paths, self.root_fn())
        return self.registry.claim(handle, prefixes, files,
                                   intent=intent, desc=desc)

    def release(self, claim_id: str, handle: str) -> bool:
        return self.registry.release(claim_id, handle)

    def active(self):
        return self.registry.active()


def make_locks_bridge(*, live_handles: Callable[[], set[str]],
                      root_fn: Callable[[], Path],
                      state_dir: Path | None = None) -> _LocksBridge:
    log = PersistedClaimLog(state_dir) if state_dir is not None else None
    registry = ClaimRegistry(live_handles=live_handles, log=log)
    if log is not None:
        registry.start()
    return _LocksBridge(registry=registry, root_fn=root_fn)
```

- [ ] **Step 4: Add `locks` to the `AppBridge` protocol**

In `src/aegis/mcp/bridge.py`, add to the attribute block of the `AppBridge` Protocol (next to `groups: object`):

```python
    locks: object                # _LocksBridge
```

- [ ] **Step 5: Wire `self.locks` into `SessionManager`**

In `src/aegis/core/manager.py` `__init__`, after the groups block (around line 54), add:

```python
        from aegis.locks.bridge import make_locks_bridge
        self.locks = make_locks_bridge(
            live_handles=self.live_handles,
            root_fn=lambda: self.state_root or Path.cwd(),
            state_dir=None)  # in-memory v1; live-handle filter reaps dead holders
```

`Path` is already imported in this module (used at line 104-107); if not, add `from pathlib import Path` at the top.

- [ ] **Step 6: Wire `self.locks` into `AegisApp`**

In `src/aegis/tui/app.py` `__init__`, after the groups block (around line 242-244), add — the TUI knows its state dir at construction, so turn persistence ON:

```python
        from aegis.locks.bridge import make_locks_bridge
        self.locks = make_locks_bridge(
            live_handles=lambda: {p.handle for p in self._panes
                                  if isinstance(p, ConversationPane)},
            root_fn=lambda: self.state_root or Path.cwd(),
            state_dir=self._state_dir)
```

- [ ] **Step 7: Run tests to verify they pass**

Run: `uv run python -m pytest tests/test_locks_bridge.py tests/test_core_manager.py::test_implements_appbridge -q`
Expected: PASS. (`test_implements_appbridge` confirms `SessionManager` still structurally satisfies `AppBridge` with the new `locks` attribute.)

- [ ] **Step 8: Commit**

```bash
git add src/aegis/locks/bridge.py src/aegis/mcp/bridge.py src/aegis/core/manager.py src/aegis/tui/app.py tests/test_locks_bridge.py
git commit -m "feat(locks): _LocksBridge + wire self.locks into SessionManager and AegisApp"
```

---

### Task 6: MCP tools — `aegis_claim` / `aegis_release` / `aegis_claims`

Expose the bridge with teaching docstrings.

**Files:**
- Modify: `src/aegis/mcp/server.py` (three new tools)
- Test: `tests/test_locks_mcp.py`, and update the tool-set assertion in `tests/test_mcp_server.py`

**Interfaces:**
- Consumes: `bridge.locks.claim/release/active` (Task 5).
- Produces MCP tools:
  - `aegis_claim(paths: list[str], from_handle: str, intent: str = "shared", desc: str = "") -> {"claim_id", "granted", "overlaps": [{"handle","paths","intent","desc"}]}`
  - `aegis_release(claim_id: str, from_handle: str) -> {"released": bool}`
  - `aegis_claims() -> [{"claim_id","handle","paths","intent","desc","since"}]`

- [ ] **Step 1: Write the failing test**

Create `tests/test_locks_mcp.py`. It builds a real server over a small fake bridge exposing `.locks`:

```python
import pytest

from aegis.locks.bridge import make_locks_bridge
from aegis.mcp.server import build_server
from tests.test_mcp_server import FakeBridge, _call


def _bridge_with_locks(tmp_path):
    br = FakeBridge()
    live = {"lucid-knuth", "civic-codd"}
    br.locks = make_locks_bridge(live_handles=lambda: set(live),
                                 root_fn=lambda: tmp_path)
    return br


@pytest.mark.asyncio
async def test_claim_shared_then_exclusive_conflict(tmp_path):
    br = _bridge_with_locks(tmp_path)
    srv = build_server(br)
    a = await _call(srv, "aegis_claim", paths=["src/tui/"],
                    from_handle="lucid-knuth", intent="exclusive")
    assert a["granted"] is True and a["overlaps"] == []
    b = await _call(srv, "aegis_claim", paths=["src/tui/app.py"],
                    from_handle="civic-codd", intent="shared")
    assert b["granted"] is False
    assert b["overlaps"][0]["handle"] == "lucid-knuth"
    assert b["overlaps"][0]["intent"] == "exclusive"


@pytest.mark.asyncio
async def test_release_and_board(tmp_path):
    br = _bridge_with_locks(tmp_path)
    srv = build_server(br)
    a = await _call(srv, "aegis_claim", paths=["src/x/"],
                    from_handle="lucid-knuth")
    board = await _call(srv, "aegis_claims")
    assert [c["handle"] for c in board] == ["lucid-knuth"]
    out = await _call(srv, "aegis_release",
                      claim_id=a["claim_id"], from_handle="lucid-knuth")
    assert out == {"released": True}
    assert await _call(srv, "aegis_claims") == []
```

Also, in `tests/test_mcp_server.py`, add `"aegis_claim"`, `"aegis_release"`, `"aegis_claims"` to the expected set in `test_build_server_registers_all_aegis_tools`, and add `locks` to `FakeBridge` so the existing tests still build a server (give `FakeBridge` a class attribute `locks = None`, or set it in `__init__`; the locks tools are only *called* in `test_locks_mcp.py`, so `None` is fine for the other tests as long as `build_server` doesn't touch it at registration time).

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run python -m pytest tests/test_locks_mcp.py -v`
Expected: FAIL — no tool named `aegis_claim`.

- [ ] **Step 3: Register the three tools**

In `src/aegis/mcp/server.py`, alongside the other bridge tools:

```python
    @server.tool
    async def aegis_claim(paths: list[str], from_handle: str,
                          intent: str = "shared", desc: str = "") -> dict:
        """Register that you're working on a set of files, and find out who
        else is. Returns whether your claim was granted plus the overlapping
        claims of other agents.

        intent="shared" (default) means "I'm working here, FYI" — other agents
        may hold overlapping shared claims too; you'll simply see each other in
        `overlaps`. intent="exclusive" means "keep out" and is refused if it
        overlaps ANY existing claim.

        Grant rule: a shared claim is denied only if it overlaps an EXCLUSIVE
        claim; an exclusive claim is denied if it overlaps anything. A denied
        claim (`granted: false`) is NOT recorded.

        `paths` may list subtree prefixes (end with "/", e.g. "src/aegis/tui/"),
        concrete files, or globs (resolved to concrete paths now). `overlaps`
        gives you the other agents' handles + intent. **When you overlap
        someone, coordinate — don't barge in:** `aegis_handoff` a holder to ask
        what they're doing, agree who owns what, wait for them to
        `aegis_release`, or narrow your claim. Claims are held across turns
        until you `aegis_release` (or your session ends). See the whole board
        with `aegis_claims`.

        Args:
            paths: prefixes / files / globs you intend to work on.
            from_handle: your own aegis handle.
            intent: "shared" (default) or "exclusive".
            desc: short note on what you're doing (shown to others).
        """
        claim, granted, overlaps = bridge.locks.claim(
            from_handle, paths, intent=intent, desc=desc)
        return {
            "claim_id": claim.claim_id,
            "granted": granted,
            "overlaps": [
                {"handle": c.handle,
                 "paths": sorted(set(c.prefixes) | set(c.files)),
                 "intent": c.intent, "desc": c.desc}
                for c in overlaps
            ],
        }

    @server.tool
    async def aegis_release(claim_id: str, from_handle: str) -> dict:
        """Release a file claim you hold (idempotent; releasing a claim you
        don't own is a no-op). Claims also auto-release when your session ends.
        """
        return {"released": bridge.locks.release(claim_id, from_handle)}

    @server.tool
    async def aegis_claims() -> list[dict]:
        """The board: every active file claim across all agents — who is
        working on what, with which intent. Use this to see where others are
        before you claim, or to decide whom to coordinate with.
        """
        return [
            {"claim_id": c.claim_id, "handle": c.handle,
             "paths": sorted(set(c.prefixes) | set(c.files)),
             "intent": c.intent, "desc": c.desc, "since": c.since}
            for c in bridge.locks.active()
        ]
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run python -m pytest tests/test_locks_mcp.py tests/test_mcp_server.py -q`
Expected: PASS (new locks-MCP tests + the updated registry-set assertion).

- [ ] **Step 5: Commit**

```bash
git add src/aegis/mcp/server.py tests/test_locks_mcp.py tests/test_mcp_server.py
git commit -m "feat(mcp): aegis_claim / aegis_release / aegis_claims with teaching docstrings"
```

---

### Task 7: Reap-on-close wiring + docs + full-suite gate

Give the reap an active hook (belt to the live-filter's suspenders) and document the surface.

**Files:**
- Modify: `src/aegis/core/manager.py` (`_sync_spawn`: add a close observer that reaps)
- Modify: `AGENTS.md` (new `src/aegis/locks/` layout bullet + MCP tools)
- Test: `tests/test_locks_registry.py` (already covers `reap`; add a manager-level close→reap test in `tests/test_core_manager.py`)

**Interfaces:**
- Consumes: `AgentSession.add_close_observer(cb)` where `cb(session, reason)` (session.py:128); `self.locks.registry.reap(handle)`.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_core_manager.py`:

```python
@pytest.mark.asyncio
async def test_claim_reaped_when_session_closes():
    m = make_mgr()
    s = m._sync_spawn("default", handle="worker-1")
    m.locks.claim("worker-1", ["src/x/"], intent="exclusive")
    assert [c.handle for c in m.locks.active()] == ["worker-1"]
    await m.close("worker-1")
    assert m.locks.active() == []
```

(`m.locks.claim` here goes through `_LocksBridge.claim(handle, paths, intent=...)`.)

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run python -m pytest tests/test_core_manager.py::test_claim_reaped_when_session_closes -v`
Expected: it may already pass via the live-handle filter (closed session leaves `_sessions`, so `live_handles()` no longer includes it). If it PASSES, the live-filter already delivers the semantics — keep the test as a regression guard and **skip Step 3's observer** (note that in the commit). If it FAILS (e.g. `active()` still lists the claim because pruning only runs lazily), proceed to Step 3.

- [ ] **Step 3: Add the active reap hook (only if Step 2 failed)**

In `src/aegis/core/manager.py` `_sync_spawn`, after the session is created and before returning, register a close observer:

```python
        s.add_close_observer(
            lambda sess, reason, h=h: self.locks.registry.reap(h))
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run python -m pytest tests/test_core_manager.py -q`
Expected: PASS.

- [ ] **Step 5: Document the surface in AGENTS.md**

In `AGENTS.md`, add a `src/aegis/locks/` layout bullet mirroring the `src/aegis/groups/` one: "inter-agent file-claims registry. `models.py` (`Claim` + `claims_overlap`), `resolver.py` (`resolve_paths` — prefixes/files/glob), `registry.py` (`ClaimRegistry` — grant rule, release, reap, live-filter), `persistence.py` (JSONL log + boot replay), `bridge.py` (`_LocksBridge` + `make_locks_bridge`). MCP surface: `aegis_claim` / `aegis_release` / `aegis_claims`. `shared` (FYI) vs `exclusive` (gate) intents; overlap = prefix-containment ∪ set-intersection; claims auto-reap on session close. New store under `.aegis/state/locks/`, coexists with `bin/ws-lock`." Also add the three tools to the `src/aegis/mcp/` tool enumeration.

- [ ] **Step 6: Full hermetic suite gate**

Run: `uv run python -m pytest -q -m "not live"`
Expected: PASS. (Per known zion inotify flakiness, re-run any flaky TUI/watchdog test in isolation; all `test_locks_*` and MCP tests must be green.)

- [ ] **Step 7: Commit**

```bash
git add src/aegis/core/manager.py AGENTS.md tests/test_core_manager.py
git commit -m "feat(locks): reap claims on session close + document the locks surface"
```

---

## Self-Review

- **Spec coverage:** intents shared/exclusive + grant rule → Task 3/6; path atom (prefix/file/glob) + overlap math → Tasks 1–2; surface `aegis_claim`/`aegis_release`/`aegis_claims` with the exact return shapes → Task 6; teaching docstrings → Task 6; lifecycle held-across-turns + explicit release + auto-reap on close → Tasks 3, 7; JSONL persistence + boot replay + torn-line tolerance → Task 4; package mirrors `groups/` → Tasks 1–5; scope (coexist with ws-lock, per-host, new store) → wiring in Task 5, docs in Task 7; non-goals (no cross-host, no auto-notify, no glob∩glob, no dashboard) → respected. Covered.
- **Type consistency:** `Claim` fields identical across models/persistence/registry; `ClaimRegistry.claim(...) -> (Claim, bool, list[Claim])` consumed unchanged by `_LocksBridge.claim` and the MCP tool; `resolve_paths(...) -> (frozenset, frozenset)` used identically in resolver tests and the bridge; record-builders `claimed/released/reaped` defined in Task 4 and referenced by the registry with matching arity; `make_locks_bridge(*, live_handles, root_fn, state_dir=None)` called with the same kwargs in both implementers and the MCP test.
- **Placeholder scan:** none — every step carries concrete code/commands. Task 7 Step 2/3 is a deliberate conditional (verify-then-wire), not a placeholder: the observable behavior is asserted either way.
