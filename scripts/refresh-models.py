#!/usr/bin/env python3
"""Refresh ``src/aegis/data/models.yaml`` from Models.dev.

Run manually (the YAML otherwise drifts as vendors ship new models):

    uv run python scripts/refresh-models.py            # preview to stdout
    uv run python scripts/refresh-models.py --diff     # unified diff vs current
    uv run python scripts/refresh-models.py --apply    # write the YAML

Source: ``https://models.dev/api.json`` — a structured catalog covering
Anthropic, Google, Moonshot, MiniMax, Alibaba/Qwen, DeepSeek, and many
others. Each model exposes ``cost.{input,output,cache_read,cache_write}``
in per-million-tokens USD and ``limit.context`` in tokens, both ready to
copy verbatim into our YAML. This is the same database OpenCode itself
consults (per opencode.ai/docs/models), so the OpenCode-routed entries
here match what users will actually see in their opencode config.

Curation: aegis only surfaces a handful of well-known slugs per provider
in the model picker. The lists below are what flows into the YAML;
everything else stays reachable via the modal's ``<custom>`` option.

Renaming: the ``claude-code`` and ``gemini`` providers run CLIs that
accept short model names (``opus``, ``gemini-2.5-pro``), so the YAML
uses those as the canonical key and lists the explicit version ID in
``aliases`` so prices and context-windows still resolve correctly when a
user writes the long form by hand.
"""
from __future__ import annotations

import argparse
import difflib
import sys
from datetime import date
from io import StringIO
from pathlib import Path

import httpx
from ruamel.yaml import YAML
from ruamel.yaml.scalarstring import DoubleQuotedScalarString as Q

MODELS_DEV_URL = "https://models.dev/api.json"
DEFAULT_OUT = Path(__file__).parent.parent / "src" / "aegis" / "data" / "models.yaml"
HTTP_TIMEOUT_S = 20.0


# -----------------------------------------------------------------------------
# Curation per provider.
#
# Format per row: (models.dev provider id, models.dev model id, optional dict
# of overrides). Overrides:
#   - "key":     name in the YAML for this entry (defaults to the model id)
#   - "label":   human label shown in the picker
#   - "aliases": list of alternate names lookups should resolve through
# -----------------------------------------------------------------------------

CLAUDE_CODE: list[tuple[str, str, dict]] = [
    ("anthropic", "claude-opus-4-7",
     {"key": "opus",   "label": "claude-opus-4-7",
      "aliases": ["claude-opus-4-7", "claude-opus-4-6", "claude-opus-4"]}),
    ("anthropic", "claude-sonnet-4-6",
     {"key": "sonnet", "label": "claude-sonnet-4-6",
      "aliases": ["claude-sonnet-4-6", "claude-sonnet-4-5",
                  "claude-sonnet-4-5-20250929", "claude-sonnet-4"]}),
    ("anthropic", "claude-haiku-4-5",
     {"key": "haiku",  "label": "claude-haiku-4-5",
      "aliases": ["claude-haiku-4-5", "claude-haiku-4-5-20251001"]}),
]

GEMINI: list[tuple[str, str, dict]] = [
    ("google", "gemini-3-pro-preview",
     {"key": "gemini-3-pro", "label": "Gemini 3 Pro (preview)",
      "aliases": ["gemini-3-pro-preview"]}),
    ("google", "gemini-3.5-flash",
     {"label": "Gemini 3.5 Flash"}),
    ("google", "gemini-3.1-flash-lite",
     {"label": "Gemini 3.1 Flash Lite"}),
    ("google", "gemini-2.5-pro",
     {"label": "Gemini 2.5 Pro"}),
    ("google", "gemini-2.5-flash",
     {"label": "Gemini 2.5 Flash"}),
]

