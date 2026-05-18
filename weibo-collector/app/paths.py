"""应用路径：开发态与 PyInstaller 打包态统一入口。"""
from __future__ import annotations

import sys
from pathlib import Path


def get_weibo_collector_dir() -> Path:
    """`weibo-collector` 工程根目录（含 launcher、data）。"""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parents[1]


def get_data_dir() -> Path:
    d = get_weibo_collector_dir() / "data"
    d.mkdir(parents=True, exist_ok=True)
    return d


def get_reports_dir() -> Path:
    d = get_weibo_collector_dir() / "reports"
    d.mkdir(parents=True, exist_ok=True)
    return d


def get_env_path() -> Path:
    return get_weibo_collector_dir() / ".env"


def get_weibo_auth_path() -> Path:
    return get_data_dir() / "weibo_auth.json"


def get_vendor_models_dir() -> Path:
    """打包用内置模型目录（阶段 6 写入）。"""
    return get_weibo_collector_dir() / "vendor" / "models"


def get_vendor_browsers_dir() -> Path:
    return get_weibo_collector_dir() / "vendor" / "browsers"


def apply_runtime_env() -> None:
    """启动时设置 Playwright / HuggingFace 路径（若 vendor 存在）。"""
    import os

    browsers = get_vendor_browsers_dir()
    if browsers.is_dir():
        os.environ.setdefault("PLAYWRIGHT_BROWSERS_PATH", str(browsers))

    models = get_vendor_models_dir()
    if models.is_dir():
        os.environ.setdefault("HF_HOME", str(models))
        os.environ.setdefault("SENTENCE_TRANSFORMERS_HOME", str(models))
        os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")
