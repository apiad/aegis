# Aegis Web Client — S1 (Foundation) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the shared foundation the web client needs — a YAML-backed theme that emits a Textual `Theme`, an `AegisColors`, and CSS variables; a `render_event_html` sibling to `render_event` that shares a medium-agnostic formatter layer; and transcript-windowing constants in a single source of truth — without changing any TUI behavior.

**Architecture:** A new `aegis.themes` package owns the canonical theme data (loaded from `src/aegis/data/themes/*.yaml`) and the `AegisColors` dataclass; `aegis.tui.themes` becomes a thin back-compat shim re-exporting the same names so every existing importer and test is untouched. The per-kind rendering *semantics* (icons, plan glyphs, diff windowing, result formatting) move into a pure `aegis.render_shared` module that both the existing Rich renderer (`aegis.render`) and a new HTML renderer (`aegis.render_html`) consume. Transcript constants move from `tui/pane.py` into `aegis.transcript_constants`.

**Tech Stack:** Python 3.13+, `uv`, `pytest`, PyYAML (already a dependency), Textual 8.x, Rich. No new third-party dependencies.

## Global Constraints

- Python **3.13+**.
- Dependency + test commands use **`uv`**: `uv run pytest -q -m "not live"` for the fast hermetic suite.
- **TDD**: failing test first → minimal implementation → commit per logical unit.
- **No behavior change to the TUI.** Every existing test must pass unchanged, especially `tests/test_themes.py`, `tests/test_render_event.py`, `tests/test_pane_windowing.py`.
- **No new third-party dependencies.** PyYAML is already available (used across `cli_config.py`, `cli_schedule.py`).
- **Option B (colors-only YAML).** The theme YAML carries *colors only*. Event icons and plan glyphs stay as Python constants shared by both renderers — they are identical Unicode in terminal and browser. Do **not** move icons/glyphs into YAML in this slice.
- Commit straight to **main** (no feature branch). **Conventional commits.**
- Color hex values are copied **verbatim** from the current `src/aegis/tui/themes.py` so existing assertions hold.

---

## File Structure

**New files:**
- `src/aegis/data/themes/aegis-ink.yaml` — color data for the default theme (extracted from `tui/themes.py:INK`).
- `src/aegis/data/themes/aegis-parchment.yaml` — extracted from `tui/themes.py:PARCHMENT`.
- `src/aegis/data/themes/aegis-slate.yaml` — extracted from `tui/themes.py:SLATE`.
- `src/aegis/themes/__init__.py` — `AegisColors`, `AegisTheme`, `aegis_colors()`, `load_theme()`, `list_theme_names()`.
- `src/aegis/render_shared.py` — `KIND_ICON`, `PLAN_STATUS_GLYPH`, `pathhint()`, `diff_window()`, `result_parts()`. Pure, medium-agnostic.
- `src/aegis/render_html.py` — `render_event_html(ev) -> str | None`.
- `src/aegis/transcript_constants.py` — `N_MAX`, `EVICT_BATCH`, `LOAD_BATCH`, `STICKY_EPS`, `LOAD_MORE_EPS`, `DEBOUNCE_S`.
- `tests/test_theme_loader.py`, `tests/test_render_html.py`, `tests/test_transcript_constants.py`.

**Modified files:**
- `src/aegis/tui/themes.py` — becomes a thin shim re-exporting from `aegis.themes`, building `INK`/`PARCHMENT`/`SLATE` Textual themes from loaded YAML.
- `src/aegis/render.py` — imports the shared helpers from `render_shared`; deletes local duplicates; behavior unchanged.
- `src/aegis/tui/pane.py` — imports the six constants from `aegis.transcript_constants` instead of defining them.

**Investigation deliverable (no code):**
- Append a "Persistence reality check (S1 audit)" note to `docs/superpowers/specs/2026-06-19-aegis-web-client-design.md`.

---

### Task 1: `aegis.themes` package — YAML theme loader

**Files:**
- Create: `src/aegis/data/themes/aegis-ink.yaml`
- Create: `src/aegis/data/themes/aegis-parchment.yaml`
- Create: `src/aegis/data/themes/aegis-slate.yaml`
- Create: `src/aegis/themes/__init__.py`
- Test: `tests/test_theme_loader.py`

**Interfaces:**
- Consumes: nothing (new, isolated — no existing module imports it yet).
- Produces:
  - `AegisColors` — frozen dataclass, fields exactly: `ready, working, error, accent, muted, ok, err, user, user_bg, ink="", work=""` (identical to the current `tui/themes.py` definition).
  - `AegisTheme` — frozen dataclass: `name: str`, `dark: bool`, `colors: dict[str, str]`, `variables: dict[str, str]`. Methods: `to_textual_theme() -> textual.theme.Theme`, `to_aegis_colors() -> AegisColors`, `to_css_variables() -> str`.
  - `aegis_colors(theme: textual.theme.Theme) -> AegisColors` — same logic as the current `tui/themes.py:aegis_colors`.
  - `load_theme(name: str, user_dir: Path | None = None) -> AegisTheme` — reads `src/aegis/data/themes/<name>.yaml`, deep-merges an optional `<user_dir>/<name>.yaml` overlay over `colors` and `variables`. Raises `FileNotFoundError` on a missing base file.
  - `list_theme_names() -> list[str]` — sorted stems of the bundled `*.yaml` files.

- [ ] **Step 1: Write the failing test**

Create `tests/test_theme_loader.py`:

