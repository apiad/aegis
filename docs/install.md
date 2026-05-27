# Install

## Prerequisites

- **Python 3.13+** — aegis uses some 3.13-only features and is tested
  exclusively against it.
- **At least one coding-agent CLI on `PATH`**, signed-in:
    - [Claude Code](https://docs.claude.com/en/docs/claude-code) — `claude`
    - [Gemini CLI](https://github.com/google-gemini/gemini-cli) — `gemini`
    - [OpenCode](https://opencode.ai) — `opencode`
- Optional: [uv](https://docs.astral.sh/uv/) — the fastest way to
  install and run aegis.

You can mix and match — install only the providers you actually use.
The ConfigPanel + `aegis config agent add` only let you wire up
providers that are actually on `PATH`.

## Install

=== "pip"

    ```bash
    pip install aegis-harness
    ```

=== "uv"

    ```bash
    uv pip install aegis-harness
    # …or, for an isolated install:
    uv tool install aegis-harness
    ```

The PyPI distribution is `aegis-harness`; the importable Python package
is `aegis` (so `pip install aegis-harness` then `from aegis import …`).

## First run

```bash
aegis            # full-screen TUI; opens ConfigPanel if no .aegis.yaml
```

When you launch `aegis` in a directory without a `.aegis.yaml`, the
TUI drops you straight into the ConfigPanel — press `a` to add your
first agent and save. Alternatively, the scriptable CLI:

```bash
aegis config agent add main --provider claude-code \
                            --model opus --effort high
```

writes a minimal `.aegis.yaml` with one agent and sets it as the
default. `aegis config show` prints the current file; `aegis config
agent list` enumerates declared profiles.

## Verify

```bash
aegis --version
```

If you see `aegis 0.3.0` (or higher), you're good.

## Where aegis looks for config

`aegis` walks up from the current directory to find the closest
ancestor containing a `.aegis.yaml`. With no config anywhere, it
launches the TUI ConfigPanel so you can set one up in place.

## Development install (from source)

```bash
git clone https://github.com/apiad/aegis
cd aegis
uv sync                 # or: uv pip install -e .
uv run aegis            # run from the working tree
uv run pytest -m "not live"
```

The hermetic test suite runs in ~15s with no external dependencies.
The `live` marker exists for tests that spawn a real `claude`, `gemini`,
or `opencode` subprocess — those auto-skip when the corresponding CLI
isn't on `PATH`.
