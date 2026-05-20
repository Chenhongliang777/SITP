#!/usr/bin/env python3
"""
launcher.py
足球舆情监测流水线统一启动器（含微博登录子命令：python launcher.py login）
"""
import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path

from dotenv import load_dotenv


from utils.project_root import get_project_root

SCRIPT_DIR = get_project_root()
DATA_DIR = SCRIPT_DIR / "data"
REPORT_DIR = SCRIPT_DIR / "reports"
DATA_DIR.mkdir(exist_ok=True)
REPORT_DIR.mkdir(exist_ok=True)

STEPS = [
    {
        "name": "collector",
        "script": "collector_backend.py",
        "output_prefix": "raw",
        "ext": "json",
        "needs_input": False,
        "min_records": 1,
        "build_args": lambda ctx, _: [
            "--keyword", ctx.keyword,
            "--start-date", ctx.start_date,
            "--end-date", ctx.end_date,
            "--target-count", str(ctx.target_count),
            *(["--no-headless"] if ctx.no_headless else []),
            *(["--proxy", ctx.proxy] if ctx.proxy else []),
        ],
    },
    {
        "name": "preprocess",
        "script": "preprocess.py",
        "output_prefix": "deduped",
        "ext": "json",
        "needs_input": True,
        "min_records": 1,
        "build_args": lambda ctx, inp: [
            "--input", str(inp),
            "--start-date", ctx.start_date,
            "--end-date", ctx.end_date,
        ],
    },
    {
        "name": "analysis_chain",
        "script": "analysis_chain.py",
        "output_prefix": "warning",
        "ext": "json",
        "needs_input": True,
        "min_records": 1,
        "build_args": lambda ctx, inp: [
            "--input", str(inp),
            *(["--no-semantic-llm"] if ctx.no_llm else []),
            *(["--tfidf-topic-only"] if ctx.tfidf_topic_only else []),
            *(["--rule-risk-only"] if ctx.rule_risk_only else []),
            *(["--rule-absa"] if ctx.rule_absa else []),
            *(["--semantic-gray-reject"] if ctx.semantic_gray_reject else []),
            *(["--no-sentiment-llm-fallback"] if ctx.no_sentiment_llm_fallback else []),
        ],
    },
    {
        "name": "report_html",
        "script": "report_html.py",
        "output_prefix": "report",
        "ext": "html",
        "needs_input": False,
        "min_records": 0,
        "output_dir": REPORT_DIR,
        "build_args": lambda ctx, _: [
            "--warning", str(find_latest_file(DATA_DIR, "warning", ctx.keyword)),
            "--risk", str(find_latest_file(DATA_DIR, "risk", ctx.keyword)),
            *(["--rule-report-only"] if ctx.rule_report_only else []),
        ],
    },
]


def find_latest_file(directory: Path, prefix: str, keyword: str, ext: str = "json") -> Path:
    escaped_kw = re.escape(keyword)
    pattern = re.compile(rf"^{re.escape(prefix)}_{escaped_kw}_\d{{8}}_\d{{6}}\.{ext}$")
    candidates = [f for f in directory.iterdir() if f.is_file() and pattern.match(f.name)]
    if not candidates:
        raise FileNotFoundError(
            f"在 [{directory}] 中未找到匹配 '{prefix}_{keyword}_YYYYMMDD_HHMMSS.{ext}' 的文件。\n"
            f"请确认前置步骤已成功执行，且 keyword 与之前保持一致。"
        )
    candidates.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    return candidates[0]


def check_env() -> None:
    try:
        from app.config import bootstrap, require_api_key

        bootstrap()
        require_api_key()
    except ImportError:
        env_path = SCRIPT_DIR / ".env"
        if not env_path.exists():
            print("\n❌ 启动失败：未找到 .env 配置文件")
            sys.exit(1)
        load_dotenv(dotenv_path=env_path)
        if not os.getenv("DEEPSEEK_API_KEY", "").strip():
            print("\n❌ 启动失败：.env 中未配置 API Key")
            sys.exit(1)
    except RuntimeError as e:
        print(f"\n❌ 启动失败：{e}")
        sys.exit(1)
    print("✅ 环境校验通过：已加载 .env，API Key 已配置\n")