```python
from pathlib import Path

from aegis.themes import (
    AegisColors, AegisTheme, aegis_colors, load_theme, list_theme_names,
)


def test_load_ink_reproduces_textual_theme():
    t = load_theme("aegis-ink")
    assert isinstance(t, AegisTheme)
    tt = t.to_textual_theme()
    assert tt.name == "aegis-ink"
    assert tt.dark is True
    assert tt.background == "#0e0e0d"
    assert tt.foreground == "#DCD9CF"
    assert tt.accent == "#E0A872"
    assert tt.success == "#9DB07E"
    assert tt.variables["aegis-muted"] == "#76736a"
    assert tt.variables["aegis-userbg"] == "#24241f"


def test_to_aegis_colors_matches_golden():
    c = load_theme("aegis-ink").to_aegis_colors()
    assert isinstance(c, AegisColors)
    assert c.ready == "#9DB07E"
    assert c.working == "#E0A872"
    assert c.error == "#C56B5C"
    assert c.accent == "#E0A872"
    assert c.muted == "#76736a"
    assert c.user_bg == "#24241f"


def test_to_css_variables_emits_expected_vars():
    css = load_theme("aegis-ink").to_css_variables()
    assert ":root" in css
    assert "--aegis-bg: #0e0e0d" in css
    assert "--aegis-fg: #DCD9CF" in css
    assert "--aegis-accent: #E0A872" in css
    assert "--aegis-muted: #76736a" in css
    assert "--aegis-user-bg: #24241f" in css
    assert "--aegis-ok: #9DB07E" in css
    assert "--aegis-err: #C56B5C" in css


def test_all_three_bundled_themes_load():
    names = list_theme_names()
    assert {"aegis-ink", "aegis-parchment", "aegis-slate"} <= set(names)
    for name in ("aegis-ink", "aegis-parchment", "aegis-slate"):
        assert load_theme(name).to_textual_theme().name == name


def test_missing_theme_raises():
    import pytest
    with pytest.raises(FileNotFoundError):
        load_theme("does-not-exist")


def test_overlay_merges_over_base(tmp_path: Path):
    overlay = tmp_path / "aegis-ink.yaml"
    overlay.write_text("colors:\n  accent: \"#ABCDEF\"\n", encoding="utf-8")
    t = load_theme("aegis-ink", user_dir=tmp_path)
    # Overlaid key wins; untouched keys keep base values.
    assert t.colors["accent"] == "#ABCDEF"
    assert t.colors["background"] == "#0e0e0d"
    assert t.variables["aegis-muted"] == "#76736a"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_theme_loader.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'aegis.themes'`.

- [ ] **Step 3: Create the three YAML data files**

`src/aegis/data/themes/aegis-ink.yaml`:

```yaml
name: aegis-ink
dark: true
colors:
  background: "#0e0e0d"
  surface: "#141412"
  panel: "#1a1a17"
  foreground: "#DCD9CF"
  primary: "#E0A872"
  accent: "#E0A872"
  success: "#9DB07E"
  warning: "#E0A872"
  error: "#C56B5C"
variables:
  aegis-muted: "#76736a"
  aegis-faint: "#4a4843"
  aegis-rule: "#26241f"
  aegis-userbg: "#24241f"
```

`src/aegis/data/themes/aegis-parchment.yaml`:

```yaml
name: aegis-parchment
dark: true
colors:
  background: "#1c1a16"
  surface: "#201e19"
  panel: "#23211c"
  foreground: "#E9E2D2"
  primary: "#D97757"
  accent: "#D97757"
  success: "#9CAE78"
  warning: "#E3B341"
  error: "#E0775F"
variables:
  aegis-muted: "#8c8676"
  aegis-faint: "#5c574a"
  aegis-rule: "#3a362d"
  aegis-userbg: "#2b281f"
```

`src/aegis/data/themes/aegis-slate.yaml`:

```yaml
name: aegis-slate
dark: true
colors:
  background: "#10141b"
  surface: "#13171f"
  panel: "#161b24"
  foreground: "#CDD6E3"
  primary: "#E0A35E"
  accent: "#E0A35E"
  success: "#5FB39A"
  warning: "#E0A35E"
  error: "#E07A86"
variables:
  aegis-muted: "#6b7686"
  aegis-faint: "#454e5c"
  aegis-rule: "#27303d"
  aegis-userbg: "#1e2530"
```

- [ ] **Step 4: Write `src/aegis/themes/__init__.py`**

