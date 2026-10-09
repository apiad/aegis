"""aegis stores for the usage tests, in the format aegis writes."""

import json
from datetime import datetime
from pathlib import Path


def epoch(iso: str) -> float:
    return datetime.fromisoformat(iso.replace("Z", "+00:00")).timestamp()


def assistant(mid: str | None, model: str = "claude-opus-4-7", **usage) -> dict:
    message: dict = {
        "model": model,
        "content": [{"type": "text", "text": "ok"}],
        "usage": {
            "input_tokens": 10,
            "output_tokens": 20,
            "cache_read_input_tokens": 100,
            **usage,
        },
    }
    if mid:
        message["id"] = mid
    return {"type": "assistant", "message": message}


def store(
    state: Path,
    log_id: str,
    cwd: Path,
    lines: list[tuple[str, dict]],
    model: str = "opus",
    **meta,
) -> None:
    """One aegis session as aegis writes it: a spawn record, then each
    (time, Claude line)."""
    (state / "transcripts").mkdir(parents=True, exist_ok=True)
    (state / "sessions").mkdir(parents=True, exist_ok=True)
    first = lines[0][0]
    records = [
        {
            "ts": epoch(first),
            "src": "aegis",
            "kind": "spawn",
            "cwd": str(cwd),
            "model": model,
        }
    ] + [
        {"ts": epoch(ts), "src": "claude", "line": json.dumps(obj)} for ts, obj in lines
    ]
    (state / "transcripts" / f"{log_id}.jsonl").write_text(
        "".join(json.dumps({"i": i, **r}) + "\n" for i, r in enumerate(records))
    )
    (state / "sessions" / f"{log_id}.json").write_text(
        json.dumps({"log_id": log_id, "handle": log_id, **meta})
    )


def opencode_step(part_id: str, *, inp: int, out: int, read: int) -> dict:
    """OpenCode's step-finish part, which carries a step's tokens."""
    return {
        "type": "message.part.updated",
        "properties": {
            "part": {
                "id": part_id,
                "type": "step-finish",
                "tokens": {"input": inp, "output": out, "cache": {"read": read}},
            }
        },
    }


def opencode_store(state: Path, log_id: str, cwd: Path, ts: str, *steps: dict) -> None:
    """One OpenCode session holding ``steps``, all at ``ts``."""
    (state / "transcripts").mkdir(parents=True, exist_ok=True)
    (state / "sessions").mkdir(parents=True, exist_ok=True)
    records = [
        {"ts": epoch(ts), "src": "aegis", "kind": "spawn", "cwd": str(cwd)},
    ] + [{"ts": epoch(ts), "src": "opencode", "line": json.dumps(st)} for st in steps]
    (state / "transcripts" / f"{log_id}.jsonl").write_text(
        "".join(json.dumps({"i": i, **r}) + "\n" for i, r in enumerate(records))
    )
    (state / "sessions" / f"{log_id}.json").write_text(
        json.dumps({"log_id": log_id, "handle": log_id, "harness": "opencode"})
    )


def codex_turn(model: str) -> dict:
    """CodexProcess's own line naming the model a turn was sent with."""
    return {
        "method": "aegis/turn",
        "params": {"model": model, "effort": "", "permission": "full"},
    }


def codex_usage(turn: str, *, inp: int, cached: int = 0, out: int = 0) -> dict:
    """One model request's tokens, as Codex reports them."""
    last = {
        "inputTokens": inp,
        "cachedInputTokens": cached,
        "cacheWriteInputTokens": 0,
        "outputTokens": out,
        "reasoningOutputTokens": 0,
        "totalTokens": inp + out,
    }
    return {
        "method": "thread/tokenUsage/updated",
        "params": {
            "threadId": "t1",
            "turnId": turn,
            "tokenUsage": {"last": last, "total": last},
        },
    }


def codex_store(state: Path, log_id: str, cwd: Path, ts: str, *lines: dict) -> None:
    """One Codex session holding ``lines``, all at ``ts``."""
    (state / "transcripts").mkdir(parents=True, exist_ok=True)
    (state / "sessions").mkdir(parents=True, exist_ok=True)
    records = [
        {"ts": epoch(ts), "src": "aegis", "kind": "spawn", "cwd": str(cwd)},
    ] + [{"ts": epoch(ts), "src": "codex", "line": json.dumps(ln)} for ln in lines]
    (state / "transcripts" / f"{log_id}.jsonl").write_text(
        "".join(json.dumps({"i": i, **r}) + "\n" for i, r in enumerate(records))
    )
    (state / "sessions" / f"{log_id}.json").write_text(
        json.dumps({"log_id": log_id, "handle": log_id, "harness": "codex"})
    )
