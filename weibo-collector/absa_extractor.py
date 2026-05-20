import argparse
import json
import re
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from utils.llm_client import try_llm_client
from utils.runtime import get_llm_batch_size, get_llm_max_workers

from utils.project_root import get_project_root

SCRIPT_DIR = get_project_root()
DATA_DIR = SCRIPT_DIR / "data"
DATA_DIR.mkdir(exist_ok=True)

ASPECT_KEYWORDS = [
    "裁判", "VAR", "球员", "球队", "教练", "门将", "俱乐部", "赛程", "青训",
    "中超", "国足", "主教练", "球迷", "转会", "战术", "比赛", "足协", "德比",
    "草皮", "转播", "镜头", "进攻", "防守", "反击", "点球", "红牌", "角球",
    "任意球", "越位", "补时", "换人", "阵容", "体能", "跑动", "对抗", "过人",
    "射正", "进球", "丢球", "零封", "绝杀", "绝平", "主场", "客场", "氛围",
]

NEGATIVE_INDICATORS = [
    "烂", "差", "垃圾", "黑哨", "假球", "输", "糟糕", "不行", "崩盘", "失误",
    "错判", "漏判", "疑似", "黑", "憋屈", "恶心", "愤怒", "失望", "遗憾", "离谱",
    "过分", "冤", "惨", "弱", "菜", "水", "拉胯", "无语", "臭", "烂", "差劲",
    "失败", "惨败", "崩盘", "失衡", "失控", "混乱", "低迷", "下滑", "受伤",
    "不公", "不满", "抗议", "申诉", "追责", "谴责", "批评", "吐槽", "骂",
    "急", "气", "崩", "炸", "爆", "凉", "惨", "疼", "坑", "糊", "废",
]

POSITIVE_INDICATORS = [
    "好", "棒", "优秀", "精彩", "稳", "给力", "出色", "漂亮", "胜利", "晋级",
    "绝杀", "加油", "完美", "顶级", "牛", "强", "神", "猛", "炸裂", "顶", "赞",
    "燃", "热血", "感动", "惊喜", "惊艳", "流畅", "高效", "关键", "满分",
    "无敌", "神勇", "亮眼", "回升", "提升", "突破", "成功", "夺冠", "零封",
    "好评", "点赞", "值得", "舒服", "享受", "专业", "及时", "到位", "靠谱",
]

DENIAL_WORDS = ["不", "没", "无", "别", "未", "不要", "不够", "不太", "不是很", "并没有"]


def build_output_path(keyword: str):
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    return DATA_DIR / f"absa_{keyword}_{stamp}.json"


def simple_sentiment(text: str):
    text = text or ""
    neg_count = sum(text.count(w) for w in NEGATIVE_INDICATORS)
    pos_count = sum(text.count(w) for w in POSITIVE_INDICATORS)

    denial = any(w in text for w in DENIAL_WORDS)
    if denial:
        if pos_count > 0:
            pos_count *= 0.3

    if neg_count > pos_count:
        return "negative"
    if pos_count > neg_count:
        return "positive"
    return "neutral"


def extract_aspects_rule(text: str):
    aspects = []
    if not text:
        return aspects

    try:
        import jieba.posseg as pseg
        for word, flag in pseg.lcut(text):
            if flag == "nr" and len(word) >= 2:
                aspects.append({"target": word, "sentiment": simple_sentiment(text)})
    except Exception:
        pass

    sentences = re.split(r'[。！？!?\n]', text)
    for sentence in sentences:
        sentence = sentence.strip()
        if not sentence:
            continue
        for keyword in ASPECT_KEYWORDS:
            if keyword in sentence:
                sentiment = simple_sentiment(sentence)
                aspects.append({"target": keyword, "sentiment": sentiment})

    unique = []
    seen = set()
    for item in aspects:
        key = (item["target"], item["sentiment"])
        if key not in seen:
            seen.add(key)
            unique.append({"target": item["target"], "sentiment": item["sentiment"]})
    return unique


def _normalize_aspect_sentiment(s: str) -> str:
    s = (s or "").lower().strip()
    if s in ("positive", "pos", "正面"):
        return "positive"
    if s in ("negative", "neg", "负面"):
        return "negative"
    return "neutral"


