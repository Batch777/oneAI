"""TUI headless tests: completion menu, scrolling, command dispatch."""
from __future__ import annotations

import asyncio

from textual.events import MouseScrollUp
from textual.widgets import Input, Label, OptionList

from oneai.tui import OneAIApp, WHEEL_SCROLL_LINES


def run(coro):
    asyncio.run(coro)


async def submit(pilot, app, text):
    box = app.query_one("#input", Input)
    box.value = text
    await pilot.press("enter")
    await pilot.pause(0.4)


async def make_app():
    app = OneAIApp()
    for c in app.UI_COMMANDS:  # wire handlers like run() does
        c.handler = {"help": app._ui_help}.get(c.name, c.handler)
    return app


class TestCompletion:
    def test_vertical_menu_flow(self):
        run(self._flow())

    async def _flow(self):
        app = await make_app()
        async with app.run_test() as pilot:
            await pilot.pause(0.5)
            box = app.query_one("#input", Input)
            ol = app.query_one("#completion", OptionList)

            box.value = "/"
            app._refresh_completion("/")
            assert app.completion_active() and ol.option_count >= 9

            box.value = "/re"
            app._refresh_completion("/re")
            assert ol.option_count == 3  # reindex, reload, resume

            app.move_completion(1)
            app.apply_completion()
            assert box.value in ("/reload", "/reindex")

            # args typed → menu hides
            box.value = "/draft 写"
            app._refresh_completion(box.value)
            assert not app.completion_active()

    def test_enter_completes_prefix_but_submits_exact(self):
        run(self._enter())

    async def _enter(self):
        app = await make_app()
        async with app.run_test() as pilot:
            await pilot.pause(0.5)
            box = app.query_one("#input", Input)

            box.value = "/hel"
            app._refresh_completion("/hel")
            box.action_submit_or_complete()  # completes, not submits
            assert box.value == "/help"

            box.value = "/help"
            app._refresh_completion("/help")
            assert not app.completion_active()
            box.action_submit_or_complete()  # submits
            await pilot.pause(0.4)
            chat = "\n".join(str(l.text) for l in app.query_one("#chat").lines)
            assert "命令一览" in chat


class TestScrolling:
    def test_wheel_step_and_keyboard(self):
        run(self._wheel())

    async def _wheel(self):
        app = await make_app()
        async with app.run_test(size=(80, 24)) as pilot:
            await pilot.pause(0.5)
            chat = app.chat()
            for i in range(100):
                chat.write(f"line {i}")
            await pilot.pause(0.3)
            bottom = chat.scroll_offset.y
            assert bottom > 0

            chat.post_message(MouseScrollUp(chat, x=10, y=5, delta_x=0, delta_y=0,
                                            button=0, shift=False, meta=False, ctrl=False))
            await pilot.pause(0.3)
            assert bottom - chat.scroll_offset.y == WHEEL_SCROLL_LINES

            # arrows navigate input history when completion hidden
            app.input_history = ["newer-cmd", "older-cmd"]  # newest first
            box = app.query_one("#input", Input)
            box.value = "live draft"
            await pilot.press("up")
            await pilot.pause(0.2)
            assert box.value == "newer-cmd"
            await pilot.press("up")
            assert box.value == "older-cmd"
            await pilot.press("down")
            assert box.value == "newer-cmd"
            await pilot.press("down")         # past newest -> restore draft
            assert box.value == "live draft"
            box = app.query_one("#input", Input)
            box.value = "/re"
            app._refresh_completion("/re")
            ol = app.query_one("#completion", OptionList)
            await pilot.press("down")
            assert ol.highlighted == 1


