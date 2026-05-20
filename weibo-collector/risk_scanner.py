import argparse
import json
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from utils.analysis_helpers import (
    CANONICAL_RISK_CATEGORIES,
    canonicalize_risk_category,
    reconcile_sentiment_with_absa,
)
from utils.llm_client import try_llm_client
from utils.runtime import get_llm_batch_size, get_llm_max_workers

from utils.project_root import get_project_root

SCRIPT_DIR = get_project_root()
DATA_DIR = SCRIPT_DIR / "data"
DATA_DIR.mkdir(exist_ok=True)

FORCE_HIGH_WORDS = ["假球", "黑哨", "赌球", "操纵比赛", "默契球", "协议球"]


def build_output_path(keyword: str):
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    return DATA_DIR / f"risk_{keyword}_{stamp}.json"


def determine_risk_llm(post: dict, client) -> Optional[Dict[str, Any]]:
    text = (post.get("clean_text") or post.get("raw_text") or "").strip()
    sentiment = post.get("sentiment", "中性")
    topic_label = post.get("topic_label", "")
    aspects = post.get("aspect_sentiments", [])

    system_prompt = (
        "你是一个足球舆情风险判定专家。请根据微博内容、主题标签和方面级情感，"
        "动态生成最贴切的风险类别和风险等级。\n"
        "规则：\n"
        "1. risk_category 优先从以下标准类复用（不要为每条微博发明新类名）："
        + "、".join(CANONICAL_RISK_CATEGORIES)
        + "。仅在确实无法归类时再新建，且须带具体主题或'风险/舆论'后缀。\n"
        "禁止输出泛泛的'负面舆情''一般舆情'。\n"
        "2. risk_level：high / medium / low\n"
        "   - high：假球、黑哨、赌球、操纵比赛、赛场暴力、球迷骚乱、群体性事件。\n"
        "   - medium：裁判争议（VAR/漏判/错判）且情感负面；"
        "德比火药味升级；多家俱乐部被批评；退钱/下课/解散诉求；强烈负面情感集中。\n"
        "   - low：单场战术讨论、球员 praise、青训进展、观赛体验反馈、中性建议。\n"
        "3. risk_entities：列出引发风险的具体对象或关键词列表（如 ['VAR','裁判']），"
        "无风险则空列表。\n"
        "4. 只输出合法 JSON，字段：risk_level、risk_category、risk_entities。"
    )

    user_content = (
        f"微博内容：{text}\n"
        f"主题标签：{topic_label}\n"
        f"整体情感：{sentiment}\n"
        f"方面级情感：{json.dumps(aspects, ensure_ascii=False)}"
    )

    result = client.chat_json(
        system_prompt,
        user_content,
        temperature=0.1,
        max_tokens=120,
        response_format={"type": "json_object"},
    )
    return _normalize_risk_dict(result)


def _normalize_risk_dict(result: Any) -> Optional[Dict[str, Any]]:
    if not isinstance(result, dict):
        return None
    if not all(k in result for k in ("risk_level", "risk_category", "risk_entities")):
        return None
    if result["risk_level"] not in ("high", "medium", "low"):
        return None
    if not isinstance(result["risk_entities"], list):
        result["risk_entities"] = []

    cat = str(result["risk_category"]).strip()
    if cat in ("负面舆情", "一般舆情", "无", ""):
        return None

    return {
        "risk_level": result["risk_level"],
        "risk_category": canonicalize_risk_category(cat),
        "risk_entities": [str(e) for e in result["risk_entities"]],
    }


def _risk_compact_block(idx: int, post: dict, max_text: int = 360) -> str:
    text = (post.get("clean_text") or post.get("raw_text") or "").strip()[:max_text].replace("\n", " ")
    sentiment = post.get("sentiment", "中性")
    topic_label = post.get("topic_label", "")
    aspects = post.get("aspect_sentiments") or []
    asp_s = json.dumps(aspects, ensure_ascii=False)[:500]
    return f"[{idx}] 正文:{text}\n情感:{sentiment}\n主题:{topic_label}\n方面:{asp_s}"


