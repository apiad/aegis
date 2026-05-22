"""Simple option formatter for ask_human prompts."""
from __future__ import annotations


def format_options(options: list[str]) -> str:
    return "\n".join(f"  {i + 1}. {opt}" for i, opt in enumerate(options))
