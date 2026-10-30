"""Turn one session's store records into usage rows (spec 06).

The UsageDeriver is pure, like the journal's Deriver beside it: records in,
rows out, the same rows live and in a backfill. A turn opens at the first
record after the previous one closed and closes on a harness result or on an
own record that ends a turn (``TURN_ENDS``, ``close``), as the journal closes
it. Its cost is Claude's running total's rise where Claude reports one, else
its messages' priced tokens. The cost goes to the turn's messages by weight
(their priced tokens, else their token count), the weight of text-only
messages spread over the messages with tool calls, and each message's share
equally to its tool calls. Calls are grouped into parts by repo, purpose,
activity and whether the purpose is settled.

A purpose may wait on a later record: a code edit on its commit, a support
call on the next purposeful call. Such a part is written unsettled, with the
purpose it would have if the session ended now, and a later ``Settle`` fixes
it.

Claude repeats a message's usage on each content block's line, so messages
are keyed by their id and the last line wins. A resume restarts Claude's
running total, so a total below the previous one is a new segment. A turn
interrupted without a result is priced from its tokens; the next result of the
same process includes those tokens again, so its rise is taken net of them.
A resume after an exit or a server stop carries Claude's total on, so only a
total that falls starts a new segment, as ``cost.py`` reads it.
"""

from __future__ import annotations

import json
import os
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from decimal import Decimal
from fractions import Fraction

from ..claude.stream import Echo, Init, Result, ToolCall, ToolOutput
from ..journal import shell
from ..journal.derive import REPO_FLAG, TURN_ENDS
from ..transcript.entries import PARSERS
from .kinds import (
    activity,
    commit_purpose,
    direct_purpose,
    edit_purpose,
    is_shell_exit,
)
from .context import START, Replay, band
from .prices import Prices, codex_prices_for, prices_for
from .store import split_cache

MAX_WAIT = 28_800  # an idle gap counts at most 8 hours
WAKE = re.compile(r"^> from ([\w-]+)")
MODES = {
    "agent": "agent",
    "peer": "agent",
    "queue": "queue",
    "workflow": "workflow",
    "schedule": "schedule",
    "monitor": "monitor",
    "artifact": "artifact",
}
KIND_MODES = {"worker": "queue", "workflow": "workflow", "schedule": "schedule"}
PATH_KEYS = ("file_path", "notebook_path", "path")


@dataclass
class SessionInfo:
    person: str = ""
    kind: str = "foreground"  # foreground, worker, workflow, schedule or outside
    harness: str = "claude-code"
    continues: str | None = None
    account: Callable[[str], str | None] = lambda harness: None
    replay_start: int = START  # successors' median start context here


@dataclass
class TurnRow:
    rec: int
    ts: float
    person: str
    kind: str
    harness: str
    model: str
    mode: str
    billing: str
    cost: Decimal
    cost_kind: str
    tok_in: int
    tok_out: int
    tok_cw: int
    tok_cr: int
    secs: float
    calls: int
    errors: int
    exits: int
    ctx_start: int
    ctx_peak: int
    continues: str | None


@dataclass
class PartRow:
    rec: int
    n: int
    ts: float
    repo: str | None
    purpose: str
    activity: str
    settled: bool
    cost: Decimal
    tokens: int
    secs: float
    calls: int = 0
    errors: int = 0
    exits: int = 0


@dataclass
class WaitRow:
    rec: int
    ts: float
    woke_by: str
    secs: float


@dataclass
class BandRow:
    rec: int
    band: int
    calls: int
    cost: Decimal
    tok_cr: int


@dataclass
class WhatIfRow:
    rec: int
    at: int
    handoffs: int
    saved: Decimal
    rewrite: Decimal


@dataclass(frozen=True)
class Settle:
    rec: int
    n: int
    purpose: str


