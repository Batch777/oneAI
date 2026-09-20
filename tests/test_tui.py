"""TUI headless tests: completion menu, scrolling, command dispatch."""
from __future__ import annotations

import asyncio

from textual.events import MouseScrollUp
from textual.widgets import Input, OptionList

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
            assert ol.option_count == 2

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

            # arrows scroll chat when completion hidden, navigate when visible
            chat.scroll_end(animate=False)
            await pilot.pause(0.2)
            b2 = chat.scroll_offset.y
            await pilot.press("up")
            await pilot.pause(0.2)
            assert chat.scroll_offset.y < b2
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

            # ctrl+d with text = delete forward (pi: deleteCharForward)
            box.value = "abc"
            box.cursor_position = 0
            await pilot.press("ctrl+d")
            assert box.value == "bc"

            # ctrl+d in NORMAL mode quits directly
            exited = []
            app.exit = lambda: exited.append(1)
            await pilot.press("escape")       # normal mode
            await pilot.press("ctrl+d")
            assert exited

            # double ctrl+d on empty input quits (INSERT mode)
            await pilot.press("i")            # back to insert
            box.value = ""
            exited = []
            app.exit = lambda: exited.append(1)  # spy instead of quitting
            await pilot.press("ctrl+d")
            await pilot.pause(0.2)
            assert not exited  # first press only warns
            chat = "\n".join(str(l.text) for l in app.query_one("#chat").lines)
            assert "再按一次" in chat
            await pilot.press("ctrl+d")
            assert exited  # second press quits

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
