"""分析链共用：主导话题、趋势文案、同质词过滤、情感与 ABSA 对齐。"""
from __future__ import annotations

import re
from collections import defaultdict
from typing import Any, Dict, List, Optional, Tuple

FOOTBALL_MARKERS = (
    "中超联赛",
    "#中超联赛",
    "#中超",
    "中超第",
    "中国足球超级联赛",
    "足球",
    "国足",
    "球员",
    "进球",
    "裁判",
    "VAR",
    "联赛",
    "球场",
    "客队",
    "主队",
    "足协杯",
    "亚冠",
    "门将",
    "前锋",
    "后卫",
    "德比",
)

POSITIVE_SENTIMENTS = frozenset({"强烈正面", "轻微正面"})
NEGATIVE_SENTIMENTS = frozenset({"强烈负面", "轻微负面"})

CANONICAL_RISK_CATEGORIES = (
    "敏感事件",
    "裁判争议风险",
    "球迷文化舆论",
    "球队表现舆论",
    "赛事运营舆论",
    "转播版权舆论",
    "赛程密集舆论",
    "青训舆论",
    "俱乐部财务舆论",
)


def pick_dominant_by_count(category_counts: Dict[str, int]) -> Tuple[str, int]:
    filtered = {
        k: v
        for k, v in category_counts.items()
        if k and k != "未分类" and v > 0
    }
    if not filtered:
        return "未分类", 0
    cat = max(filtered, key=filtered.get)
    return cat, filtered[cat]


def pick_alert_focus(posts: List[Dict[str, Any]]) -> Tuple[str, int]:
    """按 high/medium 条数加权，用于「需复核的风险焦点」。"""
    scores: Dict[str, int] = defaultdict(int)
    for p in posts:
        cat = p.get("risk_category") or "未分类"
        lvl = p.get("risk_level", "low")
        if lvl == "high":
            scores[cat] += 3
        elif lvl == "medium":
            scores[cat] += 1
    if not scores:
        counts: Dict[str, int] = defaultdict(int)
        for p in posts:
            counts[p.get("risk_category") or "未分类"] += 1
        return pick_dominant_by_count(dict(counts))
    cat = max(scores, key=scores.get)
    n = sum(
        1
        for p in posts
        if p.get("risk_category") == cat
        and p.get("risk_level") in ("high", "medium")
    )
    return cat, n


def category_weight_for_scoring(category: str) -> int:
    """仅用于评分加成，不用于主导话题选取。"""
    cat = (category or "").lower()
    if "敏感" in cat:
        return 3
    if "风险" in cat and "舆论" not in cat:
        return 2
    if "负面" in cat:
        return 1
    return 0


def compute_sentiment_tone(posts: List[Dict[str, Any]]) -> Dict[str, Any]:
    pos = sum(1 for p in posts if p.get("sentiment") in POSITIVE_SENTIMENTS)
    neg = sum(1 for p in posts if p.get("sentiment") in NEGATIVE_SENTIMENTS)
    neu = len(posts) - pos - neg
    total = len(posts) or 1
    if pos > neg * 1.2:
        label = "偏正面"
    elif neg > pos * 1.2:
        label = "偏负面"
    else:
        label = "中性为主"
    return {
        "positive_count": pos,
        "negative_count": neg,
        "neutral_count": neu,
        "tone_label": label,
        "positive_ratio": round(pos / total, 4),
    }


def summarize_high_risk_categories(posts: List[Dict[str, Any]]) -> Dict[str, Any]:
    """汇总 high 帖的类别分布，供趋势文案与 LLM 建议约束使用。"""
    by_cat: Dict[str, int] = defaultdict(int)
    sensitive = 0
    for p in posts:
        if p.get("risk_level") != "high":
            continue
        cat = p.get("risk_category") or "未分类"
        by_cat[cat] += 1
        if "敏感" in cat or cat == "敏感事件":
            sensitive += 1
    total_high = sum(by_cat.values())
    parts = [
        f"{cat}{n}条"
        for cat, n in sorted(by_cat.items(), key=lambda x: x[1], reverse=True)
    ]
    return {
        "total": total_high,
        "by_category": dict(by_cat),
        "sensitive_count": sensitive,
        "summary": "；".join(parts) if parts else "无",
        "has_sensitive": sensitive > 0,
    }


