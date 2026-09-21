"""TUI vim-mode tests (pi-vimmode style modal editing, single-line subset)."""
from __future__ import annotations

import asyncio

from textual.widgets import Input, Label

from oneai.legacy.tui import CommandInput, OneAIApp


def run(coro):
    asyncio.run(coro)


async def press(pilot, *keys):
    for k in keys:
        await pilot.press(k)
        await pilot.pause(0.05)


class TestVimMode:
    def test_modal_editing(self):
        run(self._modal())

    async def _modal(self):
        app = OneAIApp()
        async with app.run_test() as pilot:
            await pilot.pause(0.5)
            box = app.query_one("#input", CommandInput)
            mode = app.query_one("#mode", Label)
            box.focus()

            # starts in INSERT, typing works
            assert box.vim_mode == "insert"
            await press(pilot, "h", "e", "l", "l", "o")
            assert box.value == "hello"

            # Esc -> NORMAL; letters are motions, not text
            await press(pilot, "escape")
            assert box.vim_mode == "normal"
            assert "NORMAL" in str(mode.render())
            box.cursor_position = 0
            await press(pilot, "x")          # delete char forward
            assert box.value == "ello"
            await press(pilot, "dollar_sign")  # $ -> end (textual key name)
            assert box.cursor_position == 4
            await press(pilot, "0")
            assert box.cursor_position == 0

            # dd clears the line
            await press(pilot, "d", "d")
            assert box.value == ""

            # i -> INSERT again, typing works
            await press(pilot, "i")
            assert box.vim_mode == "insert"
            await press(pilot, "a", "b")
            assert box.value == "ab"

            # A -> append at end in insert mode
            await press(pilot, "escape", "0", "A")
            assert box.vim_mode == "insert"
            assert box.cursor_position == 2

            # D deletes to end in normal
            await press(pilot, "escape", "0", "l", "D")
            assert box.value == "a"

    def test_esc_closes_completion_first(self):
        run(self._esc())

    async def _esc(self):
        app = OneAIApp()
        async with app.run_test() as pilot:
            await pilot.pause(0.5)
            box = app.query_one("#input", CommandInput)
            box.focus()
            box.value = "/re"
            await pilot.pause(0.2)  # flush the pending Input.Changed message
            assert app.completion_active()
            await press(pilot, "escape")
            await pilot.pause(0.2)
            assert not app.completion_active()   # menu closed
            assert box.vim_mode == "insert"      # still insert
            await press(pilot, "escape")
            assert box.vim_mode == "normal"      # second Esc -> normal

    def test_normal_enter_submits(self):
        run(self._submit())

    async def _submit(self):
        app = OneAIApp()
        for c in app.UI_COMMANDS:
            c.handler = {"help": app._ui_help}.get(c.name, c.handler)
        async with app.run_test() as pilot:
            await pilot.pause(0.5)
            box = app.query_one("#input", CommandInput)
            box.focus()
            box.value = "/help"
            await press(pilot, "escape")  # normal mode
            await press(pilot, "enter")   # submit from normal
            await pilot.pause(0.4)
            chat = "\n".join(str(l.text) for l in app.query_one("#chat").lines)
            assert "命令一览" in chat

    def test_vim_toggle(self):
        run(self._toggle())

    async def _toggle(self):
        app = OneAIApp()
        for c in app.UI_COMMANDS:
            c.handler = {"vim": app._ui_vim}.get(c.name, c.handler)
        async with app.run_test() as pilot:
            await pilot.pause(0.5)
            box = app.query_one("#input", CommandInput)
            box.focus()
            app._ui_vim("off")
            await press(pilot, "escape")
            assert box.vim_mode == "insert"  # no modal switch when disabled
            assert "PLAIN" in str(app.query_one("#mode", Label).render())

    def test_text_objects(self):
        run(self._objects())

    async def _objects(self):
        app = OneAIApp()
        async with app.run_test() as pilot:
            await pilot.pause(0.5)
            box = app.query_one("#input", CommandInput)
            box.focus()

            def set_normal(text, pos):
                box.value = text
                box.cursor_position = pos
                box.set_vim_mode("normal")

            # diw: delete inner word
            set_normal("hello world foo", 7)
            await press(pilot, "d", "i", "w")
            assert box.value == "hello  foo", box.value

            # daw: delete around word (incl. trailing space)
            set_normal("hello world foo", 7)
            await press(pilot, "d", "a", "w")
            assert box.value == "hello foo", box.value

            # ciw: change inner word -> insert mode
            set_normal("hello world", 2)
            await press(pilot, "c", "i", "w")
            assert box.value == " world"
            assert box.vim_mode == "insert"

            # di" : inner quoted
            set_normal('say "hello world" ok', 9)
            await press(pilot, "d", "i", '"')
            assert box.value == 'say "" ok', box.value

            # da( : around parens
            set_normal("f(x + 1) end", 4)
            await press(pilot, "d", "a", "(")
            assert box.value == "f end", box.value

            # ci{ : change inside braces
            set_normal("{a, b}", 2)
            await press(pilot, "c", "i", "{")
            assert box.value == "{}"
            assert box.vim_mode == "insert"

    def test_jk_chord(self):
        run(self._jk())

    async def _jk(self):
        app = OneAIApp()
        async with app.run_test() as pilot:
            await pilot.pause(0.5)
            box = app.query_one("#input", CommandInput)
            box.focus()
            assert box.vim_mode == "insert"
            # fast jk -> normal mode, 'j' removed
            await pilot.press("j")
            await pilot.press("k")
            await pilot.pause(0.2)
            assert box.vim_mode == "normal"
            assert box.value == ""
            # slow j,k -> both inserted
            box.set_vim_mode("insert")
            await pilot.press("j")
            await pilot.pause(0.7)
            await pilot.press("k")
            await pilot.pause(0.2)
            assert box.vim_mode == "insert"
            assert box.value == "jk"
