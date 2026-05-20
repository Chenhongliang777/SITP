import argparse
import json
import os
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from dotenv import load_dotenv

from utils.llm_client import try_llm_client
from utils.runtime import get_llm_max_workers, get_sentiment_batch

from utils.project_root import get_project_root

SCRIPT_DIR = get_project_root()
PROJECT_DIR = SCRIPT_DIR
DATA_DIR = PROJECT_DIR / "data"
DATA_DIR.mkdir(exist_ok=True)

ENV_PATH = SCRIPT_DIR / ".env"
if ENV_PATH.exists():
    load_dotenv(dotenv_path=ENV_PATH)

LABELS = [
    "强烈负面",
    "轻微负面",
    "中性",
    "轻微正面",
    "强烈正面",
]

PRETRAINED_MODEL = os.getenv(
    "SENTIMENT_MODEL",
    "lxyuan/distilbert-base-multilingual-cased-sentiments-student",
)
MODEL_MAX_LENGTH = 512
INTENSITY_THRESHOLD = 0.70


def build_output_path(keyword: str):
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    return DATA_DIR / f"sentiment_{keyword}_{stamp}.json"


def load_pretrained_model():
    try:
        from transformers import pipeline
    except ImportError as exc:
        raise RuntimeError("缺少 transformers 依赖，请执行: pip install transformers") from exc

    print(f"正在加载预训练情感模型: {PRETRAINED_MODEL} ...")
    classifier = pipeline(
        "sentiment-analysis",
        model=PRETRAINED_MODEL,
        device=-1,
    )
    print("模型加载完成")
    return classifier


def _from_pretrained_raw(raw: Any) -> Dict[str, Any]:
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


def analyze_with_pretrained(model, text: str):
    return _from_pretrained_raw(
        model(text, truncation=True, max_length=MODEL_MAX_LENGTH)
    )


def prompt_llm_sentiment(text: str, client) -> Dict[str, Any] | None:
    if client is None:
        return None
    system_prompt = (
        "你是一个中文微博情感分析专家。请判断给定文本的情感倾向。"
        "只能从以下五个标签中选择一个：强烈负面、轻微负面、中性、轻微正面、强烈正面。"
        "只需输出 JSON，包含两个字段：sentiment（字符串，必须是上述五者之一）、"
        "confidence（0.0-1.0 浮点数，表示你对该判断的确信度）。"
    )
    parsed = client.chat_json(
        system_prompt,
        f"待分析文本：\n{text}\n",
        temperature=0.0,
        max_tokens=100,
        response_format={"type": "json_object"},
    )
    if not isinstance(parsed, dict):
        return None
    sentiment = parsed.get("sentiment", "中性")
    if sentiment not in LABELS:
        sentiment = "中性"
    try:
        confidence = max(0.0, min(1.0, float(parsed.get("confidence", 0.5))))
    except (TypeError, ValueError):
        confidence = 0.5
    return {
        "sentiment": sentiment,
        "sentiment_confidence": round(confidence, 4),
        "method": "llm",
    }


def default_fallback(text: str):
    return {
        "sentiment": "中性",
        "sentiment_confidence": 0.5,
        "method": "default_fallback",
    }