```python
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml
from textual.theme import Theme as TextualTheme

_DATA_DIR = Path(__file__).resolve().parent.parent / "data" / "themes"
_DEFAULT_USER_DIR = Path(".aegis") / "themes"


@dataclass(frozen=True)
class AegisColors:
    ready: str
    working: str
    error: str
    accent: str
    muted: str
    ok: str
    err: str
    user: str
    user_bg: str
    ink: str = ""        # default foreground / "ink" of the page
    work: str = ""       # alias for working — used by queue dashboard


def aegis_colors(theme: TextualTheme) -> AegisColors:
    fg = theme.foreground or "#DCD9CF"
    variables = theme.variables or {}

    def var(key: str) -> str:
        return variables.get(key) or fg

    return AegisColors(
        ready=theme.success or fg,
        working=theme.warning or fg,
        error=theme.error or fg,
        accent=theme.accent or fg,
        muted=var("aegis-muted"),
        ok=theme.success or fg,
        err=theme.error or fg,
        user=theme.accent or fg,
        user_bg=var("aegis-userbg"),
        ink=theme.foreground or fg,
        work=theme.warning or fg,
    )


@dataclass(frozen=True)
class AegisTheme:
    name: str
    dark: bool
    colors: dict[str, str]
    variables: dict[str, str]

    def to_textual_theme(self) -> TextualTheme:
        c = self.colors
        return TextualTheme(
            name=self.name,
            dark=self.dark,
            background=c["background"],
            surface=c["surface"],
            panel=c["panel"],
            foreground=c["foreground"],
            primary=c["primary"],
            accent=c["accent"],
            success=c["success"],
            warning=c["warning"],
            error=c["error"],
            variables=dict(self.variables),
        )

    def to_aegis_colors(self) -> AegisColors:
        return aegis_colors(self.to_textual_theme())

    def to_css_variables(self) -> str:
        c = self.colors
        v = self.variables
        lines = [
            ("--aegis-bg", c["background"]),
            ("--aegis-surface", c["surface"]),
            ("--aegis-panel", c["panel"]),
            ("--aegis-fg", c["foreground"]),
            ("--aegis-primary", c["primary"]),
            ("--aegis-accent", c["accent"]),
            ("--aegis-ready", c["success"]),
            ("--aegis-working", c["warning"]),
            ("--aegis-error", c["error"]),
            ("--aegis-ok", c["success"]),
            ("--aegis-err", c["error"]),
            ("--aegis-user", c["accent"]),
            ("--aegis-muted", v.get("aegis-muted", c["foreground"])),
            ("--aegis-faint", v.get("aegis-faint", c["foreground"])),
            ("--aegis-rule", v.get("aegis-rule", c["foreground"])),
            ("--aegis-user-bg", v.get("aegis-userbg", c["background"])),
        ]
        body = "\n".join(f"  {k}: {val};" for k, val in lines)
        return ":root {\n" + body + "\n}\n"


def _deep_merge(base: dict, overlay: dict) -> dict:
    out = dict(base)
    for key, val in overlay.items():
        if isinstance(val, dict) and isinstance(out.get(key), dict):
            out[key] = _deep_merge(out[key], val)
        else:
            out[key] = val
    return out


def load_theme(name: str, user_dir: Path | None = None) -> AegisTheme:
    base_path = _DATA_DIR / f"{name}.yaml"
    if not base_path.exists():
        raise FileNotFoundError(f"no bundled theme named {name!r} at {base_path}")
    data = yaml.safe_load(base_path.read_text(encoding="utf-8")) or {}

    overlay_dir = user_dir if user_dir is not None else _DEFAULT_USER_DIR
    overlay_path = overlay_dir / f"{name}.yaml"
    if overlay_path.exists():
        overlay = yaml.safe_load(overlay_path.read_text(encoding="utf-8")) or {}
        data = _deep_merge(data, overlay)

    return AegisTheme(
        name=data["name"],
        dark=bool(data.get("dark", True)),
        colors=dict(data["colors"]),
        variables=dict(data.get("variables", {})),
    )


def list_theme_names() -> list[str]:
    return sorted(p.stem for p in _DATA_DIR.glob("*.yaml"))
```

- [ ] **Step 5: Run test to verify it passes**

Run: `uv run pytest tests/test_theme_loader.py -q`
Expected: PASS (6 tests).

- [ ] **Step 6: Verify the package data ships**

Run: `uv run python -c "from aegis.themes import load_theme; print(load_theme('aegis-ink').to_textual_theme().name)"`
Expected: prints `aegis-ink`. If it raises `FileNotFoundError`, the `data/themes/*.yaml` files are not packaged — check `pyproject.toml` `[tool.*]` package-data / `include` rules and confirm `src/aegis/data/` is already shipped (it is, for `models.yaml`); the new `themes/` subdir under it is covered by the same rule.

- [ ] **Step 7: Commit**

```bash
git add src/aegis/themes/ src/aegis/data/themes/ tests/test_theme_loader.py
git commit -m "feat(themes): YAML-backed theme loader (AegisTheme + CSS variables)"
```

---

### Task 2: Make `tui/themes.py` a thin shim over the loader

**Files:**
- Modify: `src/aegis/tui/themes.py` (full rewrite — 110 lines → ~20)
- Test: `tests/test_themes.py` (existing — must pass unchanged, do NOT edit)

**Interfaces:**
- Consumes: `aegis.themes.load_theme`, `AegisColors`, `aegis_colors` (from Task 1).
- Produces (unchanged public surface): `INK`, `PARCHMENT`, `SLATE` (Textual `Theme` objects), `THEMES: dict[str, Theme]`, `DEFAULT_THEME: str`, `AegisColors`, `aegis_colors`. Importers (`app.py`, `pane.py`, `state.py`, `widgets.py`, several tests) keep working through re-exports.

- [ ] **Step 1: Confirm the existing test is the spec**

Run: `uv run pytest tests/test_themes.py -q`
Expected: PASS (4 tests) against the current implementation. This is the contract the shim must preserve — do not modify this test file.

- [ ] **Step 2: Rewrite `src/aegis/tui/themes.py` as a shim**

```python
"""Back-compat shim. Theme data now lives in YAML under
``src/aegis/data/themes/`` and is loaded by ``aegis.themes``. This module
re-exports the names the TUI has always imported, building the Textual
``Theme`` objects from the loaded YAML so existing call sites and snapshot
tests are unaffected.
"""
from __future__ import annotations

from textual.theme import Theme

from aegis.themes import AegisColors, aegis_colors, load_theme

INK: Theme = load_theme("aegis-ink").to_textual_theme()
PARCHMENT: Theme = load_theme("aegis-parchment").to_textual_theme()
SLATE: Theme = load_theme("aegis-slate").to_textual_theme()

THEMES: dict[str, Theme] = {"ink": INK, "parchment": PARCHMENT, "slate": SLATE}
DEFAULT_THEME = "aegis-ink"

__all__ = [
    "INK", "PARCHMENT", "SLATE", "THEMES", "DEFAULT_THEME",
    "AegisColors", "aegis_colors",
]
```

- [ ] **Step 3: Run the theme test to verify it still passes**

Run: `uv run pytest tests/test_themes.py -q`
Expected: PASS (4 tests) — `INK.name == "aegis-ink"`, `aegis_colors(INK).muted == "#76736a"`, `user_bg == "#24241f"`, etc., all hold because the YAML carries the verbatim hex values.