def determine_risk_llm_batch(batch: List[Tuple[int, dict]], client) -> Optional[Dict[int, Dict[str, Any]]]:
    """一批多条共一次 LLM；batch 为 (下标, post)。"""
    if not batch:
        return {}
    lines = [_risk_compact_block(i, p) for i, p in batch]
    block = "\n---\n".join(lines)
    system_prompt = (
        "你是足球舆情风险判定专家。输入多段，每段以「[整数id]」开头，含正文/情感/主题/方面级情感。"
        "请为每个 id 输出一条风险判定。输出 JSON 对象，仅含 results 数组；"
        "每项含 id（整数）与 risk_level（high/medium/low）、risk_category（优先复用标准类："
        + "、".join(CANONICAL_RISK_CATEGORIES)
        + "，禁止泛泛的负面舆情/一般舆情）、risk_entities（字符串数组）。"
        "必须覆盖每一个输入 id，不要遗漏。"
    )
    max_tokens = min(4096, 100 + 110 * len(batch))
    parsed = client.chat_json(
        system_prompt,
        f"待判定：\n{block}\n",
        temperature=0.1,
        max_tokens=max_tokens,
        response_format={"type": "json_object"},
    )
    if not isinstance(parsed, dict):
        return None
    arr = parsed.get("results")
    if not isinstance(arr, list):
        return None
    out: Dict[int, Dict[str, Any]] = {}
    for item in arr:
        if not isinstance(item, dict):
            continue
        try:
            idx = int(item.get("id"))
        except (TypeError, ValueError):
            continue
        one = _normalize_risk_dict(item)
        if one is None:
            return None
        out[idx] = one
    expected = {i for i, _ in batch}
    if expected != set(out.keys()):
        return None
    return out