def _sanitize_aspects(raw: Any) -> List[Dict[str, Any]]:
    if not isinstance(raw, list):
        return []
    out: List[Dict[str, Any]] = []
    for it in raw:
        if not isinstance(it, dict):
            continue
        t = str(it.get("target", "")).strip()
        if not t:
            continue
        out.append({"target": t[:80], "sentiment": _normalize_aspect_sentiment(str(it.get("sentiment", "neutral")))})
    return out


def extract_aspects_llm(text: str, client) -> Optional[List[Dict[str, Any]]]:
    system_prompt = (
        "你是一个中文足球微博方面级情感分析(ABSA)专家。"
        "请从给定文本中提取所有'评价对象(target)+情感(sentiment)'对。"
        "要求：\n"
        "1. target 可以是具体人名、角色、球队、赛事元素、制度等，尽量具体（如'武磊'优于'球员'）\n"
        "2. sentiment 只能从 [positive, negative, neutral] 中选\n"
        "3. 遇到'但''然而''不过'等转折，要分别提取前后不同对象的独立情感\n"
        "4. 输出必须是合法 JSON 数组，每个元素包含 target 和 sentiment 字段，不要任何解释\n"
        "示例：\n"
        '文本：武磊今天状态真差，但裁判那个点球判得也太黑了\n'
        '输出：[{"target": "武磊", "sentiment": "negative"}, {"target": "裁判", "sentiment": "negative"}]'
    )

    parsed = client.chat_json(
        system_prompt,
        f"文本：{text}\n输出：",
        temperature=0.0,
        max_tokens=200,
        response_format={"type": "json_object"},
    )
    if parsed is None:
        return None

    if isinstance(parsed, list):
        return _sanitize_aspects(parsed)
    if isinstance(parsed, dict):
        for k in ["aspects", "result", "data", "items"]:
            if k in parsed and isinstance(parsed[k], list):
                return _sanitize_aspects(parsed[k])
        for v in parsed.values():
            if isinstance(v, list):
                return _sanitize_aspects(v)
    return None


def extract_aspects_llm_batch(batch: List[Tuple[int, str]], client, max_text_len: int = 380) -> Optional[Dict[int, List[Dict[str, Any]]]]:
    """
    一批多条共一次 LLM。输入为 (全局下标, 文本) 列表；成功返回 {下标: aspects 列表}；失败返回 None。
    """
    if not batch:
        return {}
    lines = []
    for i, t in batch:
        t2 = (t or "")[:max_text_len].replace("\n", " ").replace("\r", "")
        lines.append(f"[{i}] {t2}")
    block = "\n".join(lines)
    system_prompt = (
        "你是中文足球微博 ABSA 专家。输入多行，每行格式为「[整数id] 微博正文」。"
        "请为每个 id 分别抽取方面级情感。输出一个 JSON 对象，仅含字段 results："
        "results 为数组，每项必须含 id（与输入相同的整数）和 aspects（数组，"
        "元素为 {target, sentiment}，sentiment 只能是 positive / negative / neutral）。"
        "某条无方面可 aspects 为空数组。必须覆盖输入中的每一个 id，不要遗漏。"
    )
    max_tokens = min(4096, 120 + 90 * len(batch))
    parsed = client.chat_json(
        system_prompt,
        f"待分析：\n{block}\n",
        temperature=0.0,
        max_tokens=max_tokens,
        response_format={"type": "json_object"},
    )
    if not isinstance(parsed, dict):
        return None
    arr = parsed.get("results")
    if not isinstance(arr, list):
        return None
    out: Dict[int, List[Dict[str, Any]]] = {}
    for item in arr:
        if not isinstance(item, dict):
            continue
        try:
            idx = int(item.get("id"))
        except (TypeError, ValueError):
            continue
        aspects = _sanitize_aspects(item.get("aspects"))
        out[idx] = aspects
    expected = {i for i, _ in batch}
    if expected != set(out.keys()):
        return None
    return out