- [ ] **Step 4: Run every theme/render/TUI consumer test**

Run: `uv run pytest tests/test_themes.py tests/test_render_event.py tests/test_tui.py tests/test_tui_state.py tests/test_tui_dashboard.py tests/test_pending_strip.py tests/test_tui_strip.py -q`
Expected: PASS. These import `AegisColors`/`aegis_colors`/`INK`/`THEMES` from `aegis.tui.themes`; the re-exports keep them green.

- [ ] **Step 5: Run the full hermetic suite as a regression gate**

Run: `uv run pytest -q -m "not live"`
Expected: PASS (no regressions). Check the exit code is 0 — do not pipe to `tail`.

- [ ] **Step 6: Commit**

```bash
git add src/aegis/tui/themes.py
git commit -m "refactor(themes): tui/themes becomes a thin shim over aegis.themes"
```

---

### Task 3: Extract shared, medium-agnostic render helpers

**Files:**
- Create: `src/aegis/render_shared.py`
- Modify: `src/aegis/render.py` (delete local `_KIND_ICON`, `_PLAN_STATUS_GLYPH`, `_pathhint`; rewire `_render_diff`, `_render_agent_plan`, the `ToolUse` and `Result` branches to the shared helpers)
- Test: `tests/test_render_event.py` (existing — must pass unchanged, do NOT edit)

**Interfaces:**
- Consumes: `aegis.events` types.
- Produces (the shared layer both renderers import):
  - `KIND_ICON: dict[str, str]` — semantic-kind → emoji (verbatim from the current `_KIND_ICON`).
  - `PLAN_STATUS_GLYPH: dict[str, str]` — `{"completed": "●", "in_progress": "◐", "pending": "○"}`.
  - `pathhint(ev) -> str` — tail of `locations[0]` with optional `:line`, else `ev.summary` (verbatim from `_pathhint`).
  - `diff_window(old_text: str, new_text: str, max_lines: int = 6) -> tuple[list[str], list[str], int]` — returns `(shown_removed, shown_added, elided)` after trimming common prefix/suffix and capping total shown rows at `max_lines` (removed first, then added).
  - `result_parts(ev) -> list[str]` — `["done in X.Xs", <cost?>, <stop_reason?>]` (cost via `aegis.tui.metrics._fmt_cost`; `stop_reason` omitted when `end_turn`).

- [ ] **Step 1: Write the failing test**

Create `tests/test_render_shared.py`:

```python
from aegis.events import Result, ToolUse
from aegis.render_shared import (
    KIND_ICON, PLAN_STATUS_GLYPH, diff_window, pathhint, result_parts,
)


def test_kind_icon_and_glyph_tables():
    assert KIND_ICON["read"] == "📖"
    assert KIND_ICON["execute"] == "⌬"
    assert PLAN_STATUS_GLYPH == {
        "completed": "●", "in_progress": "◐", "pending": "○"}


def test_pathhint_prefers_location_tail_with_line():
    ev = ToolUse(name="Read", summary="", kind="read",
                 locations=(("/deep/nested/foo.py", 42),))
    assert pathhint(ev) == "foo.py:42"


def test_pathhint_falls_back_to_summary():
    ev = ToolUse(name="Bash", summary="echo hi", kind="execute")
    assert pathhint(ev) == "echo hi"


def test_diff_window_trims_common_and_reports_change():
    removed, added, elided = diff_window("alpha\nbeta\n", "alpha\nGAMMA\nbeta\n")
    assert removed == []
    assert added == ["GAMMA"]
    assert elided == 0


def test_diff_window_caps_to_max_lines():
    old = "".join(f"old-{i}\n" for i in range(40))
    new = "".join(f"new-{i}\n" for i in range(40))
    removed, added, elided = diff_window(old, new, max_lines=6)
    assert len(removed) + len(added) == 6
    assert elided == (40 + 40) - 6


def test_result_parts_duration_cost_and_stop_reason():
    parts = result_parts(Result(duration_ms=2500, is_error=False,
                                 cost_usd=0.05, stop_reason="refusal"))
    assert parts[0] == "done in 2.5s"
    assert "5¢" in parts
    assert "refusal" in parts


def test_result_parts_omits_end_turn():
    parts = result_parts(Result(duration_ms=1000, is_error=False,
                                stop_reason="end_turn"))
    assert all("end_turn" not in p for p in parts)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_render_shared.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'aegis.render_shared'`.

- [ ] **Step 3: Write `src/aegis/render_shared.py`**