def build_trend(
    total_score: float,
    high_count: int,
    medium_count: int,
    dominant_cat: str,
    dominant_ratio: float,
    negative_rate: float,
    tone_label: str,
    sample_size: int,
    *,
    alert_focus: str = "",
) -> str:
    caveat = ""
    if sample_size < 30:
        caveat = "（样本量较小，以下判断仅供参考）"

    high_ratio = high_count / sample_size if sample_size else 0.0
    focus_hint = f"「{alert_focus}」" if alert_focus else "相关话题"

    # 仅在高风险占比或总分同时偏高时，才使用「上报主管部门」级表述
    severe_high = high_count >= 5 and high_ratio >= 0.05
    severe_high = severe_high or (
        high_count >= 3 and high_ratio >= 0.08 and negative_rate >= 0.35
    )
    if total_score >= 80 or severe_high:
        return (
            f"高风险内容占比较高（{high_count} 条，约 {high_ratio:.1%}），"
            f"建议立即核查并视情况上报主管部门。{caveat}"
        )
    if high_count >= 1:
        return (
            f"存在 {high_count} 条高风险微博（约占 {high_ratio:.1%}），"
            f"建议优先复核{focus_hint}的事实依据与传播路径，整体情绪基调{tone_label}。{caveat}"
        )
    if total_score >= 60:
        return (
            f"负面议题占比较高（负面率约 {negative_rate:.0%}），"
            f"话题「{dominant_cat}」讨论集中（约 {dominant_ratio:.0%}），建议加强跟踪。{caveat}"
        )
    if total_score >= 35:
        if negative_rate > 0.35 and tone_label == "偏负面":
            return (
                f"局部议题关注度上升，「{dominant_cat}」占比约 {dominant_ratio:.0%}，"
                f"情绪基调{tone_label}，建议做好应对准备。{caveat}"
            )
        if medium_count > 0:
            return (
                f"整体风险中等，主要讨论围绕「{dominant_cat}」（约 {dominant_ratio:.0%}），"
                f"另有 {medium_count} 条中风险内容，情绪基调{tone_label}，保持监测即可。{caveat}"
            )
        return (
            f"整体风险中等，话题以「{dominant_cat}」为主（约 {dominant_ratio:.0%}），"
            f"情绪基调{tone_label}，未见明显升温信号，保持常规监测。{caveat}"
        )
    return f"舆情总体平稳，以常规跟踪为主。{caveat}"


_SENSITIVE_TERMS = ("假球", "黑哨", "赌球", "操纵比赛", "默契球", "协议球")


def flatten_insight_text(value: Any) -> str:
    """将 LLM 返回的字符串/字典/列表统一为可读段落。"""
    if value is None:
        return ""
    if isinstance(value, str):
        s = value.strip()
        if s.startswith("{") and s.endswith("}"):
            try:
                import ast

                parsed = ast.literal_eval(s)
                if isinstance(parsed, dict):
                    return flatten_insight_text(parsed)
            except (ValueError, SyntaxError):
                pass
        return s
    if isinstance(value, dict):
        action = value.get("action")
        responsibility = value.get("responsibility")
        if action:
            prefix = f"【{responsibility}】" if responsibility else ""
            return f"{prefix}{action}".strip()
        parts: List[str] = []
        label_map = {
            "发帖量": "发帖量",
            "风险等级": "风险等级",
            "主要观点": "主要观点",
        }
        for key, label in label_map.items():
            if key not in value or value[key] in (None, ""):
                continue
            v = value[key]
            if key == "发帖量":
                parts.append(f"发帖量 {v} 条")
            else:
                parts.append(f"{label}：{v}")
        for key, v in value.items():
            if key in label_map or key in ("action", "responsibility") or not v:
                continue
            parts.append(f"{key}：{v}")
        return "；".join(parts) if parts else ""
    if isinstance(value, (list, tuple)):
        return "；".join(t for t in (flatten_insight_text(x) for x in value) if t)
    return str(value).strip()


def filter_sensitive_alarm_phrases(text: str, *, allow_sensitive: bool) -> str:
    if allow_sensitive or not text:
        return text
    if any(term in text for term in _SENSITIVE_TERMS):
        return (
            "核实高风险博文内容与传播路径，2 小时内完成内部通报并准备口径说明。"
        )
    return text


