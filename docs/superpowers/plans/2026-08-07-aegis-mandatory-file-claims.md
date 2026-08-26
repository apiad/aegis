# Mandatory File Claims Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn the advisory file-claims board in `src/aegis/locks/` into an enforced one, so a careless agent cannot silently clobber a peer's in-flight work.

**Architecture:** `ClaimRegistry` becomes the policy decision point (PDP) behind one pure function, `locks.policy.decide()`. Three policy enforcement points call it: the ACP client seam aegis already owns, a Claude Code `PreToolUse` hook injected via `--settings`, and a positive-match Bash heuristic. Writes to unclaimed paths silently record a `shared` auto-claim so the board stays accurate without agent discipline.

**Tech Stack:** Python 3.13+, `uv`, pytest, Textual 8.x, Rich, FastMCP.

**Spec:** `docs/superpowers/specs/2026-08-07-aegis-mandatory-file-claims-design.md`

> **Re-grounded against `main` on 2026-08-26** (plan written 2026-08-07; three
> releases have landed since). Two references had gone stale and are corrected
> in the tasks below — read these before starting, because both would have been
> discovered mid-task:
>
> - **The `aegis_close` claims count is no longer in `mcp/server.py`.** It moved
>   into `core/close_guard.py:151-157` (`gather_facts`), which is now the single
>   source of those facts — `aegis_close` and the queue finalizer both read it.
>   Task 3 Step 6 is corrected accordingly. There is no claims count anywhere in
>   `mcp/server.py` today.
> - **`write_text_file` is a method of `_AegisAcpClient` (`drivers/acp.py:133`),
>   not of `AcpSession` (`:309`).** The client is the ACP-side object that
>   performs the write; the session drives the connection. Task 5 put the gate
>   and its five attributes on the wrong class, and its tests construct the
>   wrong one. Corrected in Task 5.
>
> Everything else verified present as written: the whole `locks/` surface
> (`Claim`, `_file_under_prefix`, `ClaimRegistry.{claim,release,reap,active,
> start,_prune_dead}`, `PersistedClaimLog.{claimed,released,reaped,renamed,
> replay}`, `_LocksBridge`, `make_locks_bridge`), `CloseFacts.claims`
> (`close_guard.py:27`), `AegisConfig` (no `locks:` field yet, as assumed), and
> `build_argv` (`drivers/claude.py:285` — shifted from the cited 234, and it
> passes no `--settings` today, so Task 7 adds that flag rather than editing it).

## Global Constraints

- Python 3.13+. Use `uv run python -m pytest`, never bare `pytest`.
- TDD: failing test first, minimal implementation, commit per logical unit.
- Run the fast suite as `uv run python -m pytest -q -m "not live"`. Never use `-k "not live"` — it matches `live` as a substring and silently eats unrelated names.
- English for all code, comments, identifiers, error strings, and commit messages.
- Commit with conventional-commit messages, scope `locks` unless the change is clearly elsewhere.
- New tests follow the existing naming convention: `tests/test_locks_*.py`.
- Enforcement applies **only within the project root subtree**; paths resolving outside root are outside the domain and always pass.
- All width arithmetic in TUI code measures **cells** (`rich.cells.cell_len`), never `len()`.
- Claims are host-scoped: never compare claims across different `Claim.host` values.

---

### Task 1: The pure policy function

The five-line write policy as a pure function, with no registry, liveness, or I/O involvement. Everything downstream calls this.

**Files:**
- Modify: `src/aegis/locks/models.py`
- Create: `src/aegis/locks/policy.py`
- Test: `tests/test_locks_policy.py`

**Interfaces:**
- Consumes: `Claim` from `aegis.locks.models`.
- Produces:
  - `OP_EDIT`, `OP_CREATE`, `OP_OVERWRITE` string constants in `aegis.locks.models`
  - `Claim.auto: bool = False`
  - `covers(claim: Claim, path: str) -> bool` in `aegis.locks.models`
  - `Decision` frozen dataclass in `aegis.locks.policy` with fields `allow: bool`, `reason: str`, `notify: bool`, `auto_claim: bool`, `holders: tuple[str, ...]`
  - `decide(writer: str, path: str, op: str, overlapping: list[Claim]) -> Decision` in `aegis.locks.policy`

- [ ] **Step 1: Write the failing test**

Create `tests/test_locks_policy.py`:

```python
import pytest

from aegis.locks.models import (
    Claim, OP_CREATE, OP_EDIT, OP_OVERWRITE, covers,
)
from aegis.locks.policy import Decision, decide


def mk(handle: str, *paths: str, intent: str = "shared",
       auto: bool = False) -> Claim:
    prefixes = frozenset(p for p in paths if p.endswith("/"))
    files = frozenset(p for p in paths if not p.endswith("/"))
    return Claim(claim_id=f"c-{handle}", handle=handle, prefixes=prefixes,
                 files=files, intent=intent, desc="", since="2026-08-07T00:00:00Z",
                 auto=auto)


def test_covers_exact_file():
    assert covers(mk("a", "src/x.py"), "src/x.py")
    assert not covers(mk("a", "src/x.py"), "src/y.py")


def test_covers_under_prefix():
    assert covers(mk("a", "src/tui/"), "src/tui/pane.py")
    assert not covers(mk("a", "src/tui/"), "src/locks/pane.py")


def test_unclaimed_path_allows_and_auto_claims():
    d = decide("alice", "src/x.py", OP_OVERWRITE, [])
    assert d.allow
    assert d.auto_claim
    assert not d.notify


def test_own_claim_allows_silently():
    d = decide("alice", "src/x.py", OP_OVERWRITE, [mk("alice", "src/x.py")])
    assert d.allow
    assert not d.auto_claim
    assert not d.notify


def test_foreign_shared_edit_allows_with_notify():
    d = decide("alice", "src/x.py", OP_EDIT, [mk("bob", "src/")])
    assert d.allow
    assert d.notify
    assert d.holders == ("bob",)
    assert not d.auto_claim


def test_foreign_shared_create_allows_with_notify():
    d = decide("alice", "src/new.py", OP_CREATE, [mk("bob", "src/")])
    assert d.allow
    assert d.notify


def test_foreign_shared_overwrite_denies_and_names_the_escape():
    d = decide("alice", "src/x.py", OP_OVERWRITE, [mk("bob", "src/")])
    assert not d.allow
    assert "bob" in d.reason
    assert "aegis_claim" in d.reason


def test_foreign_exclusive_denies_every_op():
    holder = [mk("bob", "src/", intent="exclusive")]
    for op in (OP_EDIT, OP_CREATE, OP_OVERWRITE):
        d = decide("alice", "src/x.py", op, holder)
        assert not d.allow, op
        assert "bob" in d.reason
        # No self-service escape from an exclusive claim.
        assert "aegis_claim" not in d.reason


def test_exclusive_wins_over_shared_when_both_overlap():
    claims = [mk("bob", "src/"), mk("carol", "src/", intent="exclusive")]
    d = decide("alice", "src/x.py", OP_EDIT, claims)
    assert not d.allow
    assert "carol" in d.reason


def test_writers_own_claim_does_not_block_alongside_foreign():
    claims = [mk("alice", "src/"), mk("bob", "src/")]
    d = decide("alice", "src/x.py", OP_OVERWRITE, claims)
    # A foreign shared claim still denies the overwrite even though the
    # writer also holds one — joining is what the escape hatch records.
    assert not d.allow
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run python -m pytest tests/test_locks_policy.py -q`
Expected: FAIL — `ImportError: cannot import name 'OP_CREATE' from 'aegis.locks.models'`

- [ ] **Step 3: Extend `models.py`**

Add to `src/aegis/locks/models.py` — the `auto` field goes last so existing positional constructions keep working:

```python
# What the writer is about to do to the path. Only OP_OVERWRITE is
# destructive: an Edit carries an old_string and fails on its own if a
# peer moved the region, and creating a new file clobbers nothing.
OP_EDIT = "edit"
OP_CREATE = "create"
OP_OVERWRITE = "overwrite"
```

In the `Claim` dataclass, after `host`:

```python
    auto: bool = False         # recorded by the write path, not requested
```

And after `_file_under_prefix`:

```python
def covers(claim: Claim, path: str) -> bool:
    """Whether ``claim`` covers ``path`` (root-relative, posix, no
    trailing slash). Host is the caller's business — a claim only means
    something on its own machine."""
    if path in claim.files:
        return True
    return any(_file_under_prefix(path, p) for p in claim.prefixes)
```

- [ ] **Step 4: Write `policy.py`**

Create `src/aegis/locks/policy.py`:

```python
"""The write policy — pure, no registry, no clock, no I/O.

Five lines, in order:

1. unclaimed            -> allow, record a shared auto-claim
2. writer's own claim   -> allow, silent
3. foreign shared, non-destructive -> allow, shadow + notify
4. foreign shared, full overwrite  -> deny; escape is aegis_claim
5. foreign exclusive    -> deny; no escape but negotiation

Only the overwrite case is denied among shared claims because it is the
only genuine clobber vector. Denying every write into a shared claim
would be routed around within one turn, which buys nothing and spends
the signal.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from aegis.locks.models import OP_OVERWRITE, Claim


@dataclass(frozen=True)
class Decision:
    allow: bool
    reason: str = ""                      # deny text; empty when allowed
    notify: bool = False                  # shadow-copy + notify both sides
    auto_claim: bool = False              # record a shared claim for writer
    holders: tuple[str, ...] = field(default=())


def _describe(claims: list[Claim]) -> str:
    return ", ".join(
        f"{c.handle} (claim {c.claim_id}, since {c.since}"
        + (f" — {c.desc!r}" if c.desc else "") + ")"
        for c in claims
    )


def decide(writer: str, path: str, op: str,
           overlapping: list[Claim]) -> Decision:
    """Verdict for ``writer`` performing ``op`` on ``path``.

    ``overlapping`` is every live claim covering ``path`` on the writer's
    host, including the writer's own. Filtering by path and host is the
    caller's job so this function stays trivially testable.
    """
    if not overlapping:
        return Decision(allow=True, auto_claim=True)

    foreign = [c for c in overlapping if c.handle != writer]
    if not foreign:
        return Decision(allow=True)

    holders = tuple(dict.fromkeys(c.handle for c in foreign))

    exclusive = [c for c in foreign if c.intent == "exclusive"]
    if exclusive:
        return Decision(
            allow=False,
            reason=(f"denied: {path} is exclusively claimed by "
                    f"{_describe(exclusive)}. Hand off to negotiate, wait "
                    f"for release, or narrow your edit."),
            holders=holders,
        )

    if op == OP_OVERWRITE:
        return Decision(
            allow=False,
            reason=(f"denied: {path} would be fully overwritten and is "
                    f"shared with {_describe(foreign)}. If you know it is "
                    f"shared and mean to write anyway, call "
                    f"aegis_claim([{path!r}]) to join the claim, then "
                    f"retry."),
            holders=holders,
        )

    return Decision(allow=True, notify=True, holders=holders)
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run python -m pytest tests/test_locks_policy.py -q`
Expected: PASS (11 tests)