def _risk_batch_recursive(batch: List[Tuple[int, dict]], client, depth: int) -> Dict[int, Dict[str, Any]]:
    if not batch:
        return {}
    if len(batch) == 1:
        idx, post = batch[0]
        llm = determine_risk_llm(post, client)
        if llm is not None:
            return {idx: llm}
        return {idx: determine_risk_rule(post)}

    parsed = determine_risk_llm_batch(batch, client)
    if parsed is not None:
        return parsed

    if depth >= 7:
        return {i: determine_risk_rule(p) for i, p in batch}

    mid = max(1, len(batch) // 2)
    a = _risk_batch_recursive(batch[:mid], client, depth + 1)
    b = _risk_batch_recursive(batch[mid:], client, depth + 1)
    return {**a, **b}


def determine_risk_rule(post: dict):
    text = (post.get("clean_text") or post.get("raw_text") or "").lower()
    sentiment = post.get("sentiment", "中性")
    topic_label = post.get("topic_label", "")
    aspects = post.get("aspect_sentiments") or []

    risk_entities = set()
    has_negative_aspect = False
    has_referee = False

    for aspect in aspects:
        target = aspect.get("target", "")
        aspect_sentiment = aspect.get("sentiment", "neutral")
        if aspect_sentiment == "negative":
            has_negative_aspect = True
            risk_entities.add(target)
            if "裁判" in target or "var" in target.lower():
                has_referee = True

    for w in FORCE_HIGH_WORDS:
        if w in text:
            return {
                "risk_level": "high",
                "risk_category": "敏感事件",
                "risk_entities": sorted(set(risk_entities) | {w}),
            }

    if not topic_label:
        base = "未分类"
    else:
        base = topic_label

    if has_referee and has_negative_aspect:
        level = "medium"
        category = f"{base}风险" if "风险" not in base else base
    elif has_negative_aspect and sentiment in ("强烈负面", "轻微负面"):
        level = "medium"
        category = f"{base}风险" if "风险" not in base else base
    elif has_negative_aspect:
        level = "low"
        category = f"{base}舆论" if "舆论" not in base else base
    else:
        level = "low"
        category = f"{base}舆论" if "舆论" not in base else base

    return {
        "risk_level": level,
        "risk_category": canonicalize_risk_category(category),
        "risk_entities": sorted(risk_entities),
    }


def enforce_hard_rules(post: dict, risk: dict):
    text = (post.get("clean_text") or post.get("raw_text") or "").lower()
    entities = set(risk["risk_entities"])

    for w in FORCE_HIGH_WORDS:
        if w in text:
            risk["risk_level"] = "high"
            risk["risk_category"] = "敏感事件"
            entities.add(w)

    aspects = post.get("aspect_sentiments") or []
    has_referee_negative = any(
        ("裁判" in a.get("target", "") or "var" in a.get("target", "").lower())
        and a.get("sentiment") == "negative"
        for a in aspects
    )
    if has_referee_negative and risk["risk_level"] not in ("high",):
        risk["risk_level"] = "medium"
        if "风险" not in risk["risk_category"]:
            risk["risk_category"] = "裁判争议风险"
        entities.add("裁判争议")

    risk["risk_entities"] = sorted(entities)
    return risk


def needs_risk_llm_after_rule(post: dict, risk: dict) -> bool:
    """规则分层：已 high 或整体中性无负面方面则不再调 LLM。"""
    if risk.get("risk_level") == "high":
        return False
    sentiment = post.get("sentiment", "中性")
    if sentiment in ("强烈负面", "轻微负面"):
        return True
    aspects = post.get("aspect_sentiments") or []
    if any(a.get("sentiment") == "negative" for a in aspects):
        return True
    return False


def run_risk_scan(input_path: Path, use_llm: bool = True) -> Path:
    with open(input_path, "r", encoding="utf-8") as f:
        payload = json.load(f)

    keyword = payload.get("meta", {}).get("keyword") or input_path.stem
    posts: List[Dict] = payload.get("data", [])

    llm_client = try_llm_client() if use_llm else None

    llm_used_posts = 0
    rule_only_posts = 0
    high_count = 0
    medium_count = 0
    low_count = 0

    for post in posts:
        reconcile_sentiment_with_absa(post)

    rule_base: List[Dict[str, Any]] = []
    for post in posts:
        r = enforce_hard_rules(post, determine_risk_rule(post))
        rule_base.append(r)

    need_idx: List[int] = []
    if llm_client is not None:
        for i, post in enumerate(posts):
            if needs_risk_llm_after_rule(post, rule_base[i]):
                need_idx.append(i)

    llm_batch_requests = 0
    llm_map: Dict[int, Dict[str, Any]] = {}
    if llm_client is not None and need_idx:
        bs = get_llm_batch_size()
        chunks: List[List[Tuple[int, dict]]] = []
        for s in range(0, len(need_idx), bs):
            chunk_idx = need_idx[s : s + bs]
            chunks.append([(i, posts[i]) for i in chunk_idx])
        llm_batch_requests = len(chunks)

        def _process_risk_chunk(ch: List[Tuple[int, dict]]) -> Dict[int, Dict[str, Any]]:
            return _risk_batch_recursive(ch, llm_client, 0)

        workers = min(get_llm_max_workers(), max(1, len(chunks)))
        with ThreadPoolExecutor(max_workers=workers) as ex:
            for part in ex.map(_process_risk_chunk, chunks):
                llm_map.update(part)

    for idx, post in enumerate(posts):
        if idx in llm_map and llm_map[idx]:
            risk = enforce_hard_rules(post, llm_map[idx])
            llm_used_posts += 1
        else:
            risk = rule_base[idx]
            rule_only_posts += 1

        post.update(risk)

        if risk["risk_level"] == "high":
            high_count += 1
        elif risk["risk_level"] == "medium":
            medium_count += 1
        else:
            low_count += 1

    output_path = build_output_path(keyword)
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(
            {
                "meta": {
                    "keyword": keyword,
                    "date_range": payload.get("meta", {}).get("date_range", ""),
                    "actual": len(posts),
                    "high_risk_count": high_count,
                    "medium_risk_count": medium_count,
                    "low_risk_count": low_count,
                    "llm_used_posts": llm_used_posts,
                    "rule_only_posts": rule_only_posts,
                    "llm_used": llm_used_posts,
                    "rule_fallback": rule_only_posts,
                    "llm_batch_requests": llm_batch_requests if llm_client and need_idx else 0,
                    "llm_batch_size": get_llm_batch_size() if llm_client and need_idx else None,
                    "llm_max_workers": get_llm_max_workers() if llm_client and need_idx else None,
                    "llm_risk": bool(use_llm and llm_client is not None),
                },
                "data": posts,
            },
            f,
            ensure_ascii=False,
            indent=2,
        )

    print(f"风险扫描完成：high {high_count} 条，medium {medium_count} 条，low {low_count} 条。")
    rw = min(get_llm_max_workers(), max(1, llm_batch_requests)) if need_idx else 0
    print(
        f"规则先行：{rule_only_posts} 条未再调 LLM；"
        f"LLM 细化 {llm_used_posts} 条（顶层批 {llm_batch_requests} 次，"
        f"批大小≤{get_llm_batch_size()}，顶层并发 {rw}，可调 LLM_MAX_WORKERS）。"
    )
    print(f"结果已写入: {output_path}")

    return output_path


def main():
    parser = argparse.ArgumentParser(
        description="风险扫描（规则先行 + 批量 LLM 细化；--rule-only 则全规则）"
    )
    parser.add_argument("--input", required=True, help="上游 absa JSON 文件")
    parser.add_argument(
        "--rule-only",
        action="store_true",
        help="仅用规则 + 硬规则校正，不调用 LLM（加速）",
    )
    args = parser.parse_args()

    input_path = Path(args.input)
    if not input_path.exists():
        raise SystemExit(f"未找到输入文件: {input_path}")

    run_risk_scan(input_path, use_llm=not args.rule_only)


if __name__ == "__main__":
    main()
