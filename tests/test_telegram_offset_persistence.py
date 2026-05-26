from __future__ import annotations

from pathlib import Path

from aegis.telegram.frontend import TelegramFrontend


def make_frontend(tmp_path: Path) -> TelegramFrontend:
    return TelegramFrontend(bot=None, manager=None, bridge=None, cfg=None,
                            chat_id=1, auto_prompt="", state_dir=tmp_path)


def test_load_offset_missing_returns_zero(tmp_path):
    f = make_frontend(tmp_path)
    assert f._load_offset() == 0


def test_save_then_load(tmp_path):
    f = make_frontend(tmp_path)
    f._save_offset(42)
    assert f._load_offset() == 42


def test_load_corrupt_returns_zero(tmp_path, caplog):
    (tmp_path / "telegram.offset").write_text("not-a-number")
    f = make_frontend(tmp_path)
    import logging
    with caplog.at_level(logging.WARNING, logger="aegis.telegram"):
        assert f._load_offset() == 0
    assert "corrupt" in caplog.text.lower()


def test_save_atomic(tmp_path):
    f = make_frontend(tmp_path)
    f._save_offset(7)
    assert not list(tmp_path.glob("*.tmp"))
    assert (tmp_path / "telegram.offset").read_text().strip() == "7"
