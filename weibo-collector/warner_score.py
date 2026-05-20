import argparse
import json
from datetime import datetime
from pathlib import Path

from utils.analysis_helpers import (
    build_trend,
    category_weight_for_scoring,
    compute_sentiment_tone,
    pick_alert_focus,
    pick_dominant_by_count,
    summarize_high_risk_categories,
)

from utils.project_root import get_project_root

SCRIPT_DIR = get_project_root()
DATA_DIR = SCRIPT_DIR / "data"
DATA_DIR.mkdir(exist_ok=True)


def build_output_path(keyword: str):
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    return DATA_DIR / f"warning_{keyword}_{stamp}.json"


def classify_risk(total_score: float):
    if total_score >= 60:
        return "high"
    if total_score >= 35:
        return "medium"
    return "low"


def run_warner(input_path: Path) -> Path:
    with open(input_path, "r", encoding="utf-8") as f:
        payload = json.load(f)

    keyword = payload.get("meta", {}).get("keyword") or input_path.stem
    posts = payload.get("data", [])
    total = len(posts)
    if total == 0:
        raise SystemExit("输入文件中没有数据")

    high_count = sum(1 for p in posts if p.get("risk_level") == "high")
    medium_count = sum(1 for p in posts if p.get("risk_level") == "medium")
    low_count = total - high_count - medium_count

    extreme_neg_count = sum(1 for p in posts if p.get("sentiment") == "强烈负面")
    slight_neg_count = sum(1 for p in posts if p.get("sentiment") == "轻微负面")

    entity_counts = {}
    for p in posts:
        for ent in p.get("risk_entities", []):
            entity_counts[ent] = entity_counts.get(ent, 0) + 1

    category_counts = {}
    for p in posts:
        cat = p.get("risk_category", "未分类")
        category_counts[cat] = category_counts.get(cat, 0) + 1

    dominant_cat, dominant_count = pick_dominant_by_count(category_counts)
    alert_cat, alert_count = pick_alert_focus(posts)
    concentration_ratio = dominant_count / total if total else 0.0

    tone = compute_sentiment_tone(posts)

    high_ratio = high_count / total
    medium_ratio = medium_count / total
    extreme_ratio = extreme_neg_count / total
    slight_ratio = slight_neg_count / total

    density_score = (
        high_ratio * 150
        + medium_ratio * 80
        + extreme_ratio * 60
        + slight_ratio * 15
    )

    typical_bonus = 20 if high_count > 0 else 0

    concentration_bonus = 0.0
    if category_weight_for_scoring(alert_cat) > 0 and concentration_ratio >= 0.10:
        alert_share = alert_count / total if total else 0.0
        concentration_bonus = min(10.0, max(0.0, (alert_share - 0.05) * 80))

    total_score = min(100.0, density_score + typical_bonus + concentration_bonus)

    contributions = []
    for cat, count in sorted(category_counts.items(), key=lambda x: x[1], reverse=True):
        cat_high = sum(
            1
            for p in posts
            if p.get("risk_category") == cat and p.get("risk_level") == "high"
        )
        cat_medium = sum(
            1
            for p in posts
            if p.get("risk_category") == cat and p.get("risk_level") == "medium"
        )
        cat_extreme = sum(
            1
            for p in posts
            if p.get("risk_category") == cat and p.get("sentiment") == "强烈负面"
        )
        cat_slight = sum(
            1
            for p in posts
            if p.get("risk_category") == cat and p.get("sentiment") == "轻微负面"
        )

        if cat_high > 0:
            level = "high"
        elif cat_medium > 0:
            level = "medium"
        else:
            level = "low"

        score_contrib = (
            (cat_high / total) * 150
            + (cat_medium / total) * 80
            + (cat_extreme / total) * 60
            + (cat_slight / total) * 15
        )

        contributions.append({
            "category": cat,
            "count": count,
            "ratio": round(count / total, 4),
            "risk_level": level,
            "score_contribution": round(score_contrib, 2),
        })

    risk_level = classify_risk(total_score)
    neg_total = extreme_neg_count + slight_neg_count
    negative_rate = round(neg_total / total, 4) if total else 0.0

    high_summary = summarize_high_risk_categories(posts)
    trend = build_trend(
        total_score,
        high_count,
        medium_count,
        dominant_cat,
        concentration_ratio,
        negative_rate,
        tone["tone_label"],
        total,
        alert_focus=alert_cat,
    )

    output = {
        "meta": {
            "keyword": keyword,
            "date_range": payload.get("meta", {}).get("date_range", ""),
            "actual": total,
            "high_risk_count": high_count,
            "medium_risk_count": medium_count,
            "low_risk_count": low_count,
            "extreme_negative_count": extreme_neg_count,
            "negative_count": neg_total,
            "negative_rate": negative_rate,
            "positive_count": tone["positive_count"],
            "sentiment_tone": tone["tone_label"],
            "sample_caveat": total < 30,
            "high_risk_summary": high_summary["summary"],
            "high_risk_has_sensitive": high_summary["has_sensitive"],
        },
        "total_score": round(total_score, 2),
        "risk_level": risk_level,
        "dominant_risk": dominant_cat,
        "alert_focus": alert_cat,
        "dominant_contribution": {
            "count": dominant_count,
            "ratio": round(concentration_ratio, 4),
            "description": (
                f"{dominant_count} 条微博涉及 {dominant_cat}，"
                f"占总样本 {concentration_ratio:.1%}"
            ),
        },
        "alert_focus_contribution": {
            "count": alert_count,
            "description": (
                f"中高风险条目最多的类别为 {alert_cat}（{alert_count} 条）"
                if alert_count
                else "暂无中高风险集中类别"
            ),
        },
        "category_breakdown": contributions,
        "entity_counts": entity_counts,
        "trend_description": trend,
        "generated_at": datetime.now().isoformat(),
    }

    output_path = build_output_path(keyword)
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)

    print(f"预警评分完成，总分 {total_score:.2f}，等级 {risk_level}")
    print(
        f"主导话题(按量): {dominant_cat} ({dominant_count}/{total}, "
        f"{concentration_ratio:.1%})"
    )
    if alert_cat != dominant_cat:
        print(f"风险焦点: {alert_cat} ({alert_count} 条中高风险)")
    print(f"结果已写入: {output_path}")
    return output_path


def main():
    parser = argparse.ArgumentParser(description="预警评分脚本（密度制，与采集总量无关）")
    parser.add_argument("--input", required=True, help="上游 risk JSON 文件")
    args = parser.parse_args()

    input_path = Path(args.input)
    if not input_path.exists():
        raise SystemExit(f"未找到输入文件: {input_path}")

    run_warner(input_path)


if __name__ == "__main__":
    main()
