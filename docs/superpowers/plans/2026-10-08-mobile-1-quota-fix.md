# Mobile slice 1: the quota bars under 1100 px, implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Keep every Fleet quota bar on its gauge's row at any window width.

**Architecture:** One CSS selector. `#a2 .band .q{grid-column:1/-1}` in `src/aegis/client/css/base.css:372` is meant for the band's quota column (`<div class="q" id="band-quota-col">` in `index.html`), but every quota bar is `<div class="bar q …">` (`js/gauges.js:53`), so under 1100 px each bar spans its gauge's grid and pushes the percentage onto a row of its own. A child combinator scopes it to the column.

**Tech Stack:** plain CSS; pytest + Playwright browser tests.

**Spec:** `docs/superpowers/specs/2026-10-08-mobile-and-pwa-design.md`, section 2, "The quota fix". This branch also carries the spec and the four slice plans.

## Global Constraints

- No build step, no framework: the client is plain ES modules and CSS.
- A user-visible change has a `changelog.d/` fragment (`changelog.d/README.md` has the format).
- Run the gates as AGENTS.md says; `make test` skips browser tests (they are `slow`), so run the new test with `uv run pytest -q -m browser -k <name>`.

## Review Focus

- A window exactly 1100 px wide and one at 1101 px: the media query flips there, and both must show each percentage on its bar's row.
- The sidebar's quota rows (`.side .qrow`) also contain `.bar.q`; they are outside `.band` and must not change.
- A Fleet with no quota reading (`#band-quota-col` hidden): nothing to lay out, no error.

---

### Task 1: Scope the rule to the quota column

**Files:**
- Modify: `src/aegis/client/css/base.css:372`
- Test: `tests/test_browser.py` (append)
- Create: `changelog.d/mobile-quota-bars.fixed.md`

**Interfaces:**
- Consumes: the `quota_server` and `page` fixtures in `tests/test_browser.py`.
- Produces: nothing other slices call.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_browser.py`:

```python
@pytest.mark.parametrize("width", [1000, 1100, 1366])
def test_each_quota_bar_shares_a_row_with_its_label_and_value(
    quota_server, browser, width
):
    errors: list = []
    page = new_page(browser, errors)
    page.set_viewport_size({"width": width, "height": 800})
    page.goto(quota_server.url)
    page.wait_for_selector("#band-quota .gauge")
    rows = page.evaluate(
        """[...document.querySelectorAll('#band-quota .gauge')]
            .filter(g => g.children.length === 3)
            .map(g => [...g.children].map(c => Math.round(c.getBoundingClientRect().top)))"""
    )
    assert rows, "no quota gauges drawn"
    for tops in rows:
        assert max(tops) - min(tops) < 12, tops
    assert errors == []
```

- [ ] **Step 2: Run it and watch it fail at 1000 and 1100**

Run: `uv run pytest -q -m browser -k each_quota_bar_shares`
Expected: FAIL for `width=1000` and `width=1100` with an assertion like `[279, 303, 317]`; PASS for 1366.

- [ ] **Step 3: Fix the selector**

In `src/aegis/client/css/base.css`, line 372, change

```css
@media (max-width:1100px){#a2 .band{grid-template-columns:1fr 1fr}#a2 .band .q{grid-column:1/-1}}
```

to

```css
@media (max-width:1100px){#a2 .band{grid-template-columns:1fr 1fr}#a2 .band>.q{grid-column:1/-1}}
```

- [ ] **Step 4: Run the test again**

Run: `uv run pytest -q -m browser -k "each_quota_bar_shares or quota"`
Expected: PASS, including the existing `test_the_fleet_band_and_the_sidebar_show_quota_and_the_host` and `test_quota_rows_survive_session_updates_so_their_tooltip_stays`.

- [ ] **Step 5: Release note**

Create `changelog.d/mobile-quota-bars.fixed.md`:

```markdown
- **The Fleet's quota bars keep their percentage beside them in a window under 1100 px.** A rule meant for the quota column also matched every quota bar, so each bar took a row of its own and its percentage dropped under it, cut off on the left.
```

Run: `make changelog-check`
Expected: exit 0.

- [ ] **Step 6: Check it in a browser against a fresh server**

Start `uv run aegis serve --root <a temp dir with an .aegis.yaml> --port 8793 -d`, open the printed URL at 1000 px wide, confirm the quota rows read `label · bar · 38% ↻ 2h 07m` on one line, then `kill` the printed pid.

- [ ] **Step 7: Commit**

```bash
git add src/aegis/client/css/base.css tests/test_browser.py changelog.d/mobile-quota-bars.fixed.md
git commit -m "fix(client): quota bars keep their row under 1100 px"
```
