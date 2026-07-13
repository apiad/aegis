# Native lovelaice agent (VS4 — token usage surfacing) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: superpowers:executing-plans. Steps use checkbox (`- [ ]`) syntax.

**Goal:** ACP `PromptResponse.usage` carries real token counts, so aegis's status-line metrics stop showing 0/0 for native lovelaice sessions.

**Architecture:** Entirely server-side (no engine change). Each `AssistantMessageFinalized.message` carries `.usage` (lingo `Usage(prompt_tokens, completion_tokens, total_tokens)`). `AcpServerV1` already subscribes to these events — accumulate per-turn usage and build an ACP `Usage` into `PromptResponse`.

**Scope note:** VS4 is scoped to **usage** — the clear, low-risk parity win. The other originally-listed VS4 items are deferred: **streaming chunks** (a real engine change — lingo `on_token` callback + a new delta event + changed emit strategy), **`load_session` resume** (needs a session_id→conversation persistence design for the default factory), and **`workflow/run`/`conversation/archive` ext-methods** (no consumer until the deferred warden migration). Each can be its own later slice.

## Global Constraints
- No changes to the legacy `AcpServer` or the lovelaice engine — server-side only.
- lingo `Usage`: `prompt_tokens`/`completion_tokens`/`total_tokens`. ACP `Usage`: `input_tokens`/`output_tokens`/`total_tokens` (+ cached/thought, left 0). Map prompt→input, completion→output.
- Ship lovelaice **2.10.0**; aegis floor → `>=2.10,<3`.
- Real-model probe confirms non-zero usage before release.

## File Structure
- Modify `src/lovelaice/acp/v1/server.py` — per-turn usage accumulator + `PromptResponse(usage=…)`.
- Extend `tests/acp/v1/test_server_v1.py`.
- Bump `pyproject.toml` + `CHANGELOG.md`; aegis `pyproject.toml` floor.

## Task 1: accumulate turn usage → PromptResponse.usage

**Files:** Modify `src/lovelaice/acp/v1/server.py`; Test `tests/acp/v1/test_server_v1.py`.

**Interfaces:**
- `prompt` resets a per-turn accumulator, and after the turn builds `acp.schema.Usage(input_tokens=Σprompt, output_tokens=Σcompletion, total_tokens=Σtotal)` when any tokens were seen; else `usage=None`.
- `_emit` (or `_translate`) accumulates `ev.message.usage` on each `AssistantMessageFinalized`.

- [ ] **Step 1: Failing test**

```python
# tests/acp/v1/test_server_v1.py (append)
@pytest.mark.asyncio
async def test_prompt_surfaces_token_usage(tmp_path):
    from lovelaice.agent.events import AssistantMessageFinalized

    class _UsageMsg:
        content = "answer"
        class usage:  # lingo Usage-shaped
            prompt_tokens, completion_tokens, total_tokens = 100, 20, 120

    # Factory whose agent, when prompted, emits one finalized message with usage.
    class _Ag:
        def __init__(self): self._subs = []
        def subscribe(self, fn): self._subs.append(fn)
        async def prompt(self, text):
            for fn in self._subs:
                fn(AssistantMessageFinalized(message=_UsageMsg()))
            from lovelaice.agent.errors import StopReason
            return StopReason.END_TURN

    server = AcpServerV1(agent_factory=lambda **kw: _Ag())
    server.on_connect(_FakeConn())
    new = await server.new_session(cwd=str(tmp_path))
    resp = await server.prompt(prompt=[{"type": "text", "text": "hi"}],
                               session_id=new.session_id)
    assert resp.usage is not None
    assert resp.usage.input_tokens == 100
    assert resp.usage.output_tokens == 20
    assert resp.usage.total_tokens == 120
```

- [ ] **Step 2: Run → fail** (`resp.usage is None`). `cd repos/lovelaice && uv run python -m pytest tests/acp/v1/test_server_v1.py::test_prompt_surfaces_token_usage -v`

- [ ] **Step 3: Implement** in `server.py`:
  - `__init__`: `self._turn_usage: dict[str, int] = {}` is overkill; use three ints reset per prompt. Add `self._acc_in = self._acc_out = self._acc_total = 0`.
  - In `_emit`, before translating, accumulate:
    ```python
    if isinstance(ev, AssistantMessageFinalized):
        u = getattr(ev.message, "usage", None)
        if u is not None:
            self._acc_in += int(getattr(u, "prompt_tokens", 0) or 0)
            self._acc_out += int(getattr(u, "completion_tokens", 0) or 0)
            self._acc_total += int(getattr(u, "total_tokens", 0) or 0)
    ```
  - In `prompt`, set `self._acc_in = self._acc_out = self._acc_total = 0` before running the turn; after, build usage:
    ```python
    usage = None
    if self._acc_in or self._acc_out or self._acc_total:
        from acp.schema import Usage
        usage = Usage(input_tokens=self._acc_in, output_tokens=self._acc_out,
                      total_tokens=self._acc_total or (self._acc_in + self._acc_out))
    return acp.PromptResponse(stop_reason=value, usage=usage)
    ```
  Also set `usage=None` on the cancelled-path PromptResponse.

- [ ] **Step 4: Run → pass** the new test + the full v1 suite (`uv run python -m pytest tests/acp/v1 -q`).
- [ ] **Step 5: Commit** — `feat(acp-v1): surface per-turn token usage in PromptResponse`

## Task 2: real-model probe + release 2.10.0

- [ ] **Step 1:** Probe (install local editable into aegis, `uv run --no-sync`): a real haiku turn through `LovelaiceDriver`; assert the terminal `Result` event carries non-zero `usage.input`/`output`. (aegis's `AcpSession.send` already maps `PromptResponse.usage` → `TokenUsage`.)
- [ ] **Step 2:** Bump `version = "2.10.0"`; CHANGELOG § 2.10.0 (token usage in ACP v1 PromptResponse). Full suite green.
- [ ] **Step 3:** Commit, push, `gh release create v2.10.0 …` → OIDC publish; poll PyPI == 2.10.0.

## Task 3: aegis bump + metrics check

- [ ] **Step 1:** aegis `pyproject.toml` → `lovelaice>=2.10,<3`; `uv lock --upgrade-package lovelaice`, `uv sync` (separate steps, check rc; don't pipe the gate).
- [ ] **Step 2:** Re-run `tests/test_lovelaice_live.py` + `tests/test_lovelaice_mcp_live.py` green; a quick check that a live run's `Result` usage is non-zero.
- [ ] **Step 3:** Commit + push.

## Self-Review
**Coverage:** usage surfaced (T1), proven with a real model (T2), consumed by aegis (T3). Streaming / load_session / ext-methods explicitly deferred with rationale in the scope note.
**Type consistency:** lingo `Usage(prompt_tokens,…)` → ACP `Usage(input_tokens,…)` mapping consistent T1↔spec. `PromptResponse(usage=Usage|None)` matches the SDK annotation.