# OpenCode: model IDs follow ``<models.dev provider id>/<model id>`` — the
# same shape opencode writes in its config. We use the slash form as the
# canonical key here.
OPENCODE: list[tuple[str, str, dict]] = [
    # Anthropic (via OpenCode's anthropic provider)
    ("anthropic", "claude-opus-4-7",   {"label": "Claude Opus 4.7"}),
    ("anthropic", "claude-sonnet-4-6", {"label": "Claude Sonnet 4.6"}),
    ("anthropic", "claude-haiku-4-5",  {"label": "Claude Haiku 4.5"}),
    # Google
    ("google", "gemini-3-pro-preview", {"label": "Gemini 3 Pro (preview)"}),
    ("google", "gemini-3.5-flash",     {"label": "Gemini 3.5 Flash"}),
    ("google", "gemini-2.5-pro",       {"label": "Gemini 2.5 Pro"}),
    ("google", "gemini-2.5-flash",     {"label": "Gemini 2.5 Flash"}),
    # Moonshot Kimi
    ("moonshotai", "kimi-k2.6",            {"label": "Kimi K2.6",
                                             "aliases": ["kimi-k2.6"]}),
    ("moonshotai", "kimi-k2-thinking",     {"label": "Kimi K2 Thinking"}),
    ("moonshotai", "kimi-k2-0905-preview", {"label": "Kimi K2 0905"}),
    # MiniMax
    ("minimax", "MiniMax-M2.7", {"label": "MiniMax M2.7"}),
    ("minimax", "MiniMax-M2.1", {"label": "MiniMax M2.1"}),
    ("minimax", "MiniMax-M2",   {"label": "MiniMax M2"}),
    # DeepSeek
    ("deepseek", "deepseek-v4-pro",   {"label": "DeepSeek V4 Pro"}),
    ("deepseek", "deepseek-v4-flash", {"label": "DeepSeek V4 Flash"}),
    ("deepseek", "deepseek-chat",     {"label": "DeepSeek Chat"}),
    ("deepseek", "deepseek-reasoner", {"label": "DeepSeek Reasoner"}),
    # Alibaba / Qwen
    ("alibaba", "qwen3.7-max",      {"label": "Qwen 3.7 Max"}),
    ("alibaba", "qwen3-coder-plus", {"label": "Qwen 3 Coder Plus"}),
    ("alibaba", "qwen3.6-plus",     {"label": "Qwen 3.6 Plus"}),
]


# -----------------------------------------------------------------------------
# Pricing + tree builders.
# -----------------------------------------------------------------------------

def _fmt_money(n: object) -> str | None:
    """Format a per-million-tokens USD price as a clean fixed-point string.
    Returns None when the field is absent or zero."""
    if n is None or n == "" or n == 0:
        return None
    try:
        f = float(n)
    except (TypeError, ValueError):
        return None
    if f == 0:
        return None
    # Strip trailing zeros while keeping at least 2 decimal places when
    # the number has fractional content.
    s = f"{f:.6f}".rstrip("0").rstrip(".")
    return s if "." in s else f"{s}.00"


def _model_entry(provider_id: str, model_id: str, ov: dict,
                 db: dict) -> tuple[str, dict] | None:
    prov = db.get(provider_id) or {}
    model = (prov.get("models") or {}).get(model_id)
    if model is None:
        print(f"  WARN: models.dev has no {provider_id}/{model_id} — skipping",
              file=sys.stderr)
        return None
    key = ov.get("key", model_id)
    entry: dict = {}
    if "label" in ov:
        entry["label"] = Q(ov["label"])
    aliases = list(ov.get("aliases") or [])
    if aliases:
        entry["aliases"] = aliases
    limit = model.get("limit") or {}
    if "context" in limit:
        entry["context_window"] = int(limit["context"])
    cost = model.get("cost") or {}
    pricing = {
        "input":       _fmt_money(cost.get("input")),
        "output":      _fmt_money(cost.get("output")),
        "cache_hit":   _fmt_money(cost.get("cache_read")),
        "cache_write": _fmt_money(cost.get("cache_write")),
        # Models.dev doesn't surface a separate "thinking" rate. Treat
        # thinking tokens as billed at the completion rate (matches what
        # Anthropic + Google + Moonshot all do today).
        "thinking":    _fmt_money(cost.get("output")),
    }
    pricing = {k: Q(v) for k, v in pricing.items() if v is not None}
    if pricing:
        entry["prices"] = pricing
    return key, entry


