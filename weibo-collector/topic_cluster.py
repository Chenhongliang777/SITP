import argparse
import json
import os
import re
from datetime import datetime
from pathlib import Path

import requests
from dotenv import load_dotenv

SCRIPT_DIR = Path(__file__).parent.resolve()
DATA_DIR = SCRIPT_DIR / "data"
DATA_DIR.mkdir(exist_ok=True)

ENV_PATH = SCRIPT_DIR / ".env"
if ENV_PATH.exists():
    load_dotenv(dotenv_path=ENV_PATH)

DS_KEY = os.getenv("DEEPSEEK_API_KEY")
DS_URL = os.getenv("DEEPSEEK_API_URL", "https://api.deepseek.com/chat/completions")


def build_output_path(keyword: str):
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    return DATA_DIR / f"topic_{keyword}_{stamp}.json"


def build_keyword_label(vectorizer, kmeans, cluster_id):
    """LLM 不可用或输出违规时的 fallback：取 TF-IDF 前 2 个词拼接"""
    terms = (
        vectorizer.get_feature_names_out()
        if hasattr(vectorizer, "get_feature_names_out")
        else vectorizer.get_feature_names()
    )
    centroid = kmeans.cluster_centers_[cluster_id]
    top_indices = centroid.argsort()[::-1][:2]
    label = "".join(terms[i] for i in top_indices if i < len(terms))
    return label[:6] or f"主题{cluster_id + 1}"


def sanitize_label(raw: str) -> str | None:
    """
    清洗 LLM 输出：
    1. 若包含顿号、逗号、斜杠，说明是列举式，只取第一个子串
    2. 去掉引号、空格
    3. 长度超过 8 字截断
    4. 空串返回 None（触发 fallback）
    """
    if not raw:
        return None

    # 去掉常见标点
    cleaned = raw.strip().replace('"', "").replace("'", "").replace("“", "").replace("”", "")
    # 若出现列举分隔符，只取第一个主题
    for sep in ["、", "，", ",", "/", "；", ";", "和", "与"]:
        if sep in cleaned:
            cleaned = cleaned.split(sep)[0].strip()
            break
    # 截断到 8 个字
    cleaned = cleaned[:8]
    # 若清洗后为空或只剩"主题"等无意义词，返回 None
    if len(cleaned) < 2 or cleaned.startswith("主题"):
        return None
    return cleaned


def prompt_llm_topic(samples: list[str]):
    """调用 LLM 为主题生成单一短标签"""
    if not DS_KEY or not samples:
        return None

    display_samples = samples[:5]
    content = "\n".join(f"{i+1}. {t}" for i, t in enumerate(display_samples))

    system_prompt = (
        "你是一个微博舆情主题分类专家。请根据以下微博内容，"
        "生成一个极简的中文主题标签，要求：\n"
        "1. 只能是一个单一主题，如'裁判争议'、'球员表现'、'球迷文化'\n"
        "2. 字数严格控制在 4~6 个字，绝对不要超过 6 个字\n"
        "3. 禁止用顿号、逗号列举多个主题\n"
        "4. 只输出标签文本本身，不要有任何解释、引号或多余内容\n"
        "错误示例：球队战术、主场氛围、球员表现\n"
        "正确示例：裁判争议 / 球员表现 / 球迷文化 / 赛程讨论"
    )

    try:
        resp = requests.post(
            DS_URL,
            headers={"Authorization": f"Bearer {DS_KEY}", "Content-Type": "application/json"},
            json={
                "model": "deepseek-chat",
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": f"微博内容：\n{content}\n\n主题标签："},
                ],
                "temperature": 0.2,
                "max_tokens": 15,
            },
            timeout=20,
        )
        resp.raise_for_status()
        data = resp.json()
        raw_label = data.get("choices", [{}])[0].get("message", {}).get("content", "").strip()
        return sanitize_label(raw_label)
    except Exception:
        return None


def main():
    parser = argparse.ArgumentParser(description="主题聚类与 LLM 标签生成脚本")
    parser.add_argument("--input", required=True, help="上游 sentiment JSON 文件")
    args = parser.parse_args()

    input_path = Path(args.input)
    if not input_path.exists():
        raise SystemExit(f"未找到输入文件: {input_path}")

    with open(input_path, "r", encoding="utf-8") as f:
        payload = json.load(f)

    keyword = payload.get("meta", {}).get("keyword") or input_path.stem
    posts = payload.get("data", [])

    texts = [(p.get("clean_text") or p.get("raw_text") or "").strip() for p in posts]
    if not texts:
        raise SystemExit("输入文件中没有有效文本")

    n_posts = len(texts)

    # ---------- 聚类数量：严格 5~8 簇 ----------
    if n_posts >= 40:
        n_clusters = 8
    elif n_posts >= 25:
        n_clusters = 7
    elif n_posts >= 15:
        n_clusters = 5
    else:
        n_clusters = min(5, max(1, n_posts))

    try:
        from sklearn.feature_extraction.text import TfidfVectorizer
        from sklearn.cluster import KMeans
    except ImportError as exc:
        raise SystemExit("缺少 sklearn 依赖，请先安装 requirements.txt") from exc

    vectorizer = TfidfVectorizer(max_features=1000, ngram_range=(1, 2), stop_words=None)
    X = vectorizer.fit_transform(texts)

    kmeans = KMeans(n_clusters=n_clusters, random_state=42, n_init=10)
    cluster_ids = kmeans.fit_predict(X)

    # ---------- 按簇聚合 ----------
    cluster_texts = {i: [] for i in range(n_clusters)}
    for text, cid in zip(texts, cluster_ids):
        cluster_texts[cid].append(text)

    # ---------- 生成标签 ----------
    label_map = {}
    llm_called = 0
    llm_failed = 0

    for cid in range(n_clusters):
        samples = cluster_texts[cid]
        if not samples:
            label_map[cid] = f"主题{cid + 1}"
            continue

        label = None
        if DS_KEY:
            llm_called += 1
            label = prompt_llm_topic(samples)

        if not label:
            llm_failed += 1
            label = build_keyword_label(vectorizer, kmeans, cid)

        label_map[cid] = label

    # ---------- 写回记录 ----------
    for post, cid in zip(posts, cluster_ids):
        post["topic_id"] = int(cid)
        post["topic_label"] = label_map.get(cid, f"主题{cid + 1}")

    output_path = build_output_path(keyword)
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(
            {
                "meta": {
                    "keyword": keyword,
                    "date_range": payload.get("meta", {}).get("date_range", ""),
                    "actual": n_posts,
                    "topic_count": n_clusters,
                    "llm_called": llm_called,
                    "llm_failed": llm_failed,
                },
        "data": posts,
            },
            f,
            ensure_ascii=False,
            indent=2,
        )

    unique_labels = set(label_map.values())
    print(f"聚类完成，生成 {n_clusters} 个主题，{len(unique_labels)} 个不同标签。")
    if llm_called:
        print(f"LLM 标签生成: {llm_called} 次，失败/清洗回退: {llm_failed} 次。")
    print(f"结果已写入: {output_path}")


if __name__ == "__main__":
    main()