```python
"""Medium-agnostic render helpers shared by the Rich renderer
(``aegis.render``) and the HTML renderer (``aegis.render_html``). Pure
functions and lookup tables only — no Rich, no HTML, no I/O.
"""
from __future__ import annotations

# Glyph per semantic kind (parity with ACP's tool_call kind enum; claude
# paths derive kind from the tool name in events.py).
KIND_ICON = {
    "read": "📖",
    "edit": "✏️",
    "execute": "⌬",
    "search": "🔎",
    "think": "✻",
    "fetch": "🌐",
    "move": "➡️",
    "delete": "🗑",
    "switch_mode": "🔄",
    "other": "⏺",
}

PLAN_STATUS_GLYPH = {
    "completed": "●",
    "in_progress": "◐",
    "pending": "○",
}


def pathhint(ev) -> str:
    """One-line context for a tool call: the tail of the first known
    location (with :line suffix when known), falling back to the tool's
    legacy summary string."""
    if ev.locations:
        path, line = ev.locations[0]
        tail = path.rsplit("/", 1)[-1] if path else ""
        if line is not None:
            return f"{tail}:{line}"
        return tail
    return ev.summary


def diff_window(old_text: str, new_text: str,
                max_lines: int = 6) -> tuple[list[str], list[str], int]:
    """Trim a (old_text, new_text) pair to the changed window and cap the
    visible rows. Returns ``(shown_removed, shown_added, elided)`` — removed
    rows fill the budget first, then added; ``elided`` is how many changed
    rows were dropped past ``max_lines``. Common prefix/suffix lines are
    elided — this is a change preview, not a diff viewer."""
    old_lines = old_text.splitlines() if old_text else []
    new_lines = new_text.splitlines() if new_text else []
    head = 0
    while (head < len(old_lines) and head < len(new_lines)
           and old_lines[head] == new_lines[head]):
        head += 1
    tail = 0
    while (tail < len(old_lines) - head
           and tail < len(new_lines) - head
           and old_lines[len(old_lines) - 1 - tail]
               == new_lines[len(new_lines) - 1 - tail]):
        tail += 1
    removed = old_lines[head:len(old_lines) - tail]
    added = new_lines[head:len(new_lines) - tail]

    shown_removed: list[str] = []
    shown_added: list[str] = []
    budget = max_lines
    for line in removed:
        if budget <= 0:
            break
        shown_removed.append(line)
        budget -= 1
    for line in added:
        if budget <= 0:
            break
        shown_added.append(line)
        budget -= 1
    elided = (len(removed) + len(added)) \
        - (len(shown_removed) + len(shown_added))
    return shown_removed, shown_added, elided


def result_parts(ev) -> list[str]:
    """The segments of a turn-terminator line: duration, optional cost,
    optional non-boring stop_reason. Joined with ' · ' by each renderer."""
    secs = (ev.duration_ms or 0) / 1000
    parts = [f"done in {secs:.1f}s"]
    if ev.cost_usd is not None and ev.cost_usd > 0:
        from decimal import Decimal
        from aegis.tui.metrics import _fmt_cost
        parts.append(_fmt_cost(Decimal(str(ev.cost_usd))))
    if ev.stop_reason and ev.stop_reason != "end_turn":
        parts.append(ev.stop_reason)
    return parts
```

- [ ] **Step 4: Run the new test to verify it passes**

Run: `uv run pytest tests/test_render_shared.py -q`
Expected: PASS (7 tests).

- [ ] **Step 5: Rewire `src/aegis/render.py` to use the shared helpers**

Make these edits to `src/aegis/render.py`:

1. Add the import after the existing `aegis.events` import block:

```python
from aegis.render_shared import (
    KIND_ICON, PLAN_STATUS_GLYPH, diff_window, pathhint, result_parts,
)
```

2. Delete the module-level `_PLAN_STATUS_GLYPH` dict, the `_KIND_ICON` dict, and the `_pathhint` function (their content now lives in `render_shared`).

3. Replace the body of `_render_diff` so it uses `diff_window` (the rendering stays identical):

```python
def _render_diff(diff: tuple[str, str, str], colors,
                  max_lines: int = 6) -> "Text":
    """Render a (path, old_text, new_text) tuple as a small unified
    preview using the shared diff windowing."""
    path, old_text, new_text = diff
    removed, added, elided = diff_window(old_text, new_text, max_lines)

    body = Text()
    body.append(f"  ┌ {path}\n", style=colors.muted)
    for line in removed:
        body.append("  │ -", style=colors.err)
        body.append(f" {line}\n", style=colors.err)
    for line in added:
        body.append("  │ +", style=colors.ok)
        body.append(f" {line}\n", style=colors.ok)
    if elided > 0:
        body.append(f"  │ … {elided} more line"
                    f"{'s' if elided != 1 else ''}\n",
                    style=colors.muted)
    body.append("  └", style=colors.muted)
    return body
```

4. In `_render_agent_plan`, change `_PLAN_STATUS_GLYPH.get(entry.status, "○")` to `PLAN_STATUS_GLYPH.get(entry.status, "○")`.

5. In `render_event`, the `ToolUse` branch: change `_KIND_ICON.get(ev.kind or "", "⏺")` to `KIND_ICON.get(ev.kind or "", "⏺")` and `_pathhint(ev)` to `pathhint(ev)`.

6. In `render_event`, replace the `Result` branch body with:

```python
    if isinstance(ev, Result):
        return Text(f"── {' · '.join(result_parts(ev))} ──",
                    style=colors.muted)
```

- [ ] **Step 6: Run the existing Rich-renderer test unchanged**

Run: `uv run pytest tests/test_render_event.py -q`
Expected: PASS — every assertion (icons `📖`/`⌬`/`✏`/`🔎`/`✻`/`⏺`, plan glyphs `●`/`◐`/`○`, diff `x.py`/`+`/`GAMMA`, `2.5`, cost `5¢`/`1¢`/`0.5¢`, `max_tokens`, no `end_turn`) holds because the moved code is byte-identical.

- [ ] **Step 7: Commit**

```bash
git add src/aegis/render_shared.py src/aegis/render.py tests/test_render_shared.py
git commit -m "refactor(render): extract medium-agnostic helpers into render_shared"
```

---

### Task 4: `render_event_html` — the HTML renderer

**Files:**
- Create: `src/aegis/render_html.py`
- Test: `tests/test_render_html.py`

**Interfaces:**
- Consumes: `aegis.events` types; `aegis.render_shared` (`KIND_ICON`, `PLAN_STATUS_GLYPH`, `pathhint`, `diff_window`, `result_parts`).
- Produces: `render_event_html(ev: Event) -> str | None`. Returns a self-contained HTML fragment (escaped) for unit-block events; `None` for `SystemInit`/`Unknown` and empty assistant text. **No `colors` parameter** — HTML colors come from theme CSS variables (`to_css_variables`), so the renderer emits stable CSS classes rather than inline colors. This is a deliberate, documented deviation from the spec's `render_event_html(event, palette)` signature.

