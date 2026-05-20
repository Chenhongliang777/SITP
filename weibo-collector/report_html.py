import argparse
import json
from collections import defaultdict
from datetime import datetime
from pathlib import Path

from utils.analysis_helpers import (
    flatten_insight_text,
    normalize_insight_payload,
    summarize_high_risk_categories,
)
from utils.llm_client import try_llm_client

from utils.project_root import get_project_root

SCRIPT_DIR = get_project_root()
DATA_DIR = SCRIPT_DIR / "data"
REPORT_DIR = SCRIPT_DIR / "reports"
REPORT_DIR.mkdir(parents=True, exist_ok=True)


# ==================== 辅助函数（全部前置，避免 NameError） ====================
def safe_load(path):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def build_output_path(keyword: str):
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    return REPORT_DIR / f"report_{keyword}_{stamp}.html"


def make_metric(label, value):
    return f"<div class='metric'><div class='metric-value'>{value}</div><div class='metric-label'>{label}</div></div>"


def svg_gauge(score, level):
    try:
        score = float(score)
    except Exception:
        return "<div class='no-data'>暂无数据</div>"
    color = {"low": "#52c41a", "medium": "#faad14", "high": "#f5222d"}.get(level, "#999")
    r, c = 80, 3.1416 * 80
    progress = min(100, max(0, score)) / 100 * c
    return f"""
    <svg viewBox="0 0 200 120" width="240" height="144" style="margin:0 auto;display:block;">
      <path d="M 20 100 A {r} {r} 0 0 1 180 100" stroke="#e8e8e8" stroke-width="14" fill="none" stroke-linecap="round"/>
      <path d="M 20 100 A {r} {r} 0 0 1 180 100" stroke="{color}" stroke-width="14" fill="none" stroke-linecap="round"
            stroke-dasharray="{progress:.1f} {c:.1f}" stroke-dashoffset="0"/>
      <text x="100" y="85" text-anchor="middle" font-size="32" font-weight="bold" fill="#2e4668">{score:.0f}</text>
      <text x="100" y="105" text-anchor="middle" font-size="12" fill="#667288">总评分</text>
    </svg>"""


def svg_risk_donut(high, medium, low, total):
    if total == 0:
        return "<div class='no-data'>暂无数据</div>"
    r, c = 70, 2 * 3.1416 * 70
    data = [("high", high, "#f5222d"), ("medium", medium, "#faad14"), ("low", low, "#52c41a")]
    segments, offset = [], 0
    for label, count, color in data:
        if count == 0:
            continue
        seg_len = (count / total) * c
        segments.append(
            f'<circle cx="100" cy="100" r="{r}" fill="none" stroke="{color}" stroke-width="22" '
            f'stroke-dasharray="{seg_len:.2f} {c:.2f}" stroke-dashoffset="-{offset:.2f}" transform="rotate(-90 100 100)"/>')
        offset += seg_len
    legend = "".join(
        f'<span class="legend-item"><span class="dot" style="background:{color}"></span>{label} {count}条 ({count/total:.1%})</span>'
        for label, count, color in data if count > 0)
    return f"""
    <div style="text-align:center;">
      <svg viewBox="0 0 200 200" width="200" height="200" style="margin:0 auto;display:block;">
        {''.join(segments)}
        <text x="100" y="95" text-anchor="middle" font-size="24" font-weight="bold" fill="#2e4668">{total}</text>
        <text x="100" y="115" text-anchor="middle" font-size="11" fill="#667288">总样本</text>
      </svg>
      <div class="legend-bar">{legend}</div>
    </div>"""


def svg_horizontal_bars(data_dict, color="#2e4668", max_items=8):
    if not data_dict:
        return "<div class='no-data'>暂无数据</div>"
    items = sorted(data_dict.items(), key=lambda x: x[1], reverse=True)[:max_items]
    max_val = max(v for _, v in items)
    h, gap = 36, 8
    total_h = len(items) * (h + gap) + gap
    bars = []
    for i, (label, val) in enumerate(items):
        y = gap + i * (h + gap)
        width = (val / max_val) * 320 if max_val else 0
        pct = f"{val / sum(v for _, v in items):.1%}" if sum(v for _, v in items) > 0 else "0%"
        bars.append(f"""
        <g>
          <text x="0" y="{y + 20}" font-size="12" fill="#555" style="font-weight:500;">{label}</text>
          <rect x="0" y="{y + 24}" width="{width}" height="10" rx="5" fill="{color}" opacity="0.85"/>
          <text x="{max(width + 6, 6)}" y="{y + 33}" font-size="11" fill="#667288">{val}条 ({pct})</text>
        </g>""")
    return f"""<svg viewBox="0 0 400 {total_h}" width="100%" height="{total_h}" preserveAspectRatio="xMidYMid meet">{''.join(bars)}</svg>"""


