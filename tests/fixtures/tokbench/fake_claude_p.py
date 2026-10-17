"""A fake ``claude -p`` for the token bench's stock arm.

Reads argv as ``claude -p PROMPT ... [--resume ID]``, appends one assistant line
with a usage block to ``$CLAUDE_CONFIG_DIR/projects/x/<session>.jsonl``, records
its argv in ``$CLAUDE_CONFIG_DIR/argv.jsonl``, and prints
``{"session_id": ...}``. ``FAKE_P_MODE=crash`` prints to stderr and exits 2;
``FAKE_P_MODE=sleep`` sleeps a minute first.
"""

import json
import os
import sys
import time
import uuid
from pathlib import Path

argv = sys.argv[1:]
mode = os.environ.get("FAKE_P_MODE", "")
if mode == "crash":
    print("fake claude: something broke", file=sys.stderr)
    sys.exit(2)
if mode == "sleep":
    time.sleep(60)
sid = argv[argv.index("--resume") + 1] if "--resume" in argv else str(uuid.uuid4())
root = Path(os.environ["CLAUDE_CONFIG_DIR"]) / "projects" / "x"
root.mkdir(parents=True, exist_ok=True)
with open(root.parents[1] / "argv.jsonl", "a") as f:
    f.write(json.dumps(argv) + "\n")
line = {
    "type": "assistant",
    "requestId": str(uuid.uuid4()),
    "uuid": str(uuid.uuid4()),
    "timestamp": f"{time.time():.6f}",
    "message": {
        "content": [{"type": "text", "text": "ok"}],
        "usage": {
            "input_tokens": 0,
            "cache_creation_input_tokens": 1000,
            "cache_read_input_tokens": 0,
            "output_tokens": 10,
        },
    },
}
with open(root / f"{sid}.jsonl", "a") as f:
    f.write(json.dumps(line) + "\n")
print(json.dumps({"type": "result", "session_id": sid, "result": "ok"}))