def _absa_batch_recursive(
    batch: List[Tuple[int, str]], client, depth: int
) -> Dict[int, List[Dict[str, Any]]]:
    """批量失败则二分；单条走原 extract_aspects_llm；仍失败用规则。"""
    if not batch:
        return {}
    if len(batch) == 1:
        idx, text = batch[0]
        llm = extract_aspects_llm(text, client)
        if llm is not None:
            return {idx: llm}
        return {idx: extract_aspects_rule(text)}

    parsed = extract_aspects_llm_batch(batch, client)
    if parsed is not None:
        return parsed

    if depth >= 7:
        return {i: extract_aspects_rule(t) for i, t in batch}

    mid = max(1, len(batch) // 2)
    a = _absa_batch_recursive(batch[:mid], client, depth + 1)
    b = _absa_batch_recursive(batch[mid:], client, depth + 1)
    return {**a, **b}


def run_absa(input_path: Path, use_llm: bool) -> Path:
    with open(input_path, "r", encoding="utf-8") as f:
        payload = json.load(f)

    keyword = payload.get("meta", {}).get("keyword") or input_path.stem
    posts = payload.get("data", [])

    llm_client = try_llm_client() if use_llm else None

    texts = [(post.get("clean_text") or post.get("raw_text") or "").strip() for post in posts]
    llm_batch_requests = 0

    if use_llm and llm_client is not None:
        bs = get_llm_batch_size()
        chunks: List[List[Tuple[int, str]]] = []
        for start in range(0, len(posts), bs):
            chunk = [(start + j, texts[start + j]) for j in range(min(bs, len(posts) - start))]
            chunks.append(chunk)
        llm_batch_requests = len(chunks)

        def _process_absa_chunk(ch: List[Tuple[int, str]]) -> Dict[int, List[Dict[str, Any]]]:
            return _absa_batch_recursive(ch, llm_client, 0)

        workers = min(get_llm_max_workers(), max(1, len(chunks)))
        merged: Dict[int, List[Dict[str, Any]]] = {}
        with ThreadPoolExecutor(max_workers=workers) as ex:
            for part in ex.map(_process_absa_chunk, chunks):
                merged.update(part)

        for idx in range(len(posts)):
            asp = merged.get(idx)
            if asp is None:
                posts[idx]["aspect_sentiments"] = extract_aspects_rule(texts[idx])
            else:
                posts[idx]["aspect_sentiments"] = asp
    else:
        for i, post in enumerate(posts):
            post["aspect_sentiments"] = extract_aspects_rule(texts[i])

    output_path = build_output_path(keyword)
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(
            {
                "meta": {
                    "keyword": keyword,
                    "date_range": payload.get("meta", {}).get("date_range", ""),
                    "actual": len(posts),
                    "llm_enabled": bool(use_llm and llm_client is not None),
                    "llm_batch_size": get_llm_batch_size() if use_llm and llm_client else None,
                    "llm_max_workers": get_llm_max_workers() if use_llm and llm_client else None,
                    "llm_batch_requests": llm_batch_requests if use_llm and llm_client else 0,
                },
                "data": posts,
            },
            f,
            ensure_ascii=False,
            indent=2,
        )

    print(f"方面级情感抽取完成，已写入 {output_path}")
    if use_llm and llm_client:
        w = min(get_llm_max_workers(), max(1, llm_batch_requests))
        print(
            f"LLM 批量：约 {llm_batch_requests} 次顶层批请求（每批最多 {get_llm_batch_size()} 条，"
            f"顶层并发 {w}，可用环境变量 LLM_MAX_WORKERS 调节）；"
            "批解析失败时会二分或单条重试，仍失败则用规则。"
        )
    else:
        print("已使用纯规则/jieba 路径。")

    return output_path


def main():
    parser = argparse.ArgumentParser(description="方面级情感抽取（默认 LLM 批量 + 规则兜底；可用 --rule-only 全规则）")
    parser.add_argument("--input", required=True, help="上游 topic JSON 文件")
    parser.add_argument(
        "--rule-only",
        action="store_true",
        help="仅使用规则/jieba 抽取，不调用 LLM（更快）",
    )
    args = parser.parse_args()

    input_path = Path(args.input)
    if not input_path.exists():
        raise SystemExit(f"未找到输入文件: {input_path}")

    run_absa(input_path, use_llm=not args.rule_only)


if __name__ == "__main__":
    main()
