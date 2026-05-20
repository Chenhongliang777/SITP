"""流水线四步定义与单步执行（供 launcher 与 GUI 共用）。"""
from __future__ import annotations

import json
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, List, Optional

from app.paths import get_data_dir, get_reports_dir, get_script_path, get_weibo_collector_dir
from app.reports_util import find_latest_file
from app.subprocess_io import decode_subprocess_line, subprocess_env

LogFn = Optional[Callable[[str], None]]

STEP_LABELS = {
    "collector": "正在采集微博数据",
    "preprocess": "正在清洗与去重",
    "analysis_chain": "正在分析舆情（语义/情感/风险/预警）",
    "report_html": "正在生成 HTML 研判报告",
}


@dataclass
class PipelineContext:
    keyword: str
    start_date: str
    end_date: str
    target_count: int = 80
    no_headless: bool = False
    proxy: Optional[str] = None
    no_llm: bool = False
    tfidf_topic_only: bool = False
    rule_risk_only: bool = False
    rule_absa: bool = False
    rule_report_only: bool = False
    semantic_gray_reject: bool = False
    no_sentiment_llm_fallback: bool = False
    start_from: str = "collector"

    @classmethod
    def from_gui(
        cls,
        keyword: str,
        start_date: str,
        end_date: str,
        target_count: int,
        efficient: bool,
    ) -> "PipelineContext":
        ctx = cls(
            keyword=keyword.strip(),
            start_date=start_date.strip(),
            end_date=end_date.strip(),
            target_count=max(1, int(target_count)),
        )
        if efficient:
            ctx.no_llm = True
            ctx.tfidf_topic_only = True
            ctx.rule_absa = True
            ctx.rule_risk_only = True
            ctx.rule_report_only = True
        return ctx


def _log(msg: str, log_fn: LogFn) -> None:
    if log_fn:
        log_fn(msg)
    else:
        print(msg)


def _build_step_command(script_path: Path, args: list) -> list:
    if getattr(sys, "frozen", False):
        return [sys.executable, "--run-script", script_path.name] + args
    return [sys.executable, str(script_path)] + args


def _build_steps():
    data_dir = get_data_dir()
    report_dir = get_reports_dir()

    return [
        {
          "name": "collector",
          "script": get_script_path("collector_backend.py"),
          "output_prefix": "raw",
          "ext": "json",
          "needs_input": False,
          "min_records": 1,
          "build_args": lambda ctx, _: [
              "--keyword",
              ctx.keyword,
              "--start-date",
              ctx.start_date,
              "--end-date",
              ctx.end_date,
              "--target-count",
              str(ctx.target_count),
              *(["--no-headless"] if ctx.no_headless else []),
              *(["--proxy", ctx.proxy] if ctx.proxy else []),
          ],
      },
      {
          "name": "preprocess",
          "script": get_script_path("preprocess.py"),
          "output_prefix": "deduped",
          "ext": "json",
          "needs_input": True,
          "min_records": 1,
          "build_args": lambda ctx, inp: [
              "--input",
              str(inp),
              "--start-date",
              ctx.start_date,
              "--end-date",
              ctx.end_date,
          ],
      },
      {
          "name": "analysis_chain",
          "script": get_script_path("analysis_chain.py"),
          "output_prefix": "warning",
          "ext": "json",
          "needs_input": True,
          "min_records": 1,
          "build_args": lambda ctx, inp: [
              "--input",
              str(inp),
              *(["--no-semantic-llm"] if ctx.no_llm else []),
              *(["--tfidf-topic-only"] if ctx.tfidf_topic_only else []),
              *(["--rule-risk-only"] if ctx.rule_risk_only else []),
              *(["--rule-absa"] if ctx.rule_absa else []),
              *(["--semantic-gray-reject"] if ctx.semantic_gray_reject else []),
              *(
                  ["--no-sentiment-llm-fallback"]
                  if ctx.no_sentiment_llm_fallback
                  else []
              ),
          ],
      },
      {
          "name": "report_html",
          "script": get_script_path("report_html.py"),
          "output_prefix": "report",
          "ext": "html",
          "needs_input": False,
          "min_records": 0,
          "output_dir": report_dir,
          "build_args": lambda ctx, _: [
              "--warning",
              str(find_latest_file(data_dir, "warning", ctx.keyword)),
              "--risk",
              str(find_latest_file(data_dir, "risk", ctx.keyword)),
              *(["--rule-report-only"] if ctx.rule_report_only else []),
          ],
        },
    ]


STEPS = _build_steps()


def validate_output(step_name: str, file_path: Path, min_records: int) -> None:
    if not file_path.exists():
        raise RuntimeError(f"步骤 [{step_name}] 未生成输出: {file_path.name}")

    if file_path.suffix == ".html":
        if file_path.stat().st_size < 200:
            raise RuntimeError(f"步骤 [{step_name}] HTML 过小，可能内容缺失")
        return

    with open(file_path, "r", encoding="utf-8") as f:
        payload = json.load(f)

    actual = payload.get("meta", {}).get("actual")
    data = payload.get("data")
    if actual is not None:
        effective = actual
    elif data is not None:
        effective = len(data)
    else:
        effective = 1 if min_records == 0 else 0

    if effective < min_records:
        raise RuntimeError(
            f"步骤 [{step_name}] 有效数据 {effective} 条，低于要求 {min_records}"
        )


def run_step(
    step: dict,
    ctx: PipelineContext,
    last_output: Optional[Path] = None,
    *,
    log_fn: LogFn = None,
) -> Path:
    name = step["name"]
    script_path = step["script"]
    data_dir = get_data_dir()

    if not script_path.exists():
        raise FileNotFoundError(f"找不到脚本: {script_path}")

    if step["needs_input"]:
        if last_output is None:
            prev_idx = STEPS.index(step) - 1
            prev_prefix = STEPS[prev_idx]["output_prefix"]
            last_output = find_latest_file(data_dir, prev_prefix, ctx.keyword)
        args = step["build_args"](ctx, last_output)
    else:
        args = step["build_args"](ctx, None)

    cmd = _build_step_command(script_path, args)
    _log(f"▶ 步骤 [{name}] 启动", log_fn)
    _log(f"   {' '.join(cmd)[:200]}", log_fn)

    proc = subprocess.Popen(
        cmd,
        cwd=str(get_weibo_collector_dir()),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        env=subprocess_env(),
        bufsize=0,
    )
    assert proc.stdout is not None
    for raw in iter(proc.stdout.readline, b""):
        line = decode_subprocess_line(raw)
        if line:
            _log(line, log_fn)
    code = proc.wait()
    if code != 0:
        raise RuntimeError(f"步骤 [{name}] 失败，退出码 {code}")

    search_dir = step.get("output_dir", data_dir)
    out_file = find_latest_file(
        search_dir, step["output_prefix"], ctx.keyword, step["ext"]
    )
    validate_output(name, out_file, step.get("min_records", 1))
    _log(f"✓ 步骤 [{name}] 完成 → {out_file.name}", log_fn)
    return out_file
