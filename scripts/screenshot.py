"""Render the TUI to a PNG screenshot without a real terminal.

    .venv/bin/python scripts/screenshot.py [out.png]

Uses Textual's headless mode + SVG export (converted via qlmanage on macOS).
Chat content is fake sample data — no API calls.
"""
from __future__ import annotations

import asyncio
import subprocess
import sys
import tempfile
from pathlib import Path

from rich.markdown import Markdown

from oneai.tui import OneAIApp

SAMPLE_ANSWER = """根据现有记录：

- **硕士**：中国科学院大学（2023.09 - 2026.06），地理信息工程，GPA 3.58/4.0 [[facts/education.md#L8-L13]]
- **本科**：中国海洋大学（2018.09 - 2022.07），物理学，优秀毕业论文 [[facts/education.md#L14-L16]]

Sources:
- facts/education.md#L8-L13
- facts/education.md#L14-L16"""


async def main() -> None:
    out = Path(sys.argv[1] if len(sys.argv) > 1 else "/tmp/oneai-tui.png")
    app = OneAIApp()
    async with app.run_test(size=(110, 34)) as pilot:
        await pilot.pause(0.6)
        chat = app.chat()
        chat.write("\n[bold cyan]❯ 我的教育背景是什么？[/bold cyan]")
        chat.write("[dim]  🔧 vault_search(query=教育背景)[/dim]")
        chat.write("[dim]  🔧 vault_search(query=学历 本科 硕士)[/dim]")
        chat.write("[dim]助手[/dim]")
        chat.write(Markdown(SAMPLE_ANSWER))
        # completion menu open state
        box = app.query_one("#input")
        box.value = "/re"
        app._refresh_completion("/re")
        app.show_status("再按一次 Ctrl+D 退出", fade_after=99)
        app.update_mode_indicator()
        await pilot.pause(0.3)

        svg = Path(tempfile.mktemp(suffix=".svg"))
        app.save_screenshot(str(svg))
        # qlmanage renders CJK correctly (magick doesn't); poll since it's flaky
        # qlmanage writes <name>.svg.png into the -o directory
        produced = out.parent / (svg.name + ".png")
        import time as _t

        for _ in range(6):
            subprocess.run(["qlmanage", "-t", "-s", "2048", "-o", str(out.parent), str(svg)],
                           capture_output=True)
            _t.sleep(0.6)
            if produced.exists():
                break
        produced.replace(out)
        print(f"screenshot -> {out}")


if __name__ == "__main__":
    asyncio.run(main())