- [ ] **Step 6: Run the existing locks suite for regressions**

Run: `uv run python -m pytest tests/test_locks_models.py tests/test_locks_registry.py tests/test_locks_persistence.py tests/test_locks_bridge.py tests/test_locks_mcp.py tests/test_hosts_claims.py -q`
Expected: PASS — the `auto` field has a default, so no existing construction breaks.

- [ ] **Step 7: Commit**

```bash
git add src/aegis/locks/models.py src/aegis/locks/policy.py tests/test_locks_policy.py
git commit -m "feat(locks): pure write-policy decision function"
```

---

### Task 2: Liveness — gone, live, dormant

An exclusive claim held by a session that will never take another turn is a permanent wall. This task adds the dormancy rule that dissolves it, before any enforcement point exists to deadlock on.

**Files:**
- Create: `src/aegis/locks/liveness.py`
- Test: `tests/test_locks_liveness.py`

**Interfaces:**
- Consumes: `Claim` from `aegis.locks.models`.
- Produces:
  - `Activity` frozen dataclass in `aegis.locks.liveness` with fields `exists: bool`, `state: str`, `idle_s: float`, `monitors: int`, `reminders: int`, `inbox_depth: int`, `worker_label: str | None`, `loop_armed: bool`
  - `GONE`, `LIVE`, `DORMANT` string constants
  - `classify(a: Activity | None, dormant_after_s: float) -> str`
  - `effective_claims(claims: list[Claim], activity: dict[str, Activity], dormant_after_s: float) -> list[Claim]`

- [ ] **Step 1: Write the failing test**

Create `tests/test_locks_liveness.py`:

```python
from aegis.locks.liveness import (
    Activity, DORMANT, GONE, LIVE, classify, effective_claims,
)
from aegis.locks.models import Claim


def act(**kw) -> Activity:
    base = dict(exists=True, state="ready", idle_s=0.0, monitors=0,
                reminders=0, inbox_depth=0, worker_label=None,
                loop_armed=False)
    base.update(kw)
    return Activity(**base)


def mk(handle: str, intent: str = "exclusive") -> Claim:
    return Claim(claim_id=f"c-{handle}", handle=handle,
                 prefixes=frozenset({"src/"}), files=frozenset(),
                 intent=intent, desc="", since="2026-08-07T00:00:00Z")


def test_missing_session_is_gone():
    assert classify(None, 1200) == GONE
    assert classify(act(exists=False), 1200) == GONE


def test_mid_turn_is_live_however_long_idle():
    assert classify(act(state="working", idle_s=99999), 1200) == LIVE


def test_recent_turn_is_live():
    assert classify(act(idle_s=5), 1200) == LIVE


def test_idle_but_pending_work_is_live():
    for kw in ({"monitors": 1}, {"reminders": 1}, {"inbox_depth": 1},
               {"worker_label": "build#7"}, {"loop_armed": True}):
        assert classify(act(idle_s=99999, **kw), 1200) == LIVE, kw


def test_idle_with_nothing_pending_is_dormant():
    assert classify(act(idle_s=1201), 1200) == DORMANT


def test_dormant_exclusive_degrades_to_shared():
    out = effective_claims([mk("bob")], {"bob": act(idle_s=9999)}, 1200)
    assert [c.intent for c in out] == ["shared"]


def test_live_exclusive_is_untouched():
    out = effective_claims([mk("bob")], {"bob": act(idle_s=1)}, 1200)
    assert [c.intent for c in out] == ["exclusive"]


def test_gone_holder_claims_are_dropped_entirely():
    assert effective_claims([mk("bob")], {}, 1200) == []


def test_shared_claim_of_dormant_holder_survives_as_shared():
    out = effective_claims([mk("bob", intent="shared")],
                           {"bob": act(idle_s=9999)}, 1200)
    assert [c.intent for c in out] == ["shared"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run python -m pytest tests/test_locks_liveness.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'aegis.locks.liveness'`

- [ ] **Step 3: Write `liveness.py`**

Create `src/aegis/locks/liveness.py`:

```python
"""Three liveness states, because two are not enough under enforcement.

``live_handles`` — the predicate the registry has always used — is tab
existence (``core/manager.py`` returns a handle per session object,
``tui/app.py`` one per ConversationPane). Agents in aegis characteristically
finish and sit there, so by that predicate a session that completed three
hours ago is fully live and its exclusive claim is a permanent wall.

So: GONE (no session) reaps, LIVE enforces, and DORMANT degrades exclusive
to shared. Degrade rather than delete — the board keeps reading "bob was
working here", only the wall comes down. A write into a dormant claim then
takes the notify path, and that notification is the liveness probe: if the
holder is really alive, its inbox wakes it and it can re-claim exclusive.
That is why there is no force verb and no break-in tool.

"Has a future" is deliberately the same predicate ``core/close_guard`` uses
to refuse a close. One definition, two consumers.
"""
from __future__ import annotations

from dataclasses import dataclass, replace

from aegis.locks.models import Claim

GONE = "gone"
LIVE = "live"
DORMANT = "dormant"


@dataclass(frozen=True)
class Activity:
    """Everything liveness needs about one session. Assembled by the
    bridge from the same sources ``close_guard`` reads."""
    exists: bool
    state: str                 # "ready" | "working" | "error"
    idle_s: float              # seconds since this session last took a turn
    monitors: int
    reminders: int
    inbox_depth: int
    worker_label: str | None
    loop_armed: bool

    @property
    def has_future(self) -> bool:
        return bool(self.monitors or self.reminders or self.inbox_depth
                    or self.worker_label or self.loop_armed)


def classify(a: Activity | None, dormant_after_s: float) -> str:
    if a is None or not a.exists:
        return GONE
    if a.state == "working" or a.has_future:
        return LIVE
    return DORMANT if a.idle_s > dormant_after_s else LIVE


def effective_claims(claims: list[Claim],
                     activity: dict[str, Activity],
                     dormant_after_s: float) -> list[Claim]:
    """``claims`` with dead holders dropped and dormant holders' exclusive
    claims degraded to shared. The returned claims are what the policy
    should see; the stored claims are untouched."""
    out: list[Claim] = []
    for c in claims:
        state = classify(activity.get(c.handle), dormant_after_s)
        if state == GONE:
            continue
        if state == DORMANT and c.intent == "exclusive":
            out.append(replace(c, intent="shared"))
        else:
            out.append(c)
    return out
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run python -m pytest tests/test_locks_liveness.py -q`
Expected: PASS (9 tests)

- [ ] **Step 5: Commit**

```bash
git add src/aegis/locks/liveness.py tests/test_locks_liveness.py
git commit -m "feat(locks): gone/live/dormant liveness with exclusive degradation"
```

---

### Task 3: Auto-claim, `ClaimRegistry.check()`, and the `aegis_close` fix

Wires policy + liveness into the registry, adds auto-claim, and fixes the close regression auto-claim would otherwise introduce. These belong in one task because auto-claim breaks `aegis_close` the moment it lands.

**Files:**
- Modify: `src/aegis/locks/registry.py`
- Modify: `src/aegis/locks/persistence.py`
- Modify: `src/aegis/locks/bridge.py`
- Modify: `src/aegis/core/close_guard.py` — the `claims` comment (`:27`), and
  the count inside `gather_facts` (`:151-157`). **Not `mcp/server.py`** — the
  count moved into `close_guard` and is now shared with the queue finalizer.
- Test: `tests/test_locks_check.py`
- Test: `tests/test_locks_close_guard.py`

**Interfaces:**
- Consumes: `decide`/`Decision` (Task 1), `Activity`/`effective_claims` (Task 2).
- Produces:
  - `ClaimRegistry.check(handle: str, path: str, op: str, host: str = "local") -> Decision`
  - `ClaimRegistry.auto_claim(handle: str, path: str, host: str = "local") -> Claim`
  - `ClaimRegistry.explicit_count(handle: str) -> int`
  - `ClaimRegistry.on_change(cb: Callable[[], None]) -> None`
  - `PersistedClaimLog.violation(...)` and `.degraded(...)` record builders
  - `_LocksBridge.check(...)`, `.auto_claim(...)`, `.explicit_count(...)`

- [ ] **Step 1: Write the failing test**

Create `tests/test_locks_check.py`:

```python
from aegis.locks.liveness import Activity
from aegis.locks.models import OP_EDIT, OP_OVERWRITE
from aegis.locks.registry import ClaimRegistry


def act(idle_s=0.0):
    return Activity(exists=True, state="ready", idle_s=idle_s, monitors=0,
                    reminders=0, inbox_depth=0, worker_label=None,
                    loop_armed=False)


def reg(*handles, activity=None):
    live = set(handles)
    acts = activity if activity is not None else {h: act() for h in handles}
    return ClaimRegistry(live_handles=lambda: live,
                         session_activity=lambda: acts,
                         dormant_after_s=1200)


def test_check_on_unclaimed_path_allows_and_flags_auto_claim():
    r = reg("alice")
    d = r.check("alice", "src/x.py", OP_OVERWRITE)
    assert d.allow and d.auto_claim


def test_check_denies_foreign_exclusive():
    r = reg("alice", "bob")
    r.claim("bob", ["src/"], [], intent="exclusive")
    d = r.check("alice", "src/x.py", OP_EDIT)
    assert not d.allow and "bob" in d.reason


def test_dormant_holder_exclusive_stops_denying():
    r = reg("alice", "bob",
            activity={"alice": act(), "bob": act(idle_s=9999)})
    r.claim("bob", ["src/"], [], intent="exclusive")
    d = r.check("alice", "src/x.py", OP_EDIT)
    assert d.allow and d.notify and d.holders == ("bob",)


def test_check_ignores_claims_on_another_host():
    r = reg("alice", "bob")
    r.claim("bob", ["src/"], [], intent="exclusive", host="vps")
    d = r.check("alice", "src/x.py", OP_EDIT, host="local")
    assert d.allow


def test_auto_claim_records_a_shared_auto_claim():
    r = reg("alice")
    c = r.auto_claim("alice", "src/x.py")
    assert c.auto and c.intent == "shared"
    assert c.files == frozenset({"src/x.py"})
    assert [x.claim_id for x in r.active()] == [c.claim_id]


def test_auto_claim_is_idempotent_for_an_already_covered_path():
    r = reg("alice")
    r.claim("alice", ["src/"], [])
    before = len(r.active())
    r.auto_claim("alice", "src/x.py")
    assert len(r.active()) == before


def test_explicit_count_excludes_auto_claims():
    r = reg("alice")
    r.auto_claim("alice", "src/x.py")
    r.auto_claim("alice", "src/y.py")
    assert r.explicit_count("alice") == 0
    r.claim("alice", ["docs/"], [])
    assert r.explicit_count("alice") == 1


def test_on_change_fires_for_claim_and_auto_claim():
    r = reg("alice")
    seen = []
    r.on_change(lambda: seen.append(1))
    r.claim("alice", ["docs/"], [])
    r.auto_claim("alice", "src/x.py")
    assert len(seen) == 2
```

