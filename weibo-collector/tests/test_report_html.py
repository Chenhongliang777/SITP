import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from report_html import fill_missing_suggestion_tiers, posts_for_risk_appendix


class TestReportHtmlHelpers(unittest.TestCase):
    def test_appendix_sorts_high_first(self):
        posts = [
            {"risk_level": "low", "sentiment": "强烈正面"},
            {"risk_level": "high", "sentiment": "轻微负面"},
            {"risk_level": "medium", "sentiment": "强烈负面"},
        ]
        ordered = posts_for_risk_appendix(posts, limit=3)
        self.assertEqual(ordered[0]["risk_level"], "high")
        self.assertEqual(ordered[1]["risk_level"], "medium")

    def test_fill_missing_suggestion_tiers(self):
        insight = {
            "summary": "x",
            "theme_analysis": {},
            "suggestions": {
                "emergency": ["LLM 紧急建议"],
            },
        }
        warning = {"risk_level": "medium", "dominant_risk": "球迷文化舆论", "meta": {"high_risk_count": 1}}
        risk = {
            "meta": {"high_risk_count": 1, "medium_risk_count": 0},
            "data": [{"risk_level": "high", "risk_category": "球队表现舆论"}],
        }
        out = fill_missing_suggestion_tiers(insight, warning, risk)
        self.assertTrue(out["suggestions"]["emergency"])
        self.assertTrue(out["suggestions"]["short"])
        self.assertTrue(out["suggestions"]["medium"])
        self.assertTrue(out["suggestions"]["long"])


if __name__ == "__main__":
    unittest.main()
