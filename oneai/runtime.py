"""Extensible agent runtime — pi-style architecture in Python.

Extension model borrowed from pi (docs/extensions.md):
- Flat registries: tools, commands (extension = one Python file with setup())
- Lifecycle hooks: before_agent_start, tool_call (can block), tool_result, agent_end
- Auto-discovery: ~/.oneai/extensions/*.py and .oneai/extensions/*.py (project)
- Permission gates: tools marked confirm=True require user approval

An extension looks like:

    # ~/.oneai/extensions/my_ext.py
    def setup(rt):
        rt.register_command(Command("hello", "打招呼", lambda arg: print("hi")))
        rt.on("tool_call", lambda name, arguments: {"block": True, "reason": "no"}
              if name == "draft_save" else None)
"""
from __future__ import annotations

import importlib.util
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from .agent import _identity_context
from .config import Config
from .events import EventLog
from .indexer import Index
from .llm import LLM
from .vault import new_id, now_iso, write_note

ConfirmFn = Callable[[str, str], bool]  # (title, message) -> approved


@dataclass
class Tool:
    name: str
    description: str
    parameters: dict  # JSON Schema for arguments
    func: Callable[..., str]
    confirm: bool = False  # permission gate: user must approve each call


@dataclass
class Command:
    name: str
    description: str
    handler: Callable[[str], None] = lambda _: None
    argument_hint: str = ""


SYSTEM = """你是 oneAI，用户的个人助理。
- 默认用中文回复。
- 涉及用户个人信息（背景、经历、偏好、联系人等），先用 vault_search 检索；
  一次搜不到就换关键词再搜，不要凭印象回答。
- 来自 vault 的每条信息必须标注引用 [[path#Lx-Ly]]。
- 需要看完整笔记时用 vault_read。
- 起草手稿（邮件、自我介绍等）时：先用 vault_search 收集事实，撰写正文，
  然后调用 draft_save 保存（会向用户弹窗确认）。绝不发送任何内容。
- 用户想查看图片时：先用 image_find 或 vault_search 找到图片路径，
  再用 image_show 显示。"""


