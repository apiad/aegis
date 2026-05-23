from __future__ import annotations

_MD = set(r"_*[]()~`>#+-=|{}.!") | {"\\"}


def escape_md(text: str) -> str:
    return "".join("\\" + c if c in _MD else c for c in text)


def status_line(handle: str, state: str, model: str, metrics: str) -> str:
    icon = {"working": "⏳", "ready": "✅", "error": "⚠️"}.get(state, "•")
    return f"{icon} {handle} · {state} · {model} {metrics}"


def chunk(text: str, *, label: str, limit: int = 4096,
          max_parts: int = 5) -> list[str]:
    body = escape_md(text.strip() or "(no output)")
    safe_label = escape_md(label)
    slice_size = limit - 60
    raw: list[str] = []
    i = 0
    while i < len(body):
        end = min(i + slice_size, len(body))
        # Never end a slice on a lone `\` — it escapes the following
        # char and the pair must travel together.
        if end < len(body) and body[end - 1] == "\\":
            end -= 1
        raw.append(body[i:end])
        i = end
    if len(raw) == 1:
        return [raw[0]]
    kept = raw[:max_parts]
    out = [f"{safe_label} \\({i + 1}/{len(kept)}\\)\n{p}"
           for i, p in enumerate(kept)]
    dropped = len(raw) - len(kept)
    if dropped > 0:
        plural = "s" if dropped != 1 else ""
        out[-1] += f"\n… \\(truncated, {dropped} more chunk{plural}\\)"
    return out
