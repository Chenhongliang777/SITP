"""供 Web 轮询的流水线任务状态（线程安全）。"""
from __future__ import annotations

import threading
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Deque, List, Optional


@dataclass
class JobSnapshot:
    running: bool = False
    keyword: str = ""
    step_index: int = 0
    step_name: str = ""
    step_label: str = "等待开始"
    progress: float = 0.0
    logs: List[str] = field(default_factory=list)
    error: Optional[str] = None
    report_path: Optional[str] = None
    updated_at: float = field(default_factory=time.time)


class JobStatusStore:
    MAX_LOG_LINES = 400

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._logs: Deque[str] = deque(maxlen=self.MAX_LOG_LINES)
        self._running = False
        self._keyword = ""
        self._step_index = 0
        self._step_name = ""
        self._step_label = "等待开始"
        self._progress = 0.0
        self._error: Optional[str] = None
        self._report_path: Optional[str] = None

    def reset(self, keyword: str = "") -> None:
        with self._lock:
            self._logs.clear()
            self._running = True
            self._keyword = keyword
            self._step_index = 0
            self._step_name = ""
            self._step_label = "准备启动"
            self._progress = 0.0
            self._error = None
            self._report_path = None

    def append_log(self, line: str) -> None:
        if not line:
            return
        with self._lock:
            self._logs.append(line)

    def set_step(self, index: int, name: str, label: str, progress: float) -> None:
        with self._lock:
            self._step_index = index
            self._step_name = name
            self._step_label = label
            self._progress = max(0.0, min(1.0, progress))

    def finish_success(self, report_path: str) -> None:
        with self._lock:
            self._running = False
            self._progress = 1.0
            self._step_label = "全部完成"
            self._report_path = report_path
            self._logs.append(f"报告已生成: {report_path}")

    def finish_error(self, message: str) -> None:
        with self._lock:
            self._running = False
            self._error = message
            self._step_label = "失败"
            self._logs.append(f"错误: {message}")

    def snapshot(self) -> JobSnapshot:
        with self._lock:
            return JobSnapshot(
                running=self._running,
                keyword=self._keyword,
                step_index=self._step_index,
                step_name=self._step_name,
                step_label=self._step_label,
                progress=self._progress,
                logs=list(self._logs),
                error=self._error,
                report_path=self._report_path,
                updated_at=time.time(),
            )


_store: Optional[JobStatusStore] = None


def get_job_store() -> JobStatusStore:
    global _store
    if _store is None:
        _store = JobStatusStore()
    return _store
