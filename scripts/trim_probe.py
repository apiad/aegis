"""Turn a raw claude stream-json recording into a trimmed aegis store fixture.

    uv run python scripts/trim_probe.py OUT.jsonl SEND [SEND ...] > fixture.jsonl

Each SEND becomes a ``send`` record placed before the Claude lines up to and
including the next ``result``. Hook notices and rate-limit events are dropped,
and each object keeps only the keys aegis reads, so no local path or skill list
lands in the repo."""

import json
import sys

KEEP = {
    "type",
    "subtype",
    "isReplay",
    "local_command_run",
    "trigger",
    "session_id",
    "model",
    "claude_code_version",
    "total_cost_usd",
    "duration_ms",
    "is_error",
    "stop_reason",
    "num_turns",
    "parent_tool_use_id",
}


def trim(obj: dict) -> dict:
    out = {k: v for k, v in obj.items() if k in KEEP}
    if isinstance(obj.get("message"), dict):
        m = obj["message"]
        out["message"] = {k: m[k] for k in ("role", "content", "model") if k in m}
        if obj.get("type") == "user" and not obj.get("isReplay"):
            # A compaction summary or a tool result: content aegis does not need.
            if isinstance(m.get("content"), str):
                out["message"]["content"] = "Summary: (trimmed)"
    if isinstance(obj.get("compact_metadata"), dict):
        c = obj["compact_metadata"]
        out["compact_metadata"] = {k: c.get(k) for k in ("pre_tokens", "post_tokens")}
    return out


def main() -> None:
    path, sends = sys.argv[1], sys.argv[2:]
    with open(path) as f:
        lines = [json.loads(x) for x in f]
    lines = [
        x
        for x in lines
        if x.get("type") != "rate_limit_event"
        and not (
            x.get("type") == "system" and str(x.get("subtype", "")).startswith("hook_")
        )
    ]
    out: list[dict] = []

    def rec(r: dict) -> None:
        out.append({"i": len(out), "ts": 1000.0 + len(out), **r})

    for text in sends:
        rec({"src": "aegis", "kind": "send", "text": text})
        while lines:
            x = lines.pop(0)
            rec({"src": "claude", "line": json.dumps(trim(x))})
            if x.get("type") == "result":
                break
    for r in out:
        print(json.dumps(r))


if __name__ == "__main__":
    main()
