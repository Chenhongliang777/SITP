#!/usr/bin/env python3
"""
合并步骤：语义过滤 → 情感 → 主题聚类 → ABSA → 风险扫描 → 预警评分。
单进程内串联，避免多次启动 Python、重复加载语义/情感模型。
默认各子阶段逻辑与拆分脚本 / 旧版流水线一致（有 API Key 时按原策略调用 LLM）。
"""

import argparse
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).parent.resolve()
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from absa_extractor import run_absa
from risk_scanner import run_risk_scan
from semantic_filter import run_semantic_filter
from sentiment_model import run_sentiment
from topic_cluster import run_topic_cluster
from warner_score import run_warner


def run_analysis_chain(
    deduped_path: Path,
    *,
    no_semantic_llm: bool = False,
    tfidf_topic_only: bool = False,
    rule_absa: bool = False,
    rule_risk_only: bool = False,
    semantic_gray_reject: bool = False,
    no_sentiment_llm_fallback: bool = False,
) -> Path:
    if not deduped_path.exists():
        raise SystemExit(f"未找到输入文件: {deduped_path}")

    filtered_path, _rej = run_semantic_filter(
        deduped_path,
        no_semantic_llm=no_semantic_llm,
        semantic_gray_reject=semantic_gray_reject,
    )
    sentiment_path = run_sentiment(
        filtered_path, no_llm_fallback=no_sentiment_llm_fallback
    )
    topic_path = run_topic_cluster(sentiment_path, use_llm_labels=not tfidf_topic_only)
    absa_path = run_absa(topic_path, use_llm=not rule_absa)
    risk_path = run_risk_scan(absa_path, use_llm=not rule_risk_only)
    warning_path = run_warner(risk_path)
    print("分析链（6 子阶段）已全部完成。")
    return warning_path


def main():
    parser = argparse.ArgumentParser(
        description="分析链：语义过滤 / 情感 / 主题 / ABSA / 风险 / 预警（默认与旧版逐步跑一致）"
    )
    parser.add_argument("--input", required=True, help="上游 deduped JSON 文件路径")
    parser.add_argument(
        "--no-semantic-llm",
        action="store_true",
        help="语义过滤禁用 LLM 回退（同 semantic_filter.py --no-llm）",
    )
    parser.add_argument(
        "--tfidf-topic-only",
        action="store_true",
        help="主题簇标签仅用 TF-IDF（不调用 LLM）",
    )
    parser.add_argument(
        "--rule-absa",
        action="store_true",
        help="ABSA 仅用规则/jieba",
    )
    parser.add_argument(
        "--rule-risk-only",
        action="store_true",
        help="风险扫描仅用规则 + 硬规则",
    )
    parser.add_argument(
        "--semantic-gray-reject",
        action="store_true",
        help="语义灰区一律判非足球并丢弃（不调 LLM、不用启发式捞回）",
    )
    parser.add_argument(
        "--no-sentiment-llm-fallback",
        action="store_true",
        help="情感：模型失败或单条失败时不调用 LLM，使用默认中性兜底",
    )
    args = parser.parse_args()

    run_analysis_chain(
        Path(args.input),
        no_semantic_llm=args.no_semantic_llm,
        tfidf_topic_only=args.tfidf_topic_only,
        rule_absa=args.rule_absa,
        rule_risk_only=args.rule_risk_only,
        semantic_gray_reject=args.semantic_gray_reject,
        no_sentiment_llm_fallback=args.no_sentiment_llm_fallback,
    )


if __name__ == "__main__":
    main()
