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

# ---------- 改进的规则法词典（回退用） ----------
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
    """改进版规则情感判定：带否定检测"""
    text = text or ""
    neg_count = sum(text.count(w) for w in NEGATIVE_INDICATORS)
    pos_count = sum(text.count(w) for w in POSITIVE_INDICATORS)

    # 否定翻转：句中出现"不""没"等，大概率翻转情感
    denial = any(w in text for w in DENIAL_WORDS)
    if denial:
        # 简单处理：如果原先是 positive，翻成 negative；反之亦然
        # 这里采用更保守策略：否定词削弱正面，增强负面
        if pos_count > 0:
            pos_count *= 0.3

    if neg_count > pos_count:
        return "negative"
    if pos_count > neg_count:
        return "positive"
    return "neutral"


def extract_aspects_rule(text: str):
    """改进的规则法：提取关键词 + 简单人名识别"""
    aspects = []
    if not text:
        return aspects

    # 1. 提取 jieba 人名（nr）作为潜在 target
    try:
        import jieba.posseg as pseg
        for word, flag in pseg.lcut(text):
            if flag == "nr" and len(word) >= 2:
                # 只保留常见足球人名（2~4字中文人名）
                aspects.append({"target": word, "sentiment": simple_sentiment(text)})
    except Exception:
        pass

    # 2. 按句子匹配关键词
    sentences = re.split(r'[。！？!?\n]', text)
    for sentence in sentences:
        sentence = sentence.strip()
        if not sentence:
            continue
        for keyword in ASPECT_KEYWORDS:
            if keyword in sentence:
                sentiment = simple_sentiment(sentence)
                aspects.append({"target": keyword, "sentiment": sentiment})

    # 3. 去重：同 target 同 sentiment 只保留一次
    unique = []
    seen = set()
    for item in aspects:
        key = (item["target"], item["sentiment"])
        if key not in seen:
            seen.add(key)
            unique.append({"target": item["target"], "sentiment": item["sentiment"]})
    return unique


def extract_aspects_llm(text: str):
    """LLM 精确抽取：能识别人名、复杂情感、转折句"""
    if not DS_KEY:
        return None

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

    try:
        resp = requests.post(
            DS_URL,
            headers={"Authorization": f"Bearer {DS_KEY}", "Content-Type": "application/json"},
            json={
                "model": "deepseek-chat",
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": f"文本：{text}\n输出："},
                ],
                "temperature": 0.0,
                "max_tokens": 200,
                "response_format": {"type": "json_object"},
            },
            timeout=25,
        )
        resp.raise_for_status()
        data = resp.json()
        content = data.get("choices", [{}])[0].get("message", {}).get("content", "")
        if not content:
            return None

        # LLM 可能直接返回数组，也可能包在 {"aspects": [...]} 里，做兼容
        parsed = json.loads(content)
        if isinstance(parsed, list):
            return parsed
        if isinstance(parsed, dict):
            for k in ["aspects", "result", "data", "items"]:
                if k in parsed and isinstance(parsed[k], list):
                    return parsed[k]
            # 如果 dict 里只有一个 list 值
            for v in parsed.values():
                if isinstance(v, list):
                    return v
        return None
    except Exception:
        return None


def extract_aspects(text: str):
    """主路径：LLM；回退：规则法"""
    result = None
    if DS_KEY:
        result = extract_aspects_llm(text)
    if result is None:
        result = extract_aspects_rule(text)
    # 保证返回 list
    return result if isinstance(result, list) else []


def main():
    parser = argparse.ArgumentParser(description="方面级情感抽取脚本（LLM 主路径 + 规则法回退）")
    parser.add_argument("--input", required=True, help="上游 topic JSON 文件")
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

    for post in posts:
        text = (post.get("clean_text") or post.get("raw_text") or "").strip()
        aspects = extract_aspects(text)
        post["aspect_sentiments"] = aspects

        # 统计路径
        if DS_KEY and aspects and any(
            a["target"] not in ASPECT_KEYWORDS and len(a["target"]) >= 2
            for a in aspects
        ):
            # 如果出现了非关键词列表里的具体人名，大概率是 LLM 输出的
            llm_count += 1
        else:
            rule_count += 1

    output_path = build_output_path(keyword)
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(
            {
                "meta": {
                    "keyword": keyword,
                    "date_range": payload.get("meta", {}).get("date_range", ""),
                    "actual": len(posts),
                },
                "data": posts,
            },
            f,
            ensure_ascii=False,
            indent=2,
        )

    print(f"方面级情感抽取完成，已写入 {output_path}")
    if DS_KEY:
        print(f"LLM 主路径估计覆盖: ~{llm_count} 条，规则回退: ~{rule_count} 条")


if __name__ == "__main__":
    main()