@dataclass
class Derived:
    turns: list[TurnRow] = field(default_factory=list)
    parts: list[PartRow] = field(default_factory=list)
    waits: list[WaitRow] = field(default_factory=list)
    bands: list[BandRow] = field(default_factory=list)
    whatifs: list[WhatIfRow] = field(default_factory=list)
    settles: list[Settle] = field(default_factory=list)

    def extend(self, other: Derived) -> Derived:
        for name in ("turns", "parts", "waits", "bands", "whatifs", "settles"):
            getattr(self, name).extend(getattr(other, name))
        return self

    def __bool__(self) -> bool:
        return any(
            (self.turns, self.parts, self.waits, self.bands, self.whatifs, self.settles)
        )


@dataclass
class _Msg:
    model: str | None
    inp: int = 0
    out: int = 0
    cc5: int = 0
    cc1: int = 0
    cr: int = 0
    calls: list[str] = field(default_factory=list)

    @property
    def tokens(self) -> int:
        return self.inp + self.out + self.cc5 + self.cc1 + self.cr

    @property
    def context(self) -> int:
        return self.inp + self.cc5 + self.cc1 + self.cr


@dataclass
class _Call:
    id: str
    tool: str
    inp: dict
    activity: str
    repo: str | None
    output: str = ""
    is_error: bool = False
    commits: list[tuple[str | None, str]] = field(
        default_factory=list
    )  # (repo, purpose)
    purpose: str | None = None
    kind: str | None = None  # "code" or "support" while unsettled


@dataclass
class _Turn:
    first_ts: float
    last_ts: float
    wake: str | None = None
    msgs: dict[str, _Msg] = field(default_factory=dict)
    calls: dict[str, _Call] = field(default_factory=dict)
    errors: int = 0
    exits: int = 0
    # Calls of a harness that reports usage per request after its calls.
    pending: list[str] = field(default_factory=list)


def _dec(f: Fraction) -> Decimal:
    return Decimal(f.numerator) / Decimal(f.denominator)


