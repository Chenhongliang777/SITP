import argparse
import json
import os
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

# 仅保留绝对红线：文本出现这些词，强制 high，覆盖 LLM 任何结果
FORCE_HIGH_WORDS = ["假球", "黑哨", "赌球", "操纵比赛", "默契球", "协议球"]


def build_output_path(keyword: str):
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    return DATA_DIR / f"risk_{keyword}_{stamp}.json"


def determine_risk_llm(post: dict):
    """
    LLM 主路径：零字典，完全基于语义动态输出 risk_category。
    可输出任意中文类别，如"裁判争议风险""德比冲突风险""草皮舆论""青训正面反馈"等。
    """
    if not DS_KEY:
        return None

    text = (post.get("clean_text") or post.get("raw_text") or "").strip()
    sentiment = post.get("sentiment", "中性")
    topic_label = post.get("topic_label", "")
    aspects = post.get("aspect_sentiments", [])

    system_prompt = (
        "你是一个足球舆情风险判定专家。请根据微博内容、主题标签和方面级情感，"
        "动态生成最贴切的风险类别和风险等级。\n"
        "规则：\n"
        "1. risk_category 自由命名，必须体现具体方向，如："
        "'裁判争议风险'、'德比冲突风险'、'球员舆论'、'青训舆论'、"
        "'草皮舆论'、'转播服务舆论'、'赛程舆论'、'球迷冲突风险'、'敏感事件'等。"
        "禁止输出泛泛的'负面舆情''一般舆情'，必须带具体主题或'风险'后缀。\n"
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

    try:
        resp = requests.post(
            DS_URL,
            headers={"Authorization": f"Bearer {DS_KEY}", "Content-Type": "application/json"},
            json={
                "model": "deepseek-chat",
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_content},
                ],
                "temperature": 0.1,
                "max_tokens": 120,
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
        if not all(k in result for k in ("risk_level", "risk_category", "risk_entities")):
            return None
        if result["risk_level"] not in ("high", "medium", "low"):
            return None
        if not isinstance(result["risk_entities"], list):
            result["risk_entities"] = []

        # 清洗：确保 category 不是空泛词
        cat = str(result["risk_category"]).strip()
        if cat in ("负面舆情", "一般舆情", "无", ""):
            return None  # 让 fallback 重新生成带 topic 的类别

        return {
            "risk_level": result["risk_level"],
            "risk_category": cat,
            "risk_entities": [str(e) for e in result["risk_entities"]],
        }
    except Exception:
        return None


def determine_risk_rule(post: dict):
    """
    规则回退：LLM 失败时使用。基于 topic_label + sentiment + aspect 动态生成类别，
    不查任何字典。
    """
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

    # 强制红线
    for w in FORCE_HIGH_WORDS:
        if w in text:
            return {
                "risk_level": "high",
                "risk_category": "敏感事件",
                "risk_entities": sorted(set(risk_entities) | {w}),
            }

    # 动态生成类别：用 topic_label 直接拼接
    if not topic_label:
        base = "未分类"
    else:
        base = topic_label

    # 判定等级
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
        "risk_category": category,
        "risk_entities": sorted(risk_entities),
    }


def enforce_hard_rules(post: dict, risk: dict):
    """强制校验：假球/黑哨/裁判负面 绝不漏网"""
    text = (post.get("clean_text") or post.get("raw_text") or "").lower()
    entities = set(risk["risk_entities"])

    # 假球/黑哨 → 强制 high
    for w in FORCE_HIGH_WORDS:
        if w in text:
            risk["risk_level"] = "high"
            risk["risk_category"] = "敏感事件"
            entities.add(w)

    # 裁判 + 负面 aspect → 至少 medium
    aspects = post.get("aspect_sentiments") or []
    has_referee_negative = any(
        ("裁判" in a.get("target", "") or "var" in a.get("target", "").lower())
        and a.get("sentiment") == "negative"
        for a in aspects
    )
    if has_referee_negative and risk["risk_level"] not in ("high",):
        risk["risk_level"] = "medium"
        # 如果当前类别不是风险类，升级之
        if "风险" not in risk["risk_category"]:
            risk["risk_category"] = "裁判争议风险"
        entities.add("裁判争议")

    risk["risk_entities"] = sorted(entities)
    return risk


def main():
    parser = argparse.ArgumentParser(
        description="风险扫描脚本（LLM 零字典动态语义 + 规则回退）"
    )
    parser.add_argument("--input", required=True, help="上游 absa JSON 文件")
    args = parser.parse_args()

    input_path = Path(args.input)
    if not input_path.exists():
        raise SystemExit(f"未找到输入文件: {input_path}")

    with open(input_path, "r", encoding="utf-8") as f:
        payload = json.load(f)

    keyword = payload.get("meta", {}).get("keyword") or input_path.stem
    posts = payload.get("data", [])

    llm_count = 0
    rule_count = 0
    high_count = 0
    medium_count = 0
    low_count = 0

    for post in posts:
        risk = determine_risk_llm(post)
        if risk:
            llm_count += 1
        else:
            risk = determine_risk_rule(post)
            rule_count += 1

        risk = enforce_hard_rules(post, risk)

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
                    "llm_used": llm_count,
                    "rule_fallback": rule_count,
                },
                "data": posts,
            },
            f,
            ensure_ascii=False,
            indent=2,
        )

    print(f"风险扫描完成：high {high_count} 条，medium {medium_count} 条，low {low_count} 条。")
    print(f"LLM 主路径: {llm_count} 条，规则回退: {rule_count} 条。")
    print(f"结果已写入: {output_path}")


if __name__ == "__main__":
    main()