import argparse
import json
import os
import sys
from datetime import datetime
from pathlib import Path

import requests
from dotenv import load_dotenv

SCRIPT_DIR = Path(__file__).parent.resolve()
PROJECT_DIR = SCRIPT_DIR
DATA_DIR = PROJECT_DIR / "data"
DATA_DIR.mkdir(exist_ok=True)

ENV_PATH = SCRIPT_DIR / ".env"
if ENV_PATH.exists():
    load_dotenv(dotenv_path=ENV_PATH)

DS_KEY = os.getenv("DEEPSEEK_API_KEY")
DS_URL = os.getenv("DEEPSEEK_API_URL", "https://api.deepseek.com/chat/completions")

LABELS = [
    "强烈负面",
    "轻微负面",
    "中性",
    "轻微正面",
    "强烈正面",
]

# ---------- 预训练情感模型配置（零样本，无需用户标注数据） ----------
PRETRAINED_MODEL = "lxyuan/distilbert-base-multilingual-cased-sentiments-student"
INTENSITY_THRESHOLD = 0.70   # 区分强烈/轻微的置信度阈值


def build_output_path(keyword: str):
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    return DATA_DIR / f"sentiment_{keyword}_{stamp}.json"


def load_pretrained_model():
    """加载预训练多语言情感模型（零样本，开箱即用）"""
    try:
        from transformers import pipeline
    except ImportError as exc:
        raise RuntimeError("缺少 transformers 依赖，请执行: pip install transformers") from exc

    print(f"正在加载预训练情感模型: {PRETRAINED_MODEL} ...")
    classifier = pipeline(
        "sentiment-analysis",
        model=PRETRAINED_MODEL,
        device=-1,   # CPU 运行；如有 GPU 可改为 0
    )
    print("模型加载完成")
    return classifier


def analyze_with_pretrained(model, text: str):
    """预训练模型推理：3 类输出映射为任务要求的 5 类标签"""
    raw = model(text)
    if isinstance(raw, list) and len(raw) > 0:
        raw = raw[0]

    label = raw["label"].lower()
    score = float(raw["score"])

    if label == "negative":
        sentiment = "强烈负面" if score >= INTENSITY_THRESHOLD else "轻微负面"
    elif label == "positive":
        sentiment = "强烈正面" if score >= INTENSITY_THRESHOLD else "轻微正面"
    else:
        sentiment = "中性"

    return {
        "sentiment": sentiment,
        "sentiment_confidence": round(score, 4),
        "method": "pretrained_model",
    }


def prompt_llm_sentiment(text: str):
    """LLM 回退：DeepSeek 做情感判定"""
    if not DS_KEY:
        return None

    system_prompt = (
        "你是一个中文微博情感分析专家。请判断给定文本的情感倾向。"
        "只能从以下五个标签中选择一个：强烈负面、轻微负面、中性、轻微正面、强烈正面。"
        "只需输出 JSON，包含两个字段：sentiment（字符串，必须是上述五者之一）、"
        "confidence（0.0-1.0 浮点数，表示你对该判断的确信度）。"
    )
    try:
        resp = requests.post(
            DS_URL,
            headers={"Authorization": f"Bearer {DS_KEY}", "Content-Type": "application/json"},
            json={
                "model": "deepseek-chat",
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": f"待分析文本：\n{text}\n"},
                ],
                "temperature": 0.0,
                "max_tokens": 100,
                "response_format": {"type": "json_object"},
            },
            timeout=20,
        )
        resp.raise_for_status()
        data = resp.json()
        content = data.get("choices", [{}])[0].get("message", {}).get("content", "")
        if not content:
            return None
        result = json.loads(content)
        sentiment = result.get("sentiment", "中性")
        # 严格限定在候选标签内，防止模型胡编
        if sentiment not in LABELS:
            sentiment = "中性"
        confidence = max(0.0, min(1.0, float(result.get("confidence", 0.5))))
        return {
            "sentiment": sentiment,
            "sentiment_confidence": round(confidence, 4),
            "method": "llm",
        }
    except Exception:
        return None


def default_fallback(text: str):
    """终极兜底：预训练模型 + LLM 全部失败时，保守返回中性"""
    return {
        "sentiment": "中性",
        "sentiment_confidence": 0.5,
        "method": "default_fallback",
    }


def main():
    parser = argparse.ArgumentParser(
        description="情感分析模型化脚本（预训练零样本模型主路径 + LLM 回退，无词典法）"
    )
    parser.add_argument("--input", required=True, help="上游 filtered JSON 文件")
    args = parser.parse_args()

    input_path = Path(args.input)
    if not input_path.exists():
        raise SystemExit(f"未找到输入文件: {input_path}")

    with open(input_path, "r", encoding="utf-8") as f:
        payload = json.load(f)

    keyword = payload.get("meta", {}).get("keyword") or input_path.stem
    posts = payload.get("data", [])

    output_path = build_output_path(keyword)
    processed = []

    # 尝试加载预训练模型
    model = None
    model_ok = False
    try:
        model = load_pretrained_model()
        model_ok = True
    except Exception as exc:
        print(f"预训练模型加载失败: {exc}")
        print("将使用 LLM 作为回退路径...")

    model_count = 0
    llm_count = 0
    default_count = 0

    for idx, post in enumerate(posts, 1):
        text = (post.get("clean_text") or post.get("raw_text") or "").strip()
        result = None

        # 1. 主路径：预训练模型
        if model_ok:
            try:
                result = analyze_with_pretrained(model, text)
                model_count += 1
            except Exception as exc:
                # 单条推理异常，进入 LLM 回退
                print(f"[{idx}] 模型单条推理失败，转 LLM: {exc}")
                result = prompt_llm_sentiment(text)
                if result:
                    llm_count += 1
                else:
                    result = default_fallback(text)
                    default_count += 1
        else:
            # 2. 模型未加载成功，直接走 LLM
            result = prompt_llm_sentiment(text)
            if result:
                llm_count += 1
            else:
                result = default_fallback(text)
                default_count += 1

        post["sentiment"] = result["sentiment"]
        post["sentiment_confidence"] = result["sentiment_confidence"]
        post["method"] = result["method"]
        processed.append(post)

        if idx % 500 == 0:
            print(f"  已处理 {idx}/{len(posts)} 条...")

    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(
            {
                "meta": {
                    "keyword": keyword,
                    "date_range": payload.get("meta", {}).get("date_range", ""),
                    "actual": len(processed),
                },
                "data": processed,
            },
            f,
            ensure_ascii=False,
            indent=2,
        )

    total = len(processed)
    print(f"\n情感分析完成，共 {total} 条")
    print(f"  预训练模型: {model_count} 条 ({model_count/total:.1%})")
    print(f"  LLM 回退:   {llm_count} 条 ({llm_count/total:.1%})")
    print(f"  默认兜底:   {default_count} 条 ({default_count/total:.1%})")
    print(f"结果已写入: {output_path}")


if __name__ == "__main__":
    main()