def _build_provider(rows: list[tuple[str, str, dict]], db: dict, *,
                    default_context_window: int,
                    context_window_patterns: list[dict] | None = None,
                    ) -> dict:
    models: dict[str, dict] = {}
    for provider_id, model_id, ov in rows:
        out = _model_entry(provider_id, model_id, ov, db)
        if out is not None:
            key, entry = out
            models[key] = entry
    block: dict = {"default_context_window": default_context_window}
    if context_window_patterns:
        block["context_window_patterns"] = context_window_patterns
    block["models"] = models
    return block


def build_tree(db: dict) -> dict:
    return {
        "version": 1,
        "updated": Q(date.today().isoformat()),
        "providers": {
            "claude-code": _build_provider(
                CLAUDE_CODE, db,
                default_context_window=200_000,
                context_window_patterns=[
                    {"match": "1m",   "context_window": 1_000_000},
                    {"match": "opus", "context_window": 1_000_000},
                ]),
            "gemini": _build_provider(
                GEMINI, db,
                default_context_window=1_048_576),
            "opencode": _build_provider(
                [(p, m, {**ov, "key": f"{p}/{m}"}) for p, m, ov in OPENCODE],
                db,
                default_context_window=200_000),
        },
    }


# -----------------------------------------------------------------------------
# YAML render + CLI.
# -----------------------------------------------------------------------------

HEADER = """\
# aegis model registry — provider → model → {context_window, prices, aliases}.
#
# This file is REGENERATED by scripts/refresh-models.py from
# https://models.dev/api.json (the same catalog OpenCode itself consults).
# Spot-check against vendor docs after running. Do NOT hand-edit unless
# you also update the script's curation lists.
#
# Bundled in the wheel as src/aegis/data/models.yaml. At CLI boot aegis fires
# a best-effort background fetch of
#   https://raw.githubusercontent.com/apiad/aegis/main/src/aegis/data/models.yaml
# into ~/.cache/aegis/models.yaml; the cache wins over the bundled file when
# present so updates propagate to installed aegis copies within 24h.
#
# Prices are per-million-tokens in USD, written as strings so the loader
# uses Decimal arithmetic without float drift.
#
# aliases: alternate model names the underlying CLI accepts that map to
# the same canonical entry. context_window_patterns are a substring-match
# fallback applied to the model name when no exact/alias entry matches.
"""


def render_yaml(tree: dict) -> str:
    y = YAML()
    y.default_flow_style = False
    y.indent(mapping=2, sequence=4, offset=2)
    y.preserve_quotes = True
    buf = StringIO()
    buf.write(HEADER)
    y.dump(tree, buf)
    return buf.getvalue()


def fetch_models_dev(url: str = MODELS_DEV_URL) -> dict:
    r = httpx.get(url, timeout=HTTP_TIMEOUT_S, follow_redirects=True)
    r.raise_for_status()
    return r.json()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--apply", action="store_true",
        help="Write the regenerated YAML to disk.")
    parser.add_argument(
        "--diff", action="store_true",
        help="Show a unified diff against the current YAML.")
    parser.add_argument(
        "--out", default=str(DEFAULT_OUT),
        help="Output path (default: src/aegis/data/models.yaml).")
    parser.add_argument(
        "--source", default=MODELS_DEV_URL,
        help="Models.dev catalog URL.")
    args = parser.parse_args(argv)

    print(f"Fetching {args.source} ...", file=sys.stderr)
    db = fetch_models_dev(args.source)
    print(f"Loaded {len(db)} providers.", file=sys.stderr)

    tree = build_tree(db)
    new_yaml = render_yaml(tree)

    out = Path(args.out)
    if args.diff and out.exists():
        old = out.read_text(encoding="utf-8")
        diff = difflib.unified_diff(
            old.splitlines(keepends=True),
            new_yaml.splitlines(keepends=True),
            fromfile=str(out), tofile=str(out) + " (new)")
        sys.stdout.writelines(diff)

    if args.apply:
        out.write_text(new_yaml, encoding="utf-8")
        print(f"Wrote {out}", file=sys.stderr)
    elif not args.diff:
        sys.stdout.write(new_yaml)
    return 0


if __name__ == "__main__":
    sys.exit(main())