Create `tests/test_locks_close_guard.py`:

```python
from aegis.core.close_guard import CloseFacts, refuse_reasons


def facts(**kw):
    base = dict(exists=True, spawned_by="alice", state="ready", monitors=0,
                reminders=0, inbox_depth=0, worker_label=None,
                loop_armed=False, claims=0)
    base.update(kw)
    return CloseFacts(**base)


def test_explicit_claims_still_refuse_close():
    reasons = refuse_reasons(facts(claims=2), requester="alice",
                             target="bob")
    assert any("file claim" in r for r in reasons)


def test_a_worker_with_only_auto_claims_closes_cleanly():
    # `claims` counts EXPLICIT claims only. Under auto-claim every agent
    # that ever edited a file holds claims, so counting all of them would
    # refuse essentially every close.
    assert refuse_reasons(facts(claims=0), requester="alice",
                          target="bob") == []
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run python -m pytest tests/test_locks_check.py tests/test_locks_close_guard.py -q`
Expected: FAIL — `TypeError: ClaimRegistry.__init__() got an unexpected keyword argument 'session_activity'`

- [ ] **Step 3: Add the new persistence records**

In `src/aegis/locks/persistence.py`, after `renamed()`:

```python
    def violation(self, handle: str, path: str, op: str,
                  holders: list[str], at: str) -> dict[str, Any]:
        """A denied write. This log is the second deliverable of
        enforcement: it is how we learn which agents drift and where."""
        return {"kind": "violation", "handle": handle, "path": path,
                "op": op, "holders": sorted(holders), "at": at}

    def degraded(self, claim_id: str, handle: str, at: str) -> dict[str, Any]:
        return {"kind": "degraded", "claim_id": claim_id,
                "handle": handle, "at": at}
```

In `replay()`, add `auto` to the reconstructed `Claim` and ignore the two new kinds. Change the `Claim(...)` construction to include:

```python
                    host=rec.get("host", "local"),
                    auto=rec.get("auto", False))
```

and add to `claimed()`:

```python
                "host": claim.host, "auto": claim.auto}
```

(replacing the existing `"host": claim.host}` terminator). `violation` and `degraded` are audit-only — `replay()` already ignores unknown kinds by falling through its `if/elif` chain, so no change is needed there.

- [ ] **Step 4: Extend the registry**

In `src/aegis/locks/registry.py`, replace `__init__` and add the new methods:

```python
    def __init__(self,
                 live_handles: Callable[[], set[str]] | None = None,
                 log=None,
                 session_activity: Callable[[], dict] | None = None,
                 dormant_after_s: float = 1200.0) -> None:
        self._claims: dict[str, Claim] = {}
        self._live = live_handles or (lambda: set())
        self._log = log
        self._activity = session_activity or (lambda: {})
        self._dormant_after_s = dormant_after_s
        self._observers: list[Callable[[], None]] = []

    def on_change(self, cb: Callable[[], None]) -> None:
        """Register a UI refresh callback. Claims now change on every
        write, so polling would be wrong; the write path is the emitter."""
        self._observers.append(cb)

    def _changed(self) -> None:
        for cb in self._observers:
            try:
                cb()
            except Exception:  # noqa: BLE001 — a bad observer must not
                pass           # take down the write path

    def check(self, handle: str, path: str, op: str,
              host: str = "local"):
        """The PDP entry point every enforcement point calls."""
        from aegis.locks.liveness import effective_claims
        from aegis.locks.policy import decide
        self._prune_dead()
        same_host = [c for c in self._claims.values() if c.host == host]
        live = effective_claims(same_host, self._activity(),
                                self._dormant_after_s)
        overlapping = [c for c in live if covers(c, path)]
        d = decide(handle, path, op, overlapping)
        if not d.allow and self._log is not None:
            self._log.write(self._log.violation(
                handle, path, op, list(d.holders), now_iso()))
        return d

    def auto_claim(self, handle: str, path: str,
                   host: str = "local") -> Claim | None:
        """Record a shared claim on ``handle``'s behalf for a path it just
        wrote. Returns None when the path is already covered by one of its
        own claims — re-recording would grow the board without adding
        information."""
        self._prune_dead()
        mine = [c for c in self._claims.values()
                if c.handle == handle and c.host == host]
        if any(covers(c, path) for c in mine):
            return None
        c = Claim(claim_id=new_ulid(), handle=handle,
                  prefixes=frozenset(), files=frozenset({path}),
                  intent="shared", desc="(auto)", since=now_iso(),
                  host=host, auto=True)
        self._claims[c.claim_id] = c
        if self._log is not None:
            self._log.write(self._log.claimed(c))
        self._changed()
        return c

    def explicit_count(self, handle: str) -> int:
        """Claims ``handle`` actually asked for. Auto-claims do not count
        toward the aegis_close refusal — otherwise every agent that ever
        edited a file would become unclosable."""
        self._prune_dead()
        return len([c for c in self._claims.values()
                    if c.handle == handle and not c.auto])
```

Add `covers` to the import at the top:

```python
from aegis.locks.models import Claim, claims_overlap, covers
```

Call `self._changed()` at the end of the granted branch in `claim()`, and in `release()` and `reap()` after mutation.

- [ ] **Step 5: Extend the bridge**

In `src/aegis/locks/bridge.py`, add to `_LocksBridge`:

```python
    def check(self, handle: str, path: str, op: str, host: str = "local"):
        return self.registry.check(handle, path, op, host=host)

    def auto_claim(self, handle: str, path: str, host: str = "local"):
        return self.registry.auto_claim(handle, path, host=host)

    def explicit_count(self, handle: str) -> int:
        return self.registry.explicit_count(handle)
```

And thread the two new arguments through `make_locks_bridge`:

```python
def make_locks_bridge(*, live_handles: Callable[[], set[str]],
                      root_fn: Callable[[], Path],
                      state_dir: Path | None = None,
                      session_activity: Callable[[], dict] | None = None,
                      dormant_after_s: float = 1200.0) -> _LocksBridge:
    log = PersistedClaimLog(state_dir) if state_dir is not None else None
    registry = ClaimRegistry(live_handles=live_handles, log=log,
                             session_activity=session_activity,
                             dormant_after_s=dormant_after_s)
    if log is not None:
        registry.start()
    return _LocksBridge(registry=registry, root_fn=root_fn)
```

- [ ] **Step 6: Fix the close path**

In `src/aegis/core/close_guard.py`, update the comment on the `claims` field (line 27) to say what it now counts:

```python
    claims: int                    # EXPLICIT file claims it holds
```

Then replace the count inside `gather_facts` (`close_guard.py:151-157`) — this
is the corrected location; the plan originally pointed at `mcp/server.py`, where
the count no longer lives:

```python
    claims = 0
    if locks is not None:
        try:
            claims = locks.explicit_count(handle)
        except Exception:  # noqa: BLE001
            claims = 0
```

Note this is the *shared* fact-gatherer — `aegis_close` and the queue finalizer
both read it, so the fix lands in both consumers at once, which is the point of
there being one copy.

- [ ] **Step 7: Run tests to verify they pass**

Run: `uv run python -m pytest tests/test_locks_check.py tests/test_locks_close_guard.py -q`
Expected: PASS (10 tests)

- [ ] **Step 8: Run the full locks + close surface for regressions**

Run: `uv run python -m pytest tests/ -q -m "not live" -k "locks or close or claims"`
Expected: PASS

- [ ] **Step 9: Commit**

```bash
git add src/aegis/locks/registry.py src/aegis/locks/persistence.py \
        src/aegis/locks/bridge.py src/aegis/core/close_guard.py \
        src/aegis/mcp/server.py tests/test_locks_check.py \
        tests/test_locks_close_guard.py
git commit -m "feat(locks): registry check(), auto-claim, explicit-only close guard"
```

---

### Task 4: Config kill-switch and the path-domain resolver

Enforcement must be switchable off before it is switchable on, and every PEP needs the same answer to "is this path even in scope".

**Files:**
- Create: `src/aegis/locks/domain.py`
- Modify: `src/aegis/config/yaml_loader.py`
- Test: `tests/test_locks_domain.py`
- Test: `tests/test_locks_config.py`

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces:
  - `LocksConfig` frozen dataclass with `enforce: bool = True`, `dormant_after_s: float = 1200.0`, exposed as `AegisConfig.locks`
  - `in_domain(abs_path: str, root: Path) -> str | None` in `aegis.locks.domain` — returns the root-relative posix path, or `None` when out of domain

- [ ] **Step 1: Write the failing test**

Create `tests/test_locks_domain.py`:

```python
from pathlib import Path

from aegis.locks.domain import in_domain


def test_path_inside_root_is_relative_posix(tmp_path):
    (tmp_path / "src").mkdir()
    f = tmp_path / "src" / "x.py"
    f.write_text("")
    assert in_domain(str(f), tmp_path) == "src/x.py"


def test_path_outside_root_is_out_of_domain(tmp_path):
    assert in_domain("/tmp/scratch.txt", tmp_path) is None


def test_dotdot_escape_is_out_of_domain(tmp_path):
    assert in_domain(str(tmp_path / ".." / "elsewhere.txt"), tmp_path) is None


def test_symlink_escaping_root_is_out_of_domain(tmp_path):
    outside = tmp_path.parent / "outside.txt"
    outside.write_text("")
    link = tmp_path / "link.txt"
    link.symlink_to(outside)
    assert in_domain(str(link), tmp_path) is None


def test_nonexistent_file_inside_root_is_in_domain(tmp_path):
    # A create targets a path that does not exist yet — it must still be
    # in domain, or every new file would bypass the policy.
    assert in_domain(str(tmp_path / "src" / "new.py"), tmp_path) == "src/new.py"
```

Create `tests/test_locks_config.py`:

```python
from aegis.config.yaml_loader import load_config


def test_locks_defaults_to_enforcing(tmp_path):
    (tmp_path / ".aegis.yaml").write_text("agents:\n  main:\n    harness: claude-code\n")
    cfg = load_config(tmp_path)
    assert cfg.locks.enforce is True
    assert cfg.locks.dormant_after_s == 1200.0


def test_locks_can_be_switched_off(tmp_path):
    (tmp_path / ".aegis.yaml").write_text(
        "agents:\n  main:\n    harness: claude-code\n"
        "locks:\n  enforce: false\n  dormant_after: 5m\n")
    cfg = load_config(tmp_path)
    assert cfg.locks.enforce is False
    assert cfg.locks.dormant_after_s == 300.0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run python -m pytest tests/test_locks_domain.py tests/test_locks_config.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'aegis.locks.domain'`

