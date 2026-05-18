import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from utils.analysis_helpers import (
    build_trend,
    canonicalize_risk_category,
    filter_sensitive_alarm_phrases,
    flatten_insight_text,
    keyword_false_positive_reason,
    normalize_insight_payload,
    pick_alert_focus,
    pick_dominant_by_count,
    reconcile_sentiment_with_absa,
)


class TestDominantRisk(unittest.TestCase):
    def test_volume_beats_name_weight(self):
        counts = {"球迷文化舆论": 60, "裁判执法风险": 1}
        cat, n = pick_dominant_by_count(counts)
        self.assertEqual(cat, "球迷文化舆论")
        self.assertEqual(n, 60)

    def test_alert_focus_uses_severity(self):
        posts = [
            {"risk_category": "球迷文化舆论", "risk_level": "low"},
            {"risk_category": "裁判争议风险", "risk_level": "medium"},
        ]
        cat, _ = pick_alert_focus(posts)
        self.assertEqual(cat, "裁判争议风险")


class TestHomonymFilter(unittest.TestCase):
    def test_basketball_phrase(self):
        reason = keyword_false_positive_reason(
            "篮球博主：投中超远三分球太帅了", "中超"
        )
        self.assertIsNotNone(reason)

    def test_football_kept(self):
        reason = keyword_false_positive_reason(
            "#中超联赛# 本轮德比进球精彩", "中超联赛"
        )
        self.assertIsNone(reason)


class TestSentimentReconcile(unittest.TestCase):
    def test_neg_overridden_by_positive_aspects(self):
        post = {
            "sentiment": "强烈负面",
            "aspect_sentiments": [
                {"target": "央视", "sentiment": "positive"},
                {"target": "FIFA", "sentiment": "positive"},
            ],
        }
        reconcile_sentiment_with_absa(post)
        self.assertIn(post["sentiment"], ("中性", "轻微正面"))


class TestTrend(unittest.TestCase):
    def test_medium_no_false_warming(self):
        text = build_trend(
            40, 0, 2, "球迷文化舆论", 0.5, 0.2, "偏正面", 177
        )
        self.assertNotIn("升温", text)

    def test_few_high_not_escalate_to_report(self):
        text = build_trend(
            38.64,
            4,
            18,
            "球迷文化舆论",
            0.42,
            0.24,
            "偏正面",
            177,
            alert_focus="球队表现舆论",
        )
        self.assertNotIn("上报主管部门", text)
        self.assertIn("4 条高风险", text)


class TestInsightNormalize(unittest.TestCase):
    def test_flatten_theme_dict(self):
        raw = {"球迷文化舆论": {"发帖量": 74, "主要观点": "讨论活跃"}}
        out = normalize_insight_payload({"summary": "x", "theme_analysis": raw, "suggestions": {}})
        self.assertIn("74", out["theme_analysis"]["球迷文化舆论"])
        self.assertNotIn("{", out["theme_analysis"]["球迷文化舆论"])

    def test_flatten_suggestion_dict(self):
        raw = {
            "suggestions": {
                "emergency": [
                    {"action": "核查内容", "responsibility": "公关部"}
                ]
            }
        }
        out = normalize_insight_payload(
            {"summary": "", "theme_analysis": {}, **raw},
            allow_sensitive_alarm=False,
        )
        self.assertEqual(out["suggestions"]["emergency"][0], "【公关部】核查内容")

    def test_strip_fake_ball_when_not_sensitive(self):
        text = "评估是否涉及假球、黑哨等严重违规"
        cleaned = filter_sensitive_alarm_phrases(text, allow_sensitive=False)
        self.assertNotIn("假球", cleaned)


class TestCanonicalCategory(unittest.TestCase):
    def test_referee_merge(self):
        self.assertEqual(
            canonicalize_risk_category("VAR漏判风险"), "裁判争议风险"
        )


if __name__ == "__main__":
    unittest.main()
