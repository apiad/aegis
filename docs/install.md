# Install

## Prerequisites

- Python 3.13+
- [uv](https://docs.astral.sh/uv/)
- A working `claude` CLI on `PATH` (Claude Code), logged in.

## Install & run

```bash
git clone https://github.com/apiad/aegis
cd aegis
uv pip install -e .
aegis init     # writes .aegis.py in the current directory
aegis
```

`aegis --version` prints the version. With no `.aegis.py` in the current
directory or `~`, `aegis` refuses to run and points you at `aegis init`.
