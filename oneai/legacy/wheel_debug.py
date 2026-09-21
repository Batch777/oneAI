"""Numeric wheel/trackpad scroll debugger.

Shows exactly what the terminal sends per wheel/trackpad gesture and lets you
tune the scroll step live to pick a comfortable value for WHEEL_SCROLL_LINES.

    oneai wheel-debug

Keys: + / - adjust step · q quit
"""
from __future__ import annotations

import time

from textual.app import App, ComposeResult
from textual.events import MouseScrollDown, MouseScrollUp
from textual.widgets import Footer, Header, Label, RichLog

from .tui import WHEEL_SCROLL_LINES


class ProbeLog(RichLog):
    """RichLog that reports every wheel event to the app and scrolls by step."""

    def _scroll_up_for_pointer(self, animate: bool = False) -> bool:
        app: WheelDebug = self.app  # type: ignore[assignment]
        app.record("up")
        app.step_scroll(-1)
        return True

    def _scroll_down_for_pointer(self, animate: bool = False) -> bool:
        app: WheelDebug = self.app  # type: ignore[assignment]
        app.record("down")
        app.step_scroll(1)
        return True


class WheelDebug(App):
    CSS = """
    #stats { height: 7; padding: 0 1; background: $boost; }
    #log { height: 1fr; }
    """

    def __init__(self) -> None:
        super().__init__()
        self.step = WHEEL_SCROLL_LINES
        self.events = 0
        self.up_events = 0
        self.down_events = 0
        self.last_event = "-"
        self.last_time = 0.0
        self.intervals: list[float] = []

    def compose(self) -> ComposeResult:
        yield Header()
        yield Label(id="stats")
        yield ProbeLog(id="log")
        yield Footer()

    def on_mount(self) -> None:
        self.title = "oneAI wheel-debug"
        log = self.query_one("#log", ProbeLog)
        for i in range(400):
            log.write(f"scroll line {i:03d} " + "·" * 40)
        self._update_stats()

    # --- probe API -----------------------------------------------------------

    def record(self, direction: str) -> None:
        now = time.monotonic()
        if self.last_time:
            self.intervals.append(now - self.last_time)
            self.intervals = self.intervals[-50:]
        self.last_time = now
        self.events += 1
        if direction == "up":
            self.up_events += 1
        else:
            self.down_events += 1
        self.last_event = direction
        self._update_stats()

    def step_scroll(self, sign: int) -> None:
        self.query_one("#log", ProbeLog).scroll_relative(
            y=sign * self.step, animate=False)
        self._update_stats()

    def _update_stats(self) -> None:
        log = self.query_one("#log", ProbeLog)
        hz = f"{1 / (sum(self.intervals) / len(self.intervals)):.0f}" if self.intervals else "-"
        self.query_one("#stats", Label).update(
            f"[bold]step[/bold]: {self.step} 行/格   (+/- 调整)\n"
            f"[bold]events[/bold]: {self.events} (↑{self.up_events} ↓{self.down_events})  "
            f"事件频率 ~{hz} Hz\n"
            f"[bold]last[/bold]: {self.last_event}\n"
            f"[bold]offset[/bold]: {log.scroll_offset.y} / 最大 {log.max_scroll_y}"
        )

    # --- keys ----------------------------------------------------------------

    def on_key(self, event) -> None:
        if event.key in ("plus", "equals_sign", "+"):
            self.step += 1
            self._update_stats()
        elif event.key in ("minus", "-"):
            self.step = max(1, self.step - 1)
            self._update_stats()
        elif event.key == "q":
            self.exit()


def run() -> None:
    WheelDebug().run()


if __name__ == "__main__":
    run()
