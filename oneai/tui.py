"""Terminal UI for oneAI — slash-command driven (modeled after pi's design).

Like pi (dist/core/slash-commands.js): a flat command registry of
{name, description, argumentHint}, dispatched on submit. Text without a
leading '/' goes straight to the assistant as a question.

Commands:
  /search <query>   本地全文检索（不调用 LLM）
  /draft <指令>     生成手稿到 inbox/drafts/ —— 唯一的起草入口（绝不自动）
  /inbox            列出待处理捕获与手稿
  /reindex          重建检索索引（改过笔记后用）
  /copy             复制最近一条回答到剪贴板
  /help             显示帮助
  /quit             退出（或 Ctrl+Q）
"""
from __future__ import annotations

import subprocess
from dataclasses import dataclass

from textual import work
from textual.app import App, ComposeResult
from textual.widgets import Footer, Header, Input, RichLog

from .agent import ask, draft_manuscript
from .config import Config
from .indexer import Index


@dataclass
class Command:
    name: str
    description: str
    argument_hint: str = ""


# Flat registry, pi-style. Adding a command = one entry + one handler method.
COMMANDS = [
    Command("search", "本地全文检索（不调用 LLM）", "<关键词>"),
    Command("draft", "生成手稿到 inbox/drafts/（仅此入口，绝不自动）", "<起草指令>"),
    Command("inbox", "列出待处理捕获与手稿"),
    Command("reindex", "重建检索索引"),
    Command("copy", "复制最近一条回答到剪贴板"),
    Command("help", "显示本帮助"),
    Command("quit", "退出"),
]


class OneAIApp(App):
    CSS = """
    #chat { height: 1fr; border: solid $primary; }
    #input { height: auto; }
    """

    BINDINGS = [("ctrl+q", "quit", "退出")]

    def __init__(self) -> None:
        super().__init__()
        self.cfg = Config.load()
        self._last_answer: str = ""

    def compose(self) -> ComposeResult:
        yield Header()
        yield RichLog(id="chat", markup=True, wrap=True)
        yield Input(placeholder="直接输入提问；/ 开头为命令，/help 查看全部", id="input")
        yield Footer()

    def on_mount(self) -> None:
        self.title = "oneAI"
        self.chat().write(f"[dim]Vault: {self.cfg.vault_path}[/dim]")
        self.chat().write(f"[dim]模型: {self.cfg.model}[/dim]")
        self.chat().write("[dim]选中复制：按住 Option 拖拽；或用 /copy 复制最近回答[/dim]")
        self.query_one("#input", Input).focus()

    def chat(self) -> RichLog:
        return self.query_one("#chat", RichLog)

    # --- dispatch (pi-style: "/cmd ..." → handler, else → chat) -----------

    def on_input_submitted(self, event: Input.Submitted) -> None:
        text = event.value.strip()
        self.query_one("#input", Input).value = ""
        if not text:
            return
        if not text.startswith("/"):
            self._start_chat(text)
            return

        name, _, arg = text[1:].partition(" ")
        arg = arg.strip()
        handler = getattr(self, f"cmd_{name}", None)
        if handler is None:
            self.chat().write(f"[red]未知命令: /{name}[/red] — 输入 /help 查看全部命令")
        elif arg or not self._hint(name):
            handler(arg) if arg else handler()
        else:
            self.chat().write(f"[yellow]/{name} 需要参数: {self._hint(name)}[/yellow]")

    def _hint(self, name: str) -> str:
        return next((c.argument_hint for c in COMMANDS if c.name == name), "")

    # --- commands ----------------------------------------------------------

    def cmd_help(self) -> None:
        self.chat().write("[bold]命令一览：[/bold]")
        for c in COMMANDS:
            hint = f" [dim]{c.argument_hint}[/dim]" if c.argument_hint else ""
            self.chat().write(f"  [bold cyan]/{c.name}[/bold cyan]{hint}  {c.description}")
        self.chat().write("[dim]不带 / 的输入直接作为问题提问（回答附 [[path#Lx-Ly]] 引用）[/dim]")

    def cmd_quit(self) -> None:
        self.exit()

    def cmd_copy(self) -> None:
        if not self._last_answer:
            self.chat().write("[dim]还没有可复制的回答[/dim]")
            return
        try:
            subprocess.run(["pbcopy"], input=self._last_answer.encode(), check=True)
            self.chat().write(f"[dim]✔ 已复制最近回答（{len(self._last_answer)} 字符）[/dim]")
        except Exception as e:
            self.chat().write(f"[red]复制失败: {e}[/red]")

    def cmd_search(self, query: str) -> None:
        index = Index(self.cfg.index_db)
        results = index.search(query, k=5)
        index.close()
        self.chat().write(f"\n[bold yellow]搜索:[/bold yellow] {query}")
        if not results:
            self.chat().write("[dim]无结果[/dim]")
        for r in results:
            self.chat().write(f"  [bold]{r.citation}[/bold] [{r.heading}]\n  [dim]{r.snippet}[/dim]")

    def cmd_reindex(self) -> None:
        index = Index(self.cfg.index_db)
        n = index.rebuild(self.cfg.vault_path)
        index.close()
        self.chat().write(f"[green]✔ 索引已重建（{n} 个 chunk）[/green]")

    def cmd_inbox(self) -> None:
        inbox = self.cfg.vault_path / "inbox"
        files = sorted(inbox.rglob("*.md")) if inbox.exists() else []
        self.chat().write("\n[bold yellow]Inbox:[/bold yellow]")
        if not files:
            self.chat().write("[dim]（空）[/dim]")
        for f in files:
            self.chat().write(f"  {f.relative_to(self.cfg.vault_path)}")

    def cmd_draft(self, instruction: str) -> None:
        self._start_draft(instruction)

    # --- async workers (LLM calls don't block the UI) -----------------------

    @work(thread=True)
    def _start_chat(self, text: str) -> None:
        self.chat().write(f"\n[bold cyan]你:[/bold cyan] {text}")
        try:
            answer = ask(self.cfg, text)
        except Exception as e:  # surface API errors in the chat
            answer = f"[red]错误: {e}[/red]"
        self._last_answer = answer
        self.chat().write(f"[bold green]助手:[/bold green] {answer}")

    @work(thread=True)
    def _start_draft(self, instruction: str) -> None:
        self.chat().write(f"\n[bold magenta]起草中:[/bold magenta] {instruction}")
        try:
            path = draft_manuscript(self.cfg, instruction)
            rel = path.relative_to(self.cfg.vault_path)
            self.chat().write(
                f"[green]✔ 手稿已保存:[/green] {rel}\n"
                f"[dim]编辑该文件并将 frontmatter 的 status 改为 approved 以确认。[/dim]"
            )
        except Exception as e:
            self.chat().write(f"[red]起草失败: {e}[/red]")


def run() -> None:
    OneAIApp().run()


if __name__ == "__main__":
    run()