def svg_sentiment_bars(posts):
    if not posts:
        return "<div class='no-data'>暂无数据</div>"
    counts = {}
    for p in posts:
        counts[p.get("sentiment", "未知")] = counts.get(p.get("sentiment", "未知"), 0) + 1
    colors = {"强烈负面": "#f5222d", "轻微负面": "#fa8c16", "中性": "#8c8c8c",
              "轻微正面": "#1890ff", "强烈正面": "#52c41a"}
    items = [(k, counts.get(k, 0)) for k in colors.keys() if counts.get(k, 0) > 0]
    if not items:
        return "<div class='no-data'>暂无数据</div>"
    max_val = max(v for _, v in items)
    h, gap = 40, 10
    total_h = len(items) * (h + gap) + gap
    bars = []
    for i, (label, val) in enumerate(items):
        y = gap + i * (h + gap)
        width = (val / max_val) * 280 if max_val else 0
        bars.append(f"""
        <g>
          <rect x="0" y="{y}" width="{width}" height="{h}" rx="6" fill="{colors.get(label, '#2e4668')}" opacity="0.9"/>
          <text x="12" y="{y + 25}" font-size="13" fill="#fff" font-weight="bold">{label}</text>
          <text x="{width + 8}" y="{y + 25}" font-size="13" fill="#2e4668" font-weight="bold">{val}条</text>
        </g>""")
    return f"""<svg viewBox="0 0 400 {total_h}" width="100%" height="{total_h}" preserveAspectRatio="xMidYMid meet">{''.join(bars)}</svg>"""


