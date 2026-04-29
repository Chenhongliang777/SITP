import argparse
import json
from datetime import datetime
from pathlib import Path

SCRIPT_DIR = Path(__file__).parent.resolve()
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


def build_trend(total_score: float, high_count: int, dominant_cat: str):
    if total_score >= 80:
        return "当前出现敏感事件或高风险集中爆发，建议立即启动应急响应并上报主管部门。"
    if total_score >= 60:
        return f"负面舆情强烈，{dominant_cat}为主要矛盾点，建议立即介入并密切跟进传播态势。"
    if total_score >= 35:
        return f"舆情存在升温迹象，{dominant_cat}需重点监测，建议加强应对准备。"
    return "舆情总体平稳，以常规跟踪为主，关注潜在发酵点。"


def category_weight(category: str) -> int:
    """动态权重：根据类别名称语义自动赋权"""
    cat = category.lower()
    if "敏感" in cat:
        return 3
    if "风险" in cat and "舆论" not in cat:
        return 2
    if "负面" in cat:
        return 1
    return 0


def main():
    parser = argparse.ArgumentParser(description="预警评分脚本（密度制，与采集总量无关）")
    parser.add_argument("--input", required=True, help="上游 risk JSON 文件")
    args = parser.parse_args()

    input_path = Path(args.input)
    if not input_path.exists():
        raise SystemExit(f"未找到输入文件: {input_path}")

    with open(input_path, "r", encoding="utf-8") as f:
        payload = json.load(f)

    keyword = payload.get("meta", {}).get("keyword") or input_path.stem
    posts = payload.get("data", [])
    total = len(posts)
    if total == 0:
        raise SystemExit("输入文件中没有数据")

    # ---------- 1. 基础统计 ----------
    high_count = sum(1 for p in posts if p.get("risk_level") == "high")
    medium_count = sum(1 for p in posts if p.get("risk_level") == "medium")
    low_count = total - high_count - medium_count

    extreme_neg_count = sum(1 for p in posts if p.get("sentiment") == "强烈负面")
    slight_neg_count = sum(1 for p in posts if p.get("sentiment") == "轻微负面")

    # 风险实体频次
    entity_counts = {}
    for p in posts:
        for ent in p.get("risk_entities", []):
            entity_counts[ent] = entity_counts.get(ent, 0) + 1

    # 风险类别分布
    category_counts = {}
    for p in posts:
        cat = p.get("risk_category", "未分类")
        category_counts[cat] = category_counts.get(cat, 0) + 1

    # ---------- 2. 主导风险（动态加权） ----------
    weighted_scores = {
        cat: count * category_weight(cat)
        for cat, count in category_counts.items()
    }
    meaningful = {k: v for k, v in weighted_scores.items() if v > 0}

    if meaningful:
        dominant_cat = max(meaningful, key=meaningful.get)
    else:
        filtered = {k: v for k, v in category_counts.items() if k != "未分类"}
        dominant_cat = max(filtered, key=filtered.get) if filtered else "未分类"

    dominant_count = category_counts[dominant_cat]
    concentration_ratio = dominant_count / total

    # ---------- 3. 评分公式（密度制 + 固定保底） ----------
    # 核心：所有分数基于"比例"而非"绝对数量"，确保 30 条和 300 条同比例同分
    high_ratio = high_count / total
    medium_ratio = medium_count / total
    extreme_ratio = extreme_neg_count / total
    slight_ratio = slight_neg_count / total

    # 3.1 风险密度分（封顶约 45 分）
    density_score = (
        high_ratio * 150 +      # high 密度权重最高
        medium_ratio * 80 +     # medium 次之
        extreme_ratio * 60 +    # 强烈负面情感密度
        slight_ratio * 15       # 轻微负面情感密度
    )

    # 3.2 典型事件保底（固定值，不随数量膨胀）
    # 只要出现 high，说明已触及红线，+20 保底
    typical_bonus = 20 if high_count > 0 else 0

    # 3.3 传播集中度加成（基于比例，自然与总量无关）
    concentration_bonus = 0.0
    if category_weight(dominant_cat) > 0 and concentration_ratio >= 0.10:
        concentration_bonus = min(10.0, (concentration_ratio - 0.10) * 100)

    total_score = min(100.0, density_score + typical_bonus + concentration_bonus)

    # ---------- 4. 贡献度明细 ----------
    contributions = []
    for cat, count in sorted(category_counts.items(), key=lambda x: x[1], reverse=True):
        w = category_weight(cat)
        level = "high" if w >= 3 else "medium" if w >= 2 else "medium" if w == 1 else "low"

        # 该类别对总分的贡献（按密度反推，用于展示）
        score_contrib = 0
        cat_high = sum(1 for p in posts if p.get("risk_category") == cat and p.get("risk_level") == "high")
        cat_medium = sum(1 for p in posts if p.get("risk_category") == cat and p.get("risk_level") == "medium")
        cat_extreme = sum(1 for p in posts if p.get("risk_category") == cat and p.get("sentiment") == "强烈负面")
        cat_slight = sum(1 for p in posts if p.get("risk_category") == cat and p.get("sentiment") == "轻微负面")

        score_contrib += (cat_high / total) * 150
        score_contrib += (cat_medium / total) * 80
        score_contrib += (cat_extreme / total) * 60
        score_contrib += (cat_slight / total) * 15

        contributions.append({
            "category": cat,
            "count": count,
            "ratio": round(count / total, 4),
            "risk_level": level,
            "score_contribution": round(score_contrib, 2),
        })

    risk_level = classify_risk(total_score)
    trend = build_trend(total_score, high_count, dominant_cat)

    # ---------- 5. 输出 ----------
    output = {
        "meta": {
            "keyword": keyword,
            "date_range": payload.get("meta", {}).get("date_range", ""),
            "actual": total,
            "high_risk_count": high_count,
            "medium_risk_count": medium_count,
            "low_risk_count": low_count,
            "extreme_negative_count": extreme_neg_count,
            "negative_count": extreme_neg_count + slight_neg_count,
        },
        "total_score": round(total_score, 2),
        "risk_level": risk_level,
        "dominant_risk": dominant_cat,
        "dominant_contribution": {
            "count": dominant_count,
            "ratio": round(concentration_ratio, 4),
            "description": f"{dominant_count} 条微博涉及 {dominant_cat}，占总样本 {concentration_ratio:.1%}",
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
    print(f"主导风险: {dominant_cat} ({dominant_count}/{total}, {concentration_ratio:.1%})")
    print(f"结果已写入: {output_path}")


if __name__ == "__main__":
    main()