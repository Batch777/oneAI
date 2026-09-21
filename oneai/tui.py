"""oneAI TUI v2 — standalone product UI on top of the extensible runtime.

pi-inspired:
- Slash commands from a flat registry (UI commands + extension commands)
- Vertical completion menu (↑↓ navigate, Tab/Enter select, Esc dismiss)
- Markdown-rendered answers, tool-call status lines, permission-gate modal
- Mouse capture off → Ghostty native drag-select & copy; /copy for last answer
- Kitty graphics: /image preview, /vision questions, agent-initiated image_show
"""
from __future__ import annotations

import concurrent.futures
import re
import subprocess
from pathlib import Path

from rich.markdown import Markdown
from textual import work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import Input, Label, OptionList, RichLog
from textual.widgets.option_list import Option

from .config import Config
from .runtime import Command, Runtime

WHEEL_SCROLL_LINES = 6   # pi: 5 for trackpad; slightly faster per request
LINE_SCROLL_LINES = 2    # ↑/↓ step when the completion menu is hidden
JK_TIMEOUT = 0.5         # insert-mode jk chord window (seconds)
PENDING_TIMEOUT = 1.0    # multi-key normal-mode sequences (dd/diw/...)
WORD_RE = re.compile(r"[\w\u4e00-\u9fff]+")
PAIRS = {'"': ('"', '"'), "'": ("'", "'"), "`": ("`", "`"),
         "(": ("(", ")"), ")": ("(", ")"), "b": ("(", ")"),
         "[": ("[", "]"), "]": ("[", "]"),
         "{": ("{", "}"), "}": ("{", "}"), "B": ("{", "}"),
         "<": ("<", ">"), ">": ("<", ">")}
# Textual reports punctuation as key names; map them back to literal chars.
PUNCT_KEYS = {"quotedbl": '"', "apostrophe": "'", "grave_accent": "`",
              "left_parenthesis": "(", "right_parenthesis": ")",
              "left_square_bracket": "[", "right_square_bracket": "]",
              "left_curly_bracket": "{", "right_curly_bracket": "}",
              "less_than_sign": "<", "greater_than_sign": ">",
              "dollar_sign": "$"}


def text_object_span(text: str, pos: int, scope: str, obj: str) -> tuple[int, int] | None:
    """Vim text objects: iw/aw (word) and i/obj a/obj for quotes & brackets."""
    pos = min(pos, len(text))
    if obj == "w":
        for m in WORD_RE.finditer(text):
            if m.start() <= pos <= m.end() and m.start() < m.end():
                if scope == "i":
                    return m.start(), m.end()
                # around: include trailing whitespace, else leading
                end = m.end()
                while end < len(text) and text[end] == " ":
                    end += 1
                if end > m.end():
                    return m.start(), end
                start = m.start()
                while start > 0 and text[start - 1] == " ":
                    start -= 1
                return start, m.end()
        return None
    if obj in PAIRS:
        o, c = PAIRS[obj]
        left = text.rfind(o, 0, pos + 1) if pos < len(text) else text.rfind(o)
        # for symmetric quotes, opening = the quote before the cursor
        if o == c:
            left = text.rfind(o, 0, pos)
            if left < 0:
                return None
            right = text.find(c, left + 1)
        else:
            left = text.rfind(o, 0, pos + 1)
            if left < 0:
                return None
            right = text.find(c, left + 1)
            if right < pos and pos < len(text):
                right = text.find(c, pos)
        if left < 0 or right < 0 or right <= left:
            return None
        return (left + 1, right) if scope == "i" else (left, right + 1)
    return None


class ChatLog(RichLog):
    """RichLog with pi-style faster wheel scrolling.

    Textual dispatches _on_<event> across the whole MRO, so we can't override
    _on_mouse_scroll_up without Widget's handler also firing. Instead we
    override the pointer-scroll primitives it delegates to.
    """

    def _scroll_up_for_pointer(self, animate: bool = False) -> bool:
        self.scroll_relative(y=-WHEEL_SCROLL_LINES, animate=False)
        return True

    def _scroll_down_for_pointer(self, animate: bool = False) -> bool:
        self.scroll_relative(y=WHEEL_SCROLL_LINES, animate=False)
        return True


