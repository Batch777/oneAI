"""Tool-use tests: agent loop with a fake LLM (no API calls)."""
from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from oneai.runtime import Command, Runtime, Tool


class FakeToolCall:
    def __init__(self, name: str, args: dict, tc_id: str = "tc1"):
        self.id = tc_id
        self.function = SimpleNamespace(name=name, arguments=json.dumps(args))

    def model_dump(self):
        return {"id": self.id, "type": "function",
                "function": {"name": self.function.name, "arguments": self.function.arguments}}


class FakeMessage:
    def __init__(self, content=None, tool_calls=None):
        self.content = content
        self.tool_calls = tool_calls
        self.reasoning_content = None


class FakeCompletions:
    """Queue of canned responses; each create() pops the next."""

    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = 0

    def create(self, **kwargs):
        msg = self.responses.pop(0)
        self.calls += 1
        return SimpleNamespace(choices=[SimpleNamespace(message=msg)])


def make_runtime(tmp_path, responses, monkeypatch) -> Runtime:
    monkeypatch.setenv("ONEAI_VAULT_PATH", str(tmp_path / "vault"))
    monkeypatch.setenv("ONEAI_STATE_PATH", str(tmp_path / "state"))
    from oneai.config import Config

    rt = Runtime(Config.load())
    rt.llm = SimpleNamespace(
        model="fake",
        client=SimpleNamespace(chat=SimpleNamespace(completions=FakeCompletions(responses))),
    )
    return rt


class TestToolLoop:
    def test_tool_call_then_answer(self, tmp_path, monkeypatch):
        rt = make_runtime(tmp_path, [
            FakeMessage(tool_calls=[FakeToolCall("echo", {"text": "hi"})]),
            FakeMessage(content="回声是 hi"),
        ], monkeypatch)
        rt.register_tool(Tool("echo", "回声", {
            "type": "object", "properties": {"text": {"type": "string"}}},
            func=lambda text: f"echo:{text}"))

        events = []
        answer = rt.run_agent("test", on_event=lambda k, d: events.append(k))
        assert answer == "回声是 hi"
        assert events == ["tool_start", "tool_end"]
        # history: user, assistant(tool_calls), tool, assistant(answer)
        assert [m["role"] for m in rt.messages] == ["user", "assistant", "tool", "assistant"]
        assert rt.messages[2]["content"] == "echo:hi"

    def test_confirm_gate_denies(self, tmp_path, monkeypatch):
        rt = make_runtime(tmp_path, [
            FakeMessage(tool_calls=[FakeToolCall("danger", {})]),
            FakeMessage(content="好吧，不执行"),
        ], monkeypatch)
        rt.register_tool(Tool("danger", "危险", {"type": "object", "properties": {}},
                              func=lambda: "executed", confirm=True))
        rt.confirm_cb = lambda title, msg: False  # user presses "n"

        answer = rt.run_agent("do it")
        assert answer == "好吧，不执行"
        assert rt.messages[2]["content"] == "用户拒绝了本次调用"

    def test_confirm_gate_approves(self, tmp_path, monkeypatch):
        rt = make_runtime(tmp_path, [
            FakeMessage(tool_calls=[FakeToolCall("danger", {})]),
            FakeMessage(content="done"),
        ], monkeypatch)
        rt.register_tool(Tool("danger", "危险", {"type": "object", "properties": {}},
                              func=lambda: "executed", confirm=True))
        rt.confirm_cb = lambda title, msg: True

        rt.run_agent("do it")
        assert rt.messages[2]["content"] == "executed"

    def test_hook_can_block(self, tmp_path, monkeypatch):
        rt = make_runtime(tmp_path, [
            FakeMessage(tool_calls=[FakeToolCall("echo", {"text": "x"})]),
            FakeMessage(content="ok"),
        ], monkeypatch)
        rt.register_tool(Tool("echo", "回声", {"type": "object", "properties": {}},
                              func=lambda **kw: "ran"))
        rt.on("tool_call", lambda name, arguments: {"block": True, "reason": "策略禁止"})

        rt.run_agent("test")
        assert "已被拦截" in rt.messages[2]["content"]
        assert "策略禁止" in rt.messages[2]["content"]

    def test_max_iters_guard(self, tmp_path, monkeypatch):
        rt = make_runtime(tmp_path, [
            FakeMessage(tool_calls=[FakeToolCall("echo", {"text": "x"}, tc_id=f"tc{i}")])
            for i in range(20)
        ], monkeypatch)
        rt.register_tool(Tool("echo", "回声", {"type": "object", "properties": {}},
                              func=lambda **kw: "ok"))
        assert "上限" in rt.run_agent("loop forever")

    def test_builtin_tools_registered(self, tmp_path, monkeypatch):
        rt = make_runtime(tmp_path, [], monkeypatch)
        for name in ("vault_search", "vault_read", "draft_save", "image_find", "image_show"):
            assert name in rt.tools
        assert rt.tools["draft_save"].confirm is True


class TestExtensionSystem:
    def test_load_from_project_dir(self, tmp_path, monkeypatch):
        ext_dir = tmp_path / ".oneai" / "extensions"
        ext_dir.mkdir(parents=True)
        (ext_dir / "demo.py").write_text(
            "from oneai.runtime import Command\n"
            "def setup(rt):\n"
            "    rt.register_command(Command('demo', '演示', lambda a: None))\n"
            "    rt.on('before_agent_start', lambda: '扩展注入的提示')\n"
        )
        monkeypatch.chdir(tmp_path)
        rt = make_runtime(tmp_path, [], monkeypatch)
        loaded = rt.load_extensions()
        assert loaded and "demo.py" in loaded[0]
        assert "demo" in rt.commands
        assert "扩展注入的提示" in rt._system_prompt()

    def test_no_extensions_dir_is_fine(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        rt = make_runtime(tmp_path, [], monkeypatch)
        assert rt.load_extensions() == []
