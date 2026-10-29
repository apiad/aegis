.PHONY: check gates hooks ops-stubs test-durations lint lint-docs lint-detail format format-check typecheck changelog changelog-check test test-cov test-all test-slow test-live test-multiuser canary test-quarantine test-browser bench coverage know-how site-css

# Every gate, on the fast lane: the tests marked slow, the browser tests among
# them, are `test-slow`'s. The tests run before typecheck so a type error cannot
# hide a failing suite; both block.
check: format lint lint-docs changelog-check test typecheck

# The checks that take seconds, as the pre-push hook runs them: what failed
# 18 of 40 red CI runs (#506). Unlike `check`, nothing here rewrites a file.
gates: format-check changelog-check lint-docs typecheck
	uv run ruff check src/ tests/ scripts/

# Run `gates` before every `git push`. Once per clone: the setting lives in the
# shared .git/config, so every worktree gets it. `git push --no-verify` skips it.
hooks:
	git config core.hooksPath .githooks

# Refresh the slow lane's per-test times, which CI's shards balance on. Serial,
# so the times are not inflated by neighbours; about 30 min.
test-durations:
	uv run pytest -q -m "slow and not live and not multiuser" --store-durations --clean-durations

# Doc drift (.rift.yaml), the help pages against the code among it. CI runs
# rift pinned to a tag; with no rift installed, so does this. `make
# lint-detail` names each entity.
RIFT := $(shell command -v rift 2>/dev/null || echo "uvx --from git+https://github.com/apiad/rift@v0.9.0 rift")

lint-docs:
	$(RIFT) check .

lint-detail:
	$(RIFT) list .

lint:
	uv run ruff check --fix src/ tests/ scripts/

format:
	uv run ruff format src/ tests/ scripts/

# What CI runs. `format` rewrites; a runner has to fail instead, or drift lands
# green and the next person's tree is dirty for reasons they did not cause.
format-check:
	uv run ruff format --check src/ tests/ scripts/

# Every fragment in changelog.d/ parses. A fragment that does not parse is a
# release note that silently goes missing.
changelog-check:
	uv run python scripts/changelog.py check

# The collated CHANGELOG, to stdout. Writes nothing; `apply` is the releaser's
# verb and lives in know-how/releasing.md.
changelog:
	@uv run python scripts/changelog.py preview

# The signatures of srv.ops (src/aegis/api/ops.pyi), from the operation
# registry. tests/test_library.py fails when the committed file is stale.
ops-stubs:
	uv run python -m aegis.api.stubs > src/aegis/api/ops.pyi
	uv run ruff format src/aegis/api/ops.pyi

# Blocking. The legacy tree reported ~235 diagnostics and kept this advisory;
# this tree has none, and new code keeps it that way.
typecheck:
	uv run ty check src/

# The fast lane: hermetic, slow tests deselected, spread across cores. A test
# over the budget without @pytest.mark.slow fails it (tests/conftest.py).
# A red run is a regression to investigate, not noise to re-roll.
test:
	uv run pytest -q -n auto -m "not slow and not quarantine" --max-unmarked-duration=3

test-cov:
	uv run pytest -q -n auto -m "not slow" --cov=aegis --cov-report=term-missing

# Everything hermetic, slow tests included.
test-all:
	uv run pytest -q -n auto --cov=aegis --cov-report=term-missing

# Every test the fast lane skips: the browser tests, restarts, detached servers.
# Serial here; CI splits it in four shards. With `check` it is every test CI
# runs (tests/test_lanes.py holds them together).
test-slow:
	uv run pytest -q -m "slow and not live and not multiuser and not quarantine"

# The client in headless Chromium against a real `aegis serve` and the fake
# claude. Marked slow too, so `make test` skips them; CI runs them.
# The flaky tests the PR gate leaves out (#776), as nightly.yml runs them.
test-quarantine:
	uv run pytest -q -n 2 -m "quarantine and not live"

test-browser:
	uv run pytest -q -m browser

# The landing page's copy of the client's CSS (tests/test_site.py checks it).
site-css:
	uv run python scripts/site_css.py

# The transcript path, measured (scripts/bench.py). Reports, never gates.
bench:
	uv run python scripts/bench.py --out .aegis-bench.json

# Real agent CLIs and models: spends quota. Select by marker, never with
# -k "not live": -k matches substrings and silently drops unrelated tests whose
# names contain "live", such as anything with "deliver".
test-live:
	uv run pytest -q --run-live -m live

# aegis as a service user with capabilities, against real Linux users it
# creates with sudo (tests/multiuser/). Needs passwordless sudo and systemd.
test-multiuser:
	uv run pytest -q --run-multiuser -m "multiuser and not quarantine"

# The latest stable release of every harness under this aegis, against a fake
# model: no quota. Installs the releases into ~/.cache/aegis-canary. CI runs it
# nightly (.github/workflows/canary.yml); know-how/canary.md says what to do
# with what it finds.
canary:
	scripts/canary.sh

coverage:
	uv run pytest -n auto --cov=aegis --cov-report=html

# The know-how menu: one when: line per procedure doc.
know-how:
	@grep -m1 -H '^when:' know-how/*.md