class ConfirmScreen(ModalScreen[bool]):
    """Permission gate modal (pi-style confirm for sensitive tool calls)."""

    BINDINGS = [("y", "yes", "允许"), ("n", "no", "拒绝"), ("escape", "no", "拒绝")]

    def __init__(self, title: str, message: str) -> None:
        super().__init__()
        self._title = title
        self._message = message

    def compose(self) -> ComposeResult:
        yield Label(f"[bold yellow]{self._title}[/bold yellow]")
        yield Label(self._message)
        yield Label("[bold]y[/bold] 允许   [bold]n[/bold]/Esc 拒绝")

    def action_yes(self) -> None:
        self.dismiss(True)

    def action_no(self) -> None:
        self.dismiss(False)


class CommandInput(Input):
    """Modal editor (pi-vimmode style, single-line subset).

    INSERT: normal typing + completion + readline ctrl keys (default).
    NORMAL: vim motions/edits; Enter submits; j/k scroll the transcript.
    Esc in INSERT closes the completion menu first, then enters NORMAL.
    """

    BINDINGS = [
        Binding("tab", "complete", "补全", show=False),
        Binding("up", "comp_up", show=False),
        Binding("down", "comp_down", show=False),
        Binding("enter", "submit_or_complete", show=False),
        Binding("ctrl+b", "cursor_left", show=False),
        Binding("ctrl+f", "cursor_right", show=False),
        Binding("alt+left", "cursor_left_word", show=False),
        Binding("alt+right", "cursor_right_word", show=False),
        Binding("ctrl+d", "ctrl_d", show=False),
        Binding("ctrl+c", "clear_input", show=False),
        Binding("escape", "esc_normal", show=False),
    ]

    def action_esc_normal(self) -> None:
        """Esc in NORMAL mode: interrupt a running agent (pi behavior)."""
        if self.vim_mode == "normal":
            app: OneAIApp = self.app  # type: ignore[assignment]
            app.abort_agent()

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.vim_enabled = True
        self.vim_mode = "insert"
        self._pending: tuple[str, float] | None = None  # multi-key (dd/diw/cip...)
        self._jk_time: float | None = None              # insert-mode jk chord

    # --- mode plumbing ----------------------------------------------------

    def set_vim_mode(self, mode: str) -> None:
        self.vim_mode = mode
        self._pending = None
        app: OneAIApp = self.app  # type: ignore[assignment]
        app.update_mode_indicator()

    def _on_key(self, event) -> None:
        """Subclass handler runs before Input's (MRO order); prevent_default
        stops further dispatch, so Input never sees normal-mode keys."""
        if not self.vim_enabled:
            if event.key == "escape":
                app: OneAIApp = self.app  # type: ignore[assignment]
                app.hide_completion()
                event.prevent_default()
                event.stop()
            return
        if self.vim_mode == "insert":
            if event.key == "escape":
                app: OneAIApp = self.app  # type: ignore[assignment]
                if app.completion_active():
                    app.hide_completion()  # pi-vimmode: Esc closes autocomplete first
                else:
                    self.set_vim_mode("normal")
                event.prevent_default()
                event.stop()
                return
            # jk chord -> NORMAL (user preference; pi-vimmode rejects chords)
            import time

            if event.key == "j":
                self._jk_time = time.monotonic()
            elif event.key == "k" and self._jk_time is not None:
                fresh = time.monotonic() - self._jk_time < JK_TIMEOUT
                pos = self.cursor_position
                if fresh and pos > 0 and self.value[pos - 1] == "j":
                    self.value = self.value[: pos - 1] + self.value[pos:]
                    self.cursor_position = pos - 1
                    self.set_vim_mode("normal")
                    event.prevent_default()
                    event.stop()
                self._jk_time = None
            elif event.is_printable:
                self._jk_time = None
            return
        # normal mode
        key = event.character or PUNCT_KEYS.get(event.key, event.key)
        if not event.is_printable and key == event.key:
            return  # ctrl/alt/enter/tab/... go to BINDINGS (quit, scroll, submit)
        if self._handle_normal_key(key):
            event.prevent_default()
            event.stop()

    def _handle_normal_key(self, key: str) -> bool:
        import time

        app: OneAIApp = self.app  # type: ignore[assignment]
        now = time.monotonic()
        pending = self._pending if self._pending and now - self._pending[1] < PENDING_TIMEOUT else None
        self._pending = None

        if pending and pending[0] in ("d", "c") and key == pending[0]:  # dd / cc
            self.value = ""
            if pending[0] == "c":
                self.set_vim_mode("insert")
            return True
        if pending and len(pending[0]) == 1 and pending[0] in ("d", "c") and key in ("i", "a"):
            self._pending = (pending[0] + key, now)  # di/ci/da/ca, waiting for object
            return True
        if pending and len(pending[0]) == 2:  # text object: diw/daw/ciw/di"/ca( ...
            op, scope = pending[0]
            span = text_object_span(self.value, self.cursor_position, scope, key)
            if span:
                s, e = span
                self.value = self.value[:s] + self.value[e:]
                self.cursor_position = s
                if op == "c":
                    self.set_vim_mode("insert")
            return True
        if key in ("d", "c"):
            self._pending = (key, now)
            return True

        match key:
            case "h": self.action_cursor_left()
            case "l": self.action_cursor_right()
            case "w" | "e": self.action_cursor_right_word()
            case "b": self.action_cursor_left_word()
            case "0": self.action_home()
            case "$" | "dollar_sign": self.action_end()
            case "i": self.set_vim_mode("insert")
            case "a": self.action_cursor_right(); self.set_vim_mode("insert")
            case "I": self.action_home(); self.set_vim_mode("insert")
            case "A": self.action_end(); self.set_vim_mode("insert")
            case "x": self.action_delete_right()
            case "X": self.action_delete_left()
            case "D": self.action_delete_right_all()
            case "C": self.action_delete_right_all(); self.set_vim_mode("insert")
            case "j": app.history_move(-1)   # newer
            case "k": app.history_move(1)    # older
            case _:
                return True  # swallow all other keys in normal mode
        return True

    def action_ctrl_d(self) -> None:
        if self.vim_enabled and self.vim_mode == "normal":
            app: OneAIApp = self.app  # type: ignore[assignment]
            app.handle_ctrl_d()  # double-press quit
        elif self.value:
            self.value = ""  # INSERT with text: clear all
        else:
            app2: OneAIApp = self.app  # type: ignore[assignment]
            app2.handle_ctrl_d()  # INSERT empty: double-press quit

    def action_clear_input(self) -> None:
        # Ctrl+C: with vim on, enter NORMAL (user preference); off -> clear line
        if self.vim_enabled:
            self.set_vim_mode("normal")
        else:
            self.value = ""

    def action_comp_dismiss(self) -> None:
        app: OneAIApp = self.app  # type: ignore[assignment]
        app.hide_completion()

    def action_complete(self) -> None:
        app: OneAIApp = self.app  # type: ignore[assignment]
        app.apply_completion()

    def action_comp_up(self) -> None:
        app: OneAIApp = self.app  # type: ignore[assignment]
        if app.completion_active():
            app.move_completion(-1)
        else:
            app.history_move(1)  # older input (pi: cursorUp browses history)

    def action_comp_down(self) -> None:
        app: OneAIApp = self.app  # type: ignore[assignment]
        if app.completion_active():
            app.move_completion(1)
        else:
            app.history_move(-1)  # newer input / back to draft

    def action_submit_or_complete(self) -> None:
        app: OneAIApp = self.app  # type: ignore[assignment]
        # If the menu is showing a strict prefix, Enter picks the highlighted
        # item (pi/Claude Code behavior); exact commands submit directly.
        if app.completion_active():
            app.apply_completion()
        else:
            self.post_message(Input.Submitted(self, self.value))

    def action_comp_dismiss(self) -> None:
        app: OneAIApp = self.app  # type: ignore[assignment]
        app.hide_completion()


