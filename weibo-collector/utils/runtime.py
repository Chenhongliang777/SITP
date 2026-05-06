"""运行时可调参数（环境变量 + launcher 注入）。"""

import os


def get_llm_max_workers() -> int:
    """顶层 LLM 批请求并发数（ABSA / 风险扫描等多批并行；单批内仍顺序递归）。"""
    try:
        return max(1, min(32, int(os.getenv("LLM_MAX_WORKERS", "6"))))
    except ValueError:
        return 6


def get_semantic_encode_batch() -> int:
    try:
        return max(8, min(256, int(os.getenv("SEMANTIC_ENCODE_BATCH", "48"))))
    except ValueError:
        return 48


def get_sentiment_batch() -> int:
    try:
        return max(1, min(64, int(os.getenv("SENTIMENT_BATCH", "24"))))
    except ValueError:
        return 24


def get_llm_batch_size() -> int:
    """ABSA / 风险扫描等「一批多条」共用的 LLM 批量大小（单请求内条数）。"""
    try:
        return max(2, min(24, int(os.getenv("LLM_BATCH_SIZE", "10"))))
    except ValueError:
        return 10