- [ ] **Step 1: Write the failing test**

Create `tests/test_render_html.py`:

```python
from aegis.events import (
    AgentPlan, AssistantText, AssistantThinking, PlanEntry, Result,
    SystemInit, ToolResult, ToolUse, Unknown,
)
from aegis.render_html import render_event_html


def test_assistant_text_wrapped_and_escaped():
    h = render_event_html(AssistantText("hello world"))
    assert "hello world" in h
    assert "assistant-text" in h


def test_assistant_text_empty_is_none():
    assert render_event_html(AssistantText("   ")) is None


def test_html_escaping_neutralizes_markup():
    h = render_event_html(AssistantText("<script>alert(1)</script>"))
    assert "<script>" not in h
    assert "&lt;script&gt;" in h


def test_tool_use_read_icon_name_and_class():
    h = render_event_html(ToolUse(name="Read", summary="foo.py", kind="read"))
    assert "📖" in h
    assert "Read" in h
    assert "tool-use" in h


def test_tool_use_hint_suppressed_when_equal_to_name():
    h = render_event_html(ToolUse(name="target.txt", summary="", kind="read",
                                  locations=(("/p/target.txt", None),)))
    assert h.count("target.txt") == 1


def test_tool_use_unknown_kind_falls_back_to_dot():
    h = render_event_html(ToolUse(name="X", summary="y"))
    assert "⏺" in h


def test_tool_result_ok_one_liner():
    h = render_event_html(ToolResult(text="bar", is_error=False, kind="read"))
    assert "tool-result" in h
    assert "ok" in h


def test_tool_result_error_marked():
    h = render_event_html(ToolResult(text="boom", is_error=True))
    assert "error" in h


def test_tool_result_diff_preview():
    h = render_event_html(ToolResult(
        text="ok", is_error=False, kind="edit",
        diff=("x.py", "alpha\nbeta\n", "alpha\nGAMMA\nbeta\n")))
    assert "x.py" in h
    assert "GAMMA" in h
    assert "+" in h


def test_agent_plan_glyphs_and_content():
    h = render_event_html(AgentPlan(entries=(
        PlanEntry(content="alpha", status="completed"),
        PlanEntry(content="beta", status="in_progress"),
        PlanEntry(content="gamma", status="pending"),
    )))
    assert "alpha" in h and "beta" in h and "gamma" in h
    assert "●" in h and "◐" in h and "○" in h
    assert "2" not in h.split("Plan")[0]  # header is present, not stray


def test_agent_plan_empty_label():
    h = render_event_html(AgentPlan(entries=()))
    assert "no plan" in h


def test_result_separator_with_cost():
    h = render_event_html(Result(duration_ms=2500, is_error=False,
                                 cost_usd=0.05))
    assert "2.5" in h
    assert "5¢" in h
    assert "result-sep" in h


def test_thinking_shows_content():
    h = render_event_html(AssistantThinking("secret chain"))
    assert "secret chain" in h
    assert "✻" in h


def test_thinking_empty_label():
    h = render_event_html(AssistantThinking(""))
    assert "Thinking" in h


def test_systeminit_and_unknown_are_none():
    assert render_event_html(SystemInit(session_id="x")) is None
    assert render_event_html(Unknown(raw="{}")) is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_render_html.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'aegis.render_html'`.

- [ ] **Step 3: Write `src/aegis/render_html.py`**

