"""应用路径：开发态与 PyInstaller 打包态统一入口。"""
from __future__ import annotations

import shutil
import sys
from pathlib import Path


def get_weibo_collector_dir() -> Path:
    """可写应用根目录：打包后为 exe 所在目录。"""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parents[1]


def get_bundle_dir() -> Path:
    """只读资源目录：打包后为 PyInstaller _MEIPASS。"""
    if getattr(sys, "frozen", False):
        meipass = getattr(sys, "_MEIPASS", None)
        if meipass:
            return Path(meipass)
    return get_weibo_collector_dir()


def get_script_path(filename: str) -> Path:
    bundled = get_bundle_dir() / filename
    if bundled.exists():
        return bundled
    return get_weibo_collector_dir() / filename


def get_data_dir() -> Path:
    d = get_weibo_collector_dir() / "data"
    d.mkdir(parents=True, exist_ok=True)
    return d


def get_reports_dir() -> Path:
    d = get_weibo_collector_dir() / "reports"
    d.mkdir(parents=True, exist_ok=True)
    return d


def get_logs_dir() -> Path:
    d = get_weibo_collector_dir() / "logs"
    d.mkdir(parents=True, exist_ok=True)
    return d


def get_env_path() -> Path:
    return get_weibo_collector_dir() / ".env"


def get_env_example_path() -> Path:
    return get_weibo_collector_dir() / ".env.example"


def get_weibo_auth_path() -> Path:
    return get_data_dir() / "weibo_auth.json"


def get_vendor_models_dir() -> Path:
    return get_weibo_collector_dir() / "vendor" / "models"


def get_vendor_browsers_dir() -> Path:
    return get_weibo_collector_dir() / "vendor" / "browsers"


def ensure_app_layout() -> bool:
    """首次启动：创建目录，从 .env.example 生成 .env。返回是否新建了 .env。"""
    get_data_dir()
    get_reports_dir()
    get_logs_dir()
    created = False
    env = get_env_path()
    example = get_env_example_path()
    if not env.exists() and example.exists():
        shutil.copy(example, env)
        created = True
    return created


def apply_runtime_env() -> None:
    """启动时设置 Playwright / HuggingFace 离线路径。"""
    import os

    browsers = get_vendor_browsers_dir()
    if browsers.is_dir():
        os.environ.setdefault("PLAYWRIGHT_BROWSERS_PATH", str(browsers))
        os.environ.setdefault("PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD", "1")

    models = get_vendor_models_dir()
    if models.is_dir():
        os.environ.setdefault("HF_HOME", str(models))
        os.environ.setdefault("SENTENCE_TRANSFORMERS_HOME", str(models))
        os.environ.setdefault("HF_HUB_OFFLINE", "1")
        os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")

        bge = models / "BAAI" / "bge-small-zh-v1.5"
        if bge.is_dir():
            os.environ.setdefault("EMBEDDING_MODEL", str(bge))

        sentiment = (
            models
            / "lxyuan"
            / "distilbert-base-multilingual-cased-sentiments-student"
        )
        if sentiment.is_dir():
            os.environ.setdefault("SENTIMENT_MODEL", str(sentiment))