- [ ] **Step 3: Write `domain.py`**

Create `src/aegis/locks/domain.py`:

```python
"""Is this path even ours to police?

Enforcement covers the project root subtree and nothing else. A write to
/tmp, to ~/.cache, or to the vault from a session rooted at repos/aegis is
outside the domain and passes untouched — without this rule every agent
bricks on its first scratch file.

Resolution happens before the claim lookup so that `..` and symlinks
cannot smuggle a path across the boundary in either direction.
"""
from __future__ import annotations

from pathlib import Path


def in_domain(abs_path: str, root: Path) -> str | None:
    """``abs_path`` as a root-relative posix path, or None if outside.

    ``strict=False`` because a create targets a path that does not exist
    yet; resolving strictly would put every new file out of domain.
    """
    try:
        p = Path(abs_path).resolve(strict=False)
        r = Path(root).resolve(strict=False)
        return p.relative_to(r).as_posix()
    except (ValueError, OSError):
        return None
```

- [ ] **Step 4: Add `LocksConfig` to the loader**

In `src/aegis/config/yaml_loader.py`, add the dataclass alongside the other config sections:

```python
@dataclass(frozen=True)
class LocksConfig:
    """A mandatory mechanism that cannot be turned off is one that will
    eventually strand someone. `enforce: false` disables the enforcement
    points but KEEPS auto-claim, so the board stays accurate even with the
    walls down."""
    enforce: bool = True
    dormant_after_s: float = 1200.0


_UNITS = {"s": 1, "m": 60, "h": 3600}


def _duration_s(raw, default: float) -> float:
    if raw is None:
        return default
    if isinstance(raw, (int, float)):
        return float(raw)
    text = str(raw).strip()
    if text and text[-1] in _UNITS:
        return float(text[:-1]) * _UNITS[text[-1]]
    return float(text)


def _load_locks(data: dict) -> LocksConfig:
    raw = data.get("locks") or {}
    return LocksConfig(
        enforce=bool(raw.get("enforce", True)),
        dormant_after_s=_duration_s(raw.get("dormant_after"), 1200.0))
```

Add a `locks: LocksConfig = field(default_factory=LocksConfig)` field to `AegisConfig`, and populate it with `_load_locks(data)` where the other sections are assembled.

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run python -m pytest tests/test_locks_domain.py tests/test_locks_config.py -q`
Expected: PASS (7 tests)

- [ ] **Step 6: Run the config suite for regressions**

Run: `uv run python -m pytest tests/ -q -m "not live" -k "config or yaml"`
Expected: PASS

- [ ] **Step 7: Commit**

```bash
git add src/aegis/locks/domain.py src/aegis/config/yaml_loader.py \
        tests/test_locks_domain.py tests/test_locks_config.py
git commit -m "feat(locks): enforcement domain resolver and locks config section"
```

---

### Task 5: The ACP enforcement point

First real enforcement, end to end. aegis owns `write_text_file` outright, so this is the cheapest place to prove the whole chain.

**Files:**
- Modify: `src/aegis/drivers/acp.py` — `_AegisAcpClient.write_text_file`
  (`:293`) and `_AegisAcpClient.__init__` (`:139`).
- Test: `tests/test_locks_acp_pep.py`

> **Corrected 2026-08-26.** This task was written against `AcpSession`. The
> write is performed by **`_AegisAcpClient`** (`drivers/acp.py:133`) — the
> ACP-side client object whose `write_text_file` is three lines of
> `Path(path).write_text(...)`. `AcpSession` (`:309`) drives the connection and
> never touches the file. So the gate, the five attributes, and the tests below
> all target `_AegisAcpClient`. `AcpSession` builds the client and is where the
> attributes are threaded *from* (it already holds `_mcp_url` and the cwd), but
> the enforcement point is the client.

**Interfaces:**
- Consumes: `check`/`auto_claim` (Task 3), `in_domain` (Task 4).
- Produces: `_AegisAcpClient.gate(abs_path: str, op: str) -> Decision | None` — `None` means out of domain or enforcement disabled.

- [ ] **Step 1: Write the failing test**

Create `tests/test_locks_acp_pep.py`:

```python
import pytest

from aegis.locks.liveness import Activity
from aegis.locks.models import OP_CREATE, OP_OVERWRITE
from aegis.locks.registry import ClaimRegistry


def act():
    return Activity(exists=True, state="ready", idle_s=0.0, monitors=0,
                    reminders=0, inbox_depth=0, worker_label=None,
                    loop_armed=False)


@pytest.fixture
def registry():
    live = {"alice", "bob"}
    return ClaimRegistry(live_handles=lambda: live,
                         session_activity=lambda: {h: act() for h in live},
                         dormant_after_s=1200)


def test_acp_write_into_foreign_exclusive_raises(tmp_path, registry):
    from aegis.drivers.acp import _AegisAcpClient
    registry.claim("bob", ["src/"], [], intent="exclusive")
    (tmp_path / "src").mkdir()
    target = tmp_path / "src" / "x.py"
    target.write_text("original")

    sess = _AegisAcpClient.__new__(_AegisAcpClient)
    sess._handle = "alice"
    sess._locks = registry
    sess._root = tmp_path
    sess._enforce = True
    sess._host = "local"

    with pytest.raises(Exception) as ei:
        import asyncio
        asyncio.run(sess.write_text_file(content="clobbered",
                                         path=str(target),
                                         session_id="s1"))
    assert "bob" in str(ei.value)
    # The substrate is what must be intact, not the error text.
    assert target.read_text() == "original"


def test_acp_write_to_unclaimed_path_succeeds_and_auto_claims(tmp_path,
                                                              registry):
    import asyncio

    from aegis.drivers.acp import _AegisAcpClient
    target = tmp_path / "new.py"

    sess = _AegisAcpClient.__new__(_AegisAcpClient)
    sess._handle = "alice"
    sess._locks = registry
    sess._root = tmp_path
    sess._enforce = True
    sess._host = "local"

    asyncio.run(sess.write_text_file(content="hello", path=str(target),
                                     session_id="s1"))
    assert target.read_text() == "hello"
    assert [c.handle for c in registry.active()] == ["alice"]
    assert registry.active()[0].auto is True


def test_acp_write_outside_root_is_never_gated(tmp_path, registry):
    import asyncio

    from aegis.drivers.acp import _AegisAcpClient
    registry.claim("bob", ["/"], [], intent="exclusive")
    outside = tmp_path.parent / "scratch.txt"

    sess = _AegisAcpClient.__new__(_AegisAcpClient)
    sess._handle = "alice"
    sess._locks = registry
    sess._root = tmp_path
    sess._enforce = True
    sess._host = "local"

    asyncio.run(sess.write_text_file(content="ok", path=str(outside),
                                     session_id="s1"))
    assert outside.read_text() == "ok"


def test_enforce_false_allows_but_still_auto_claims(tmp_path, registry):
    import asyncio

    from aegis.drivers.acp import _AegisAcpClient
    registry.claim("bob", ["src/"], [], intent="exclusive")
    (tmp_path / "src").mkdir()
    target = tmp_path / "src" / "x.py"
    target.write_text("original")

    sess = _AegisAcpClient.__new__(_AegisAcpClient)
    sess._handle = "alice"
    sess._locks = registry
    sess._root = tmp_path
    sess._enforce = False
    sess._host = "local"

    asyncio.run(sess.write_text_file(content="new", path=str(target),
                                     session_id="s1"))
    assert target.read_text() == "new"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run python -m pytest tests/test_locks_acp_pep.py -q`
Expected: FAIL — `AttributeError: '_AegisAcpClient' object has no attribute 'gate'` (or the write succeeds where a denial was expected)

- [ ] **Step 3: Add the gate to `_AegisAcpClient`**

In `src/aegis/drivers/acp.py`, add the gate method to **`_AegisAcpClient`** and enforce it in its `write_text_file`:

```python
    def gate(self, abs_path: str, op: str):
        """PDP consult for a write this session is about to perform.

        Returns None when there is nothing to enforce (no registry, no
        root, or the path is outside the project subtree). Auto-claim runs
        even when enforcement is off, so the board stays accurate.
        """
        from aegis.locks.domain import in_domain

        locks = getattr(self, "_locks", None)
        root = getattr(self, "_root", None)
        if locks is None or root is None:
            return None
        rel = in_domain(abs_path, root)
        if rel is None:
            return None
        host = getattr(self, "_host", "local")
        d = locks.check(self._handle, rel, op, host=host)
        if d.allow or not getattr(self, "_enforce", True):
            if d.auto_claim:
                locks.auto_claim(self._handle, rel, host=host)
            return d if d.allow else None
        return d

    async def write_text_file(self, content, path, session_id, **kw):
        target = Path(path)
        op = OP_OVERWRITE if target.is_file() else OP_CREATE
        d = self.gate(path, op)
        if d is not None and not d.allow:
            raise acp.RequestError(code=-32000, message=d.reason)
        target.write_text(content, encoding="utf-8")
        return None
```

Add the import at the top of the module:

```python
from aegis.locks.models import OP_CREATE, OP_OVERWRITE
```

- [ ] **Step 4: Wire the attributes at session construction**

In `_AegisAcpClient.__init__` (`:139`, which today takes only `event_queue`), initialise the five attributes the gate reads so a client built the normal way is gated:

```python
        self._locks = locks
        self._root = root
        self._enforce = enforce
        self._host = host
```

Add `locks=None, root=None, enforce=True, host="local"` as keyword parameters with those defaults, and pass them down from `AcpSession` where it constructs the client.

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run python -m pytest tests/test_locks_acp_pep.py -q`
Expected: PASS (4 tests)

- [ ] **Step 6: Mutation-check the gate**

Temporarily change `if d is not None and not d.allow:` to `if False:` in `write_text_file`, then run:

Run: `uv run python -m pytest tests/test_locks_acp_pep.py -q`
Expected: FAIL on `test_acp_write_into_foreign_exclusive_raises`. Revert the change and confirm PASS again. A gate that cannot fail is worth less than none.

- [ ] **Step 7: Commit**

```bash
git add src/aegis/drivers/acp.py tests/test_locks_acp_pep.py
git commit -m "feat(locks): enforce write policy at the ACP client seam"
```

---

### Task 6: Shadow copies and notifications

Makes the "you can undo this" claim real, and closes the loop for the holder.

**Files:**
- Create: `src/aegis/locks/shadow.py`
- Modify: `src/aegis/drivers/acp.py` (call the notifier from `gate`)
- Test: `tests/test_locks_shadow.py`

