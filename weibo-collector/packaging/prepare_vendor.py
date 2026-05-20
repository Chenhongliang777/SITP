#!/usr/bin/env python3
"""构建前下载内置语义/情感模型与 Playwright Chromium（仅需在打包机执行一次）。"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
VENDOR = ROOT / "vendor"
MODELS = VENDOR / "models"
BROWSERS = VENDOR / "browsers"

BGE_NAME = "BAAI/bge-small-zh-v1.5"
SENTIMENT_NAME = "lxyuan/distilbert-base-multilingual-cased-sentiments-student"


def _ensure_on_path() -> None:
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))


def download_models(skip_sentiment: bool = False) -> None:
    _ensure_on_path()
    MODELS.mkdir(parents=True, exist_ok=True)
    os.environ["HF_HOME"] = str(MODELS)

    print(f"[1/2] 下载语义模型 {BGE_NAME} …")
    from sentence_transformers import SentenceTransformer

    bge_dir = MODELS / "BAAI" / "bge-small-zh-v1.5"
    if bge_dir.is_dir() and any(bge_dir.iterdir()):
        print(f"  已存在，跳过: {bge_dir}")
    else:
        m = SentenceTransformer(BGE_NAME)
        bge_dir.parent.mkdir(parents=True, exist_ok=True)
        m.save(str(bge_dir))
        print(f"  已保存: {bge_dir}")

    if skip_sentiment:
        print("  跳过情感模型下载 (--skip-sentiment)")
        return

    print(f"[2/2] 下载情感模型 {SENTIMENT_NAME} …")
    from transformers import AutoModelForSequenceClassification, AutoTokenizer

    sent_dir = MODELS / "lxyuan" / "distilbert-base-multilingual-cased-sentiments-student"
    if sent_dir.is_dir() and (sent_dir / "config.json").exists():
        print(f"  已存在，跳过: {sent_dir}")
        return

    tok = AutoTokenizer.from_pretrained(SENTIMENT_NAME)
    model = AutoModelForSequenceClassification.from_pretrained(SENTIMENT_NAME)
    sent_dir.mkdir(parents=True, exist_ok=True)
    tok.save_pretrained(sent_dir)
    model.save_pretrained(sent_dir)
    print(f"  已保存: {sent_dir}")


def install_chromium() -> None:
    BROWSERS.mkdir(parents=True, exist_ok=True)
    env = os.environ.copy()
    env["PLAYWRIGHT_BROWSERS_PATH"] = str(BROWSERS)
    print(f"[playwright] 安装 Chromium 到 {BROWSERS} …")
    subprocess.run(
        [sys.executable, "-m", "playwright", "install", "chromium"],
        cwd=str(ROOT),
        env=env,
        check=True,
    )
    print("  Chromium 安装完成。")


def main() -> None:
    parser = argparse.ArgumentParser(description="预下载打包资源到 vendor/")
    parser.add_argument(
        "--skip-models", action="store_true", help="跳过 HuggingFace 模型（仅装浏览器）"
    )
    parser.add_argument(
        "--skip-sentiment", action="store_true", help="仅下载 bge，跳过 distilbert"
    )
    parser.add_argument(
        "--skip-browsers", action="store_true", help="跳过 Playwright Chromium"
    )
    args = parser.parse_args()

    if not args.skip_models:
        download_models(skip_sentiment=args.skip_sentiment)
    if not args.skip_browsers:
        install_chromium()

    print("\n完成。vendor 目录可随安装包分发。")


if __name__ == "__main__":
    main()