def run_sentiment(input_path: Path, *, no_llm_fallback: bool = False) -> Path:
    with open(input_path, "r", encoding="utf-8") as f:
        payload = json.load(f)

    keyword = payload.get("meta", {}).get("keyword") or input_path.stem
    posts: List[Dict] = payload.get("data", [])

    output_path = build_output_path(keyword)
    processed: List[Dict] = []

    model = None
    model_ok = False
    try:
        model = load_pretrained_model()
        model_ok = True
    except Exception as exc:
        print(f"预训练模型加载失败: {exc}")
        if no_llm_fallback:
            print("已禁用情感 LLM 回退，将全部使用默认中性兜底。")
        else:
            print("将使用 LLM 作为回退路径...")

    llm_when_no_model = (
        None if (model_ok or no_llm_fallback) else try_llm_client()
    )
    llm_on_row_failure = (
        None if (not model_ok or no_llm_fallback) else try_llm_client()
    )

    model_count = 0
    llm_count = 0
    default_count = 0

    texts = [(post.get("clean_text") or post.get("raw_text") or "").strip() for post in posts]

    if model_ok:
        bs = get_sentiment_batch()
        i = 0
        n = len(posts)
        while i < n:
            chunk_end = min(i + bs, n)
            batch_texts = texts[i:chunk_end]
            chunk_len = chunk_end - i
            try:
                raw_list = model(
                    batch_texts,
                    truncation=True,
                    max_length=MODEL_MAX_LENGTH,
                )
                if not isinstance(raw_list, list):
                    raw_list = [raw_list]
                if len(raw_list) != chunk_len:
                    raise ValueError("batch size mismatch")
                for j in range(chunk_len):
                    post = posts[i + j]
                    post.update(_from_pretrained_raw(raw_list[j]))
                    model_count += 1
                    processed.append(post)
            except Exception as exc:
                print(f"[{i + 1}-{chunk_end}] 批量推理失败，逐条回退: {exc}")
                for j in range(chunk_len):
                    idx = i + j + 1
                    text = texts[i + j]
                    post = posts[i + j]
                    try:
                        post.update(analyze_with_pretrained(model, text))
                        model_count += 1
                    except Exception as exc2:
                        if no_llm_fallback or llm_on_row_failure is None:
                            print(f"[{idx}] 模型单条推理失败，已禁用 LLM 回退: {exc2}")
                            post.update(default_fallback(text))
                            default_count += 1
                        else:
                            print(f"[{idx}] 模型单条推理失败，转 LLM: {exc2}")
                            result = prompt_llm_sentiment(text, llm_on_row_failure)
                            if result:
                                post.update(result)
                                llm_count += 1
                            else:
                                post.update(default_fallback(text))
                                default_count += 1
                    processed.append(post)
            i = chunk_end
            if i % 500 == 0 or i == n:
                print(f"  已处理 {i}/{n} 条...")
    else:
        if no_llm_fallback or llm_when_no_model is None:
            for j in range(len(posts)):
                post = posts[j]
                post.update(default_fallback(texts[j]))
                default_count += 1
                processed.append(post)
        else:

            def _llm_row(j: int) -> Tuple[int, Optional[Dict[str, Any]]]:
                r = prompt_llm_sentiment(texts[j], llm_when_no_model)
                return j, r

            workers = min(get_llm_max_workers(), max(1, len(posts)))
            with ThreadPoolExecutor(max_workers=workers) as pool:
                for j, result in pool.map(_llm_row, range(len(posts))):
                    post = posts[j]
                    if result:
                        post.update(result)
                        llm_count += 1
                    else:
                        post.update(default_fallback(texts[j]))
                        default_count += 1
                    processed.append(post)

    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(
            {
                "meta": {
                    "keyword": keyword,
                    "date_range": payload.get("meta", {}).get("date_range", ""),
                    "actual": len(processed),
                    "no_llm_fallback": bool(no_llm_fallback),
                },
                "data": processed,
            },
            f,
            ensure_ascii=False,
            indent=2,
        )

    total = len(processed)
    print(f"\n情感分析完成，共 {total} 条")
    if total:
        print(f"  预训练模型: {model_count} 条 ({model_count/total:.1%})")
        print(f"  LLM 回退:   {llm_count} 条 ({llm_count/total:.1%})")
        print(f"  默认兜底:   {default_count} 条 ({default_count/total:.1%})")
    print(f"结果已写入: {output_path}")

    return output_path


def main():
    parser = argparse.ArgumentParser(
        description="情感分析（预训练主路径 + LLM 回退，与旧版一致）"
    )
    parser.add_argument("--input", required=True, help="上游 filtered JSON 文件")
    parser.add_argument(
        "--no-llm-fallback",
        action="store_true",
        help="预训练模型失败或单条推理失败时不调用 LLM，使用默认中性兜底",
    )
    args = parser.parse_args()

    input_path = Path(args.input)
    if not input_path.exists():
        raise SystemExit(f"未找到输入文件: {input_path}")

    run_sentiment(input_path, no_llm_fallback=args.no_llm_fallback)


if __name__ == "__main__":
    main()