**Interfaces:**
- Consumes: `Decision` (Task 1).
- Produces:
  - `snapshot(abs_path: str, rel: str, state_dir: Path) -> Path | None` in `aegis.locks.shadow`
  - `notice_for_writer(rel: str, holders: tuple[str, ...], shadow: Path | None) -> str`
  - `notice_for_holder(rel: str, writer: str) -> str`

- [ ] **Step 1: Write the failing test**

Create `tests/test_locks_shadow.py`:

```python
from pathlib import Path

from aegis.locks.shadow import (
    notice_for_holder, notice_for_writer, snapshot,
)


def test_snapshot_preserves_prior_content(tmp_path):
    src = tmp_path / "x.py"
    src.write_text("before")
    state = tmp_path / "state"
    shadow = snapshot(str(src), "x.py", state)
    assert shadow is not None
    assert shadow.read_text() == "before"
    src.write_text("after")
    assert shadow.read_text() == "before"


def test_snapshot_of_missing_file_is_none(tmp_path):
    assert snapshot(str(tmp_path / "nope.py"), "nope.py",
                    tmp_path / "state") is None


def test_two_snapshots_of_one_path_do_not_collide(tmp_path):
    src = tmp_path / "x.py"
    state = tmp_path / "state"
    src.write_text("v1")
    a = snapshot(str(src), "x.py", state)
    src.write_text("v2")
    b = snapshot(str(src), "x.py", state)
    assert a != b
    assert a.read_text() == "v1"
    assert b.read_text() == "v2"


def test_writer_notice_names_holders_and_restore_path(tmp_path):
    text = notice_for_writer("src/x.py", ("bob",), tmp_path / "shadow.bak")
    assert "bob" in text
    assert "src/x.py" in text
    assert "shadow.bak" in text


def test_holder_notice_names_the_writer():
    assert "alice" in notice_for_holder("src/x.py", "alice")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run python -m pytest tests/test_locks_shadow.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'aegis.locks.shadow'`

- [ ] **Step 3: Write `shadow.py`**

Create `src/aegis/locks/shadow.py`:

```python
"""Pre-write snapshots for writes into a foreign claim.

An agent has no ctrl+z and no copy of what it overwrote, so a notice
after the fact lands information at the moment it is least actionable.
A shadow copy makes the undo real. This path is rare by construction —
only writes into someone ELSE's claim take it — so the cost is nil, and
it is more reliable than `git checkout --` because the file is usually
already dirty mid-work.
"""
from __future__ import annotations

from pathlib import Path

from aegis.queue.schema import new_ulid


def snapshot(abs_path: str, rel: str, state_dir: Path) -> Path | None:
    """Copy the current content of ``abs_path`` into the shadow store.

    Returns the shadow path, or None when there was nothing to save (the
    write creates a new file). Never raises: failing to snapshot must not
    fail the write.
    """
    src = Path(abs_path)
    if not src.is_file():
        return None
    try:
        root = Path(state_dir) / "locks" / "shadow"
        root.mkdir(parents=True, exist_ok=True)
        dest = root / f"{new_ulid()}-{rel.replace('/', '%')}"
        dest.write_bytes(src.read_bytes())
        return dest
    except OSError:
        return None


def notice_for_writer(rel: str, holders: tuple[str, ...],
                      shadow: Path | None) -> str:
    who = ", ".join(holders)
    tail = f" Previous content saved at {shadow}." if shadow else ""
    return (f"note: you wrote into {who}'s shared claim on {rel}.{tail}")


def notice_for_holder(rel: str, writer: str) -> str:
    return (f"note: {writer} wrote into {rel}, which you have claimed. "
            f"If you are still working there, re-claim it exclusive to "
            f"stop further writes.")
```

- [ ] **Step 4: Call it from the ACP gate**

In `src/aegis/drivers/acp.py`, extend `gate` so the notify branch snapshots and emits. Replace the `if d.allow or not getattr(self, "_enforce", True):` block body with:

```python
        if d.allow or not getattr(self, "_enforce", True):
            if d.auto_claim:
                locks.auto_claim(self._handle, rel, host=host)
            elif d.notify:
                self._notify_overlap(abs_path, rel, d)
            return d if d.allow else None
```

and add:

```python
    def _notify_overlap(self, abs_path: str, rel: str, d) -> None:
        """Shadow the prior content, then tell both sides. Never raises —
        a notification failure must not fail the write."""
        from aegis.locks.shadow import (
            notice_for_holder, notice_for_writer, snapshot,
        )
        state_dir = getattr(self, "_state_dir", None)
        shadow = snapshot(abs_path, rel, state_dir) if state_dir else None
        inbox = getattr(self, "_inbox", None)
        if inbox is None:
            return
        try:
            inbox.deliver(self._handle,
                          notice_for_writer(rel, d.holders, shadow))
            for holder in d.holders:
                inbox.deliver(holder, notice_for_holder(rel, self._handle))
        except Exception:  # noqa: BLE001
            pass
```

Add `state_dir=None, inbox=None` to the `_AegisAcpClient.__init__` keyword parameters introduced in Task 5 and store them as `self._state_dir` / `self._inbox`.

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run python -m pytest tests/test_locks_shadow.py tests/test_locks_acp_pep.py -q`
Expected: PASS (9 tests)

- [ ] **Step 6: Commit**

```bash
git add src/aegis/locks/shadow.py src/aegis/drivers/acp.py \
        tests/test_locks_shadow.py
git commit -m "feat(locks): shadow copies and overlap notifications"
```

---

### Task 7: The Claude Code enforcement point

A `PreToolUse` hook injected via `--settings`, calling back into the co-resident aegis HTTP plane. Covers Write, Edit, and NotebookEdit.

**Files:**
- Create: `src/aegis/locks/hook.py`
- Create: `src/aegis/locks/hook_settings.py`
- Modify: `src/aegis/drivers/claude.py:234-256`
- Modify: `src/aegis/mcp/runtime.py`
- Test: `tests/test_locks_claude_hook.py`

**Interfaces:**
- Consumes: `check`/`auto_claim` (Task 3), `in_domain` (Task 4).
- Produces:
  - `op_for(tool_name: str, tool_input: dict) -> tuple[str, str] | None` in `aegis.locks.hook` — returns `(abs_path, op)` or `None` for non-write tools
  - `hook_verdict(payload: dict, bridge, root, host) -> dict` — returns the Claude hook response dict
  - `settings_json(port: int, handle: str, token: str) -> str` in `aegis.locks.hook_settings`

- [ ] **Step 1: Write the failing test**

Create `tests/test_locks_claude_hook.py`:

```python
import json

from aegis.locks.hook import hook_verdict, op_for
from aegis.locks.liveness import Activity
from aegis.locks.models import OP_CREATE, OP_EDIT, OP_OVERWRITE
from aegis.locks.registry import ClaimRegistry


def act():
    return Activity(exists=True, state="ready", idle_s=0.0, monitors=0,
                    reminders=0, inbox_depth=0, worker_label=None,
                    loop_armed=False)


def registry():
    live = {"alice", "bob"}
    return ClaimRegistry(live_handles=lambda: live,
                         session_activity=lambda: {h: act() for h in live},
                         dormant_after_s=1200)


def test_op_for_edit_is_edit(tmp_path):
    f = tmp_path / "x.py"
    f.write_text("")
    assert op_for("Edit", {"file_path": str(f)}) == (str(f), OP_EDIT)


def test_op_for_write_of_existing_file_is_overwrite(tmp_path):
    f = tmp_path / "x.py"
    f.write_text("")
    assert op_for("Write", {"file_path": str(f)}) == (str(f), OP_OVERWRITE)


def test_op_for_write_of_new_file_is_create(tmp_path):
    f = tmp_path / "new.py"
    assert op_for("Write", {"file_path": str(f)}) == (str(f), OP_CREATE)


def test_op_for_read_is_none():
    assert op_for("Read", {"file_path": "/x.py"}) is None


def test_hook_denies_write_into_foreign_exclusive(tmp_path):
    r = registry()
    r.claim("bob", ["src/"], [], intent="exclusive")
    (tmp_path / "src").mkdir()
    f = tmp_path / "src" / "x.py"
    f.write_text("")
    out = hook_verdict(
        {"tool_name": "Edit", "tool_input": {"file_path": str(f)}},
        bridge=r, root=tmp_path, host="local", handle="alice")
    assert out["hookSpecificOutput"]["permissionDecision"] == "deny"
    assert "bob" in out["hookSpecificOutput"]["permissionDecisionReason"]


def test_hook_allows_unclaimed_and_auto_claims(tmp_path):
    r = registry()
    f = tmp_path / "new.py"
    out = hook_verdict(
        {"tool_name": "Write", "tool_input": {"file_path": str(f)}},
        bridge=r, root=tmp_path, host="local", handle="alice")
    assert out["hookSpecificOutput"]["permissionDecision"] == "allow"
    assert [c.auto for c in r.active()] == [True]


def test_hook_ignores_non_write_tools(tmp_path):
    r = registry()
    r.claim("bob", ["src/"], [], intent="exclusive")
    out = hook_verdict(
        {"tool_name": "Read", "tool_input": {"file_path": "src/x.py"}},
        bridge=r, root=tmp_path, host="local", handle="alice")
    assert out == {}


def test_hook_ignores_paths_outside_root(tmp_path):
    r = registry()
    r.claim("bob", ["/"], [], intent="exclusive")
    out = hook_verdict(
        {"tool_name": "Write",
         "tool_input": {"file_path": str(tmp_path.parent / "o.txt")}},
        bridge=r, root=tmp_path, host="local", handle="alice")
    assert out == {}
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run python -m pytest tests/test_locks_claude_hook.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'aegis.locks.hook'`

- [ ] **Step 3: Write `hook.py`**

Create `src/aegis/locks/hook.py`:

```python
"""The Claude Code PreToolUse verdict.

Pure enough to test without a subprocess: `hook_verdict` takes the hook
payload and the registry and returns the response dict Claude expects.
An empty dict means "no opinion" — the tool proceeds normally.
"""
from __future__ import annotations

from pathlib import Path

from aegis.locks.domain import in_domain
from aegis.locks.models import OP_CREATE, OP_EDIT, OP_OVERWRITE

# Tools that write a file at a known path. Bash is deliberately absent —
# it takes the inverted, positive-match rule in aegis.locks.bash.
_EDIT_TOOLS = {"Edit", "NotebookEdit"}
_WRITE_TOOLS = {"Write"}


def op_for(tool_name: str, tool_input: dict) -> tuple[str, str] | None:
    """(abs_path, op) for a write tool, or None when this tool does not
    write a file at a knowable path."""
    path = (tool_input or {}).get("file_path")
    if not path:
        return None
    if tool_name in _EDIT_TOOLS:
        return str(path), OP_EDIT
    if tool_name in _WRITE_TOOLS:
        # A full overwrite is the only genuine clobber vector; creating a
        # new file clobbers nothing.
        exists = Path(path).is_file()
        return str(path), (OP_OVERWRITE if exists else OP_CREATE)
    return None


