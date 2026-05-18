"""跨线程日志：队列批量刷新，避免 after(0) 洪泛导致界面卡死。"""
from __future__ import annotations

import queue
from typing import Callable, Optional


class UiLogBridge:
    """工作线程调用 write()；主线程定时批量写入 Textbox。"""

    def __init__(
        self,
        append_fn: Callable[[str], None],
        schedule_fn: Callable[[int, Callable[[], None]], None],
        *,
        poll_ms: int = 80,
        max_lines_per_tick: int = 48,
    ) -> None:
        self._append_fn = append_fn
        self._schedule = schedule_fn
        self._poll_ms = poll_ms
        self._max_lines = max_lines_per_tick
        self._q: queue.Queue[str] = queue.Queue()
        self._scheduled = False

    def write(self, line: str) -> None:
        if line:
            self._q.put(line)
        self._ensure_poll()

    def _ensure_poll(self) -> None:
        if self._scheduled:
            return
        self._scheduled = True
        self._schedule(self._poll_ms, self._flush)

    def _flush(self) -> None:
        batch: list[str] = []
        try:
            while len(batch) < self._max_lines:
                batch.append(self._q.get_nowait())
        except queue.Empty:
            pass

        if batch:
            self._append_fn("\n".join(batch))

        if not self._q.empty():
            self._schedule(self._poll_ms, self._flush)
        else:
            self._scheduled = False