def svg_time_trend(posts):
    if not posts:
        return "<div class='no-data'>暂无数据</div>"
    daily = defaultdict(lambda: {"high": 0, "medium": 0, "negative": 0})
    for p in posts:
        t = p.get("parsed_time", "")[:10]
        if not t:
            continue
        if p.get("risk_level") == "high":
            daily[t]["high"] += 1
        elif p.get("risk_level") == "medium":
            daily[t]["medium"] += 1
        if p.get("sentiment") in ("强烈负面", "轻微负面"):
            daily[t]["negative"] += 1
    if not daily:
        return "<div class='no-data'>暂无数据</div>"
    dates = sorted(daily.keys())
    max_val = max(max(v["high"], v["medium"], v["negative"]) for v in daily.values()) or 1
    n = len(dates)
    w, h_base = 500, 180
    pad_l, pad_r, pad_t, pad_b = 50, 30, 20, 40
    chart_w = w - pad_l - pad_r
    chart_h = h_base - pad_t - pad_b
    step = chart_w / (n - 1) if n > 1 else chart_w

    def line_points(key, color):
        pts = []
        for i, d in enumerate(dates):
            x = pad_l + i * step
            y = pad_t + chart_h - (daily[d][key] / max_val) * chart_h
            pts.append(f"{x:.1f},{y:.1f}")
        return f'<polyline points="{" ".join(pts)}" fill="none" stroke="{color}" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"/>'

    def dots(key, color):
        circs = []
        for i, d in enumerate(dates):
            x = pad_l + i * step
            y = pad_t + chart_h - (daily[d][key] / max_val) * chart_h
            circs.append(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="3.5" fill="{color}"/>')
        return "".join(circs)

    x_labels = "".join(
        f'<text x="{pad_l + i * step}" y="{h_base - 10}" text-anchor="middle" font-size="10" fill="#667288">{d[5:]}</text>'
        for i, d in enumerate(dates))

    return f"""
    <svg viewBox="0 0 {w} {h_base}" width="100%" height="{h_base}" preserveAspectRatio="xMidYMid meet">
      <line x1="{pad_l}" y1="{pad_t + chart_h}" x2="{pad_l + chart_w}" y2="{pad_t + chart_h}" stroke="#e5e7eb" stroke-width="1"/>
      <line x1="{pad_l}" y1="{pad_t}" x2="{pad_l}" y2="{pad_t + chart_h}" stroke="#e5e7eb" stroke-width="1"/>
      {line_points("high", "#f5222d")}
      {line_points("medium", "#faad14")}
      {line_points("negative", "#8c8c8c")}
      {dots("high", "#f5222d")}
      {dots("medium", "#faad14")}
      {dots("negative", "#8c8c8c")}
      {x_labels}
      <text x="{w - 10}" y="18" text-anchor="end" font-size="10" fill="#f5222d">● high</text>
      <text x="{w - 10}" y="32" text-anchor="end" font-size="10" fill="#faad14">● medium</text>
      <text x="{w - 10}" y="46" text-anchor="end" font-size="10" fill="#8c8c8c">● negative</text>
    </svg>"""


def svg_absa_matrix(posts):
    if not posts:
        return "<div class='no-data'>暂无数据</div>"
    matrix = defaultdict(lambda: defaultdict(int))
    for p in posts:
        for asp in p.get("aspect_sentiments", []):
            t, s = asp.get("target", "未知"), asp.get("sentiment", "neutral")
            if len(t) > 12:
                t = t[:12] + "…"
            matrix[t][s] += 1
    if not matrix:
        return "<div class='no-data'>暂无数据</div>"
    targets = sorted(matrix.keys(), key=lambda k: sum(matrix[k].values()), reverse=True)[:8]
    sentiments = ["negative", "neutral", "positive"]
    colors = {"negative": "#ffccc7", "neutral": "#f0f0f0", "positive": "#d9f7be"}
    text_colors = {"negative": "#cf1322", "neutral": "#595959", "positive": "#389e0d"}
    cell_w, cell_h = 70, 32
    header_h = 28
    w = cell_w + len(sentiments) * cell_w
    h = header_h + len(targets) * cell_h
    rects, texts = [], []

    for j, s in enumerate(sentiments):
        x = cell_w + j * cell_w
        rects.append(f'<rect x="{x}" y="0" width="{cell_w}" height="{header_h}" fill="#f3f4f6" stroke="#e5e7eb" stroke-width="1"/>')
        texts.append(f'<text x="{x + cell_w/2}" y="18" text-anchor="middle" font-size="11" fill="#374151">{s}</text>')

    for i, t in enumerate(targets):
        y = header_h + i * cell_h
        rects.append(f'<rect x="0" y="{y}" width="{cell_w}" height="{cell_h}" fill="#fafafa" stroke="#e5e7eb" stroke-width="1"/>')
        texts.append(f'<text x="6" y="{y + 20}" font-size="11" fill="#374151" style="font-weight:500;">{t}</text>')
        for j, s in enumerate(sentiments):
            x = cell_w + j * cell_w
            val = matrix[t].get(s, 0)
            bg = colors.get(s, "#fff") if val > 0 else "#fff"
            rects.append(f'<rect x="{x}" y="{y}" width="{cell_w}" height="{cell_h}" fill="{bg}" stroke="#e5e7eb" stroke-width="1"/>')
            if val > 0:
                texts.append(f'<text x="{x + cell_w/2}" y="{y + 20}" text-anchor="middle" font-size="11" fill="{text_colors.get(s, "#000")}" font-weight="bold">{val}</text>')

    return f"""
    <svg viewBox="0 0 {w} {h}" width="100%" height="{h}" preserveAspectRatio="xMidYMid meet">
      {''.join(rects)}
      {''.join(texts)}
    </svg>"""


# ==================== LLM 智能分析 ====================
def generate_insight_llm(warning_data, risk_data, client):
    score = warning_data.get("total_score", 0)
    level = warning_data.get("risk_level", "low")
    dominant = warning_data.get("dominant_risk", "暂无")
    alert_focus = warning_data.get("alert_focus", dominant)
    trend = warning_data.get("trend_description", "")
    wmeta = warning_data.get("meta", {}) or {}
    negative_rate = wmeta.get("negative_rate", 0)
    tone_label = wmeta.get("sentiment_tone", "未知")
    positive_count = wmeta.get("positive_count", 0)
    sample_caveat = wmeta.get("sample_caveat", False)
    high_count = wmeta.get("high_risk_count") or risk_data.get("meta", {}).get("high_risk_count", 0)
    medium_count = wmeta.get("medium_risk_count") or risk_data.get("meta", {}).get("medium_risk_count", 0)
    actual = wmeta.get("actual") or risk_data.get("meta", {}).get("actual", 0)
    entity_counts = warning_data.get("entity_counts", {})
    top_entities = sorted(entity_counts.items(), key=lambda x: x[1], reverse=True)[:6]
    entity_str = "、".join(f"{k}({v}次)" for k, v in top_entities) if top_entities else "无"

    category_counts = {}
    for p in risk_data.get("data", []):
        cat = p.get("risk_category")
        if cat:
            category_counts[cat] = category_counts.get(cat, 0) + 1
    cat_str = "；".join(f"{k} {v}条" for k, v in sorted(category_counts.items(), key=lambda x: x[1], reverse=True)[:5])

    posts = risk_data.get("data", []) if risk_data else []
    high_info = summarize_high_risk_categories(posts)
    allow_emergency = high_count > 0
    allow_sensitive_alarm = bool(
        wmeta.get("high_risk_has_sensitive") or high_info["has_sensitive"]
    )

    system_prompt = (
        "你是中国足协舆情监测中心的资深分析师。请根据以下监测数据，撰写一份供信息部门领导审阅的舆情研判摘要。"
        "要求：\n"
        "1. 摘要 300~400 字，分三段：①总体态势（评分、等级、须与给出的情绪基调一致）；"
        "②讨论焦点（「主导话题」为发帖量最多类别，「风险焦点」为中高风险集中类别，二者可能不同）；"
        "③升级风险（须基于高风险条数，勿夸大）。\n"
        "2. 情绪基调必须与数据一致：若基调为偏正面，不得写「情绪偏负面」或「负面情绪集中」。\n"
        "3. 无高风险条数时 emergency 必须为空数组。"
        "4. theme_analysis 的每个值为 80 字以内的纯字符串（禁止返回对象或数组）。"
        "5. suggestions 每档为字符串数组，每条为一句完整建议（禁止返回 action/responsibility 等嵌套字段）。"
        "5b. 必须填写 short、medium、long 三档，每档至少 1 条；emergency 仅在有高风险条数时填写。"
        "6. emergency 建议须针对「高风险类别分布」中的实际类别撰写；"
        "除非监测到敏感事件类高风险，否则禁止提及假球、黑哨、赌球、操纵比赛等词。\n"
        "7. 勿使用「升温」「爆发」等词，除非数据明确支持。\n"
        "8. 输出严格 JSON：summary（字符串）、theme_analysis（对象）、suggestions（对象）。"
    )

    nr = negative_rate if isinstance(negative_rate, (int, float)) else 0.0
    user_content = (
        f"监测周期：{wmeta.get('date_range', '未知')} | 样本量：{actual} 条"
        f"{'（小样本，结论宜保守）' if sample_caveat else ''}\n"
        f"总评分：{score}，风险等级：{level}\n"
        f"主导话题（按发帖量）：{dominant}\n"
        f"风险焦点（中高风险集中）：{alert_focus}\n"
        f"情绪基调：{tone_label}（正面约 {positive_count} 条）| 负面率：{nr:.1%}\n"
        f"高风险：{high_count} 条 | 中风险：{medium_count} 条 | 允许 emergency 建议：{'是' if allow_emergency else '否'}\n"
        f"高风险类别分布：{high_info['summary']}\n"
        f"是否含敏感事件类高风险：{'是' if allow_sensitive_alarm else '否'}\n"
        f"风险类别分布：{cat_str}\n"
        f"高频风险实体：{entity_str}\n"
        f"系统趋势判断（请与之保持一致，勿矛盾）：{trend}"
    )

    result = client.chat_json(
        system_prompt,
        user_content,
        temperature=0.2,
        max_tokens=800,
        response_format={"type": "json_object"},
    )
    if not isinstance(result, dict):
        return None
    return normalize_insight_payload(
        result, allow_sensitive_alarm=allow_sensitive_alarm
    )


_RISK_LEVEL_ORDER = {"high": 0, "medium": 1, "low": 2}
_SENTIMENT_NEG_FIRST = {
    "强烈负面": 0,
    "轻微负面": 1,
    "中性": 2,
    "轻微正面": 3,
    "强烈正面": 4,
}


def posts_for_risk_appendix(posts, limit: int = 20):
    """附录表：先 high → medium → low，同等级内负面情感优先。"""

    def sort_key(post: dict):
        lvl = _RISK_LEVEL_ORDER.get(post.get("risk_level", "low"), 3)
        sent = _SENTIMENT_NEG_FIRST.get(post.get("sentiment", "中性"), 2)
        return (lvl, sent)

    return sorted(posts, key=sort_key)[:limit]


def rule_suggestion_tiers(warning_data, risk_data) -> dict:
    """规则模板四档处置建议（供 LLM 缺档时补齐）。"""
    level = warning_data.get("risk_level", "low") if warning_data else "low"
    dominant = warning_data.get("dominant_risk", "暂无") if warning_data else "暂无"
    alert_focus = (warning_data or {}).get("alert_focus", dominant)
    wmeta = (warning_data or {}).get("meta", {}) or {}
    high_count = wmeta.get("high_risk_count") or (
        risk_data.get("meta", {}).get("high_risk_count", 0) if risk_data else 0
    )
    medium_count = wmeta.get("medium_risk_count") or (
        risk_data.get("meta", {}).get("medium_risk_count", 0) if risk_data else 0
    )

    posts = risk_data.get("data", []) if risk_data else []
    high_info = summarize_high_risk_categories(posts)
    has_sensitive = high_info["has_sensitive"]

    if high_count > 0:
        if has_sensitive:
            emergency = [
                "【竞赛部+公关部】对敏感事件类高风险博文逐条核实，2 小时内完成内部通报并评估是否需对外说明。",
            ]
        else:
            emergency = [
                f"【公关部+联赛部】针对高风险类别（{high_info['summary']}）逐条核实事实，"
                f"2 小时内完成内部通报与口径准备。",
            ]
        return {
            "emergency": emergency,
            "short": [
                "【媒体监测组】24 小时内跟踪高风险话题的传播路径与关键转发节点。",
            ],
            "medium": [
                "【信息中心】本周复盘高风险判定的准确率，优化关键词与 ABSA 规则。",
            ],
            "long": [
                "【联赛部】完善赛后信息沟通机制，减少信息真空引发的猜测性讨论。",
            ],
        }
    if level == "medium":
        return {
            "emergency": [],
            "short": [
                f"【媒体监测组】跟踪「{alert_focus}」相关讨论的后续走向，记录 KOL 与球迷情绪变化。",
            ],
            "medium": [
                "【联赛部】关注下一比赛日现场与线上的联动话题，防止争议线下化。",
            ],
            "long": [
                "【信息中心】定期更新监测关键词与语义过滤规则，降低同质词噪声。",
            ],
        }
    return {
        "emergency": [],
        "short": ["【公关部】可择机发布训练花絮或球员专访等正向内容。"],
        "medium": ["【联赛部】定期复盘监测关键词库，补充新热词。"],
        "long": ["【信息中心】积累本周期基线数据，用于后续阈值校准。"],
    }


def fill_missing_suggestion_tiers(insight, warning_data, risk_data):
    """LLM 未返回的 short/medium/long（或 emergency）用规则模板补缺，不覆盖已有内容。"""
    if not insight or not warning_data or not risk_data:
        return insight
    fallback = rule_suggestion_tiers(warning_data, risk_data)
    sugg = insight.setdefault("suggestions", {})
    for key in ("emergency", "short", "medium", "long"):
        if sugg.get(key):
            continue
        tier = fallback.get(key) or []
        if tier:
            sugg[key] = list(tier)
    return insight


def generate_insight_rule(warning_data, risk_data):
    level = warning_data.get("risk_level", "low") if warning_data else "low"
    dominant = warning_data.get("dominant_risk", "暂无") if warning_data else "暂无"
    alert_focus = (warning_data or {}).get("alert_focus", dominant)
    wmeta = (warning_data or {}).get("meta", {}) or {}
    tone = wmeta.get("sentiment_tone", "中性为主")
    high_count = wmeta.get("high_risk_count") or (risk_data.get("meta", {}).get("high_risk_count", 0) if risk_data else 0)
    medium_count = wmeta.get("medium_risk_count") or (risk_data.get("meta", {}).get("medium_risk_count", 0) if risk_data else 0)
    trend = (warning_data or {}).get("trend_description", "")

    theme_analysis = {}
    for p in risk_data.get("data", []) if risk_data else []:
        cat = p.get("risk_category", "一般舆情")
        if cat not in theme_analysis:
            theme_analysis[cat] = {"count": 0, "high": 0, "medium": 0}
        theme_analysis[cat]["count"] += 1
        if p.get("risk_level") == "high":
            theme_analysis[cat]["high"] += 1
        elif p.get("risk_level") == "medium":
            theme_analysis[cat]["medium"] += 1

    theme_text = {}
    for cat, info in sorted(theme_analysis.items(), key=lambda x: x[1]["count"], reverse=True)[:4]:
        if info["high"] or info["medium"]:
            theme_text[cat] = (
                f"共 {info['count']} 条，其中高风险 {info['high']} 条、中风险 {info['medium']} 条，"
                f"建议结合具体博文复核，不宜仅凭类别名判断严重程度。"
            )
        else:
            theme_text[cat] = (
                f"共 {info['count']} 条，均为低风险讨论，情绪基调与整体监测一致（{tone}），保持常规监测即可。"
            )

    focus_note = (
        f"发帖量最多的是「{dominant}」"
        + (f"，中高风险较集中的是「{alert_focus}」。" if alert_focus != dominant else "。")
    )

    posts = risk_data.get("data", []) if risk_data else []
    high_info = summarize_high_risk_categories(posts)
    has_sensitive = high_info["has_sensitive"]
    suggestions = rule_suggestion_tiers(warning_data, risk_data)

    if high_count > 0:
        summary = (
            f"本次监测出现 {high_count} 条高风险微博（{high_info['summary']}），{focus_note}"
            f"整体情绪基调为{tone}。{trend}"
        )
    elif level == "medium":
        summary = (
            f"舆情评分处于中等水平，{focus_note}"
            f"共 {medium_count} 条中风险内容。情绪基调为{tone}，未见高风险集中。{trend}"
        )
    else:
        summary = (
            f"整体舆情平稳，{focus_note}"
            f"情绪基调为{tone}，未发现需紧急处置的高风险信号。{trend}"
        )

    return normalize_insight_payload(
        {"summary": summary, "theme_analysis": theme_text, "suggestions": suggestions},
        allow_sensitive_alarm=has_sensitive if high_count > 0 else False,
    )


# ==================== 主逻辑 ====================
def run_report_html(
    warning_arg: str | None,
    risk_arg: str | None,
    use_llm_insight: bool = True,
) -> Path:
    warning_data = safe_load(warning_arg) if warning_arg else None
    risk_data = safe_load(risk_arg) if risk_arg else None

    if not warning_data or not risk_data:
        print("报告生成：未提供完整 warning/risk 数据，尝试自动从 data 目录读取...")
        warnings = sorted(DATA_DIR.glob("warning_*.json"), key=lambda x: x.stat().st_mtime, reverse=True)
        risks = sorted(DATA_DIR.glob("risk_*.json"), key=lambda x: x.stat().st_mtime, reverse=True)
        if not warning_data and warnings:
            warning_data = safe_load(str(warnings[0]))
            print(f"  自动读取 warning: {warnings[0].name}")
        if not risk_data and risks:
            risk_data = safe_load(str(risks[0]))
            print(f"  自动读取 risk: {risks[0].name}")

    keyword = "未知"
    if warning_data:
        keyword = warning_data.get("meta", {}).get("keyword", keyword)
    elif risk_data:
        keyword = risk_data.get("meta", {}).get("keyword", keyword)

    score = warning_data.get("total_score", "暂无") if warning_data else "暂无"
    level = warning_data.get("risk_level", "暂无") if warning_data else "暂无"
    dominant = warning_data.get("dominant_risk", "暂无") if warning_data else "暂无"
    alert_focus = warning_data.get("alert_focus", dominant) if warning_data else dominant
    sentiment_tone = (
        warning_data.get("meta", {}).get("sentiment_tone", "暂无") if warning_data else "暂无"
    )
    trend = warning_data.get("trend_description", "暂无") if warning_data else "暂无"
    negative_rate = warning_data.get("meta", {}).get("negative_rate", "暂无") if warning_data else "暂无"
    top_entities = warning_data.get("entity_counts", {}) if warning_data else {}

    meta = risk_data.get("meta", {}) if risk_data else {}
    actual = meta.get("actual", 0)
    high_risk_count = meta.get("high_risk_count", 0)
    medium_risk_count = meta.get("medium_risk_count", 0)
    low_risk_count = meta.get("low_risk_count", 0)
    posts = risk_data.get("data", []) if risk_data else []

    category_counts, topic_counts = {}, {}
    for p in posts:
        cat = p.get("risk_category")
        if cat:
            category_counts[cat] = category_counts.get(cat, 0) + 1
        topic = p.get("topic_label")
        if topic:
            topic_counts[topic] = topic_counts.get(topic, 0) + 1

    gauge_chart = svg_gauge(score, level)
    donut_chart = svg_risk_donut(high_risk_count, medium_risk_count, low_risk_count, actual)
    sentiment_chart = svg_sentiment_bars(posts)
    category_chart = svg_horizontal_bars(category_counts, color="#2e4668", max_items=6)
    entity_chart = svg_horizontal_bars(top_entities, color="#f5222d", max_items=10)
    trend_chart = svg_time_trend(posts)
    absa_chart = svg_absa_matrix(posts)

    insight_from_llm = False
    insight = None
    if warning_data and risk_data and use_llm_insight:
        client = try_llm_client()
        if client is not None:
            insight = generate_insight_llm(warning_data, risk_data, client)
            if insight:
                insight_from_llm = True
    if not insight:
        insight = generate_insight_rule(warning_data, risk_data)
    elif warning_data and risk_data:
        insight = fill_missing_suggestion_tiers(insight, warning_data, risk_data)

    summary_html = f"<p style='line-height:1.8;color:#374151;'>{insight['summary']}</p>" if insight else "<div class='no-data'>暂无分析数据</div>"

    theme_blocks = []
    if insight and insight.get("theme_analysis"):
        for cat, analysis in insight["theme_analysis"].items():
            analysis_text = flatten_insight_text(analysis)
            theme_blocks.append(f"""
            <div style="margin-bottom:10px;padding:10px 14px;background:#f9fafb;border-radius:8px;border-left:3px solid #2e4668;">
                <div style="font-weight:600;color:#1f2937;font-size:14px;margin-bottom:4px;">{cat}</div>
                <div style="color:#4b5563;font-size:13px;line-height:1.6;">{analysis_text}</div>
            </div>""")
    theme_section = "".join(theme_blocks) if theme_blocks else "<div class='no-data'>暂无分主题数据</div>"

    sugg_sections = []
    priority_meta = {
        "emergency": ("🔴 紧急处置（0-2 小时）", "#fef2f2", "#dc2626", "#fee2e2"),
        "short": ("🟠 短期应对（24 小时内）", "#fff7ed", "#ea580c", "#ffedd5"),
        "medium": ("🟡 中期跟进（本周）", "#fefce8", "#ca8a04", "#fef9c3"),
        "long": ("🟢 长期建设（机制层面）", "#f0fdf4", "#16a34a", "#dcfce7"),
    }
    for key, (title, bg, border, item_bg) in priority_meta.items():
        items = insight.get("suggestions", {}).get(key, []) if insight else []
        if not items:
            continue
        li_html = "".join(f'<li style="margin-bottom:8px;line-height:1.7;">{s}</li>' for s in items)
        sugg_sections.append(f"""
        <div style="margin-bottom:16px;padding:14px;border-radius:10px;background:{bg};border:1px solid {border};">
            <div style="font-weight:700;color:{border};font-size:15px;margin-bottom:10px;">{title}</div>
            <ul style="margin:0;padding-left:20px;color:#374151;font-size:13px;">
                {li_html}
            </ul>
        </div>""")
    sugg_html = "".join(sugg_sections) if sugg_sections else "<div class='no-data'>暂无建议</div>"

    risk_rows = []
    if posts:
        for item in posts_for_risk_appendix(posts, limit=20):
            mid = item.get("mid", "")
            cat = item.get("risk_category", "")
            lvl = item.get("risk_level", "")
            ents = "、".join(item.get("risk_entities", [])[:3]) or "-"
            sentiment = item.get("sentiment", "")
            raw = item.get("clean_text", "") or item.get("raw_text", "")
            raw_display = raw[:45] + "…" if len(raw) > 45 else raw
            raw_display = raw_display.replace("<html>", "").replace("</html>", "")
            risk_rows.append(
                f"<tr><td>{mid}</td><td>{raw_display}</td><td>{cat}</td>"
                f"<td class='tag-{lvl}'>{lvl}</td><td>{sentiment}</td><td>{ents}</td></tr>"
            )
    table_body = "".join(risk_rows) if risk_rows else "<tr><td colspan='6' style='text-align:center;color:#999;'>暂无数据</td></tr>"

    html = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>足球舆情监测研判报告 - {keyword}</title>
<style>
  @media print {{
    body {{ background: #fff; }}
    .card {{ box-shadow: none; border: 1px solid #e5e7eb; break-inside: avoid; }}
    .no-print {{ display: none; }}
  }}
  body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif;
         max-width: 1100px; margin: 0 auto; padding: 24px; background: #f5f6f8; color: #1f2937; }}
  .card {{ background: #fff; border-radius: 14px; padding: 24px; margin-bottom: 24px;
          box-shadow: 0 4px 20px rgba(0,0,0,0.06); }}
  h1 {{ font-size: 24px; margin: 0 0 8px 0; color: #111827; }}
  h2 {{ font-size: 18px; margin: 0 0 16px 0; color: #374151; border-left: 4px solid #2e4668; padding-left: 10px; }}
  h3 {{ font-size: 15px; margin: 12px 0 8px 0; color: #4b5563; }}
  .subtitle {{ color: #6b7280; font-size: 14px; margin-bottom: 4px; }}
  .header-bar {{ display: flex; justify-content: space-between; align-items: center; flex-wrap: wrap; gap: 8px; margin-bottom: 4px; }}
  .confidential {{ display: inline-block; padding: 2px 10px; border-radius: 4px; font-size: 11px; font-weight: 700;
                   background: #fee2e2; color: #991b1b; border: 1px solid #fecaca; }}
  .metric-grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(140px, 1fr)); gap: 16px; margin-top: 12px; }}
  .metric {{ background: #f9fafb; border-radius: 10px; padding: 16px; text-align: center; border: 1px solid #e5e7eb; }}
  .metric-value {{ font-size: 26px; font-weight: 700; color: #1f2937; }}
  .metric-label {{ color: #6b7280; margin-top: 6px; font-size: 13px; }}
  .chart-grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(320px, 1fr)); gap: 24px; margin-top: 8px; }}
  .chart-box {{ background: #fafafa; border-radius: 10px; padding: 16px; border: 1px solid #e5e7eb; }}
  .chart-title {{ font-size: 14px; color: #4b5563; margin-bottom: 12px; font-weight: 600; }}
  .no-data {{ text-align: center; color: #9ca3af; padding: 40px 0; font-size: 14px; }}
  .legend-bar {{ margin-top: 12px; display: flex; flex-wrap: wrap; gap: 12px; justify-content: center; font-size: 12px; color: #4b5563; }}
  .legend-item {{ display: inline-flex; align-items: center; gap: 6px; }}
  .dot {{ width: 10px; height: 10px; border-radius: 50%; display: inline-block; }}
  table {{ width: 100%; border-collapse: collapse; margin-top: 12px; font-size: 12px; }}
  th, td {{ border: 1px solid #e5e7eb; padding: 8px 10px; text-align: left; vertical-align: top; }}
  th {{ background: #f3f4f6; font-weight: 600; color: #374151; }}
  tr:hover {{ background: #f9fafb; }}
  .tag-high {{ color: #dc2626; font-weight: 700; }}
  .tag-medium {{ color: #ea580c; font-weight: 700; }}
  .tag-low {{ color: #16a34a; font-weight: 700; }}
  .insight-box {{ background: #f0f7ff; border-radius: 10px; padding: 18px; border: 1px solid #d1e3f6; margin-top: 12px; }}
  .insight-box h3 {{ margin: 0 0 10px 0; font-size: 15px; color: #1d4ed8; }}
  .footer {{ text-align: center; color: #9ca3af; font-size: 12px; margin-top: 40px; padding-bottom: 20px; }}
  .page-break {{ page-break-before: always; }}
</style>
</head>
<body>

<div class="card">
  <div class="header-bar">
    <div>
      <div style="font-size:13px;color:#6b7280;margin-bottom:6px;">中国足球协会 · 信息中心 · 舆情监测室</div>
      <h1>足球舆情监测研判报告</h1>
      <div class="subtitle">监测关键词：{keyword} | 样本量：{actual} 条 | 报告生成：{datetime.now().strftime('%Y-%m-%d %H:%M')}</div>
    </div>
    <div style="text-align:right;">
      <span class="confidential">测试版</span>
      <div style="font-size:36px;font-weight:800;color:{'#dc2626' if level=='high' else '#ea580c' if level=='medium' else '#16a34a'};margin-top:6px;">{level.upper()}</div>
      <div style="font-size:12px;color:#6b7280;">风险等级</div>
    </div>
  </div>
</div>

<div class="card">
  <h2>核心指标看板</h2>
  <div class="metric-grid">
    {make_metric("总评分", score)}
    {make_metric("主导话题", dominant)}
    {make_metric("风险焦点", alert_focus if alert_focus != dominant else "—")}
    {make_metric("情绪基调", sentiment_tone)}
    {make_metric("高风险条数", high_risk_count)}
    {make_metric("负面率", f"{negative_rate:.1%}" if isinstance(negative_rate, (int, float)) else negative_rate)}
    {make_metric("中风险条数", medium_risk_count)}
    {make_metric("低风险条数", low_risk_count)}
  </div>
  <div style="margin-top:16px;padding:12px;background:#f9fafb;border-radius:8px;border-left:4px solid {'#dc2626' if level=='high' else '#ea580c' if level=='medium' else '#16a34a'};color:#374151;font-size:14px;line-height:1.6;">
    <strong>趋势研判：</strong>{trend}
  </div>
</div>

<div class="card">
  <h2>传播态势与情绪分布</h2>
  <div class="chart-grid">
    <div class="chart-box">
      <div class="chart-title">评分仪表盘</div>
      {gauge_chart}
    </div>
    <div class="chart-box">
      <div class="chart-title">风险等级分布</div>
      {donut_chart}
    </div>
    <div class="chart-box">
      <div class="chart-title">情感分布</div>
      {sentiment_chart}
    </div>
    <div class="chart-box">
      <div class="chart-title">时间趋势（high / medium / negative）</div>
      {trend_chart}
    </div>
    <div class="chart-box">
      <div class="chart-title">风险类别 TOP5</div>
      {category_chart}
    </div>
    <div class="chart-box">
      <div class="chart-title">高频风险实体 TOP10</div>
      {entity_chart}
    </div>
    <div class="chart-box" style="grid-column: 1 / -1;">
      <div class="chart-title">ABSA 对象-情感矩阵（对象 × 情感频次）</div>
      {absa_chart}
    </div>
  </div>
</div>

<div class="card">
  <h2>舆情研判摘要
    <span style="font-size:12px;font-weight:400;color:#6b7280;margin-left:8px;">
      {'（LLM 生成）' if insight_from_llm else '（规则模板）'}
    </span>
  </h2>
  <div class="insight-box">
    <h3>总体态势</h3>
    {summary_html}
  </div>
  <div style="margin-top:16px;">
    <h3>分主题风险剖析</h3>
    {theme_section}
  </div>
</div>

<div class="card">
  <h2>分级处置建议</h2>
  {sugg_html}
</div>

<div class="card page-break">
  <h2>风险明细附录（前 20 条）</h2>
  <table>
    <thead>
      <tr><th>微博 ID</th><th>内容摘要</th><th>风险类别</th><th>等级</th><th>情感</th><th>风险实体</th></tr>
    </thead>
    <tbody>
      {table_body}
    </tbody>
  </table>
  <div style="margin-top:10px;font-size:12px;color:#9ca3af;">
    * 附录按风险等级（high → medium → low）及负面情感优先排序，仅展示前 20 条；完整数据请查阅 risk_*.json 与 warning_*.json。
  </div>
</div>

<div class="footer">
  中国足球协会信息中心 · 舆情监测系统自动生成 · 仅供内部决策参考
</div>

</body>
</html>"""

    output_path = build_output_path(keyword)
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(html)

    print(f"HTML 报告已生成: {output_path}")
    print(f"智能分析来源: {'LLM 生成' if insight_from_llm else '规则模板'}")
    print(f"请用浏览器直接打开查看；支持打印（Ctrl+P）导出 PDF。")
    return output_path


def main():
    parser = argparse.ArgumentParser(description="足协舆情监测 HTML 报告生成（专业研判版）")
    parser.add_argument("--warning", help="warning JSON 文件路径")
    parser.add_argument("--risk", help="risk JSON 文件路径")
    parser.add_argument(
        "--rule-report-only",
        action="store_true",
        help="研判摘要仅用规则模板，不调用 LLM（加速；默认与旧版一致：有 Key 则先尝试 LLM）",
    )
    args = parser.parse_args()
    run_report_html(args.warning, args.risk, use_llm_insight=not args.rule_report_only)


if __name__ == "__main__":
    main()