def _response(decision: str, reason: str) -> dict:
    return {"hookSpecificOutput": {
        "hookEventName": "PreToolUse",
        "permissionDecision": decision,
        "permissionDecisionReason": reason,
    }}


def hook_verdict(payload: dict, *, bridge, root: Path, host: str,
                 handle: str) -> dict:
    parsed = op_for(payload.get("tool_name", ""),
                    payload.get("tool_input") or {})
    if parsed is None:
        return {}
    abs_path, op = parsed
    rel = in_domain(abs_path, root)
    if rel is None:
        return {}
    d = bridge.check(handle, rel, op, host=host)
    if d.allow:
        if d.auto_claim:
            bridge.auto_claim(handle, rel, host=host)
        return _response("allow", "")
    return _response("deny", d.reason)
```

- [ ] **Step 4: Write `hook_settings.py`**

Create `src/aegis/locks/hook_settings.py`:

```python
"""The --settings blob injected into every claude session.

The hook is a one-liner that POSTs the payload to the co-resident aegis
HTTP plane and echoes the response. Remote hosts work unchanged: the MCP
port is already reverse-tunnelled by hosts/connection.py, so the far side
has an address.
"""
from __future__ import annotations

import json

_CMD = (
    "python3 -c \"import json,sys,urllib.request;"
    "p=sys.stdin.read();"
    "r=urllib.request.urlopen(urllib.request.Request("
    "'http://127.0.0.1:{port}/locks/pretooluse',"
    "data=p.encode(),"
    "headers={{'X-Aegis-Handle':'{handle}','X-Aegis-Token':'{token}',"
    "'Content-Type':'application/json'}}),timeout=5);"
    "sys.stdout.write(r.read().decode())\""
)


def settings_json(port: int, handle: str, token: str) -> str:
    """A --settings payload registering the PreToolUse hook."""
    return json.dumps({
        "hooks": {
            "PreToolUse": [{
                "matcher": "Write|Edit|NotebookEdit|Bash",
                "hooks": [{
                    "type": "command",
                    "command": _CMD.format(port=port, handle=handle,
                                           token=token),
                    "timeout": 10,
                }],
            }],
        },
    })
```

- [ ] **Step 5: Serve the endpoint**

In `src/aegis/mcp/runtime.py`, register a `POST /locks/pretooluse` route on the co-resident HTTP server that reads `X-Aegis-Handle`, validates `X-Aegis-Token` against the runtime's token, and returns `hook_verdict(payload, bridge=self._bridge.locks, root=..., host=..., handle=...)` as JSON. On any internal error return `{}` — **fail open**, because under a careless-agent threat model a hook outage that bricks every write is a worse failure than a missed gate.

- [ ] **Step 6: Inject `--settings` in `build_argv`**

In `src/aegis/drivers/claude.py`, in `build_argv` after the `--append-system-prompt` primer, add:

```python
        if locks_settings is not None:
            argv += ["--settings", locks_settings]
```

Thread `locks_settings: str | None = None` through the `build_argv` signature and pass `settings_json(port, handle, token)` from the driver where `mcp_config_json` is already assembled.

- [ ] **Step 7: Run tests to verify they pass**

Run: `uv run python -m pytest tests/test_locks_claude_hook.py -q`
Expected: PASS (8 tests)

- [ ] **Step 8: Commit**

```bash
git add src/aegis/locks/hook.py src/aegis/locks/hook_settings.py \
        src/aegis/drivers/claude.py src/aegis/mcp/runtime.py \
        tests/test_locks_claude_hook.py
git commit -m "feat(locks): Claude Code PreToolUse enforcement point"
```

---

### Task 8: The Bash heuristic

The inverted rule: deny only on a positive match, pass everything unparseable.

**Files:**
- Create: `src/aegis/locks/bash.py`
- Modify: `src/aegis/locks/hook.py`
- Test: `tests/test_locks_bash.py`

**Interfaces:**
- Consumes: `in_domain` (Task 4).
- Produces: `write_targets(cmd: str) -> list[tuple[str, str]]` in `aegis.locks.bash` — `(path, op)` pairs for targets it is confident about.

- [ ] **Step 1: Write the failing test**

Create `tests/test_locks_bash.py`:

```python
from aegis.locks.bash import write_targets
from aegis.locks.models import OP_OVERWRITE


def paths(cmd):
    return [p for p, _ in write_targets(cmd)]


def test_truncating_redirect_is_an_overwrite():
    assert write_targets("echo hi > src/x.py") == [("src/x.py",
                                                    OP_OVERWRITE)]


def test_appending_redirect_is_detected():
    assert paths("echo hi >> src/x.py") == ["src/x.py"]


def test_sed_in_place_is_detected():
    assert paths("sed -i 's/a/b/' src/x.py") == ["src/x.py"]


def test_tee_target_is_detected():
    assert paths("echo hi | tee src/x.py") == ["src/x.py"]


def test_mv_and_cp_destinations_are_detected():
    assert paths("mv a.py src/x.py") == ["src/x.py"]
    assert paths("cp a.py src/x.py") == ["src/x.py"]


def test_multiple_targets_across_a_chain():
    got = paths("echo a > one.txt && sed -i s/x/y/ two.txt")
    assert sorted(got) == ["one.txt", "two.txt"]


def test_read_only_commands_yield_nothing():
    for cmd in ("cat src/x.py", "ls -la", "grep -rn foo src/",
                "git status", "uv run pytest -q"):
        assert write_targets(cmd) == [], cmd


def test_variable_and_substitution_targets_are_not_guessed():
    # An unparseable target must PASS, never deny. A deny-by-default
    # guess makes Bash unusable within one turn.
    for cmd in ("echo hi > $OUT", "echo hi > \"$(mktemp)\"",
                "sed -i s/a/b/ ${FILE}"):
        assert write_targets(cmd) == [], cmd


def test_redirect_to_devnull_is_ignored():
    assert write_targets("cmd > /dev/null 2>&1") == []


