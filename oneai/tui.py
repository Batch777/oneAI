"""oneAI TUI v2 — standalone product UI on top of the extensible runtime.

pi-inspired:
- Slash commands from a flat registry (UI commands + extension commands)
- Live autocomplete hint + Tab completion when typing "/"
- Markdown-rendered answers, tool-call status lines, permission-gate modal
- /copy copies the last answer; Option+drag for native terminal selection
"""
from __future__ import annotations

import concurrent.futures
import subprocess
import sys
from pathlib import Path

from rich.markdown import Markdown
from textual import work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.screen import ModalScreen
from textual.widgets import Footer, Header, Input, Label, OptionList, RichLog
from textual.widgets.option_list import Option

from .config import Config
from .runtime import Command, Runtime


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
    BINDINGS = [
        Binding("tab", "complete", "补全", show=False),
        Binding("up", "comp_up", show=False),
        Binding("down", "comp_down", show=False),
        Binding("enter", "submit_or_complete", show=False),
        Binding("escape", "comp_dismiss", show=False),
    ]

    def action_complete(self) -> None:
        app: OneAIApp = self.app  # type: ignore[assignment]
        app.apply_completion()

    def action_comp_up(self) -> None:
        app: OneAIApp = self.app  # type: ignore[assignment]
        app.move_completion(-1)

    def action_comp_down(self) -> None:
        app: OneAIApp = self.app  # type: ignore[assignment]
        app.move_completion(1)

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
    #chat { height: 1fr; border: solid $primary; }
    #completion { height: auto; max-height: 9; display: none; border: solid $secondary; }
    #completion.visible { display: block; }
    #input { height: auto; }
    ConfirmScreen { align: center middle; }
    ConfirmScreen Label { width: 60; padding: 1 2; background: $surface; }
    """

    BINDINGS = [("ctrl+q", "quit", "退出")]

    UI_COMMANDS = [
        Command("help", "显示帮助"),
        Command("draft", "起草手稿（对话式，带确认门）", argument_hint="<起草指令>"),
        Command("inbox", "列出 inbox 捕获与手稿"),
        Command("reindex", "重建检索索引"),
        Command("reload", "重新加载扩展（~/.oneai/extensions/）"),
        Command("copy", "复制最近一条回答到剪贴板"),
        Command("new", "开启新会话（清空对话上下文）"),
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

    def compose(self) -> ComposeResult:
        yield Header()
        yield RichLog(id="chat", markup=True, wrap=True)
        yield OptionList(id="completion")
        yield CommandInput(placeholder="直接输入提问；/ 开头为命令（↑↓ 选择，Tab 补全）", id="input")
        yield Footer()

    def on_mount(self) -> None:
        self.title = "oneAI"
        loaded = self.runtime.load_extensions()
        self.chat().write(f"[dim]Vault: {self.cfg.vault_path} · 模型: {self.cfg.model}[/dim]")
        if loaded:
            self.chat().write(f"[dim]已加载扩展: {', '.join(loaded)}[/dim]")
        self.chat().write("[dim]复制：鼠标直接框选 + Cmd+C；/copy 复制最近回答[/dim]")
        self.query_one("#input", Input).focus()

    def chat(self) -> RichLog:
        return self.query_one("#chat", RichLog)

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
        except Exception:  # widget gone during shutdown
            return

    # --- dispatch --------------------------------------------------------------

    def on_input_submitted(self, event: Input.Submitted) -> None:
        box = self.query_one("#input", Input)
        text = box.value.strip()
        box.value = ""
        self.hide_completion()
        if not text:
            return
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
        shown = text + ("".join(f" 📎{p.name}" for p in images) if images else "")
        self.call_from_thread(self.chat().write, f"\n[bold cyan]你:[/bold cyan] {shown}")
        try:
            answer = self.runtime.run_agent(text, on_event=self._on_agent_event, images=images)
        except Exception as e:
            answer = f"**错误**: {e}"
        self._last_answer = answer
        self.call_from_thread(self.chat().write, Markdown(f"**助手:** {answer}"))

    def _on_agent_event(self, kind: str, data: dict) -> None:
        if kind == "tool_start":
            args = ", ".join(f"{k}={str(v)[:40]}" for k, v in data["args"].items())
            self.call_from_thread(self.chat().write, f"[dim]🔧 {data['name']}({args})[/dim]")
        elif kind == "tool_denied":
            self.call_from_thread(self.chat().write, f"[yellow]⛔ {data['name']} 被拒绝[/yellow]")

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
        if not img.supports_kitty():
            subprocess.run(["open", str(path)])
            self.chat().write(f"[dim]当前终端不支持 Kitty 图形协议，已用系统预览打开 {path.name}[/dim]")
            return
        with self.suspend():  # leave alt-screen, draw into normal screen
            img.display(path)
            try:
                input("（图片已显示，回车返回 oneAI）")
            except EOFError:
                pass

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


def run() -> None:
    app = OneAIApp()
    # Wire UI command handlers (kept here to keep the registry declarative).
    handlers = {
        "help": app._ui_help, "copy": app._ui_copy, "inbox": app._ui_inbox,
        "reindex": app._ui_reindex, "reload": app._ui_reload,
        "new": app._ui_new,
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
    app.run(mouse=False)  # mouse off → Ghostty native drag-select & copy works


if __name__ == "__main__":
    run()
