"""流水线调度：单任务锁 + 进度回调。"""
from __future__ import annotations

import os
import threading
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Callable, Optional

from app.config import require_api_key
from app.pipeline_core import STEP_LABELS, STEPS, PipelineContext, run_step
from app.reports_util import find_latest_report, open_report

LogCallback = Callable[[str], None]
StepCallback = Callable[[int, str, str], None]  # index, step_name, label
DoneCallback = Callable[[Path], None]
ErrorCallback = Callable[[Exception], None]


class RunnerState(str, Enum):
    IDLE = "idle"
    RUNNING = "running"


@dataclass
class PipelineRunResult:
    report_path: Path
    keyword: str


class PipelineRunner:
    """全局单例式流水线执行器。"""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._state = RunnerState.IDLE
        self._thread: Optional[threading.Thread] = None

    @property
    def state(self) -> RunnerState:
        return self._state

    def is_running(self) -> bool:
        return self._state == RunnerState.RUNNING

    def run_async(
        self,
        ctx: PipelineContext,
        *,
        on_log: LogCallback,
        on_step_start: StepCallback,
        on_step_done: StepCallback,
        on_complete: DoneCallback,
        on_error: ErrorCallback,
        auto_open_report: bool = True,
    ) -> bool:
        if self.is_running():
            on_error(RuntimeError("已有任务在运行，请等待完成。"))
            return False

        def worker() -> None:
            with self._lock:
                self._state = RunnerState.RUNNING
            try:
                require_api_key()
                os.environ["LLM_MAX_WORKERS"] = os.environ.get("LLM_MAX_WORKERS", "6")
                result = self._run_sync(
                    ctx,
                    on_log=on_log,
                    on_step_start=on_step_start,
                    on_step_done=on_step_done,
                )
                if auto_open_report:
                    open_report(result.report_path)
                on_complete(result.report_path)
            except Exception as e:
                on_error(e)
            finally:
                with self._lock:
                    self._state = RunnerState.IDLE

        self._thread = threading.Thread(target=worker, daemon=True)
        self._thread.start()
        return True

    def _run_sync(
        self,
        ctx: PipelineContext,
        *,
        on_log: LogCallback,
        on_step_start: StepCallback,
        on_step_done: StepCallback,
    ) -> PipelineRunResult:
        step_names = [s["name"] for s in STEPS]
        start_idx = step_names.index(ctx.start_from)
        last_output = None

        if start_idx > 0:
            from app.pipeline_core import STEPS as _STEPS
            from app.reports_util import find_latest_file
            from app.paths import get_data_dir

            prev = _STEPS[start_idx - 1]
            prev_dir = prev.get("output_dir", get_data_dir())
            last_output = find_latest_file(
                prev_dir, prev["output_prefix"], ctx.keyword
            )
            on_log(f"已加载前置产物: {last_output.name}")

        total = len(STEPS) - start_idx
        for i, step in enumerate(STEPS[start_idx:]):
            name = step["name"]
            label = STEP_LABELS.get(name, name)
            on_step_start(i, name, label)
            on_log(f"—— {label} ——")
            last_output = run_step(step, ctx, last_output, log_fn=on_log)
            on_step_done(i, name, label)

        report_path = find_latest_report(ctx.keyword)
        on_log(f"流水线完成，报告: {report_path}")
        return PipelineRunResult(report_path=report_path, keyword=ctx.keyword)


_runner: Optional[PipelineRunner] = None


def get_pipeline_runner() -> PipelineRunner:
    global _runner
    if _runner is None:
        _runner = PipelineRunner()
    return _runner