class Runtime:
    def __init__(self, cfg: Config, confirm: ConfirmFn | None = None):
        self.cfg = cfg
        self.confirm_cb = confirm
        self.image_display_cb: Callable[[Path], None] | None = None  # set by the TUI
        self.tools: dict[str, Tool] = {}
        self.commands: dict[str, Command] = {}
        self.hooks: dict[str, list[Callable]] = {}
        self.messages: list[dict] = []  # conversation history (multi-turn)
        self.llm = LLM(cfg) if cfg.deepseek_api_key else None
        self.log = EventLog(cfg.events_log)
        self._abort = False
        self.last_usage: dict = {}
        self.session_path: Path | None = None
        self._register_builtin_tools()

    def abort(self) -> None:
        """Esc-interrupt (pi behavior): the in-flight LLM call is dropped."""
        self._abort = True

    # --- extension API (registries + hooks) --------------------------------

    def register_tool(self, tool: Tool) -> None:
        self.tools[tool.name] = tool

    def register_command(self, command: Command) -> None:
        self.commands[command.name] = command

    def on(self, event: str, fn: Callable) -> None:
        self.hooks.setdefault(event, []).append(fn)

    def emit(self, event: str, **kw: Any) -> dict | None:
        """Run hooks; first dict return value wins (e.g. {"block": True})."""
        for fn in self.hooks.get(event, []):
            r = fn(**kw)
            if isinstance(r, dict):
                return r
        return None

    def emit_collect(self, event: str, **kw: Any) -> list[str]:
        """Collect string fragments from hooks (e.g. system-prompt additions)."""
        out = []
        for fn in self.hooks.get(event, []):
            r = fn(**kw)
            if isinstance(r, str) and r:
                out.append(r)
        return out

    def load_extensions(self) -> list[str]:
        """Auto-discover extensions; returns loaded paths (pi: /reload analog)."""
        loaded = []
        for d in (Path.home() / ".oneai" / "extensions", Path.cwd() / ".oneai" / "extensions"):
            if not d.is_dir():
                continue
            for f in sorted(d.glob("*.py")):
                spec = importlib.util.spec_from_file_location(f"oneai_ext_{f.stem}", f)
                mod = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(mod)  # type: ignore[union-attr]
                if hasattr(mod, "setup"):
                    mod.setup(self)
                loaded.append(str(f))
                self.log.emit("extension.loaded", path=str(f))
        return loaded

    # --- built-in tools ------------------------------------------------------

    def _register_builtin_tools(self) -> None:
        def vault_search(query: str, k: int = 8) -> str:
            index = Index(self.cfg.index_db)
            results = index.search(query, k=k)
            index.close()
            if not results:
                return "（无结果，换关键词重试）"
            return "\n\n".join(f"--- {r.citation} [{r.heading}] ---\n{r.text[:1500]}" for r in results)

        def vault_read(path: str, lines: str = "") -> str:
            raw = (self.cfg.vault_path / path).read_text(encoding="utf-8")
            if lines:
                a, _, b = lines.partition("-")
                raw = "\n".join(raw.splitlines()[int(a) - 1 : int(b)])
            return raw

        def draft_save(title: str, body: str) -> str:
            from datetime import datetime

            ts = datetime.now().strftime("%Y%m%d-%H%M%S")
            rel = f"inbox/drafts/{ts}-{new_id()}.md"
            write_note(
                self.cfg.vault_path, rel,
                {"id": new_id(), "title": title, "status": "drafted", "created": now_iso()},
                body + "\n",
            )
            self.log.emit("draft.created", path=rel, title=title, via="runtime")
            return f"手稿已保存: {rel}（status: drafted，待用户编辑并改为 approved）"

        self.register_tool(Tool(
            name="vault_search",
            description="检索用户的个人知识库（Markdown 笔记）。返回带 path#L行号 引用的笔记块。",
            parameters={"type": "object", "properties": {
                "query": {"type": "string", "description": "检索关键词（支持中文）"},
                "k": {"type": "integer", "description": "最多返回条数，默认 8"}},
                "required": ["query"]},
            func=vault_search,
        ))
        self.register_tool(Tool(
            name="vault_read",
            description="读取 vault 中某篇笔记的完整内容，可选行号范围（如 '8-16'，与引用行号一致）。",
            parameters={"type": "object", "properties": {
                "path": {"type": "string", "description": "vault 相对路径"},
                "lines": {"type": "string", "description": "行号范围，可选"}},
                "required": ["path"]},
            func=vault_read,
        ))
        self.register_tool(Tool(
            name="draft_save",
            description="把手稿保存到 inbox/drafts/（status: drafted，待用户审批）。只在用户明确要求起草时使用。",
            parameters={"type": "object", "properties": {
                "title": {"type": "string"}, "body": {"type": "string", "description": "手稿正文"}},
                "required": ["title", "body"]},
            func=draft_save,
            confirm=True,  # permission gate
        ))

        def image_find(query: str = "") -> str:
            from .images import find_image_references

            refs = find_image_references(self.cfg.vault_path)
            if query:
                refs = [r for r in refs if query.lower() in (r["ref"] + r["note"]).lower()]
            if not refs:
                return "（vault 中未找到图片引用）"
            return json.dumps(refs, ensure_ascii=False, indent=1)

        def image_show(path: str) -> str:
            p = Path(path).expanduser()
            if not p.is_absolute():
                p = self.cfg.vault_path / path
            if not p.exists():
                return f"图片不存在: {p}"
            if self.image_display_cb:
                self.image_display_cb(p)
                return f"已在终端显示图片 {p.name}"
            return f"图片位于 {p}（当前界面不支持显示）"

        self.register_tool(Tool(
            name="image_find",
            description="在 vault 笔记中查找图片引用（Markdown 嵌入或图片文件路径）。返回 JSON 列表。",
            parameters={"type": "object", "properties": {
                "query": {"type": "string", "description": "过滤关键词，可选"}}},
            func=image_find,
        ))
        self.register_tool(Tool(
            name="image_show",
            description="在终端中显示一张图片（Kitty 图形协议）。参数为绝对路径或 vault 相对路径。",
            parameters={"type": "object", "properties": {
                "path": {"type": "string", "description": "图片路径"}},
                "required": ["path"]},
            func=image_show,
        ))

    # --- agent loop ------------------------------------------------------------

    def _call_llm(self, messages: list[dict], tool_schemas: list[dict],
                  on_text_delta: Callable[[int], None] | None):
        """Streaming completion; returns (content, tool_calls, usage).
        Abort-aware: a set abort flag breaks the stream immediately."""
        stream = self.llm.client.chat.completions.create(
            model=self.llm.model,
            messages=messages,
            tools=tool_schemas,
            stream=True,
            stream_options={"include_usage": True},
        )
        content_parts: list[str] = []
        tool_calls: dict[int, dict] = {}
        usage: dict = {}
        for chunk in stream:
            if self._abort:
                stream.close()
                break
            if getattr(chunk, "usage", None):
                usage = chunk.usage.model_dump()
            if not chunk.choices:
                continue
            delta = chunk.choices[0].delta
            if delta.content:
                content_parts.append(delta.content)
                if on_text_delta:
                    on_text_delta(sum(len(p) for p in content_parts))
            for tc in delta.tool_calls or []:
                slot = tool_calls.setdefault(tc.index, {"id": "", "name": "", "arguments": ""})
                if tc.id:
                    slot["id"] = tc.id
                if tc.function and tc.function.name:
                    slot["name"] += tc.function.name
                if tc.function and tc.function.arguments:
                    slot["arguments"] += tc.function.arguments
        return "".join(content_parts), list(tool_calls.values()), usage

    def _system_prompt(self) -> str:
        extra = self.emit_collect("before_agent_start")
        parts = [SYSTEM + _identity_context(self.cfg)]
        parts.extend(extra)
        return "\n\n".join(parts)

    def run_agent(self, user_text: str, on_event: Callable[[str, dict], None] | None = None,
                  max_iters: int = 8, images: list[Path] | None = None) -> str:
        """Multi-turn capable tool loop. on_event(kind, data) for UI updates."""
        if not self.llm:
            return "错误：未设置 DEEPSEEK_API_KEY"
        if images:
            from .images import as_data_url

            content: list[dict] = [{"type": "text", "text": user_text}]
            content += [{"type": "image_url", "image_url": {"url": as_data_url(p)}}
                        for p in images]
            self.messages.append({"role": "user", "content": content})
        else:
            self.messages.append({"role": "user", "content": user_text})
        tool_schemas = [
            {"type": "function", "function": {
                "name": t.name, "description": t.description, "parameters": t.parameters}}
            for t in self.tools.values()
        ]

        for _ in range(max_iters):
            if self._abort:
                return "（已中断）"
            content, tool_calls, usage = self._call_llm(
                [{"role": "system", "content": self._system_prompt()}] + self.messages,
                tool_schemas,
                lambda n: on_event and on_event("text_delta", {"chars": n}),
            )
            self.last_usage = usage
            if self._abort:
                return "（已中断）"

            if not tool_calls:
                self.messages.append({"role": "assistant", "content": content})
                self.emit("agent_end")
                return content

            assistant: dict[str, Any] = {
                "role": "assistant",
                "content": content,
                "tool_calls": [
                    {"id": tc["id"], "type": "function",
                     "function": {"name": tc["name"], "arguments": tc["arguments"]}}
                    for tc in tool_calls
                ],
            }
            self.messages.append(assistant)

            for tc in tool_calls:
                if self._abort:
                    return "（已中断）"
                name = tc["name"]
                try:
                    args = json.loads(tc["arguments"] or "{}")
                except json.JSONDecodeError:
                    args = {}
                on_event and on_event("tool_start", {"name": name, "args": args})

                blocked = self.emit("tool_call", name=name, arguments=args)
                tool = self.tools.get(name)
                if blocked:
                    result = f"已被拦截: {blocked.get('reason', '')}"
                elif tool is None:
                    result = f"未知工具: {name}"
                elif tool.confirm and self.confirm_cb and not self.confirm_cb(
                    f"确认执行 {name}", json.dumps(args, ensure_ascii=False)[:400]
                ):
                    result = "用户拒绝了本次调用"
                    on_event and on_event("tool_denied", {"name": name})
                else:
                    try:
                        result = tool.func(**args)
                    except Exception as e:
                        result = f"工具执行出错: {e}"
                self.emit("tool_result", name=name, result=result)
                on_event and on_event("tool_end", {"name": name, "result": result[:200]})
                self.messages.append({"role": "tool", "tool_call_id": tc["id"], "content": result})

        return "（达到工具调用上限，请缩小问题范围）"

    # --- sessions (pi: session persistence) ------------------------------------

    def save_session(self) -> Path:
        """Persist the conversation to state/sessions/<id>.jsonl."""
        if self.session_path is None:
            from datetime import datetime

            ts = datetime.now().strftime("%Y%m%d-%H%M%S")
            self.session_path = self.cfg.state_path / "sessions" / f"{ts}-{new_id()}.jsonl"
        self.session_path.parent.mkdir(parents=True, exist_ok=True)
        with self.session_path.open("w", encoding="utf-8") as f:
            for m in self.messages:
                f.write(json.dumps(m, ensure_ascii=False, default=str) + "\n")
        return self.session_path

    def load_session(self, path: Path) -> int:
        self.messages = [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]
        self.session_path = path
        return len(self.messages)

    def list_sessions(self) -> list[Path]:
        d = self.cfg.state_path / "sessions"
        return sorted(d.glob("*.jsonl"), reverse=True) if d.exists() else []

    def reset_session(self) -> None:
        self.messages = []
        self.session_path = None
