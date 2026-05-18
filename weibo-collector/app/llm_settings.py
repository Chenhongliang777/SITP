"""LLM 配置：预设 + 自定义，写入 .env。"""
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Dict, List, Optional

from dotenv import load_dotenv, set_key

from app.paths import get_env_path


@dataclass(frozen=True)
class LLMPreset:
    id: str
    label: str
    base_url: str
    model: str


PRESETS: Dict[str, LLMPreset] = {
    "deepseek": LLMPreset(
        id="deepseek",
        label="DeepSeek（默认）",
        base_url="https://api.deepseek.com/v1",
        model="deepseek-chat",
    ),
    "custom": LLMPreset(
        id="custom",
        label="自定义（OpenAI 兼容接口）",
        base_url="",
        model="",
    ),
}


def list_presets() -> List[LLMPreset]:
    return list(PRESETS.values())


@dataclass
class LLMSettings:
    preset_id: str
    api_key: str
    base_url: str
    model: str

    @property
    def has_api_key(self) -> bool:
        return bool((self.api_key or "").strip())


def load_settings() -> LLMSettings:
    env_path = get_env_path()
    if env_path.exists():
        load_dotenv(dotenv_path=env_path, override=True)

    api_key = os.getenv("DEEPSEEK_API_KEY", "").strip()
    base_url = (
        os.getenv("DEEPSEEK_BASE_URL", "").strip()
        or os.getenv("DEEPSEEK_API_URL", "").strip()
    )
    model = os.getenv("DEEPSEEK_MODEL", "deepseek-chat").strip()
    preset_id = os.getenv("LLM_PRESET_ID", "deepseek").strip() or "deepseek"

    if not base_url:
        base_url = PRESETS["deepseek"].base_url
    if not model:
        model = PRESETS["deepseek"].model

    return LLMSettings(
        preset_id=preset_id,
        api_key=api_key,
        base_url=base_url,
        model=model,
    )


def save_settings(
    preset_id: str,
    api_key: str,
    *,
    base_url: Optional[str] = None,
    model: Optional[str] = None,
) -> LLMSettings:
    preset = PRESETS.get(preset_id, PRESETS["deepseek"])
    if preset_id == "deepseek":
        resolved_base = PRESETS["deepseek"].base_url
        resolved_model = PRESETS["deepseek"].model
    else:
        resolved_base = (base_url or "").strip() or preset.base_url
        resolved_model = (model or "").strip() or preset.model
        if not resolved_base or not resolved_model:
            raise ValueError("自定义预设须填写 Base URL 与 Model 名称")

    env_path = get_env_path()
    if not env_path.exists():
        env_path.write_text("", encoding="utf-8")

    set_key(str(env_path), "LLM_PRESET_ID", preset_id)
    set_key(str(env_path), "DEEPSEEK_API_KEY", api_key.strip())
    set_key(str(env_path), "DEEPSEEK_BASE_URL", resolved_base)
    set_key(str(env_path), "DEEPSEEK_MODEL", resolved_model)
    # 避免旧版完整 URL 与 BASE_URL 冲突
    set_key(str(env_path), "DEEPSEEK_API_URL", "")

    load_dotenv(dotenv_path=env_path, override=True)
    return load_settings()


def apply_settings_to_os_environ(settings: Optional[LLMSettings] = None) -> None:
    s = settings or load_settings()
    os.environ["LLM_PRESET_ID"] = s.preset_id
    os.environ["DEEPSEEK_API_KEY"] = s.api_key
    os.environ["DEEPSEEK_BASE_URL"] = s.base_url
    os.environ["DEEPSEEK_MODEL"] = s.model
