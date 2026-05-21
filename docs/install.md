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
`aegis init` detects what's available and offers only those.

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
aegis init     # interactive wizard — writes .aegis.py
aegis          # full-screen TUI
```

`aegis init` is a Rich-powered wizard. It scans your `PATH` for the
supported CLIs, lets you pick a model + permission mode for each,
optionally configures queues, and writes a `.aegis.py` to your current
directory. Re-running `aegis init` refuses to overwrite an existing
`.aegis.py` unless you pass `--force`.

## Verify

```bash
aegis --version
```

If you see `aegis 0.3.0` (or higher), you're good.

## Where aegis looks for config

`aegis` walks up from the current directory to find the closest
ancestor containing a `.aegis.py`, falling back to `~/.aegis.py`. With
no config anywhere, it refuses to start and points you at `aegis init`.

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
