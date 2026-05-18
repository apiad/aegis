#!/usr/bin/env bash
# Captures real claude stream-json output for parser fixtures.
# Run from the repo root. Requires `claude` on PATH.
set -euo pipefail
mkdir -p tests/fixtures

echo '{"type":"user","message":{"role":"user","content":"Reply with exactly: hello from aegis"}}' \
  | claude -p --input-format stream-json --output-format stream-json --replay-user-messages --verbose \
      --permission-mode plan \
  > tests/fixtures/stream_text.jsonl

echo '{"type":"user","message":{"role":"user","content":"Run the bash command: echo hi"}}' \
  | claude -p --input-format stream-json --output-format stream-json --replay-user-messages --verbose \
      --permission-mode bypassPermissions \
  > tests/fixtures/stream_tool.jsonl

# Sanitize: drop noisy/non-rendered lines and redact identifiers + local
# paths before these fixtures are committed to the (public) repo.
python3 - <<'PY'
import json
REDACT_KEYS = {"cwd","memory_paths","mcp_servers","session_id","uuid","request_id",
               "agents","skills","plugins","slash_commands","tools","apiKeySource",
               "parent_tool_use_id","modelUsage","usage","total_cost_usd","ttft_ms",
               "duration_api_ms","api_error_status","permission_denials"}
def scrub(o):
    if isinstance(o, dict):
        return {k:("<redacted>" if k in REDACT_KEYS else scrub(v)) for k,v in o.items()}
    if isinstance(o, list):
        return [scrub(x) for x in o]
    return o
for fn in ("stream_text.jsonl","stream_tool.jsonl"):
    p=f"tests/fixtures/{fn}"
    out=[]
    for l in open(p):
        if not l.strip(): continue
        o=json.loads(l)
        if o.get("type")=="system" and o.get("subtype") in ("hook_started","hook_response"):
            continue
        if o.get("type")=="rate_limit_event":
            continue
        out.append(json.dumps(scrub(o)))
    open(p,"w").write("\n".join(out)+"\n")
PY

echo "Captured + sanitized:"
wc -l tests/fixtures/stream_text.jsonl tests/fixtures/stream_tool.jsonl