class UsageDeriver:
    def __init__(self, info: SessionInfo, repo_of: Callable[[str], str | None]) -> None:
        self.info = info
        self.repo_of = repo_of
        self.harness = info.harness
        self.cwd = ""
        self.model: str | None = None  # the spawn's or the harness's init
        self._parsers: dict = {}
        self._turn: _Turn | None = None
        self._turns = 0
        self._ts = 0.0
        self._src = ""
        self._mid: dict[str, str] = {}  # tool_use id -> message id
        self._replay = Replay(info.replay_start)
        self._key_source: str | None = None  # Claude's init apiKeySource
        self._prev_total: Decimal | None = None
        self._priced_since = Decimal(0)  # interrupted turns since the last result
        self._last_end: float | None = None
        self._last_repo: str | None = None
        self._last_purpose: str | None = None
        self._last_commit: str | None = None
        self._open: list[tuple[int, int, str | None, str]] = []  # rec, n, repo, kind

    # -- records ------------------------------------------------------
    def feed(self, record: dict, events: list | None = None) -> Derived:
        i = record["i"]
        ts = record.get("ts") or self._ts
        self._ts = ts
        src = record.get("src")
        if src in PARSERS:
            self._src = src
            line = record.get("line", "")
            if src == "claude":
                self._claude_line(i, line)
            elif src in ("codex", "opencode"):
                self._usage_line(src, line)
            if events is None:
                p = self._parsers.get(src)
                if p is None:
                    p = self._parsers[src] = PARSERS[src]()
                events = p.feed(line)
            out = Derived()
            for ev in events:
                out.extend(self._event(i, ts, ev))
            return out
        return self._own(i, ts, record)

    def _open_turn(self, ts: float) -> _Turn:
        if self._turn is None:
            self._turn = _Turn(first_ts=ts, last_ts=ts)
        self._turn.last_ts = ts
        return self._turn

    def _claude_line(self, i: int, line: str) -> None:
        """Claude's own message ids, models and usage, which the parsed
        events do not carry."""
        try:
            obj = json.loads(line)
        except ValueError:
            return
        if isinstance(obj, dict) and obj.get("type") == "result":
            self._result_usage(obj)
            return
        if isinstance(obj, dict) and obj.get("subtype") == "init":
            self._key_source = obj.get("apiKeySource") or self._key_source
            return
        if not isinstance(obj, dict) or obj.get("type") != "assistant":
            return
        message = obj.get("message") or {}
        mid = str(message.get("id") or f"r{i}")
        t = self._open_turn(self._ts)
        m = t.msgs.get(mid)
        if m is None:
            m = t.msgs[mid] = _Msg(model=message.get("model"))
        u = message.get("usage")
        if isinstance(u, dict):
            m.cc5, m.cc1, _ = split_cache(u)
            m.inp = int(u.get("input_tokens") or 0)
            m.out = int(u.get("output_tokens") or 0)
            m.cr = int(u.get("cache_read_input_tokens") or 0)
        m.model = message.get("model") or m.model
        for block in message.get("content") or []:
            if isinstance(block, dict) and block.get("type") == "tool_use":
                self._mid[str(block.get("id"))] = mid

    def _result_usage(self, obj: dict) -> None:
        """A result's own usage, the turn's sum, stands in for its messages'
        when none of them reported any (an imported legacy log)."""
        u = obj.get("usage")
        t = self._turn
        if not isinstance(u, dict) or (t and any(m.tokens for m in t.msgs.values())):
            if t is None and obj.get("total_cost_usd") is not None:
                self._open_turn(self._ts)  # a billed turn with nothing else
            return
        cc5, cc1, _ = split_cache(u)
        m = _Msg(
            model=None,
            inp=int(u.get("input_tokens") or 0),
            out=int(u.get("output_tokens") or 0),
            cc5=cc5,
            cc1=cc1,
            cr=int(u.get("cache_read_input_tokens") or 0),
        )
        if m.tokens or obj.get("total_cost_usd") is not None:
            self._open_turn(self._ts).msgs["result"] = m

    def _usage_line(self, src: str, line: str) -> None:
        """A Codex request's or an OpenCode step's tokens, read as
        ``usage/scan.py`` reads them. The calls made since the previous one
        are that request's."""
        try:
            obj = json.loads(line)
        except ValueError:
            return
        if not isinstance(obj, dict):
            return
        if src == "codex":
            params = obj.get("params") or {}
            if obj.get("method") == "aegis/turn":
                self.model = str(params.get("model") or self.model or "") or None
                return
            last = (params.get("tokenUsage") or {}).get("last")
            if obj.get("method") != "thread/tokenUsage/updated" or not isinstance(
                last, dict
            ):
                return
            cached = int(last.get("cachedInputTokens") or 0)
            write = int(last.get("cacheWriteInputTokens") or 0)
            key = f"u{len(self._turn.msgs) if self._turn else 0}:{self._ts}"
            m = _Msg(
                model=self.model,
                inp=max(0, int(last.get("inputTokens") or 0) - cached - write),
                out=int(last.get("outputTokens") or 0),
                cc5=write,
                cr=cached,
            )
        else:
            if obj.get("type") not in ("session.step.ended", "session.step.failed"):
                return
            data = obj.get("data") or {}
            tokens = data.get("tokens")
            if not isinstance(tokens, dict):
                return
            cache = tokens.get("cache") if isinstance(tokens.get("cache"), dict) else {}
            key = f"opencode:{data.get('assistantMessageID')}"
            m = _Msg(
                model=self.model,
                inp=int(tokens.get("input") or 0),
                out=int(tokens.get("output") or 0) + int(tokens.get("reasoning") or 0),
                cc5=int(cache.get("write") or 0),
                cr=int(cache.get("read") or 0),
            )
        t = self._open_turn(self._ts)
        if key in t.msgs:
            return
        m.calls, t.pending = t.pending, []
        t.msgs[key] = m

    def _event(self, i: int, ts: float, ev) -> Derived:
        if isinstance(ev, Init):
            self.cwd = ev.cwd or self.cwd
            self.model = ev.model or self.model  # the harness's own, over an alias
            return Derived()
        if isinstance(ev, Echo):
            return Derived()
        if isinstance(ev, ToolCall):
            self._call(ts, ev)
            return Derived()
        if isinstance(ev, ToolOutput):
            self._output(ts, ev)
            return Derived()
        if isinstance(ev, Result):
            cost = ev.cost_usd if self._src == "claude" else None
            return self._close(i, ts, cost, ev.duration_ms)
        return Derived()

    def _own(self, i: int, ts: float, rec: dict) -> Derived:
        kind = rec.get("kind")
        if kind == "spawn":
            self.cwd = str(rec.get("cwd") or self.cwd)
            self.harness = str(rec.get("harness") or self.harness)
            self.model = rec.get("model") or self.model
            return Derived()
        if kind == "send":
            if rec.get("steered"):
                return Derived()
            out = Derived()
            if self._turn is None:
                if self._last_end is not None:
                    secs = min(max(ts - self._last_end, 0.0), MAX_WAIT)
                    woke = self._mode(str(rec.get("text") or ""), False)
                    out.waits.append(WaitRow(i, ts, woke, secs))
                t = self._open_turn(ts)
                t.wake = str(rec.get("text") or "")
            return out
        if kind in TURN_ENDS:
            self._end_parsers()
            # A resumed Claude carries its running total on, so a process end
            # is no new segment: only a total that falls is (cost.py).
            return self._close(i, ts, None, None, interrupted=kind == "stop")
        if kind == "close":
            self._end_parsers()
            return self._close(i, ts, None, None).extend(self._settle_all())
        return Derived()

    def _end_parsers(self) -> None:
        """A turn ended without a result: each parser forgets it, as the
        journal's Deriver does."""
        for p in self._parsers.values():
            end = getattr(p, "end_turn", None)
            if end is not None:
                end()

    # -- calls --------------------------------------------------------
    def _abs(self, p: str) -> str:
        return os.path.normpath(os.path.join(self.cwd or os.sep, os.path.expanduser(p)))

    def _repo(self, tool: str, inp: dict) -> str | None:
        """The repo a call names: the path it touched, the --repo of a gh
        command, the directory a shell command cd's to; else None."""
        if tool == "Bash":
            cmd = str(inp.get("command") or "")
            if m := REPO_FLAG.search(cmd):
                return self.repo_of("gh:" + m[2])
            d = shell.workdir(cmd, self.cwd or os.sep)
            return self.repo_of(d) if d != (self.cwd or os.sep) else None
        for k in PATH_KEYS:
            p = inp.get(k)
            if isinstance(p, str) and p:
                full = self._abs(p)
                return self.repo_of(full if k == "path" else os.path.dirname(full))
        return None

    def _call(self, ts: float, ev: ToolCall) -> None:
        t = self._open_turn(ts)
        if ev.id in t.calls:
            return
        repo = self._repo(ev.name, ev.input)
        if repo is not None:
            self._last_repo = repo
        t.calls[ev.id] = _Call(
            ev.id,
            ev.name,
            ev.input,
            activity(ev.name, ev.input),
            repo or self._last_repo or self._cwd_repo(),
        )
        if self._src == "claude":
            # A call no message line named is its own message.
            mid = self._mid.get(ev.id, f"c:{ev.id}")
            t.msgs.setdefault(mid, _Msg(model=None)).calls.append(ev.id)
        else:
            t.pending.append(ev.id)

    def _cwd_repo(self) -> str | None:
        return self.repo_of(self.cwd) if self.cwd else None

    def _output(self, ts: float, ev: ToolOutput) -> None:
        t = self._open_turn(ts)
        c = t.calls.get(ev.id)
        if c is None:
            return
        c.output, c.is_error = ev.text, ev.is_error
        if ev.is_error:
            t.errors += 1
            if is_shell_exit(c.tool, ev.text, ev.is_error):
                t.exits += 1
        if c.tool == "Bash":
            cmd = str(c.inp.get("command") or "")
            cwd = self.cwd or os.sep
            found = shell.commits(cmd, ev.text, ev.is_error)
            for (_, _, subject), d in zip(
                found, shell.commit_dirs(cmd, cwd, len(found))
            ):
                p = commit_purpose(subject)
                if p:
                    self._last_commit = p
                c.commits.append(
                    (self.repo_of(d) or c.repo, p or self._last_commit or "build")
                )
            if self._src == "claude" and ev.parent is None:
                self.cwd = shell.leading_cd(cmd, cwd) or self.cwd

    # -- purposes -----------------------------------------------------
    def _default(self) -> str:
        if self._last_purpose:
            return self._last_purpose
        return "chat" if self.info.kind == "foreground" else "review"

    def _purposes(self, t: _Turn, rec: int) -> list[Settle]:
        """Give each call of the turn its purpose, in order; settle what an
        earlier turn left open."""
        settles: list[Settle] = []
        calls = list(t.calls.values())
        waiting: list[_Call] = []  # this turn's calls still unsettled

        def purposeful(p: str) -> None:
            for c in waiting:
                if c.kind == "support":
                    c.purpose, c.kind = p, None
            waiting[:] = [c for c in waiting if c.kind]
            for r, n, repo, kind in self._open:
                if kind == "support":
                    settles.append(Settle(r, n, p))
            self._open = [o for o in self._open if o[3] != "support"]
            self._last_purpose = p

        def committed(repo: str | None, p: str) -> None:
            for c in waiting:
                if c.kind == "code" and c.repo == repo:
                    c.purpose, c.kind = p, None
            waiting[:] = [c for c in waiting if c.kind]
            for r, n, prepo, kind in self._open:
                if kind == "code" and prepo == repo:
                    settles.append(Settle(r, n, p))
            self._open = [
                o for o in self._open if not (o[3] == "code" and o[2] == repo)
            ]

        for k, c in enumerate(calls):
            act = c.activity
            if act == "editing":
                p = None
                for key in PATH_KEYS:
                    if isinstance(c.inp.get(key), str):
                        p = edit_purpose(self._abs(c.inp[key]))
                        break
                if p:
                    c.purpose = p
                    purposeful(p)
                else:
                    c.kind = "code"
                    waiting.append(c)
                continue
            if act == "committing":
                for repo, p in c.commits:
                    committed(repo, p)
                p = c.commits[-1][1] if c.commits else (self._last_commit or "build")
                c.purpose = p
                purposeful(p)
                continue
            p = direct_purpose(act, c.tool, c.inp)
            if p == "review" and any(x.activity == "editing" for x in calls[k + 1 :]):
                p = None
            if p:
                c.purpose = p
                purposeful(p)
            else:
                c.kind = "support"  # SUPPORT, and reading by default
                waiting.append(c)
        for c in waiting:
            c.purpose = "build" if c.kind == "code" else self._default()
        return settles

    def _settle_all(self) -> Derived:
        """The session closed: code still open is build, support takes the
        last purpose."""
        out = Derived()
        for r, n, _, kind in self._open:
            out.settles.append(
                Settle(r, n, "build" if kind == "code" else self._default())
            )
        self._open = []
        return out

    # -- closing a turn -----------------------------------------------
    def _mode(self, wake: str | None, first: bool) -> str:
        if self.info.kind == "outside":  # imported from Claude Code's files
            return "outside"
        if first and self.info.kind in KIND_MODES:
            return KIND_MODES[self.info.kind]
        if wake is None:
            return "other"
        m = WAKE.match(wake)
        if not m:
            return "person" if not wake.startswith("> from ") else "other"
        return MODES.get(m[1], "other")

    def _rates(self, m: _Msg) -> Prices | None:
        if self.harness == "claude-code":
            return prices_for(m.model or self.model)
        if self.harness == "codex":
            return codex_prices_for(m.model or self.model)
        return None

    def _price(self, m: _Msg) -> Decimal | None:
        p = self._rates(m)
        if p is None:
            return None
        return p.cost(inp=m.inp, out=m.out, cc5=m.cc5, cc1=m.cc1, cache_read=m.cr)

    def _close(
        self,
        i: int,
        ts: float,
        total: float | None,
        duration_ms: int | None,
        interrupted: bool = False,
    ) -> Derived:
        t, self._turn = self._turn, None
        billed: Decimal | None = None
        if total is not None:
            c = Decimal(str(total))
            if self._prev_total is None or c < self._prev_total:
                billed = c
            else:
                billed = max(c - self._prev_total - self._priced_since, Decimal(0))
            self._prev_total, self._priced_since = c, Decimal(0)
        if t is None or not (t.msgs or t.calls):
            return Derived()
        if t.pending:  # calls after the last request's tokens: that request's
            msgs = [m for m in t.msgs.values() if m.tokens]
            if msgs:
                msgs[-1].calls += t.pending
            else:
                for c in t.pending:
                    t.msgs[f"c:{c}"] = _Msg(model=None, calls=[c])
            t.pending = []
        self._last_end = ts
        first = self._turns == 0
        self._turns += 1
        msgs = list(t.msgs.values())
        prices = {id(m): self._price(m) for m in msgs}
        priced = sum((p for p in prices.values() if p is not None), Decimal(0))
        if billed is not None:
            cost, cost_kind = billed, "billed"
        elif any(p is not None for p in prices.values()):
            cost, cost_kind = priced, "priced"
            if interrupted:
                self._priced_since += priced
        else:
            cost, cost_kind = Decimal(0), "unpriced"
        secs = (duration_ms / 1000) if duration_ms else max(ts - t.first_ts, 0.0)
        tok = [
            sum(getattr(m, k) for m in msgs) for k in ("inp", "out", "cc5", "cc1", "cr")
        ]
        ctx = [m.context for m in msgs if m.tokens]
        model = (
            next((m.model for m in reversed(msgs) if m.model), None) or self.model or ""
        )
        turn = TurnRow(
            rec=i,
            ts=ts,
            person=self.info.person,
            kind=self.info.kind,
            harness=self.harness,
            model=model,
            mode=self._mode(t.wake, first),
            billing=self._billing(),
            cost=cost,
            cost_kind=cost_kind,
            tok_in=tok[0],
            tok_out=tok[1],
            tok_cw=tok[2] + tok[3],
            tok_cr=tok[4],
            secs=secs,
            calls=len(t.calls),
            errors=t.errors,
            exits=t.exits,
            ctx_start=ctx[0] if ctx else 0,
            ctx_peak=max(ctx) if ctx else 0,
            continues=self.info.continues if first else None,
        )
        out = Derived(turns=[turn])
        out.bands, out.whatifs = self._context(i, msgs, prices, cost)
        out.settles = self._purposes(t, i)
        out.parts = self._parts(i, ts, t, msgs, prices, cost, secs, sum(tok))
        for p in out.parts:
            if not p.settled:
                kind = "code" if p.activity == "editing" else "support"
                self._open.append((i, p.n, p.repo, kind))
        return out

    def _context(
        self, i: int, msgs: list[_Msg], prices: dict, cost: Decimal
    ) -> tuple[list[BandRow], list[WhatIfRow]]:
        """The turn's calls, cost and cache reads per context band, its cost
        shared by the calls' weight with the last band taking the rounding,
        and the replay's handoffs and savings per threshold passed."""
        calls = [m for m in msgs if m.tokens]
        w = {id(m): Fraction(prices[id(m)]) if prices[id(m)] is not None
             else Fraction(m.tokens) for m in calls}  # fmt: skip
        whole = sum(w.values())
        bands: dict[int, BandRow] = {}
        whatif: dict[int, WhatIfRow] = {}
        for m in calls:
            b = bands.setdefault(
                band(m.context), BandRow(i, band(m.context), 0, Decimal(0), 0)
            )
            b.calls += 1
            b.tok_cr += m.cr
            if whole:
                b.cost += _dec(Fraction(cost) * w[id(m)] / whole)
            rates = self._rates(m)
            if rates is None:
                continue
            for at, (h, saved, rewrite) in self._replay.call(m.context, rates).items():
                r = whatif.setdefault(at, WhatIfRow(i, at, 0, Decimal(0), Decimal(0)))
                r.handoffs += h
                r.saved += saved
                r.rewrite += rewrite
        rows = list(bands.values())
        if rows and whole:
            rows[-1].cost = cost - sum((b.cost for b in rows[:-1]), Decimal(0))
        return rows, list(whatif.values())

    def _billing(self) -> str:
        """The login a turn ran on: Claude's subscription unless its init
        names an API key, OpenCode Go's only for a Go model, Codex's plan."""
        if self.harness == "codex":
            return "codex"
        if self.harness == "claude-code" and self._key_source not in (None, "none"):
            return "api"
        if self.harness == "opencode" and not (self.model or "").startswith(
            "opencode-go/"
        ):
            return "api"
        return self.info.account(self.harness) or "api"

    def _shares(self, t: _Turn, msgs: list[_Msg], prices: dict) -> dict[str, Fraction]:
        """Each call's share of the turn, summing to 1."""

        def weight(m: _Msg) -> Fraction:
            p = prices[id(m)]
            return Fraction(p) if p is not None else Fraction(m.tokens)

        tool_msgs = [m for m in msgs if m.calls]
        w = {id(m): weight(m) for m in msgs}
        whole = sum(w.values())
        shares: dict[str, Fraction] = {}
        if not whole:
            for m in tool_msgs:
                for c in m.calls:
                    shares[c] = Fraction(1, len(tool_msgs) * len(m.calls))
            return shares
        spread = sum(w[id(m)] for m in msgs if not m.calls)
        tw = sum(w[id(m)] for m in tool_msgs)
        for m in tool_msgs:
            if tw:
                share = (w[id(m)] + spread * w[id(m)] / tw) / whole
            else:
                share = Fraction(1, len(tool_msgs))
            for c in m.calls:
                shares[c] = share / len(m.calls)
        return shares

    def _parts(
        self,
        i: int,
        ts: float,
        t: _Turn,
        msgs: list[_Msg],
        prices: dict,
        cost: Decimal,
        secs: float,
        tokens: int,
    ) -> list[PartRow]:
        if not t.calls:
            repo = self._last_repo or self._cwd_repo()
            return [
                PartRow(i, 0, ts, repo, "chat", "talking", True, cost, tokens, secs)
            ]
        shares = self._shares(t, msgs, prices)
        groups: dict[tuple, Fraction] = {}
        counts: dict[tuple, list[int]] = {}
        for c in t.calls.values():
            key = (c.repo, c.purpose, c.activity, c.kind is None)
            groups[key] = groups.get(key, Fraction(0)) + shares.get(c.id, Fraction(0))
            n = counts.setdefault(key, [0, 0, 0])
            n[0] += 1
            n[1] += c.is_error
            n[2] += is_shell_exit(c.tool, c.output, c.is_error)
        keys = list(groups)
        # Integer tokens by largest remainder, so the parts sum to the turn.
        exact = [groups[k] * tokens for k in keys]
        toks = [int(x) for x in exact]
        for k in sorted(
            range(len(keys)), key=lambda k: exact[k] - toks[k], reverse=True
        )[: tokens - sum(toks)]:
            toks[k] += 1
        parts, spent = [], Decimal(0)
        for n, key in enumerate(keys):
            share = groups[key]
            c = cost - spent if n == len(keys) - 1 else cost * _dec(share)
            spent += c
            repo, purpose, act, settled = key
            parts.append(
                PartRow(
                    i,
                    n,
                    ts,
                    repo,
                    purpose,
                    act,
                    settled,
                    c,
                    toks[n],
                    secs * float(share),
                    *counts[key],
                )
            )
        return parts
