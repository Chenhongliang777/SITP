"""
LLM 调用封装：供语义过滤、情感、主题、ABSA、风险、报告等模块复用。
默认兼容 OpenAI 风格接口（如 DeepSeek）。
支持 DEEPSEEK_BASE_URL 或完整的 DEEPSEEK_API_URL（以 /chat/completions 结尾）。
"""

import json
import os
import threading
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

import requests
from dotenv import load_dotenv


load_dotenv()


def _normalize_base_url(url: str) -> str:
    u = (url or "").strip().rstrip("/")
    if not u:
        return "https://api.deepseek.com/v1"
    if u.endswith("/chat/completions"):
        u = u[: -len("/chat/completions")]
    return u.rstrip("/") or "https://api.deepseek.com/v1"


def _load_llm_config_from_env() -> "LLMConfig":
    api_key = os.getenv("DEEPSEEK_API_KEY", "").strip()
    api_full = os.getenv("DEEPSEEK_API_URL", "").strip()
    base = os.getenv("DEEPSEEK_BASE_URL", "").strip()
    if base:
        base_url = _normalize_base_url(base)
    elif api_full:
        base_url = _normalize_base_url(api_full)
    else:
        base_url = "https://api.deepseek.com/v1"
    model = os.getenv("DEEPSEEK_MODEL", "deepseek-chat").strip()
    timeout = int(os.getenv("DEEPSEEK_TIMEOUT", "30"))
    return LLMConfig(api_key=api_key, base_url=base_url, model=model, timeout=timeout)


@dataclass
class LLMConfig:
    api_key: str
    base_url: str = "https://api.deepseek.com/v1"
    model: str = "deepseek-chat"
    timeout: int = 30


class LLMClient:
    def __init__(self, config: Optional[LLMConfig] = None) -> None:
        self.config = config or _load_llm_config_from_env()
        if not self.config.api_key:
            raise ValueError("缺少 DEEPSEEK_API_KEY，请在 .env 中配置")
        # 每线程独立 Session，便于多线程并发时复用连接且避免共享 Session 竞态
        self._thread_local = threading.local()

    def _session(self) -> requests.Session:
        s = getattr(self._thread_local, "session", None)
        if s is None:
            s = requests.Session()
            self._thread_local.session = s
        return s

    def chat(
        self,
        system_prompt: str,
        user_prompt: str,
        temperature: float = 0.2,
        max_tokens: int = 1024,
        response_format: Optional[Dict[str, str]] = None,
    ) -> str:
        url = f"{self.config.base_url.rstrip('/')}/chat/completions"
        payload: Dict[str, Any] = {
            "model": self.config.model,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
        }
        if response_format is not None:
            payload["response_format"] = response_format
        headers = {
            "Authorization": f"Bearer {self.config.api_key}",
            "Content-Type": "application/json",
        }
        resp = self._session().post(url, headers=headers, data=json.dumps(payload), timeout=self.config.timeout)
        resp.raise_for_status()
        data = resp.json()
        return data["choices"][0]["message"]["content"]

    def chat_json(
        self,
        system_prompt: str,
        user_prompt: str,
        temperature: float = 0.0,
        max_tokens: int = 512,
        response_format: Optional[Dict[str, str]] = None,
    ) -> Optional[Any]:
        fmt = response_format if response_format is not None else {"type": "json_object"}
        try:
            raw = self.chat(system_prompt, user_prompt, temperature, max_tokens, response_format=fmt)
        except Exception:
            return None
        if not (raw or "").strip():
            return None
        try:
            return json.loads(raw)
        except Exception:
            return None

    def chat_text(
        self,
        system_prompt: str,
        user_prompt: str,
        temperature: float = 0.2,
        max_tokens: int = 128,
    ) -> str:
        try:
            return self.chat(system_prompt, user_prompt, temperature, max_tokens).strip()
        except Exception:
            return ""

    def classify(self, text: str, labels: List[str], instruction: str = "") -> Dict:
        label_str = "、".join(labels)
        system_prompt = "你是严格的中文分类器。仅输出 JSON，不要输出解释。"
        user_prompt = (
            f"{instruction}\n"
            f"待分类文本：{text}\n"
            f"可选标签：{label_str}\n"
            '输出格式：{"label":"标签名","confidence":0-1,"reason":"简短理由"}'
        )
        parsed = self.chat_json(system_prompt, user_prompt, temperature=0.0, max_tokens=300)
        if isinstance(parsed, dict):
            return parsed
        return {"label": labels[0], "confidence": 0.5, "reason": "解析失败"}


def try_llm_client() -> Optional[LLMClient]:
    """无 Key 时返回 None，便于调用方走规则路径。"""
    try:
        return LLMClient()
    except ValueError:
        return None