def test_heredoc_body_is_not_scanned_for_redirects():
    cmd = "cat <<'EOF' > out.txt\nnot > a_redirect.txt\nEOF"
    assert paths(cmd) == ["out.txt"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run python -m pytest tests/test_locks_bash.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'aegis.locks.bash'`

- [ ] **Step 3: Write `bash.py`**

Create `src/aegis/locks/bash.py`:

```python
"""Positive-match write-target extraction for Bash commands.

The rule INVERTS here. For Write/Edit the hook denies unless allowed; for
Bash it denies only on a target it is confident about, and anything it
cannot parse PASSES. Any static analysis of a shell command is a guess,
and a deny-by-default guess makes Bash unusable within one turn. Bash is
porous under this design and that is a stated trade, not an oversight.

Confidence means: a literal path, no variables, no command substitution,
no globs.
"""
from __future__ import annotations

import re
import shlex

from aegis.locks.models import OP_OVERWRITE

_UNSAFE = ("$", "`", "*", "?", "~")
_IGNORED = {"/dev/null", "/dev/stdout", "/dev/stderr"}
_INPLACE = {"sed": "-i", "perl": "-i"}
_DEST_LAST = {"mv", "cp", "install", "tee"}
_HEREDOC = re.compile(r"<<-?\s*'?\"?([A-Za-z_][A-Za-z0-9_]*)'?\"?")


def _literal(tok: str) -> bool:
    return bool(tok) and not any(ch in tok for ch in _UNSAFE)


def _strip_heredocs(cmd: str) -> str:
    """Drop heredoc bodies — a `>` inside one is data, not a redirect."""
    m = _HEREDOC.search(cmd)
    if not m:
        return cmd
    lines = cmd.split("\n")
    out, terminator = [], None
    for line in lines:
        if terminator is None:
            out.append(line)
            hit = _HEREDOC.search(line)
            if hit:
                terminator = hit.group(1)
        elif line.strip() == terminator:
            terminator = None
    return "\n".join(out)


def write_targets(cmd: str) -> list[tuple[str, str]]:
    """(path, op) pairs this command certainly writes. Empty on any
    doubt."""
    try:
        tokens = shlex.split(_strip_heredocs(cmd), comments=True)
    except ValueError:
        return []

    found: list[str] = []
    words: list[str] = []
    i = 0
    while i < len(tokens):
        tok = tokens[i]
        if tok in (">", ">>") and i + 1 < len(tokens):
            found.append(tokens[i + 1])
            i += 2
            continue
        if tok.startswith(">") and len(tok) > 1 and not tok.startswith(">>"):
            found.append(tok[1:])
            i += 1
            continue
        if tok.startswith(">>") and len(tok) > 2:
            found.append(tok[2:])
            i += 1
            continue
        if tok in ("&&", "||", ";", "|"):
            found.extend(_from_words(words))
            words = []
            i += 1
            continue
        words.append(tok)
        i += 1
    found.extend(_from_words(words))

    seen: list[tuple[str, str]] = []
    for p in found:
        if p in _IGNORED or not _literal(p):
            continue
        if (p, OP_OVERWRITE) not in seen:
            seen.append((p, OP_OVERWRITE))
    return seen


def _from_words(words: list[str]) -> list[str]:
    """Targets implied by the command word itself (in-place editors and
    destination-last copiers)."""
    if not words:
        return []
    cmd = words[0].rsplit("/", 1)[-1]
    args = [w for w in words[1:] if not w.startswith("-")]
    if cmd in _INPLACE:
        flag = _INPLACE[cmd]
        if any(w == flag or w.startswith(flag) for w in words[1:]):
            # sed -i 's/a/b/' FILE — the script is an argument too, so the
            # target is the last one.
            return args[-1:] if args else []
        return []
    if cmd in _DEST_LAST:
        return args[-1:] if cmd == "tee" else (args[-1:] if len(args) >= 2
                                               else [])
    return []
```

- [ ] **Step 4: Route Bash through it in `hook.py`**

In `src/aegis/locks/hook.py`, add to `hook_verdict` before the `op_for` call:

```python
    if payload.get("tool_name") == "Bash":
        return _bash_verdict(payload, bridge=bridge, root=root, host=host,
                             handle=handle)
```

and add:

```python
def _bash_verdict(payload: dict, *, bridge, root: Path, host: str,
                  handle: str) -> dict:
    from aegis.locks.bash import write_targets
    cmd = (payload.get("tool_input") or {}).get("command", "")
    for raw, op in write_targets(cmd):
        rel = in_domain(str(Path(root) / raw) if not raw.startswith("/")
                        else raw, root)
        if rel is None:
            continue
        d = bridge.check(handle, rel, op, host=host)
        if not d.allow:
            return _response("deny", d.reason)
    # No confident target, or all of them allowed. Auto-claim is skipped
    # for Bash: the parse is a guess, and recording a claim from a guess
    # would poison the board.
    return {}
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run python -m pytest tests/test_locks_bash.py tests/test_locks_claude_hook.py -q`
Expected: PASS (18 tests)

- [ ] **Step 6: Commit**

```bash
git add src/aegis/locks/bash.py src/aegis/locks/hook.py \
        tests/test_locks_bash.py
git commit -m "feat(locks): positive-match Bash write-target heuristic"
```

---

### Task 9: The sidebar CLAIMS section

**Files:**
- Create: `src/aegis/locks/render.py`
- Modify: `src/aegis/tui/sidebar.py:37-62` (SidebarModel), `:174-176` (SECTIONS)
- Test: `tests/test_locks_render.py`

**Interfaces:**
- Consumes: `Claim` from `aegis.locks.models`.
- Produces:
  - `ClaimsView` frozen dataclass with `contested: tuple[tuple[str, str], ...]` (handle, path), `explicit: tuple[tuple[str, str], ...]` (intent, path), `auto_prefix: str`, `auto_count: int`
  - `build_claims_view(claims: list[Claim], me: str) -> ClaimsView`
  - `render_claims_dock(view: ClaimsView, palette, width: int) -> Text`

- [ ] **Step 1: Write the failing test**

Create `tests/test_locks_render.py`:

```python
from aegis.locks.models import Claim
from aegis.locks.render import build_claims_view, render_claims_dock
from aegis.themes import AegisColors

# AegisColors lives in `aegis.themes` (re-exported by `aegis.tui.themes`)
# and has no defaults — every role is required. Roles available are
# ready/working/error/accent/muted/ok/err/user/user_bg. There is NO
# `warning` role; contested rows use `err`.
COLORS = AegisColors(
    ready="green", working="yellow", error="red", accent="blue",
    muted="grey50", ok="green", err="red", user="blue", user_bg="black")


def mk(handle, *paths, intent="shared", auto=False):
    return Claim(claim_id=f"c{handle}{paths}", handle=handle,
                 prefixes=frozenset(p for p in paths if p.endswith("/")),
                 files=frozenset(p for p in paths if not p.endswith("/")),
                 intent=intent, desc="", since="2026-08-07T00:00:00Z",
                 auto=auto)


def palette():
    return COLORS


def test_view_ranks_contested_first():
    claims = [mk("alice", "src/"), mk("bob", "src/")]
    v = build_claims_view(claims, me="alice")
    assert v.contested == (("bob", "src/"),)


def test_view_rolls_auto_claims_into_a_count():
    claims = [mk("alice", "src/a.py", auto=True),
              mk("alice", "src/b.py", auto=True),
              mk("alice", "src/c.py", auto=True)]
    v = build_claims_view(claims, me="alice")
    assert v.auto_count == 3
    assert v.auto_prefix == "src/"
    assert v.explicit == ()


def test_view_keeps_explicit_claims_separate():
    claims = [mk("alice", "docs/", intent="exclusive"),
              mk("alice", "src/a.py", auto=True)]
    v = build_claims_view(claims, me="alice")
    assert v.explicit == (("exclusive", "docs/"),)
    assert v.auto_count == 1


def test_foreign_claims_that_do_not_overlap_are_not_contested():
    claims = [mk("alice", "src/"), mk("bob", "docs/")]
    v = build_claims_view(claims, me="alice")
    assert v.contested == ()


def test_render_survives_the_narrow_floor_keeping_the_handle():
    # fit_rows answers "no tier fits" by OMITTING the segment, so a
    # contested row must have a narrowest form that fits 26 cells or a
    # conflict silently vanishes — the worst failure this feature has.
    v = build_claims_view(
        [mk("alice", "src/aegis/tui/"),
         mk("lucid-knuth", "src/aegis/tui/")], me="alice")
    out = render_claims_dock(v, palette(), width=26).plain
    assert "lucid-knuth" in out


def test_render_never_exceeds_the_width_budget():
    from rich.cells import cell_len
    v = build_claims_view(
        [mk("alice", "src/aegis/tui/sidebar.py"),
         mk("bob", "src/aegis/tui/sidebar.py"),
         mk("alice", "src/aegis/locks/registry.py", auto=True)], me="alice")
    for w in (26, 40, 60):
        for line in render_claims_dock(v, palette(), width=w).plain.split("\n"):
            assert cell_len(line) <= w, (w, line)


def test_empty_view_renders_nothing():
    v = build_claims_view([], me="alice")
    assert render_claims_dock(v, palette(), width=40).plain.strip() == ""
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run python -m pytest tests/test_locks_render.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'aegis.locks.render'`

- [ ] **Step 3: Write `render.py`**

Create `src/aegis/locks/render.py`. Follow the `plan/render.py` contract: free-standing pure renderer, no Textual import, its own tests.

```python
"""The CLAIMS surface — pure, free-standing, no Textual import.

Ranked by what DEMANDS ACTION, not by recency. Under auto-claim a session
that edited thirty files holds thirty claims, so listing them is a wall of
paths the agent already knows it touched. Instead:

  1. contested — my claims a peer overlaps. The only full-line rows.
  2. explicit  — claims the agent actually asked for.
  3. auto      — one rolled-up line at the common prefix.

A contested row leads with the PEER HANDLE, not the path: the handle is
what you act on, the path is context. So when the row narrows, the path
elides and the handle survives. That is the opposite of the usual instinct
and it is what keeps the row actionable at the 26-cell floor.
"""
from __future__ import annotations

import os
from dataclasses import dataclass

from rich.cells import cell_len, set_cell_size
from rich.text import Text

from aegis.locks.models import Claim, claims_overlap


@dataclass(frozen=True)
class ClaimsView:
    contested: tuple[tuple[str, str], ...] = ()   # (peer handle, path)
    explicit: tuple[tuple[str, str], ...] = ()    # (intent, path)
    auto_prefix: str = ""
    auto_count: int = 0


def _paths(c: Claim) -> list[str]:
    return sorted(set(c.prefixes) | set(c.files))


def build_claims_view(claims: list[Claim], me: str) -> ClaimsView:
    mine = [c for c in claims if c.handle == me]
    theirs = [c for c in claims if c.handle != me]

    contested: list[tuple[str, str]] = []
    for m in mine:
        for t in theirs:
            if claims_overlap(m, t):
                for p in _paths(m):
                    if (t.handle, p) not in contested:
                        contested.append((t.handle, p))

    explicit = tuple((c.intent, p) for c in mine if not c.auto
                     for p in _paths(c))
    auto_paths = [p for c in mine if c.auto for p in _paths(c)]
    prefix = ""
    if auto_paths:
        common = os.path.commonpath(auto_paths) if len(auto_paths) > 1 \
            else os.path.dirname(auto_paths[0])
        prefix = (common + "/") if common else ""
    return ClaimsView(contested=tuple(contested), explicit=explicit,
                      auto_prefix=prefix, auto_count=len(auto_paths))


def _fit(text: str, cells: int) -> str:
    """Cells, never len(): one emoji is one character and two columns."""
    if cells <= 0:
        return ""
    if cell_len(text) <= cells:
        return text
    return "…" if cells == 1 else set_cell_size(text, cells - 1) + "…"


def _elide_left(path: str, cells: int) -> str:
    """Keep the leaf — the tail is what distinguishes one path from
    another."""
    if cell_len(path) <= cells:
        return path
    if cells <= 1:
        return "…"
    return "…" + set_cell_size(path[::-1], cells - 1)[::-1]


def render_claims_dock(view: ClaimsView, palette, width: int) -> Text:
    rows: list[str] = []
    for handle, path in view.contested:
        # The handle is what you act on, so it is charged to the budget
        # first and the path takes whatever is left.
        marker = "! "
        budget = width - cell_len(marker) - cell_len(handle) - 1
        path_part = _elide_left(path, budget) if budget > 0 else ""
        row = f"{marker}{handle}" + (f" {path_part}" if path_part else "")
        # `err`, not `warning` — AegisColors has no `warning` role.
        rows.append(f"[{palette.err}]{_fit(row, width)}[/]")

    for intent, path in view.explicit:
        mark = "x " if intent == "exclusive" else "- "
        rows.append(_fit(mark + _elide_left(path, width - 2), width))

    if view.auto_count:
        label = f"auto {view.auto_prefix} {view.auto_count} files".strip()
        rows.append(f"[{palette.muted}]{_fit(label, width)}[/]")

    return Text.from_markup("\n".join(rows)) if rows else Text("")
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run python -m pytest tests/test_locks_render.py -q`
Expected: PASS (7 tests)

- [ ] **Step 5: Add the sidebar section**

In `src/aegis/tui/sidebar.py`, add to `SidebarModel` after the PLAN block:

```python
    # CLAIMS
    claims: object | None = None          # aegis.locks.render.ClaimsView
```

Add the section function after `_plan`:

```python
def _claims(m: SidebarModel, palette, width: int) -> Text | None:
    view = m.claims
    if view is None or not (view.contested or view.explicit
                            or view.auto_count):
        return None
    body = render_claims_dock(view, palette, width)
    right = f"{len(view.contested)}!" if view.contested else ""
    return _block(heading("CLAIMS", palette, width, right=right),
                  list(body.split("\n", allow_blank=False)))
```

Import at the top: `from aegis.locks.render import render_claims_dock`.

Add `_claims` to `SECTIONS` between `_plan` and `_queues`:

```python
SECTIONS: tuple[Callable[[SidebarModel, object, int], Text | None], ...] = (
    _session, _context, _plan, _claims, _queues, _monitors, _system,
)
```

The section returns `None` when empty, which is how every sibling hides — never set `display` imperatively (the `PlanStrip` lesson: an inline style beats the CSS rule).

- [ ] **Step 6: Run the sidebar suite**

Run: `uv run python -m pytest tests/test_sidebar_render.py tests/test_sidebar_toggle.py tests/test_locks_render.py -q`
Expected: PASS

- [ ] **Step 7: Commit**

```bash
git add src/aegis/locks/render.py src/aegis/tui/sidebar.py \
        tests/test_locks_render.py
git commit -m "feat(locks): CLAIMS section in the F3 sidebar"
```

---

### Task 10: The status-bar segment

**Files:**
- Modify: `src/aegis/tui/widgets.py:342-349` (priorities), `:400-420` (setters), `:454-461` (compose)
- Test: `tests/test_locks_statusbar.py`

**Interfaces:**
- Consumes: nothing (takes plain counts).
- Produces: `StatusBar.set_claims(total: int, contested: int) -> None`, `StatusBar.P_CLAIMS_ALERT = 55`, `StatusBar.P_CLAIMS_QUIET = 28`

- [ ] **Step 1: Write the failing test**

Create `tests/test_locks_statusbar.py`:

```python
from aegis.themes import AegisColors
from aegis.tui.widgets import StatusBar

COLORS = AegisColors(
    ready="green", working="yellow", error="red", accent="blue",
    muted="grey50", ok="green", err="red", user="blue", user_bg="black")


def bar():
    return StatusBar("claude-opus-5", "high", COLORS)


def test_quiet_claims_rank_below_metrics():
    assert StatusBar.P_CLAIMS_QUIET < StatusBar.P_METRICS


def test_contested_claims_outrank_metrics_and_the_loop():
    assert StatusBar.P_CLAIMS_ALERT > StatusBar.P_METRICS
    assert StatusBar.P_CLAIMS_ALERT > StatusBar.P_LOOP
    assert StatusBar.P_CLAIMS_ALERT < StatusBar.P_STATE


def test_zero_claims_hides_the_segment():
    b = bar()
    b.set_claims(0, 0)
    assert b._claims == ()


def test_quiet_segment_shows_a_count():
    b = bar()
    b.set_claims(3, 0)
    assert "3" in b._claims[0]


def test_contested_segment_survives_to_its_narrowest_form():
    b = bar()
    b.set_claims(3, 1)
    assert "1" in b._claims[-1]


def test_segment_glyphs_stay_single_width():
    # fit.plain_width measures with len(), not cell_len — its docstring
    # asserts the bar's glyphs are single-width. A wide glyph here would
    # silently overshoot the whole bar's budget.
    from rich.cells import cell_len
    b = bar()
    b.set_claims(3, 1)
    for tier in b._claims:
        assert cell_len(tier) == len(tier), tier
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run python -m pytest tests/test_locks_statusbar.py -q`
Expected: FAIL — `AttributeError: type object 'StatusBar' has no attribute 'P_CLAIMS_QUIET'`

- [ ] **Step 3: Add the priorities, state, and setter**

In `src/aegis/tui/widgets.py`, add to the priority ladder:

```python
    # Claims carry TWO priorities, because the same segment means two
    # different things. Quiet claims are telemetry and should degrade away
    # early; a contested claim demands action and should survive to the
    # narrowest terminal. Segments are constructed fresh on every compose,
    # so this is just an expression at construction time.
    P_CLAIMS_ALERT, P_CLAIMS_QUIET = 55, 28
```

In `__init__`, add `self._claims: tuple[str, ...] = ()` and
`self._claims_contested = 0` beside the other segment state.

Add the setter beside `set_loop`:

```python
    def set_claims(self, total: int, contested: int) -> None:
        """File-claims segment; 0/0 hides it. Glyphs stay single-width —
        fit.plain_width measures with len(), not cell_len."""
        self._claims_contested = contested
        if not total and not contested:
            self._claims = ()
        elif contested:
            self._claims = (
                f"{total} claims · {contested} contested",
                f"{total}c {contested}!",
                f"{contested}!",
            )
        else:
            self._claims = (f"{total} claims", f"{total}c")
        self._refresh()
```

- [ ] **Step 4: Add the segment to the compose list**

In the compose list at line 454, add:

```python
            Segment("claims", self._claims,
                    self.P_CLAIMS_ALERT if self._claims_contested
                    else self.P_CLAIMS_QUIET),
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run python -m pytest tests/test_locks_statusbar.py tests/test_statusbar_fit.py tests/test_statusbar_segments.py -q`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add src/aegis/tui/widgets.py tests/test_locks_statusbar.py
git commit -m "feat(locks): status-bar claims segment with dual priority"
```

---

### Task 11: The `/claims` command

The sidebar and status bar are per-pane; this is the whole board.

**Files:**
- Modify: `src/aegis/commands/builtins/core.py:68-77` (beside `_sessions`), `:380-392` (registry)
- Test: `tests/test_locks_claims_command.py`

**Interfaces:**
- Consumes: `build_claims_view` (Task 9), `_LocksBridge.active()`.
- Produces: `_claims(ctx, args) -> CommandResult` and a `SlashCommand("claims", ...)` registration.

- [ ] **Step 1: Write the failing test**

Create `tests/test_locks_claims_command.py`:

```python
import asyncio
from types import SimpleNamespace

from aegis.commands import CommandContext, dispatch
from aegis.locks.models import Claim

# CommandResult fields are (ok, title, body) — NOT summary/detail.
# `summary` is a field of SlashCommand, not of the result.


def mk(handle, *paths, intent="shared", auto=False):
    return Claim(claim_id=f"c{handle}", handle=handle,
                 prefixes=frozenset(p for p in paths if p.endswith("/")),
                 files=frozenset(p for p in paths if not p.endswith("/")),
                 intent=intent, desc="working", since="2026-08-07T00:00:00Z",
                 auto=auto)


def ctx_with(claims):
    bridge = SimpleNamespace(
        locks=SimpleNamespace(active=lambda: claims),
        list_sessions=lambda: [],
    )
    return CommandContext(bridge=bridge, handle="alice")


def run(ctx, text):
    return asyncio.run(dispatch(text, ctx))


def test_claims_on_an_empty_board():
    res = run(ctx_with([]), "/claims")
    assert res.ok
    assert "no" in res.title.lower()


def test_claims_lists_holders_and_paths():
    res = run(ctx_with([mk("bob", "src/", intent="exclusive")]), "/claims")
    assert res.ok
    assert "bob" in res.body
    assert "src/" in res.body
    assert "exclusive" in res.body


def test_claims_rolls_up_auto_claims_per_holder():
    claims = [mk("bob", "src/a.py", auto=True),
              mk("bob", "src/b.py", auto=True)]
    res = run(ctx_with(claims), "/claims")
    assert "2" in res.body


def test_claims_marks_contested_paths():
    claims = [mk("alice", "src/"), mk("bob", "src/")]
    res = run(ctx_with(claims), "/claims")
    assert "contested" in res.body.lower() or "!" in res.body
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run python -m pytest tests/test_locks_claims_command.py -q`
Expected: FAIL — dispatch returns an unknown-command result

- [ ] **Step 3: Write the handler**

In `src/aegis/commands/builtins/core.py`, after `_sessions`:

```python
async def _claims(ctx: CommandContext, args) -> CommandResult:
    """The whole board — the sidebar and status bar are per-pane, so this
    is the only surface that answers 'who is working where'."""
    from aegis.locks.models import claims_overlap

    claims = list(ctx.bridge.locks.active())
    if not claims:
        return CommandResult(True, "no active claims")

    contested = {c.claim_id for c in claims
                 if any(o.handle != c.handle and claims_overlap(c, o)
                        for o in claims)}
    by_holder: dict[str, list] = {}
    for c in claims:
        by_holder.setdefault(c.handle, []).append(c)

    lines: list[str] = []
    # Contested holders first — the only rows that demand action.
    order = sorted(by_holder,
                   key=lambda h: (not any(c.claim_id in contested
                                          for c in by_holder[h]), h))
    for handle in order:
        held = by_holder[handle]
        auto = [c for c in held if c.auto]
        explicit = [c for c in held if not c.auto]
        for c in explicit:
            paths = ", ".join(sorted(set(c.prefixes) | set(c.files)))
            flag = " !contested" if c.claim_id in contested else ""
            lines.append(f"{handle} · {c.intent} · {paths}{flag}")
        if auto:
            flag = (" !contested"
                    if any(c.claim_id in contested for c in auto) else "")
            lines.append(f"{handle} · auto · {len(auto)} files{flag}")

    n = len(contested)
    summary = (f"{len(claims)} claim(s)"
               + (f", {n} contested" if n else ""))
    return CommandResult(True, summary, "\n".join(lines))
```

- [ ] **Step 4: Register the command**

In the `for _cmd in (...)` tuple, after the `sessions` entry:

```python
    SlashCommand("claims", "show the file-claims board", "/claims",
                 _claims),
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run python -m pytest tests/test_locks_claims_command.py tests/test_command_registry.py tests/test_slash_commands.py -q`
Expected: PASS

- [ ] **Step 6: Run the whole fast suite**

Run: `uv run python -m pytest -q -m "not live"`
Expected: PASS. A failing test here is a real regression to investigate, not noise to re-roll — the flakes documented in `AGENTS.md` were fixed in 0.25.0.

- [ ] **Step 7: Update `AGENTS.md`**

Extend the `src/aegis/locks/` entry in the Layout section to name the new modules (`policy.py`, `liveness.py`, `domain.py`, `shadow.py`, `hook.py`, `hook_settings.py`, `bash.py`, `render.py`), the three-state liveness rule, and the fact that auto-claims do not count toward the `aegis_close` refusal.

- [ ] **Step 8: Commit**

```bash
git add src/aegis/commands/builtins/core.py AGENTS.md \
        tests/test_locks_claims_command.py
git commit -m "feat(locks): /claims board command; document the locks package"
```

---

## Self-Review

**Spec coverage:**

| Spec section | Task |
|---|---|
| PDP / write policy (5 lines) | 1 |
| `aegis_claim` as the acknowledgment | 1 (deny text), 11 (visible on the board) |
| Deny message names holder + escape | 1 |
| gone / live / dormant, degrade not delete | 2 |
| "has a future" == close_guard predicate | 2 |
| Restart + dropped host self-heal | 2 (no code — covered by `effective_claims` over restored panes) |
| Auto-claim | 3 |
| Violation log | 3 |
| `aegis_close` regression fix | 3 |
| Enforcement domain (root subtree only) | 4 |
| Config kill-switch | 4 |
| ACP PEP | 5 |
| Shadow copies + 3 notifications | 6 |
| Claude PreToolUse PEP | 7 |
| Bash positive-match heuristic | 8 |
| Sidebar CLAIMS section | 9 |
| Status bar dual priority | 10 |
| `/claims` | 11 |
| Board = live state, JSONL = history | 3 (violation/degraded records) |

**Known gap, deliberate:** the spec's third notification ("to Alex, in the TUI") is delivered in Task 6 only as far as the inbox; surfacing it as a TUI toast is not planned, because the sidebar CLAIMS section (Task 9) already puts contested state permanently on screen and a toast would be redundant. If a toast is wanted after using it, it is a one-task follow-up.

**Type consistency:** `Decision` fields (`allow`, `reason`, `notify`, `auto_claim`, `holders`) are used identically in Tasks 1, 3, 5, 6, 7, 8. `OP_EDIT`/`OP_CREATE`/`OP_OVERWRITE` come from `aegis.locks.models` throughout. `check(handle, path, op, host=)` and `auto_claim(handle, path, host=)` keep one signature across registry, bridge, ACP gate, and Claude hook. `ClaimsView` field names match between Task 9's builder, its renderer, and Task 9's sidebar section.

**Placeholder scan:** clean — every step carries the actual code or the exact command, except Task 7 Step 5 (the `runtime.py` route), which describes the handler contract rather than showing the route registration because the surrounding HTTP-server idiom must be read from `mcp/runtime.py` at implementation time.
