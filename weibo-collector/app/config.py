"""应用配置加载。"""
from __future__ import annotations

import os
import sys

from app.llm_settings import apply_settings_to_os_environ, load_settings
from app.paths import apply_runtime_env, ensure_app_layout, get_env_path


def bootstrap() -> bool:
    """GUI / Web 启动时调用：目录、路径环境变量 + .env。返回是否首次生成 .env。"""
    created_env = ensure_app_layout()
    os.environ.setdefault("PYTHONIOENCODING", "utf-8")
    os.environ.setdefault("PYTHONUTF8", "1")
    os.environ.setdefault("PYTHONUNBUFFERED", "1")
    if sys.platform == "win32":
        os.environ.setdefault("PYTHONLEGACYWINDOWSSTDIO", "utf-8")
    apply_runtime_env()
    if get_env_path().exists():
        apply_settings_to_os_environ(load_settings())
    return created_env


def require_api_key() -> str:
    settings = load_settings()
    if not settings.has_api_key:
        raise RuntimeError(
            "未配置 API Key。请在「设置」页填写大模型 API Key 后保存。"
        )
    apply_settings_to_os_environ(settings)
    return settings.api_key