```python
"""HTML renderer — sibling to ``aegis.render.render_event``. Emits a
self-contained, escaped HTML fragment per event, mirroring the TUI's
rendering semantics via the shared ``aegis.render_shared`` helpers. Colors
are applied by CSS classes whose values come from the theme's
``to_css_variables()`` output, so this renderer takes no palette argument.
"""
from __future__ import annotations

from html import escape

from aegis.events import (
    AgentPlan, AssistantText, AssistantThinking, Event, Result, SystemInit,
    ToolResult, ToolUse, Unknown,
)
from aegis.render_shared import (
    KIND_ICON, PLAN_STATUS_GLYPH, diff_window, pathhint, result_parts,
)


def render_event_html(ev: Event) -> str | None:
    """Map one typed event to an HTML fragment, or None when it has no
    visible representation."""
    if isinstance(ev, AssistantText):
        text = ev.text.strip()
        if not text:
            return None
        return f'<div class="assistant-text">{escape(text)}</div>'

    if isinstance(ev, AssistantThinking):
        body = (ev.text or "").strip()
        if not body:
            return '<div class="thinking muted">✻ Thinking…</div>'
        return (f'<div class="thinking muted"><em>✻ '
                f'{escape(body)}</em></div>')

    if isinstance(ev, ToolUse):
        icon = KIND_ICON.get(ev.kind or "", "⏺")
        hint = pathhint(ev)
        arg = (f'<span class="tool-hint">({escape(hint)})</span>'
               if hint and hint != ev.name else "")
        return (f'<div class="tool-use">'
                f'<span class="icon">{icon}</span> '
                f'<span class="tool-name">{escape(ev.name)}</span>'
                f'{arg}</div>')

    if isinstance(ev, ToolResult):
        if ev.diff is not None and not ev.is_error:
            return _diff_html(ev.diff)
        first = ev.text.splitlines()[0] if ev.text.strip() else ""
        if len(first) > 100:
            first = first[:100] + "…"
        cls = "error" if ev.is_error else "ok"
        return (f'<div class="tool-result {cls}">└ '
                f'<span class="status">{cls}</span> '
                f'{escape(first)}</div>')

    if isinstance(ev, AgentPlan):
        return _plan_html(ev)

    if isinstance(ev, Result):
        inner = escape(" · ".join(result_parts(ev)))
        return f'<div class="result-sep">── {inner} ──</div>'

    if isinstance(ev, (SystemInit, Unknown)):
        return None
    return None


def _diff_html(diff: tuple[str, str, str]) -> str:
    path, old_text, new_text = diff
    removed, added, elided = diff_window(old_text, new_text)
    rows = [f'<div class="diff-head">┌ {escape(path)}</div>']
    for line in removed:
        rows.append(f'<div class="diff-row removed">- {escape(line)}</div>')
    for line in added:
        rows.append(f'<div class="diff-row added">+ {escape(line)}</div>')
    if elided > 0:
        s = "s" if elided != 1 else ""
        rows.append(f'<div class="diff-more">… {elided} more line{s}</div>')
    return f'<div class="tool-result diff">{"".join(rows)}</div>'


def _plan_html(plan: AgentPlan) -> str:
    total = len(plan.entries)
    if total == 0:
        return '<div class="agent-plan muted">📋 (no plan)</div>'
    done = sum(1 for e in plan.entries if e.status == "completed")
    rows = [f'<div class="plan-head">📋 Plan — {done}/{total} done</div>']
    for entry in plan.entries:
        glyph = PLAN_STATUS_GLYPH.get(entry.status, "○")
        prio = f" {entry.priority}" if entry.priority in ("high", "low") else ""
        rows.append(
            f'<div class="plan-row {entry.status}{prio}">'
            f'<span class="glyph">{glyph}</span> '
            f'{escape(entry.content)}</div>')
    return f'<div class="agent-plan">{"".join(rows)}</div>'
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `uv run pytest tests/test_render_html.py -q`
Expected: PASS (15 tests). If `test_agent_plan_glyphs_and_content`'s header assertion is brittle in review, it asserts the plan header renders; the glyph/content asserts are the substantive checks.

- [ ] **Step 5: Commit**

```bash
git add src/aegis/render_html.py tests/test_render_html.py
git commit -m "feat(render): render_event_html sibling to render_event"
```

---

### Task 5: Transcript constants — single source of truth

**Files:**
- Create: `src/aegis/transcript_constants.py`
- Modify: `src/aegis/tui/pane.py` (delete the six literal definitions at lines ~36–41; import them instead)
- Test: `tests/test_transcript_constants.py`

**Interfaces:**
- Consumes: nothing.
- Produces: module-level `N_MAX = 300`, `EVICT_BATCH = 50`, `LOAD_BATCH = 100`, `STICKY_EPS = 2`, `LOAD_MORE_EPS = 3`, `DEBOUNCE_S = 0.15`. `pane.py` re-imports them so `pane.N_MAX` etc. still resolve for any existing reference.

- [ ] **Step 1: Confirm who references these constants**

Run: `grep -rn "N_MAX\|EVICT_BATCH\|LOAD_BATCH\|STICKY_EPS\|LOAD_MORE_EPS\|DEBOUNCE_S" src/ tests/`
Expected: references in `src/aegis/tui/pane.py` (and possibly `tests/test_pane_windowing.py`). Note any test that reads them via `from aegis.tui.pane import N_MAX` — the import-rebind in Step 4 keeps that path working.

- [ ] **Step 2: Write the failing test**

Create `tests/test_transcript_constants.py`:

```python
import aegis.transcript_constants as tc
from aegis.tui import pane


def test_canonical_values():
    assert tc.N_MAX == 300
    assert tc.EVICT_BATCH == 50
    assert tc.LOAD_BATCH == 100
    assert tc.STICKY_EPS == 2
    assert tc.LOAD_MORE_EPS == 3
    assert tc.DEBOUNCE_S == 0.15


def test_pane_reexports_same_objects():
    # pane keeps exposing the names so existing references resolve, and
    # they are the very same objects (single source of truth).
    assert pane.N_MAX is tc.N_MAX
    assert pane.EVICT_BATCH is tc.EVICT_BATCH
    assert pane.STICKY_EPS is tc.STICKY_EPS
    assert pane.DEBOUNCE_S is tc.DEBOUNCE_S
```

- [ ] **Step 3: Run test to verify it fails**

Run: `uv run pytest tests/test_transcript_constants.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'aegis.transcript_constants'`.

- [ ] **Step 4: Create the constants module and rewire `pane.py`**

Create `src/aegis/transcript_constants.py`:

```python
"""Single source of truth for transcript-windowing tuning knobs, shared by
the TUI pane and (later) the web client's `hello` constants block."""

N_MAX = 300            # max mounted transcript blocks before eviction
EVICT_BATCH = 50       # blocks dropped per eviction when over N_MAX
LOAD_BATCH = 100       # older blocks re-mounted per scroll-up load
STICKY_EPS = 2         # px/row tolerance for "stuck to bottom"
LOAD_MORE_EPS = 3      # scroll-from-top tolerance to trigger load-older
DEBOUNCE_S = 0.15      # debounce window for scroll-up load-older
```

In `src/aegis/tui/pane.py`, delete the six assignment lines (currently ~36–41) and replace them with:

```python
from aegis.transcript_constants import (  # noqa: F401  (re-exported)
    N_MAX, EVICT_BATCH, LOAD_BATCH, STICKY_EPS, LOAD_MORE_EPS, DEBOUNCE_S,
)
```

Place this import with the other `aegis.*` imports near the top of `pane.py`. The `# noqa: F401` documents that the names are intentionally re-exported even though `pane.py` may not reference every one directly.

- [ ] **Step 5: Run the new test plus the windowing regression**

Run: `uv run pytest tests/test_transcript_constants.py tests/test_pane_windowing.py tests/test_pane_replay.py -q`
Expected: PASS — windowing behavior is unchanged; the constants are identical objects.

- [ ] **Step 6: Commit**

