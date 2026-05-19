from __future__ import annotations

_MD = r"_*[]()~`>#+-=|{}.!"


def escape_md(text: str) -> str:
    return "".join("\\" + c if c in _MD else c for c in text)


def status_line(handle: str, state: str, model: str, metrics: str) -> str:
    icon = {"working": "⏳", "ready": "✅", "error": "⚠️"}.get(state, "•")
    return f"{icon} {handle} · {state} · {model} {metrics}"


def chunk(text: str, *, label: str, limit: int = 4096,
          max_parts: int = 5) -> list[str]:
    body = text.strip() or "(no output)"
    raw = [body[i:i + limit - 40] for i in range(0, len(body), limit - 40)]
    if len(raw) == 1:
        return [raw[0]]
    kept = raw[:max_parts]
    out = [f"{label} ({i + 1}/{len(kept)})\n{p}" for i, p in enumerate(kept)]
    dropped = len(raw) - len(kept)
    if dropped > 0:
        out[-1] += f"\n… (truncated, {dropped} more chunk"
        out[-1] += "s)" if dropped != 1 else ")"
    return out
