import pytest
from rich.text import Text

from aegis.tui.pane import SubagentBox
from aegis.tui.themes import INK, aegis_colors


def _pal():
    return aegis_colors(INK)


@pytest.mark.asyncio
async def test_box_collapsed_shows_header_only_expanded_shows_children():
    from textual.app import App

    class Host(App):
        def compose(self):
            box = SubagentBox(Text("🤖 Task(explore)"), "Task(explore)",
                              _pal(), collapsed=True)
            box.add_child(Text("⏺ Read(a.py)"), "Read(a.py)")
            box.add_child(Text("  └ ok done"), "ok done")
            self.box = box
            yield box

    app = Host()
    async with app.run_test():
        box = app.box
        assert box.collapsed is True
        # Payload stays complete regardless of collapse state.
        assert "Read(a.py)" in box.text_payload()
        box.toggle()
        assert box.collapsed is False


@pytest.mark.asyncio
async def test_box_fold_child_result_and_close():
    from textual.app import App

    class Host(App):
        def compose(self):
            box = SubagentBox(Text("🤖 Task(x)"), "Task(x)", _pal())
            box.add_child(Text("⏺ Read(a.py)"), "Read(a.py)")
            assert box.fold_child_result(Text("  └ ok body"), "ok body")
            box.close(Text("── done ──"), "done")
            self.box = box
            yield box

    app = Host()
    async with app.run_test():
        payload = app.box.text_payload()
        assert "Read(a.py)" in payload
        assert "ok body" in payload   # folded into the child, not a new one
        assert "done" in payload      # footer


@pytest.mark.asyncio
async def test_fold_child_result_without_children_returns_false():
    from textual.app import App

    class Host(App):
        def compose(self):
            box = SubagentBox(Text("🤖 Task(x)"), "Task(x)", _pal())
            self.result = box.fold_child_result(Text("orphan"), "orphan")
            self.box = box
            yield box

    app = Host()
    async with app.run_test():
        assert app.result is False