def normalize_insight_payload(
    result: Dict[str, Any], *, allow_sensitive_alarm: bool = False
) -> Dict[str, Any]:
    raw_themes = result.get("theme_analysis") or {}
    themes: Dict[str, str] = {}
    if isinstance(raw_themes, dict):
        for cat, val in raw_themes.items():
            text = flatten_insight_text(val)
            if text:
                themes[str(cat)] = text

    suggestions: Dict[str, List[str]] = {}
    raw_sugg = result.get("suggestions") or {}
    if isinstance(raw_sugg, dict):
        for key in ("emergency", "short", "medium", "long"):
            items = raw_sugg.get(key, [])
            if not isinstance(items, list):
                items = [items] if items else []
            normalized: List[str] = []
            for item in items:
                text = flatten_insight_text(item).strip()
                if not text:
                    continue
                if key == "emergency":
                    text = filter_sensitive_alarm_phrases(
                        text, allow_sensitive=allow_sensitive_alarm
                    )
                normalized.append(text)
            suggestions[key] = normalized

    summary = flatten_insight_text(result.get("summary", ""))
    if not allow_sensitive_alarm:
        for term in _SENSITIVE_TERMS:
            if term in summary and "敏感" not in summary:
                summary = summary.replace(term, "严重违规")
                break

    return {
        "summary": summary,
        "theme_analysis": themes,
        "suggestions": suggestions,
    }


def keyword_false_positive_reason(text: str, keyword: str) -> Optional[str]:
    if not text:
        return None
    kw = (keyword or "").strip()
    has_football = any(m in text for m in FOOTBALL_MARKERS)

    if kw in ("中超", "中超联赛"):
        if re.search(r"投中\s*超远", text) or "投中超远" in text:
            return "「投中超远」为篮球等非足球语境"
        if "超like" in text.lower() or "title中超" in text.lower():
            return "明星/营销「超like」类噪声"
        if "藏海传" in text and not has_football:
            return "影视剧等非足球话题中的「中超」字样"
        if "篮球" in text and not has_football:
            return "篮球等内容，缺少足球联赛语境"
        if "乒乓球" in text and not has_football:
            return "乒乓球等内容，缺少足球联赛语境"
        if kw == "中超" and not has_football:
            if len(text) < 80 and "足球" not in text:
                return "短文本仅字面含「中超」，缺少足球赛事语境"

    return None


def reconcile_sentiment_with_absa(post: Dict[str, Any]) -> None:
    aspects = post.get("aspect_sentiments") or []
    if not aspects:
        return
    pos = sum(1 for a in aspects if a.get("sentiment") == "positive")
    neg = sum(1 for a in aspects if a.get("sentiment") == "negative")
    sent = post.get("sentiment", "中性")
    if sent in NEGATIVE_SENTIMENTS and pos >= 2 and neg == 0:
        post["sentiment"] = "轻微正面" if sent == "轻微负面" else "中性"
        post["sentiment_reconciled"] = True
    elif sent in POSITIVE_SENTIMENTS and neg >= 2 and pos == 0:
        post["sentiment"] = "轻微负面" if sent == "轻微正面" else "中性"
        post["sentiment_reconciled"] = True


def canonicalize_risk_category(category: str) -> str:
    cat = (category or "").strip()
    if not cat or cat in ("负面舆情", "一般舆情", "无"):
        return "未分类"
    if "敏感" in cat or any(w in cat for w in ("假球", "黑哨", "赌球")):
        return "敏感事件"
    if "裁判" in cat or "var" in cat.lower():
        return "裁判争议风险"
    if "球迷" in cat or "看台" in cat or "助威" in cat:
        return "球迷文化舆论"
    if "球员" in cat or "进球" in cat or "阵容" in cat or "教练" in cat:
        return "球队表现舆论"
    if "转播" in cat or "版权" in cat or "央视" in cat or "国际足联" in cat:
        return "转播版权舆论"
    if "赛程" in cat or "密集" in cat or "体能" in cat:
        return "赛程密集舆论"
    if "青训" in cat or "梯队" in cat:
        return "青训舆论"
    if "草皮" in cat or "场地" in cat or "票务" in cat or "安保" in cat:
        return "赛事运营舆论"
    if "财务" in cat or "欠薪" in cat or "解散" in cat:
        return "俱乐部财务舆论"
    return cat