class OneAIApp(App):
    CSS = """
    /* No borders: box-drawing chars would end up in mouse-selected copies. */
    #chat { height: 1fr; padding: 0 1; }
    /* bottom zone is one uniform panel matching the input box gray */
    #bottom { height: auto; background: $boost; }
    #completion { height: auto; max-height: 9; display: none; }
    #completion.visible { display: block; }
    #status { height: auto; padding: 0 2; color: $warning; display: none; }
    #status.visible { display: block; }
    /* mode badge: own row, centered, blends into the panel */
    #mode { width: 1fr; height: 1; text-align: center; color: $success; text-style: bold; }
    #mode.normal { color: $primary; }
    /* rounded prompt box (pi-style) */
    #input { width: 1fr; height: 3; border: round $secondary; background: $boost; padding: 0 1; margin-bottom: 1; }
    #input:focus { border: round $primary; }
    ConfirmScreen { align: center middle; }
    ConfirmScreen Label { width: 60; padding: 1 2; background: $surface; }
    """

    # Transcript scrolling (pi keybindings: pageUp/pageDown scroll the
    # transcript even while the editor is focused).
    BINDINGS = [
        ("ctrl+q", "quit", "退出"),
        ("pageup", "scroll_page_up", "上翻"),
        ("pagedown", "scroll_page_down", "下翻"),
    ]

    def action_scroll_page_up(self) -> None:
        self.chat().scroll_page_up()

    def action_scroll_page_down(self) -> None:
        self.chat().scroll_page_down()

    def handle_ctrl_d(self) -> None:
        """Double Ctrl+D quits (like shell EOF), with a status-line hint.
        Identical behavior in INSERT and NORMAL, with or without text."""
        import time

        now = time.monotonic()
        if now - getattr(self, "_last_ctrl_d", 0.0) < 1.5:
            self.exit()
        else:
            self._last_ctrl_d = now
            self.show_status("再按一次 Ctrl+D 退出", fade_after=1.6)

    UI_COMMANDS = [
        Command("help", "显示帮助"),
        Command("draft", "起草手稿（对话式，带确认门）", argument_hint="<起草指令>"),
        Command("inbox", "列出 inbox 捕获与手稿"),
        Command("reindex", "重建检索索引"),
        Command("reload", "重新加载扩展（~/.oneai/extensions/）"),
        Command("copy", "复制最近一条回答到剪贴板"),
        Command("new", "开启新会话（清空对话上下文）"),
        Command("resume", "恢复历史会话", argument_hint="[序号]"),
        Command("export", "导出当前会话为 Markdown 文件"),
        Command("vim", "开关 vim 编辑模式（默认开）", argument_hint="on|off"),
        Command("image", "在终端中预览图片（Kitty 协议，Ghostty 可用）", argument_hint="<路径>"),
        Command("vision", "带图提问（图片随问题发给模型）", argument_hint="<路径> <问题>"),
        Command("clear", "清空屏幕"),
        Command("quit", "退出"),
    ]

    def __init__(self) -> None:
        super().__init__()
        self.cfg = Config.load()
        self.runtime = Runtime(self.cfg, confirm=self._confirm_gate)
        self._last_answer = ""
        # input history, pi editor semantics: newest-first, no consecutive
        # duplicates, capped at 100; persisted to state dir
        self.input_history: list[str] = []
        self._hist_idx = -1
        self._hist_draft = ""
        self._history_file = self.cfg.state_path / "input_history.txt"
        self._load_history()

    def compose(self) -> ComposeResult:
        # pi-like minimal chrome: no header bar, straight into the transcript
        yield ChatLog(id="chat", markup=True, wrap=True)
        with Vertical(id="bottom"):
            yield OptionList(id="completion")
            yield Label("-- INSERT --", id="mode")
            yield CommandInput(placeholder="直接输入提问；/ 开头为命令；Esc 进入 NORMAL", id="input")
            yield Label("", id="status")

    def on_mount(self) -> None:
        self.title = "oneAI"
        loaded = self.runtime.load_extensions()
        self.chat().write(f"[dim]Vault: {self.cfg.vault_path} · 模型: {self.cfg.model}[/dim]")
        if loaded:
            self.chat().write(f"[dim]已加载扩展: {', '.join(loaded)}[/dim]")
        self.runtime.image_display_cb = self._display_image_agent
        self.query_one("#input", Input).focus()

    # --- input history (pi editor: addToHistory / navigateHistory) ----------

    def _load_history(self) -> None:
        if self._history_file.exists():
            lines = [l.strip() for l in self._history_file.read_text(encoding="utf-8").splitlines()]
            self.input_history = [l for l in reversed(lines) if l][-100:]

    def add_history(self, text: str) -> None:
        text = text.strip()
        if not text or (self.input_history and self.input_history[0] == text):
            return
        self.input_history.insert(0, text)
        if len(self.input_history) > 100:
            self.input_history.pop()
        try:
            self._history_file.parent.mkdir(parents=True, exist_ok=True)
            with self._history_file.open("a", encoding="utf-8") as f:
                f.write(text + "\n")
        except OSError:
            pass
        self._hist_idx = -1

    def history_move(self, direction: int) -> None:
        """direction=+1 older (up/k), -1 newer (down/j); index -1 = live draft."""
        box = self.query_one("#input", CommandInput)
        if not self.input_history:
            return
        new_idx = self._hist_idx + direction
        if new_idx < -1 or new_idx >= len(self.input_history):
            return
        if self._hist_idx == -1 and new_idx >= 0:
            self._hist_draft = box.value  # save live draft when entering history
        self._hist_idx = new_idx
        box.value = self._hist_draft if new_idx == -1 else self.input_history[new_idx]
        box.cursor_position = len(box.value)

    def chat(self) -> RichLog:
        return self.query_one("#chat", RichLog)

    def update_mode_indicator(self) -> None:
        box = self.query_one("#input", CommandInput)
        label = self.query_one("#mode", Label)
        if not box.vim_enabled:
            label.update("-- PLAIN --")
            label.set_class(False, "normal")
            return
        normal = box.vim_mode == "normal"
        label.update("-- NORMAL --" if normal else "-- INSERT --")
        label.set_class(normal, "normal")

    def _ui_vim(self, arg: str = "") -> None:
        box = self.query_one("#input", CommandInput)
        if arg == "on":
            box.vim_enabled = True
        elif arg == "off":
            box.vim_enabled = False
            box.set_vim_mode("insert")
        else:
            box.vim_enabled = not box.vim_enabled
        self.update_mode_indicator()
        state = "开启" if box.vim_enabled else "关闭"
        self.chat().write(f"[dim]vim 模式已{state}（Esc 切 NORMAL，i 回 INSERT）[/dim]")

    # --- command registry & vertical completion menu ----------------------

    def all_commands(self) -> list[Command]:
        return self.UI_COMMANDS + list(self.runtime.commands.values())

    def _comp_matches(self, text: str) -> list[Command]:
        if not text.startswith("/") or " " in text:
            return []
        matches = [c for c in self.all_commands() if f"/{c.name}".startswith(text)]
        # exact single match → already complete, no menu needed
        if len(matches) == 1 and text == f"/{matches[0].name}":
            return []
        return matches

    def completion_active(self) -> bool:
        ol = self.query_one("#completion", OptionList)
        return ol.has_class("visible") and ol.option_count > 0

    def move_completion(self, delta: int) -> None:
        """↑/↓ navigate the menu while focus stays in the input."""
        if not self.completion_active():
            return
        ol = self.query_one("#completion", OptionList)
        hl = ol.highlighted or 0
        ol.highlighted = (hl + delta) % ol.option_count

    def apply_completion(self) -> None:
        """Tab/Enter: fill the highlighted (or common-prefix) completion."""
        box = self.query_one("#input", Input)
        matches = self._comp_matches(box.value)
        if not matches:
            return
        ol = self.query_one("#completion", OptionList)
        picked = matches[ol.highlighted or 0] if self.completion_active() else None
        if picked is None and len(matches) == 1:
            picked = matches[0]
        if picked is not None:
            box.value = f"/{picked.name}" + (" " if picked.argument_hint else "")
        else:
            names = [f"/{c.name}" for c in matches]
            prefix = names[0]
            while not all(n.startswith(prefix) for n in names):
                prefix = prefix[:-1]
            box.value = prefix
        box.cursor_position = len(box.value)
        self._refresh_completion(box.value)

    def show_status(self, text: str, fade_after: float | None = None) -> None:
        """Transient hint line above the input (not written into the chat)."""
        label = self.query_one("#status", Label)
        label.update(text)
        label.set_class(bool(text), "visible")
        if fade_after:
            self.set_timer(fade_after, lambda: self.show_status(""))

    def hide_completion(self) -> None:
        self.query_one("#completion", OptionList).remove_class("visible")

    def _refresh_completion(self, text: str) -> None:
        ol = self.query_one("#completion", OptionList)
        matches = self._comp_matches(text)
        ol.clear_options()
        if not matches:
            ol.remove_class("visible")
            return
        for c in matches:
            hint = f" {c.argument_hint}" if c.argument_hint else ""
            ol.add_option(Option(f"[bold]/{c.name}[/bold]{hint}  [dim]{c.description}[/dim]"))
        ol.highlighted = 0
        ol.add_class("visible")

    def on_input_changed(self, event: Input.Changed) -> None:
        try:
            self._refresh_completion(event.value)
            if event.value:
                self.show_status("")  # typing clears transient hints
        except Exception:  # widget gone during shutdown
            return

    # --- dispatch --------------------------------------------------------------

    def on_input_submitted(self, event: Input.Submitted) -> None:
        box = self.query_one("#input", Input)
        text = box.value.strip()
        box.value = ""
        self.hide_completion()
        self.show_status("")
        if not text:
            return
        self.add_history(text)
        if not text.startswith("/"):
            self._start_chat(text)
            return

        name, _, arg = text[1:].partition(" ")
        arg = arg.strip()
        ui = next((c for c in self.UI_COMMANDS if c.name == name), None)
        if ui:
            ui.handler(arg)
        elif name in self.runtime.commands:
            self.runtime.commands[name].handler(arg)
        else:
            self.chat().write(f"[red]未知命令: /{name}[/red] — /help 查看全部")

    def _ui_help(self, _arg: str = "") -> None:
        self.chat().write("[bold]命令一览：[/bold]")
        for c in self.all_commands():
            hint = f" [dim]{c.argument_hint}[/dim]" if c.argument_hint else ""
            self.chat().write(f"  [bold cyan]/{c.name}[/bold cyan]{hint}  {c.description}")
        self.chat().write("[dim]不带 / 的输入直接进入对话（回答附 [[path#Lx-Ly]] 引用）[/dim]")
        self.chat().write("[dim]复制：Shift+拖拽框选，或 /copy 复制最近回答[/dim]")

    def _ui_copy(self, _arg: str = "") -> None:
        if not self._last_answer:
            self.chat().write("[dim]还没有可复制的回答[/dim]")
            return
        try:
            subprocess.run(["pbcopy"], input=self._last_answer.encode(), check=True)
            self.chat().write(f"[dim]✔ 已复制（{len(self._last_answer)} 字符）[/dim]")
        except Exception as e:
            self.chat().write(f"[red]复制失败: {e}[/red]")

    # --- chat & drafting (worker threads; LLM doesn't block UI) ---------------

    @work(thread=True)
    def _start_chat(self, text: str, images: list[Path] | None = None) -> None:
        import time

        shown = text + ("".join(f" 📎{p.name}" for p in images) if images else "")
        self.call_from_thread(self.chat().write, f"\n[bold cyan]❯ {shown}[/bold cyan]")
        self._agent_running = True
        self.call_from_thread(self.show_status, "⏳ 思考中…")
        start = time.monotonic()
        try:
            answer = self.runtime.run_agent(text, on_event=self._on_agent_event, images=images)
        except Exception as e:
            answer = f"**错误**: {e}"
        finally:
            self._agent_running = False
        self._last_answer = answer
        elapsed = time.monotonic() - start
        tokens = (self.runtime.last_usage or {}).get("total_tokens")
        stat = f"✔ {elapsed:.0f}s" + (f" · {tokens} tokens" if tokens else "")
        self.call_from_thread(self.chat().write, "[dim]助手[/dim]")
        self.call_from_thread(self.chat().write, Markdown(answer))
        self.call_from_thread(self.show_status, stat, 6.0)
        try:
            self.runtime.save_session()
        except OSError:
            pass

    def _on_agent_event(self, kind: str, data: dict) -> None:
        if kind == "tool_start":
            args = ", ".join(f"{k}={str(v)[:40]}" for k, v in data["args"].items())
            self.call_from_thread(self.chat().write, f"[dim]  🔧 {data['name']}({args})[/dim]")
            self.call_from_thread(self.show_status, f"🔧 {data['name']}…")
        elif kind == "tool_denied":
            self.call_from_thread(self.chat().write, f"  [yellow]⛔ {data['name']} 被拒绝[/yellow]")
        elif kind == "text_delta":
            self.call_from_thread(self.show_status, f"⏳ 回答中… {data['chars']} 字")

    def abort_agent(self) -> None:
        """NORMAL-mode Esc while the agent runs (pi: interrupt)."""
        if getattr(self, "_agent_running", False):
            self.runtime.abort()
            self.show_status("⛔ 已中断", 3.0)

    def _confirm_gate(self, title: str, message: str) -> bool:
        """Called from the worker thread when a confirm-gated tool fires."""
        fut: concurrent.futures.Future[bool] = concurrent.futures.Future()

        def _open() -> None:
            self.push_screen(ConfirmScreen(title, message), lambda r: fut.set_result(bool(r)))

        self.call_from_thread(_open)
        return fut.result()

    # --- remaining UI commands -------------------------------------------------

    def _ui_inbox(self, _arg: str = "") -> None:
        inbox = self.cfg.vault_path / "inbox"
        files = sorted(inbox.rglob("*.md")) if inbox.exists() else []
        self.chat().write("[bold yellow]Inbox:[/bold yellow]")
        for f in files:
            self.chat().write(f"  {f.relative_to(self.cfg.vault_path)}")
        if not files:
            self.chat().write("[dim]（空）[/dim]")

    def _ui_reindex(self, _arg: str = "") -> None:
        from .indexer import Index

        index = Index(self.cfg.index_db)
        n = index.rebuild(self.cfg.vault_path)
        index.close()
        self.chat().write(f"[green]✔ 索引已重建（{n} 个 chunk）[/green]")

    def _ui_reload(self, _arg: str = "") -> None:
        loaded = self.runtime.load_extensions()
        self.chat().write(f"[green]✔ 已加载 {len(loaded)} 个扩展[/green]" if loaded
                          else "[dim]未发现扩展（~/.oneai/extensions/）[/dim]")

    def _ui_image(self, arg: str = "") -> None:
        """Display an image inline via the Kitty graphics protocol (Ghostty)."""
        from . import images as img

        path = Path(arg).expanduser()
        if not arg or not path.exists():
            self.chat().write("[yellow]用法: /image <图片路径>[/yellow]")
            return
        with self.suspend():  # leave alt-screen, draw into normal screen
            fallback = img.display(path)
            if fallback:
                subprocess.run(["open", str(path)])
                print(fallback + "（终端不支持内联显示，已用系统预览打开）")
            try:
                input("（回车返回 oneAI）")
            except EOFError:
                pass

    def _display_image_agent(self, path: Path) -> None:
        """Called by the runtime (worker thread) when the agent shows an image."""
        from . import images as img

        def _show() -> None:
            with self.suspend():
                img.display(path)
                try:
                    input(f"（agent 显示了 {path.name}，回车返回）")
                except EOFError:
                    pass

        self.call_from_thread(_show)

    def _ui_vision(self, arg: str = "") -> None:
        path_str, _, question = arg.partition(" ")
        path = Path(path_str).expanduser()
        if not question or not path.exists():
            self.chat().write("[yellow]用法: /vision <图片路径> <问题>[/yellow]")
            return
        self._start_chat(question, images=[path])

    def _ui_new(self, _arg: str = "") -> None:
        self.runtime.reset_session()
        self.chat().write("[dim]— 新会话 —[/dim]")

    def _ui_export(self, _arg: str = "") -> None:
        """Export the conversation to a Markdown file (pi: /export)."""
        from datetime import datetime

        if not self.runtime.messages:
            self.chat().write("[dim]当前会话为空[/dim]")
            return
        out_dir = self.cfg.state_path / "exports"
        out_dir.mkdir(parents=True, exist_ok=True)
        path = out_dir / f"{datetime.now().strftime('%Y%m%d-%H%M%S')}.md"
        parts = [f"# oneAI 会话导出\n\n{datetime.now().isoformat(timespec='seconds')}\n"]
        for m in self.runtime.messages:
            role = m.get("role")
            content = m.get("content")
            if isinstance(content, list):  # image attachments
                content = " ".join("[图片]" if c.get("type") == "image_url" else c.get("text", "")
                                   for c in content)
            if role == "user":
                parts.append(f"\n## ❯ 用户\n\n{content}\n")
            elif role == "assistant":
                parts.append(f"\n## 助手\n\n{content or ''}\n")
            elif role == "tool":
                parts.append(f"\n<details><summary>tool result</summary>\n\n```\n{str(content)[:500]}\n```\n</details>\n")
        path.write_text("".join(parts), encoding="utf-8")
        self.chat().write(f"[green]✔ 已导出: {path}[/green]")

    def _ui_resume(self, arg: str = "") -> None:
        sessions = self.runtime.list_sessions()
        if not sessions:
            self.chat().write("[dim]没有历史会话[/dim]")
            return
        if not arg.strip():
            self.chat().write("[bold]历史会话：[/bold]")
            for i, p in enumerate(sessions[:10], 1):
                lines = sum(1 for _ in p.open())
                self.chat().write(f"  {i}. {p.name}  ({lines} 条消息)")
            self.chat().write("[dim]/resume <序号> 恢复[/dim]")
            return
        try:
            path = sessions[int(arg) - 1]
        except (ValueError, IndexError):
            self.chat().write(f"[red]无效序号: {arg}[/red]")
            return
        n = self.runtime.load_session(path)
        self.chat().write(f"[green]✔ 已恢复会话 {path.name}（{n} 条消息）[/green]")


def run() -> None:
    app = OneAIApp()
    # Wire UI command handlers (kept here to keep the registry declarative).
    handlers = {
        "help": app._ui_help, "copy": app._ui_copy, "inbox": app._ui_inbox,
        "reindex": app._ui_reindex, "reload": app._ui_reload,
        "new": app._ui_new,
        "resume": app._ui_resume,
        "export": app._ui_export,
        "vim": app._ui_vim,
        "image": app._ui_image,
        "vision": app._ui_vision,
        "clear": lambda a="": app.chat().clear(),
        "quit": lambda a="": app.exit(),
        "draft": lambda a: app._start_chat(f"请起草手稿：{a}") if a
                 else app.chat().write("[yellow]用法: /draft <起草指令>[/yellow]"),
    }
    for c in app.UI_COMMANDS:
        if c.name in handlers:
            c.handler = handlers[c.name]
    app.run()  # mouse on: wheel/trackpad scrolls the transcript;
    # copy via Shift+drag (bypasses mouse capture) or /copy


if __name__ == "__main__":
    run()