def validate_output(step_name: str, file_path: Path, min_records: int) -> None:
    if not file_path.exists():
        raise RuntimeError(f"步骤 [{step_name}] 未生成预期输出文件: {file_path.name}")

    if file_path.suffix == ".html":
        size = file_path.stat().st_size
        if size < 200:
            raise RuntimeError(f"步骤 [{step_name}] 生成的 HTML 过小（{size} 字节），可能内容缺失。")
        return

    try:
        with open(file_path, "r", encoding="utf-8") as f:
            payload = json.load(f)
    except Exception as e:
        raise RuntimeError(f"步骤 [{step_name}] 输出文件 JSON 解析失败: {e}")

    actual = payload.get("meta", {}).get("actual")
    data = payload.get("data")

    if actual is not None:
        effective_count = actual
    elif data is not None:
        effective_count = len(data)
    else:
        effective_count = 1 if min_records == 0 else 0

    if effective_count < min_records:
        raise RuntimeError(
            f"步骤 [{step_name}] 业务结果异常：有效数据 {effective_count}，低于最低要求 {min_records}，"
            f"无法继续后续流程。请检查上游数据或参数。"
        )


def run_step(step: dict, ctx, last_output: Path = None) -> Path:
    name = step["name"]
    script_path = SCRIPT_DIR / step["script"]

    if not script_path.exists():
        print(f"\n❌ 步骤 [{name}] 失败：找不到脚本 {script_path}")
        sys.exit(1)

    if step["needs_input"]:
        if last_output is None:
            prev_idx = STEPS.index(step) - 1
            prev_prefix = STEPS[prev_idx]["output_prefix"]
            last_output = find_latest_file(DATA_DIR, prev_prefix, ctx.keyword)
        args = step["build_args"](ctx, last_output)
    else:
        args = step["build_args"](ctx, None)

    cmd = [sys.executable, str(script_path)] + args
    cmd_display = " ".join(cmd)

    print(f"▶️  步骤 [{name}] 启动")
    print(f"   命令: {cmd_display[:160]}{'...' if len(cmd_display) > 160 else ''}")

    try:
        subprocess.run(cmd, check=True, cwd=str(SCRIPT_DIR))
    except subprocess.CalledProcessError as e:
        print(f"\n❌ 步骤 [{name}] 执行失败（返回码 {e.returncode}）")
        print(f"   完整命令: {cmd_display}")
        sys.exit(1)
    except Exception as e:
        print(f"\n❌ 步骤 [{name}] 执行异常: {e}")
        sys.exit(1)

    # 关键修复：按步骤指定的目录查找输出文件，默认 data/
    search_dir = step.get("output_dir", DATA_DIR)
    out_file = find_latest_file(search_dir, step["output_prefix"], ctx.keyword, step["ext"])

    try:
        validate_output(name, out_file, step.get("min_records", 1))
    except RuntimeError as e:
        print(f"\n❌ {e}")
        print(f"   输出文件: {out_file}")
        print("   流水线已中止，请排查问题后重试。")
        sys.exit(1)

    print(f"   输出: {out_file.name}（{out_file.stat().st_size // 1024} KB）")
    print(f"✅ 步骤 [{name}] 完成\n")
    return out_file


