"""Terminal UI for oneAI.

Layout: chat log on top, input + action buttons at the bottom.
- 发送 (Send): ask a question, answered with [[path#Lx-Ly]] citations
- 搜索 (Search): raw vault search without LLM
- AI Draft: generate a manuscript into inbox/drafts/ — the ONLY way drafts
  are created (per the 2026-09-20 decision: never automatic)
- Inbox: list captured notes / drafts awaiting review
- Ctrl+Q: quit
"""
from __future__ import annotations

from textual import work
from textual.app import App, ComposeResult
from textual.containers import Horizontal
from textual.widgets import Button, Footer, Header, Input, RichLog

from .agent import ask, draft_manuscript
from .config import Config
from .indexer import Index


class OneAIApp(App):
    CSS = """
    #chat { height: 1fr; border: solid $primary; }
    #controls { height: auto; padding: 1 0; }
    #controls Input { width: 1fr; }
    #controls Button { margin-left: 1; }
    """

    BINDINGS = [("ctrl+q", "quit", "退出")]

    def __init__(self) -> None:
        super().__init__()
        self.cfg = Config.load()

    def compose(self) -> ComposeResult:
        yield Header()
        yield RichLog(id="chat", markup=True, wrap=True)
        with Horizontal(id="controls"):
            yield Input(placeholder="输入问题或起草指令…", id="input")
            yield Button("发送", id="send", variant="primary")
            yield Button("搜索", id="search")
            yield Button("AI Draft", id="draft", variant="warning")
            yield Button("Inbox", id="inbox")
        yield Footer()

    def on_mount(self) -> None:
        self.title = "oneAI"
        self.chat().write(f"[dim]Vault: {self.cfg.vault_path}[/dim]")
        self.chat().write("[dim]模型: " + self.cfg.model + " — 提问后点「发送」，起草点「AI Draft」[/dim]")
        self.query_one("#input", Input).focus()

    def chat(self) -> RichLog:
        return self.query_one("#chat", RichLog)

    def _input_text(self) -> str:
        box = self.query_one("#input", Input)
        text = box.value.strip()
        box.value = ""
        return text

    # --- actions ---------------------------------------------------------

    def on_input_submitted(self, event: Input.Submitted) -> None:
        self._start_chat(event.value.strip())
        self.query_one("#input", Input).value = ""

    def on_button_pressed(self, event: Button.Pressed) -> None:
        bid = event.button.id
        if bid == "send":
            self._start_chat(self._input_text())
        elif bid == "search":
            self._do_search(self._input_text())
        elif bid == "draft":
            self._start_draft(self._input_text())
        elif bid == "inbox":
            self._show_inbox()

    @work(thread=True)
    def _start_chat(self, text: str) -> None:
        if not text:
            return
        self.chat().write(f"\n[bold cyan]你:[/bold cyan] {text}")
        try:
            answer = ask(self.cfg, text)
        except Exception as e:  # surface API errors in the chat
            answer = f"[red]错误: {e}[/red]"
        self.chat().write(f"[bold green]助手:[/bold green] {answer}")

    def _do_search(self, text: str) -> None:
        if not text:
            return
        index = Index(self.cfg.index_db)
        results = index.search(text, k=5)
        index.close()
        self.chat().write(f"\n[bold yellow]搜索:[/bold yellow] {text}")
        if not results:
            self.chat().write("[dim]无结果[/dim]")
        for r in results:
            self.chat().write(f"  [bold]{r.citation}[/bold] [{r.heading}]\n  [dim]{r.snippet}[/dim]")

    @work(thread=True)
    def _start_draft(self, instruction: str) -> None:
        if not instruction:
            self.chat().write("[dim]先在输入框写下起草指令，再点 AI Draft[/dim]")
            return
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

    def _show_inbox(self) -> None:
        inbox = self.cfg.vault_path / "inbox"
        files = sorted(inbox.rglob("*.md")) if inbox.exists() else []
        self.chat().write("\n[bold yellow]Inbox:[/bold yellow]")
        if not files:
            self.chat().write("[dim]（空）[/dim]")
        for f in files:
            self.chat().write(f"  {f.relative_to(self.cfg.vault_path)}")


def run() -> None:
    OneAIApp().run()


if __name__ == "__main__":
    run()