```bash
git add src/aegis/transcript_constants.py src/aegis/tui/pane.py tests/test_transcript_constants.py
git commit -m "refactor(tui): hoist transcript constants to aegis.transcript_constants"
```

---

### Task 6: Persistence reality check (S1 audit) — feeds S2 resume

**Files:**
- Read-only: `src/aegis/state/session_log.py`, `src/aegis/state/event_codec.py`, `src/aegis/groups/persistence.py` (for the torn-line-tolerant pattern to mirror).
- Modify: `docs/superpowers/specs/2026-06-19-aegis-web-client-design.md` (append an audit note).

**Interfaces:**
- Consumes: nothing at runtime. This task produces documentation only — it records the gaps between the web-client spec's resume assumptions and the actual on-disk format so S2 plans against reality.

- [ ] **Step 1: Confirm the real persistence shape**

Run: `grep -n "session_log_path\|append_event\|replay_events\|json.loads\|fsync\|flush" src/aegis/state/session_log.py`
Confirm these facts (true as of this plan):
1. Path is `<state_dir>/sessions/<handle>.jsonl` — flat, **handle-named**, one file per tab. The spec's §Reconnection assumes `<session_id>/events.jsonl` — **wrong**.
2. Each line is `{"v": 1, "aegis_ts": <iso>, "event": <encoded>}` — there is **no per-line `seq`**. The spec's resume protocol keys on a monotonic `seq` per session.
3. `append_event` opens in append mode and writes one line under a context manager (flush+close on exit); there is **no `os.fsync`**.
4. `replay_events` calls `json.loads(line)` with **no try/except** — a torn trailing line raises. By contrast `groups/persistence.py` is explicitly torn-trailing-line tolerant.

- [ ] **Step 2: Append the audit note to the web-client spec**

Add this section to `docs/superpowers/specs/2026-06-19-aegis-web-client-design.md`, immediately after the `### Reconnection` subsection:

```markdown
### Persistence reality check (S1 audit, 2026-06-30)

Grounding the resume protocol against `src/aegis/state/session_log.py` as it
actually exists today surfaced four deltas S2 must design around:

1. **Path is handle-named, not session-id-keyed.** Events persist at
   `<state_dir>/sessions/<handle>.jsonl` (flat, one file per tab), via
   `session_log_path(state_dir, handle)`. Update the protocol's
   "JSONL under `.aegis/state/sessions/<session_id>/events.jsonl`" wording —
   the resume reader keys off the tab handle.
2. **No per-line `seq` on disk.** Each line is
   `{"v": 1, "aegis_ts": <iso>, "event": <encoded>}`. `seq` must be
   *synthesized* on read as the 1-based line index. The in-memory counter
   for post-flush live events (S2's `current_seq`) starts from that line
   count. There is no stored monotonic id to rely on.
3. **No `fsync`.** `append_event` flushes on context-manager close but does
   not `os.fsync`. Acceptable for v1 (single-user, append-only), but the
   resume path must tolerate a partially-written trailing line after a crash.
4. **`replay_events` is not torn-line tolerant.** It calls `json.loads`
   per line with no guard; a torn final line raises. S2's history reader
   must wrap the final-line decode in a try/except and drop an unparseable
   trailing line — mirror the tolerant replay in `groups/persistence.py`.

None of these block S1; they retarget S2's "JSONL history reader + resume"
work at the real format.
```

- [ ] **Step 3: Commit**

```bash
git add docs/superpowers/specs/2026-06-19-aegis-web-client-design.md
git commit -m "docs(web-client): record S1 persistence audit for S2 resume"
```

---

## Final verification

- [ ] **Run the full hermetic suite**

Run: `uv run pytest -q -m "not live"`
Expected: PASS, exit code 0. No regressions in any existing test; the new `test_theme_loader.py`, `test_render_shared.py`, `test_render_html.py`, `test_transcript_constants.py` are green.

- [ ] **Smoke the TUI imports**

Run: `uv run python -c "import aegis.tui.app, aegis.tui.pane, aegis.render, aegis.render_html, aegis.themes; print('ok')"`
Expected: prints `ok` (no import-time errors from the refactor).

---

## Self-Review

**Spec coverage (S1 acceptance criteria a–f):**
- (a) `aegis-ink.yaml` + loader exposing `to_aegis_colors()` + `to_css_variables()` → Task 1. (Option B: colors-only schema, by approved decision.)
- (b) `tui/themes.py` thin shim; existing TUI tests pass unchanged → Task 2.
- (c) `render_event_html` sibling; both renderers share a formatter layer → Tasks 3 (shared layer) + 4 (HTML renderer).
- (d) HTML-renderer tests cover every event kind → Task 4 (`test_render_html.py`).
- (e) Transcript constants moved to a single source of truth → Task 5.
- (f) JSONL fsync semantics audited → Task 6.

**Deliberate deviations from the spec (approved or documented):**
- Theme YAML is colors-only (Option B), not the full icons/glyphs schema — approved.
- `render_event_html(ev)` takes no `palette` arg; HTML colors come from CSS variables — documented in Task 4.
- Constants live in a flat `aegis.transcript_constants` module, not `aegis/render/transcript_constants.py`; `render.py` is not converted to a package (YAGNI) — surgical, achieves the single-source goal.

**Placeholder scan:** none — every code and test step contains complete content.

**Type consistency:** `AegisColors` field set is identical across Task 1 and the existing dataclass. `diff_window` returns `(shown_removed, shown_added, elided)` and is consumed with that exact shape in `render.py` (Task 3) and `render_html.py` (Task 4). `result_parts(ev) -> list[str]` consumed identically in both renderers. `KIND_ICON`/`PLAN_STATUS_GLYPH` names match between `render_shared.py` and both consumers.