class TestEditorKeys:
    def test_pi_aligned_editor_keys(self):
        run(self._keys())

    async def _keys(self):
        app = await make_app()
        async with app.run_test(size=(80, 24)) as pilot:
            await pilot.pause(0.5)
            box = app.query_one("#input", Input)
            box.focus()

            # ctrl+c enters NORMAL mode (vim on)
            box.value = "draft text"
            await pilot.press("ctrl+c")
            assert box.vim_mode == "normal"
            assert box.value == "draft text"  # text kept
            await pilot.press("i")            # back to insert

            # ctrl+u deletes to line start (pi default, restored)
            box.value = "abcdef"
            box.cursor_position = 3
            await pilot.press("ctrl+u")
            assert box.value == "def"

            # ctrl+d in INSERT clears all text (never quits)
            box.value = "abc"
            exited = []
            app.exit = lambda: exited.append(1)  # spy instead of quitting
            await pilot.press("ctrl+d")
            assert box.value == ""
            assert not exited

            # NORMAL mode: double ctrl+d quits (with status hint)
            box.value = "abc"
            app._last_ctrl_d = 0.0
            await pilot.press("escape")
            await pilot.press("ctrl+d")
            await pilot.pause(0.2)
            assert not exited and box.value == "abc"
            status = str(app.query_one("#status", Label).render())
            assert "再按一次" in status
            await pilot.press("ctrl+d")
            assert exited

            # empty input INSERT: double ctrl+d quits too
            await pilot.press("i")
            box.value = ""
            exited.clear()
            app._last_ctrl_d = 0.0
            await pilot.press("ctrl+d")
            await pilot.pause(0.2)
            assert not exited  # first press warns
            await pilot.press("ctrl+d")
            assert exited      # second press quits

    def test_cursor_movement_keys(self):
        run(self._cursor())

    async def _cursor(self):
        app = await make_app()
        async with app.run_test() as pilot:
            await pilot.pause(0.5)
            box = app.query_one("#input", Input)
            box.focus()
            box.value = "hello world"
            box.cursor_position = 11
            await pilot.press("ctrl+b")           # left
            assert box.cursor_position == 10
            await pilot.press("ctrl+a")           # line start
            assert box.cursor_position == 0
            await pilot.press("ctrl+f")           # right
            assert box.cursor_position == 1
            await pilot.press("alt+right")        # word right
            assert box.cursor_position == 6  # past "hello "


class TestHistory:
    def test_history_record_and_navigate(self, tmp_path):
        run(self._hist(tmp_path))

    async def _hist(self, tmp_path):
        app = await make_app()
        async with app.run_test() as pilot:
            await pilot.pause(0.5)
            app._history_file = tmp_path / "input_history.txt"  # isolate state
            app.input_history = []
            box = app.query_one("#input", Input)

            await submit(pilot, app, "/help")
            await submit(pilot, app, "/inbox")
            assert app.input_history == ["/inbox", "/help"]  # newest first

            box.value = "draft"
            await pilot.press("up")
            assert box.value == "/inbox"
            await pilot.press("up")
            assert box.value == "/help"
            await pilot.press("down")
            assert box.value == "/inbox"
            await pilot.press("down")
            assert box.value == "draft"  # draft restored

            # consecutive duplicates skipped, non-consecutive kept (pi semantics)
            await submit(pilot, app, "/help")
            await submit(pilot, app, "/help")
            assert app.input_history[0] == "/help"
            assert app.input_history.count("/help") == 2  # not adjacent → kept

            # persisted to file
            content = (tmp_path / "input_history.txt").read_text()
            assert "/help" in content and "/inbox" in content

    def test_normal_jk_navigates_history(self):
        run(self._jk())

    async def _jk(self):
        app = await make_app()
        async with app.run_test() as pilot:
            await pilot.pause(0.5)
            app.input_history = ["newer", "older"]
            box = app.query_one("#input", Input)
            box.focus()
            await pilot.press("escape")  # NORMAL
            await pilot.press("k")
            await pilot.pause(0.1)
            assert box.value == "newer"
            await pilot.press("k")
            assert box.value == "older"
            await pilot.press("j")
            assert box.value == "newer"


class TestDispatch:
    def test_unknown_command(self):
        run(self._unknown())

    async def _unknown(self):
        app = await make_app()
        async with app.run_test() as pilot:
            await pilot.pause(0.5)
            await submit(pilot, app, "/nonexistent")
            chat = "\n".join(str(l.text) for l in app.query_one("#chat").lines)
            assert "未知命令" in chat
