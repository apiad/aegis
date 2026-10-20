import time

import pytest

import aegis.config_ops as config_ops
from aegis.app import App
from aegis.cascade import Fall
from aegis.claude.control import Model
from aegis.doctor import Found
from aegis.ops import Caller
from aegis.ranking import Ranking, annotate
from aegis.roots import make_roots

CONFIG = """\
default_agent: opus
agents:
  opus: {harness: claude-code, model: opus, effort: high, permission: full}
  free:
    harness: opencode
    models: [zen/glm-free, zen/kimi-free]
    effort: high
    permission: full
  glm: {harness: opencode, model: zen/glm-free, effort: high, permission: full}
"""

# Sample scores, not Artificial Analysis data.
RANKING = Ranking.from_dict(
    {
        "url": "https://artificialanalysis.ai",
        "refreshed": "2026-10-18",
        "models": {
            "opus": {"aa": "o", "coding": 72.0, "intelligence": 75.3,
                     "price_in": 15.0, "price_out": 75.0},
            "sonnet": {"aa": "s", "coding": 64.1, "intelligence": 68.0},
            "zen/kimi-free": {"aa": "k", "coding": 41.2, "intelligence": 54.0},
            "zen/glm-free": {"aa": "g", "coding": 38.9, "intelligence": 50.1},
            "zen/grok-free": {"aa": "x", "coding": 29.7, "intelligence": 40.3},
        },
    }
)  # fmt: skip


def m(value: str, free: bool = False, price: tuple | None = None) -> Model:
    p = price or (None, None)
    return Model(value, value, value, "", (), free=free, price_in=p[0], price_out=p[1])


FOUND = [
    Found(
        "claude-code",
        "/bin/claude",
        "1",
        annotate([m("opus"), m("sonnet"), m("haiku")], RANKING),
    ),
    Found(
        "opencode",
        "/bin/opencode",
        "1",
        annotate(
            [
                m("zen/big-pickle", True, (0, 0)),
                m("zen/grok-free", True, (0, 0)),
                m("zen/glm-free", True, (0, 0)),
                m("zen/kimi-free", True, (0, 0)),
            ],
            RANKING,
        ),
    ),
    Found("codex", None, error="codex is not on PATH"),
]


@pytest.fixture
def app(tmp_path, monkeypatch):
    async def detect(*_):
        return FOUND

    monkeypatch.setattr(config_ops, "detect", detect)
    monkeypatch.setattr(config_ops, "ranking", lambda: RANKING)
    (tmp_path / ".aegis.yaml").write_text(CONFIG)
    return App(make_roots(tmp_path, tmp_path))


async def test_models_list_marks_used_by_with_cascade_positions(app):
    out = await app.registry.call("models.list", {}, Caller("agent", log_id="x"))
    assert (out["refreshed"], out["source_url"]) == (
        "2026-10-18",
        "https://artificialanalysis.ai",
    )
    rows = {(r["harness"], r["value"]): r for r in out["models"]}
    assert rows["opencode", "zen/kimi-free"]["used_by"] == [{"agent": "free", "at": 1}]
    assert rows["opencode", "zen/glm-free"]["used_by"] == [
        {"agent": "free", "at": 0},
        {"agent": "glm", "at": 0},
    ]
    assert rows["claude-code", "opus"]["used_by"] == [{"agent": "opus", "at": 0}]
    opus = rows["claude-code", "opus"]
    assert (opus["rank"], opus["coding"], opus["intelligence"]) == (1, 72.0, 75.3)
    assert (opus["price_in"], opus["price_out"], opus["free"]) == (15.0, 75.0, False)
    pickle = rows["opencode", "zen/big-pickle"]
    assert (pickle["rank"], pickle["free"], pickle["used_by"]) == (None, True, [])
    # In use first, then by Coding Index; unranked last.
    assert [r["value"] for r in out["models"]] == [
        "opus",
        "zen/kimi-free",
        "zen/glm-free",
        "sonnet",
        "zen/grok-free",
        "haiku",
        "zen/big-pickle",
    ]
    assert "models.list" in [op.name for op in app.registry.agent_ops()]


async def test_models_list_reports_cooling(app):
    now = time.time()
    app.cooling.cool("opencode", "zen/kimi-free", Fall("model", 300), now)
    app.cooling.cool("claude-code", "opus", Fall("pool", 600), now)
    out = await app.registry.call("models.list", {})
    rows = {r["value"]: r for r in out["models"]}
    assert rows["zen/kimi-free"]["cooling_until"] == pytest.approx(now + 300)
    assert rows["zen/glm-free"]["cooling_until"] is None
    # Claude Code's limit cools the whole account: every Claude model.
    assert rows["sonnet"]["cooling_until"] == pytest.approx(now + 600)
