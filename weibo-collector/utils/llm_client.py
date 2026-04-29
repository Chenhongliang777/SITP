"""
LLM 调用封装：供 T5/T7/T8/T10 复用。
默认兼容 OpenAI 风格接口（如 DeepSeek）。
"""

import json
import os
from dataclasses import dataclass
from typing import Dict, List, Optional

import requests
from dotenv import load_dotenv


load_dotenv()


@dataclass
class LLMConfig:
    api_key: str
    base_url: str = "https://api.deepseek.com/v1"
    model: str = "deepseek-chat"
    timeout: int = 30


class LLMClient:
    def __init__(self, config: Optional[LLMConfig] = None) -> None:
        if config is None:
            api_key = os.getenv("DEEPSEEK_API_KEY", "").strip()
            base_url = os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com/v1").strip()
            model = os.getenv("DEEPSEEK_MODEL", "deepseek-chat").strip()
            config = LLMConfig(api_key=api_key, base_url=base_url, model=model)
        self.config = config
        if not self.config.api_key:
            raise ValueError("缺少 DEEPSEEK_API_KEY，请在 .env 中配置")

    def chat(
        self,
        system_prompt: str,
        user_prompt: str,
        temperature: float = 0.2,
        max_tokens: int = 1024,
    ) -> str:
        url = f"{self.config.base_url.rstrip('/')}/chat/completions"
        payload = {
            "model": self.config.model,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
        }
        headers = {
            "Authorization": f"Bearer {self.config.api_key}",
            "Content-Type": "application/json",
        }
        resp = requests.post(url, headers=headers, data=json.dumps(payload), timeout=self.config.timeout)
        resp.raise_for_status()
        data = resp.json()
        return data["choices"][0]["message"]["content"]

    def classify(self, text: str, labels: List[str], instruction: str = "") -> Dict:
        label_str = "、".join(labels)
        system_prompt = "你是严格的中文分类器。仅输出 JSON，不要输出解释。"
        user_prompt = (
            f"{instruction}\n"
            f"待分类文本：{text}\n"
            f"可选标签：{label_str}\n"
            "输出格式：{\"label\":\"标签名\",\"confidence\":0-1,\"reason\":\"简短理由\"}"
        )
        raw = self.chat(system_prompt, user_prompt, temperature=0.0, max_tokens=300)
        try:
            return json.loads(raw)
        except Exception:
            return {"label": labels[0], "confidence": 0.5, "reason": f"解析失败，原始返回: {raw[:120]}"}
