"""Demo extension: registers a /motivate command and a tool_call hook."""
from oneai.legacy.runtime import Command


def setup(rt):
    rt.register_command(Command("motivate", "来一句加油", lambda arg: print("加油！")))
    rt.on("tool_call", lambda name, arguments: print(f"[ext] tool: {name}") or None)
