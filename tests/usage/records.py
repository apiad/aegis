"""Store records with usage, for the usage deriver's tests."""

from __future__ import annotations

from aegis.usage.turns import Derived, SessionInfo, UsageDeriver
from tests.test_fold import Rec


class UseRec(Rec):
    """A Rec whose assistant lines carry ids, models and usage, and whose
    results carry Claude's running total."""

    def spawn(self, cwd="/w", harness="claude-code", model="opus", **kw):
        return self.own("spawn", cwd=cwd, harness=harness, model=model, **kw)

    def assistant(
        self,
        mid,
        model="claude-opus-5-5",
        inp=0,
        out=0,
        cr=0,
        cc5=0,
        cc1=0,
        tools=(),
        text="",
        parent=None,
    ):
        """One Claude stream line per content block, each repeating the
        message's usage, as Claude writes them."""
        usage = {
            "input_tokens": inp,
            "output_tokens": out,
            "cache_read_input_tokens": cr,
            "cache_creation_input_tokens": cc5 + cc1,
            "cache_creation": {
                "ephemeral_5m_input_tokens": cc5,
                "ephemeral_1h_input_tokens": cc1,
            },
        }
        blocks = [{"type": "text", "text": text or "ok"}] if not tools or text else []
        blocks += [
            {"type": "tool_use", "id": tid, "name": name, "input": inp_}
            for tid, name, inp_ in tools
        ]
        for b in blocks:
            self.claude(
                {
                    "type": "assistant",
                    "parent_tool_use_id": parent,
                    "message": {
                        "id": mid,
                        "model": model,
                        "role": "assistant",
                        "content": [b],
                        "usage": usage,
                    },
                }
            )

    def result(self, cost_total=None, duration_ms=1000):
        line = {
            "type": "result",
            "subtype": "success",
            "is_error": False,
            "duration_ms": duration_ms,
        }
        if cost_total is not None:
            line["total_cost_usd"] = cost_total
        return self.claude(line)


def run(r: Rec, info: SessionInfo | None = None, repo_of=None) -> Derived:
    """Feed every record to one deriver and concatenate what it returns."""
    d = UsageDeriver(info or SessionInfo(), repo_of or (lambda d: None))
    out = Derived()
    for rec in r.records:
        out.extend(d.feed(rec))
    return out
