"""
Embedding 封装：优先调远端 embedding API，失败时回退本地哈希向量。
"""

import hashlib
import json
import math
import os
from typing import List, Optional

import requests
from dotenv import load_dotenv


load_dotenv()


class Embedder:
    def __init__(
        self,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        model: Optional[str] = None,
        dim: int = 256,
    ) -> None:
        self.api_key = (api_key or os.getenv("DEEPSEEK_API_KEY", "")).strip()
        self.base_url = (base_url or os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com/v1")).strip()
        self.model = (model or os.getenv("EMBEDDING_MODEL", "text-embedding-3-small")).strip()
        self.dim = dim

    def embed_text(self, text: str) -> List[float]:
        text = (text or "").strip()
        if not text:
            return [0.0] * self.dim
        if self.api_key:
            vec = self._embed_remote(text)
            if vec:
                return vec
        return self._embed_local_hash(text)

    def embed_batch(self, texts: List[str]) -> List[List[float]]:
        return [self.embed_text(t) for t in texts]

    def cosine_similarity(self, a: List[float], b: List[float]) -> float:
        if not a or not b or len(a) != len(b):
            return 0.0
        dot = sum(x * y for x, y in zip(a, b))
        na = math.sqrt(sum(x * x for x in a))
        nb = math.sqrt(sum(y * y for y in b))
        if na == 0 or nb == 0:
            return 0.0
        return dot / (na * nb)

    def _embed_remote(self, text: str) -> Optional[List[float]]:
        url = f"{self.base_url.rstrip('/')}/embeddings"
        payload = {"model": self.model, "input": text}
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        try:
            resp = requests.post(url, headers=headers, data=json.dumps(payload), timeout=20)
            resp.raise_for_status()
            vec = resp.json()["data"][0]["embedding"]
            return vec
        except Exception:
            return None

    def _embed_local_hash(self, text: str) -> List[float]:
        digest = hashlib.sha256(text.encode("utf-8")).digest()
        vals = [((digest[i % len(digest)] / 255.0) * 2.0 - 1.0) for i in range(self.dim)]
        norm = math.sqrt(sum(v * v for v in vals))
        if norm == 0:
            return vals
        return [v / norm for v in vals]
