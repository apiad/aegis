from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from aegis.locks.models import Claim


class PersistedClaimLog:
    def __init__(self, state_dir: Path) -> None:
        self._root = Path(state_dir) / "locks"
        self._root.mkdir(parents=True, exist_ok=True)

    def path(self) -> Path:
        return self._root / "claims.jsonl"

    # --- record builders -------------------------------------------------
    def claimed(self, claim: Claim) -> dict[str, Any]:
        return {"kind": "claimed", "claim_id": claim.claim_id,
                "handle": claim.handle, "prefixes": sorted(claim.prefixes),
                "files": sorted(claim.files), "intent": claim.intent,
                "desc": claim.desc, "since": claim.since}

    def released(self, claim_id: str, handle: str, at: str) -> dict[str, Any]:
        return {"kind": "released", "claim_id": claim_id,
                "handle": handle, "at": at}

    def reaped(self, claim_id: str, handle: str, at: str) -> dict[str, Any]:
        return {"kind": "reaped", "claim_id": claim_id,
                "handle": handle, "at": at}

    # --- io --------------------------------------------------------------
    def write(self, record: dict[str, Any]) -> None:
        with self.path().open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, separators=(",", ":")))
            f.write("\n")

    def read(self) -> list[dict[str, Any]]:
        p = self.path()
        if not p.is_file():
            return []
        out: list[dict[str, Any]] = []
        for line in p.read_text().splitlines():
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                continue
        return out

    def replay(self) -> dict[str, Claim]:
        live: dict[str, Claim] = {}
        for rec in self.read():
            kind = rec.get("kind")
            if kind == "claimed":
                cid = rec["claim_id"]
                live[cid] = Claim(
                    claim_id=cid, handle=rec["handle"],
                    prefixes=frozenset(rec.get("prefixes", [])),
                    files=frozenset(rec.get("files", [])),
                    intent=rec.get("intent", "shared"),
                    desc=rec.get("desc", ""), since=rec.get("since", ""))
            elif kind in ("released", "reaped"):
                live.pop(rec.get("claim_id"), None)
        return live