def main():
    parser = argparse.ArgumentParser(
        description="足球舆情监测流水线启动器",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  python launcher.py login
  python launcher.py --keyword 中超 --start-date 2026-04-01 --end-date 2026-04-29
  python launcher.py --keyword 中超 --start-from analysis_chain --start-date 2026-04-01 --end-date 2026-04-29
  python launcher.py --keyword 中超 --start-date 2026-04-01 --end-date 2026-04-29 --efficient --fast-collect --turbo-collect
        """
    )
    parser.add_argument("--keyword", required=True, help="搜索关键词（如：中超、国足）")
    parser.add_argument("--start-date", required=True, help="开始日期 YYYY-MM-DD")
    parser.add_argument("--end-date", required=True, help="结束日期 YYYY-MM-DD")
    parser.add_argument("--target-count", type=int, default=80, help="目标采集条数（默认 80）")
    parser.add_argument("--no-headless", action="store_true",
                        help="关闭无头模式，显示浏览器窗口（仅影响采集步骤）")
    parser.add_argument("--proxy", default=None, help="代理地址，如 http://127.0.0.1:7890")
    parser.add_argument("--start-from", default="collector",
                        choices=[s["name"] for s in STEPS],
                        help="从指定步骤开始运行，方便调试")
    parser.add_argument(
        "--no-llm",
        action="store_true",
        help="语义过滤步骤禁用 LLM 回退（与旧版一致；默认启用 LLM 灰区判定）",
    )
    parser.add_argument(
        "--tfidf-topic-only",
        action="store_true",
        help="主题簇标签仅用 TF-IDF（默认与旧版一致：有 Key 则 LLM 命名）",
    )
    parser.add_argument(
        "--rule-risk-only",
        action="store_true",
        help="风险扫描仅用规则（默认与旧版一致：有 Key 则 LLM 主路径）",
    )
    parser.add_argument(
        "--rule-absa",
        action="store_true",
        help="ABSA 仅用规则/jieba（默认与旧版一致：LLM 主路径）",
    )
    parser.add_argument(
        "--rule-report-only",
        action="store_true",
        help="报告研判摘要仅用规则模板（默认与旧版一致：有 Key 则先尝试 LLM）",
    )
    parser.add_argument(
        "--efficient",
        action="store_true",
        help="一键高效：等价于同时开启 --no-llm --tfidf-topic-only --rule-absa --rule-risk-only --rule-report-only",
    )
    parser.add_argument(
        "--semantic-gray-reject",
        action="store_true",
        help="语义相似度灰区一律丢弃（非足球），不调 LLM；可与 --no-llm 叠加",
    )
    parser.add_argument(
        "--no-sentiment-llm-fallback",
        action="store_true",
        help="情感分析在模型失败或单条失败时不调用 LLM，使用默认中性兜底",
    )
    parser.add_argument(
        "--llm-workers",
        type=int,
        default=6,
        help="LLM HTTP 并发数（ABSA、风险、语义灰区等），默认 6；遇限流可改为 3",
    )
    parser.add_argument(
        "--fast-collect",
        action="store_true",
        help="采集阶段缩短页面等待（略增被风控概率，可明显省时间）",
    )
    parser.add_argument(
        "--turbo-collect",
        action="store_true",
        help="采集再加速：更短滚动/翻页等待 + 先拉满移动端分页；风控风险高于 --fast-collect，建议与 --fast-collect 同开",
    )

    args = parser.parse_args()

    if getattr(args, "efficient", False):
        args.no_llm = True
        args.tfidf_topic_only = True
        args.rule_absa = True
        args.rule_risk_only = True
        args.rule_report_only = True

    os.environ["LLM_MAX_WORKERS"] = str(max(1, min(32, args.llm_workers)))
    if getattr(args, "fast_collect", False):
        os.environ["WEIBO_FAST_COLLECT"] = "1"
    if getattr(args, "turbo_collect", False):
        os.environ["WEIBO_TURBO_COLLECT"] = "1"

    check_env()

    step_names = [s["name"] for s in STEPS]
    start_idx = step_names.index(args.start_from)

    print("🚀 流水线启动")
    print(f"   关键词 : {args.keyword}")
    print(f"   日期   : {args.start_date} ~ {args.end_date}")
    print(f"   目标   : {args.target_count} 条")
    print(f"   起始   : {args.start_from}（第 {start_idx + 1}/{len(STEPS)} 步）")
    if getattr(args, "efficient", False):
        print("   模式   : 高效（--efficient：已启用 no-llm / tfidf-topic-only / rule-absa / rule-risk-only / rule-report-only）")
    if getattr(args, "semantic_gray_reject", False):
        print("   语义   : 灰区严弃（--semantic-gray-reject）")
    if getattr(args, "no_sentiment_llm_fallback", False):
        print("   情感   : 已禁用 LLM 回退（--no-sentiment-llm-fallback）")
    if getattr(args, "turbo_collect", False):
        print("   采集   : turbo（--turbo-collect，最短等待；建议已开 --fast-collect）")
    print("-" * 50)

    last_output = None
    if start_idx > 0:
        prev_step = STEPS[start_idx - 1]
        try:
            prev_dir = prev_step.get("output_dir", DATA_DIR)
            last_output = find_latest_file(prev_dir, prev_step["output_prefix"], args.keyword)
            print(f"📂 已加载前置产物: {last_output.name}\n")
        except FileNotFoundError as e:
            print(f"⚠️  警告: {e}\n")
            print("   将从当前步骤开始，但可能因缺少输入而失败。\n")

    for step in STEPS[start_idx:]:
        last_output = run_step(step, args, last_output)

    print("=" * 55)
    print("🎉 流水线全部执行完毕！")
    print(f"📄 最终报告: {last_output}")
    print("=" * 55)


if __name__ == "__main__":
    if len(sys.argv) >= 2 and sys.argv[1] == "login":
        from login import main as login_main

        login_main()
        sys.exit(0)
    main()