"""
研判智能体
职责：对感知层 JSON 进行主题、情感、时序、传播、风险因子分析
输出：judgment_{keyword}_{timestamp}.json（纯事实，不做风险判断）
"""

import json
import re
import sys
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path

import jieba
import jieba.analyse
import requests

from sentiment_engine import SentimentEngine

SCRIPT_DIR = Path(__file__).parent.resolve()
PROJECT_DIR = SCRIPT_DIR.parent
DATA_DIR = PROJECT_DIR / "data"

DS_KEY = "sk-f1da75d5e90945daa3de76ad9791c8a4"
DS_URL = "https://api.deepseek.com/chat/completions"

TOPIC_SEEDS = {
    "赛事战况": ["比赛", "进球", "绝杀", "逆转", "平局", "比分", "积分", "排名", "晋级", "出线", "胜负", "赛果", "战绩", "半场", "全场"],
    "球员/教练": ["球员", "教练", "武磊", "张玉宁", "韦世豪", "克雷桑", "首发", "替补", "换人", "表现", "状态", "发挥", "战术", "阵型", "指挥"],
    "裁判/VAR": ["裁判", "VAR", "红牌", "黄牌", "越位", "点球", "判罚", "争议", "黑哨", "漏判", "错判", "主裁", "边裁"],
    "赛事组织": ["赛区", "球场", "门票", "交通", "安保", "组织", "运营", "管理", "观赛", "球迷服务", "组委会"],
    "青训/未来": ["青训", "梯队", "足校", "未来", "年轻", "小将", "校园", "U23", "青年队", "苗子"],
    "地域/球迷文化": ["球迷", "主场", "客场", "氛围", "助威", "德比", "城市", "球市", "远征", "看台", "啦啦队"],
    "商业/品牌": ["赞助", "转播", "版权", "球衣", "广告", "票房", "周边", "招商", "金元", "限薪", "投入"],
}


class LobsterAnalyzer:
    def __init__(self, input_path: str):
        with open(input_path, 'r', encoding='utf-8') as f:
            raw = json.load(f)
        self.posts = raw.get("data", [])
        self.meta = raw.get("meta", {})
        self.keyword = self.meta.get("keyword", "未知赛事")
        self.engine = SentimentEngine()
        self._sentiment_cache = {}

        self.judgment = {
            "meta": self.meta,
            "themes": {},
            "sentiment": {},
            "temporal": {},
            "propagation": {},
            "risk_factors": {},
            "llm_synthesis": {},
            "generated_at": datetime.now().isoformat(),
        }
    
    def analyze_themes(self):
        all_text = " ".join([p.get("content", "") for p in self.posts])
    
        # 1. 先过滤掉符号和停用词，再提关键词
        clean_text = re.sub(r'[#@]\w+', '', all_text)  # 去掉话题标签和@用户
        clean_text = re.sub(r'[vsVS]+', '', clean_text)  # 去掉 vs
        clean_text = re.sub(r'http\S+', '', clean_text)  # 去掉链接
        clean_text = re.sub(r'[^\u4e00-\u9fa5a-zA-Z]', ' ', clean_text)  # 只保留中文和英文词
    
        keywords = jieba.analyse.extract_tags(clean_text, topK=30, withWeight=True)
    
        # 2. 规则聚类（按种子词命中次数，不是权重）
        cat_counts = defaultdict(int)
        for post in self.posts:
            content = post.get("content", "")
            for cat, seeds in TOPIC_SEEDS.items():
                if any(seed in content for seed in seeds):
                    cat_counts[cat] += 1
    
        total = sum(cat_counts.values()) or 1
        topic_dist = {k: round(v / total, 4) for k, v in cat_counts.items()}
    
        # 如果没命中任何主题，标记为"其他"
        if not topic_dist:
            topic_dist = {"其他": 1.0}
    
        self.judgment["themes"] = {
            "top_keywords": [{"word": w, "weight": round(wt, 4)} for w, wt in keywords if len(w) > 1 and not w.isdigit()],
            "topic_distribution": dict(sorted(topic_dist.items(), key=lambda x: x[1], reverse=True)),
            "dominant_topic": max(topic_dist, key=topic_dist.get),
        }
    
    def analyze_sentiment(self):
        pos, neg, neu = 0, 0, 0
        pos_samples, neg_samples = [], []
        total_pos_intensity = 0.0
        total_neg_intensity = 0.0
        extreme_neg = 0

        for post in self.posts:
            content = post.get("content", "")
            r = self.engine.analyze(content)
            mid = post.get("mid", "unknown")
            self._sentiment_cache[mid] = r

            if r["polarity"] == "positive":
                pos += 1
                total_pos_intensity += r["pos_mean"]
                if len(pos_samples) < 3:
                    pos_samples.append({"text": content[:100], "score": r["pos_mean"]})
            elif r["polarity"] == "negative":
                neg += 1
                total_neg_intensity += r["neg_mean"]
                if r["neg_mean"] > 3:
                    extreme_neg += 1
                if len(neg_samples) < 3:
                    neg_samples.append({"text": content[:100], "score": r["neg_mean"]})
            else:
                neu += 1

        total = pos + neg + neu
        self.judgment["sentiment"] = {
            "distribution": {
                "positive": {"count": pos, "ratio": round(pos / total, 4) if total else 0},
                "neutral": {"count": neu, "ratio": round(neu / total, 4) if total else 0},
                "negative": {"count": neg, "ratio": round(neg / total, 4) if total else 0},
            },
            "intensity": {
                "avg_positive": round(total_pos_intensity / pos, 3) if pos else 0,
                "avg_negative": round(total_neg_intensity / neg, 3) if neg else 0,
                "extreme_negative_count": extreme_neg,
            },
            "positive_samples": pos_samples,
            "negative_samples": neg_samples,
            "overall": "positive" if pos > neg else "negative" if neg > pos else "neutral",
        }

    def analyze_temporal(self):
        hourly = defaultdict(lambda: {"count": 0, "pos": 0, "neg": 0, "neu": 0, "neg_score_sum": 0.0})

        for post in self.posts:
            t_str = post.get("time", "")
            m = re.search(r'(\d{2})月(\d{2})日\s+(\d{2}):', t_str)
            if not m:
                m = re.search(r'\d{4}-(\d{2})-(\d{2})\s+(\d{2}):', t_str)
            bucket = f"{m.group(1)}-{m.group(2)} {m.group(3)}:00" if m else "unknown"

            mid = post.get("mid", "unknown")
            r = self._sentiment_cache.get(mid) or self.engine.analyze(post.get("content", ""))

            hourly[bucket]["count"] += 1
            polarity_map = {"positive": "pos", "negative": "neg", "neutral": "neu"}
            hourly[bucket][polarity_map[r["polarity"]]] += 1
            hourly[bucket]["neg_score_sum"] += r["neg_total"]

        timeline_list = []
        for h in sorted(hourly.keys()):
            v = hourly[h]
            neg_ratio = v["neg"] / v["count"] if v["count"] else 0
            avg_neg_intensity = v["neg_score_sum"] / v["count"] if v["count"] else 0
            timeline_list.append({
                "hour": h,
                "count": v["count"],
                "pos": v["pos"],
                "neg": v["neg"],
                "neu": v["neu"],
                "negative_ratio": round(neg_ratio, 4),
                "avg_negative_intensity": round(avg_neg_intensity, 3),
            })

        spikes = [t for t in timeline_list if t["negative_ratio"] > 0.5 and t["count"] >= 2]

        accel = 0
        if len(timeline_list) >= 3:
            recent = timeline_list[-3:]
            ratios = [r["negative_ratio"] for r in recent]
            diffs = [ratios[i + 1] - ratios[i] for i in range(len(ratios) - 1)]
            accel = sum(diffs) / len(diffs) if diffs else 0

        self.judgment["temporal"] = {
            "hourly_curve": timeline_list,
            "spike_hours": [s["hour"] for s in spikes],
            "spike_details": spikes,
            "trend_acceleration": round(accel, 4),
        }

    def analyze_propagation(self):
        analysis_words = {"认为", "觉得", "应该", "建议", "问题", "原因", "分析", "看法", "指出", "总结", "战术", "阵容", "打法", "体系"}
        leaders = []
        for post in self.posts:
            content = post.get("content", "")
            if len(content) > 80 and any(w in content for w in analysis_words):
                leaders.append({
                    "username": post.get("username", "未知"),
                    "snippet": content[:80],
                    "length": len(content),
                })

        total_chars = sum(len(p.get("content", "")) for p in self.posts)
        total_chars = max(total_chars, 1)

        exclamation_count = sum(p.get("content", "").count("!") + p.get("content", "").count("！") for p in self.posts)
        question_count = sum(p.get("content", "").count("?") + p.get("content", "").count("？") for p in self.posts)
        repeat_mark_count = sum(len(re.findall(r'[!！]{2,}', p.get("content", ""))) for p in self.posts)

        user_counts = Counter(p.get("username", "未知") for p in self.posts)
        top3_count = sum(c for _, c in user_counts.most_common(3))
        concentration = top3_count / len(self.posts) if self.posts else 0

        self.judgment["propagation"] = {
            "leader_candidates": leaders[:5],
            "emotional_contagion": {
                "exclamation_density": round(exclamation_count / total_chars, 4),
                "question_density": round(question_count / total_chars, 4),
                "intense_punctuation_ratio": round(repeat_mark_count / len(self.posts), 4) if self.posts else 0,
            },
            "concentration": {
                "top3_user_post_ratio": round(concentration, 4),
                "interpretation": "高度集中（粉圈/领袖驱动）" if concentration > 0.5 else "中度集中" if concentration > 0.3 else "分散（已泛化）",
            },
        }

    def analyze_risk_factors(self):
        sensitive_words = {"黑哨", "假球", "下课", "退钱", "腐败", "暗箱", "内定", "赌球", "操纵", "收钱"}
        derby_clues = {"德比", "干死", "碾压", "惨案", "血洗", "屠杀", "打爆"}
        historical_baggage = {"又", "还是", "总是", "依旧", "仍然", "老是"}

        sensitive_hits = 0
        derby_hits = 0
        historical_hits = 0

        for post in self.posts:
            content = post.get("content", "")
            if any(w in content for w in sensitive_words):
                sensitive_hits += 1
            if any(w in content for w in derby_clues):
                derby_hits += 1

            mid = post.get("mid", "unknown")
            r = self._sentiment_cache.get(mid) or self.engine.analyze(content)
            if any(h in content for h in historical_baggage) and r["polarity"] == "negative":
                historical_hits += 1

        total = len(self.posts)
        self.judgment["risk_factors"] = {
            "sensitive_word_density": round(sensitive_hits / total, 4) if total else 0,
            "derby_confrontation_density": round(derby_hits / total, 4) if total else 0,
            "historical_baggage_density": round(historical_hits / total, 4) if total else 0,
            "sensitive_examples": [p.get("content", "")[:80] for p in self.posts if any(w in p.get("content", "") for w in sensitive_words)][:3],
        }

    def llm_synthesis(self):
        j = self.judgment
        t = j["themes"]
        s = j["sentiment"]
        tl = j["temporal"]
        p = j["propagation"]
        r = j["risk_factors"]

        prompt = f"""你是资深足球舆情分析师。请基于以下已统计的客观数据，生成一段自然语言综述和研判结论。只输出严格JSON格式。

【客观数据】
赛事: {self.keyword}
样本量: {len(self.posts)}条
主导议题: {t['dominant_topic']}
议题分布: {t['topic_distribution']}
情感总体: {s['overall']}（正{s['distribution']['positive']['ratio']:.1%} 负{s['distribution']['negative']['ratio']:.1%} 中{s['distribution']['neutral']['ratio']:.1%}）
负面强度均值: {s['intensity']['avg_negative']}
极端负面数: {s['intensity']['extreme_negative_count']}
时间突变: {', '.join(tl['spike_hours']) if tl['spike_hours'] else '无'}
趋势加速度: {tl['trend_acceleration']:.3f}（正值表示负面率在加速上升）
敏感词密度: {r['sensitive_word_density']:.1%}
对立情绪密度: {r['derby_confrontation_density']:.1%}
历史包袱密度: {r['historical_baggage_density']:.1%}
情绪传染度(感叹号密度): {p['emotional_contagion']['exclamation_density']:.4f}
争议集中度: {p['concentration']['top3_user_post_ratio']:.1%}（{p['concentration']['interpretation']}）

请输出JSON:
{{
  "summary": "一句话核心判断",
  "key_issues": ["议题1", "议题2"],
  "emotion_trend": "情感走势判断",
  "risk_forecast": "未来12-24小时风险预测",
  "pr_suggestions": ["建议1", "建议2"],
  "monitor_focus": "后续监测重点"
}}"""

        try:
            resp = requests.post(
                DS_URL,
                headers={"Authorization": f"Bearer {DS_KEY}", "Content-Type": "application/json"},
                json={"model": "deepseek-chat", "messages": [{"role": "user", "content": prompt}],
                      "temperature": 0.3, "max_tokens": 1200, "response_format": {"type": "json_object"}},
                timeout=60,
            )
            text = resp.json()["choices"][0]["message"]["content"]
            j["llm_synthesis"] = json.loads(text)
        except Exception as e:
            j["llm_synthesis"] = {"error": str(e), "fallback": f"{self.keyword}舆情整体{s['overall']}，需关注{t['dominant_topic']}相关讨论。"}

    def save(self):
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        out_path = DATA_DIR / f"judgment_{self.keyword}_{datetime.now():%Y%m%d_%H%M%S}.json"

        with open(out_path, 'w', encoding='utf-8') as f:
            json.dump(self.judgment, f, ensure_ascii=False, indent=2)

        print("\n" + "=" * 60)
        print(f"研判智能体 | {self.keyword}")
        print("=" * 60)
        print(f"主导议题: {self.judgment['themes']['dominant_topic']}")
        print(f"高频词: {', '.join([k['word'] for k in self.judgment['themes']['top_keywords'][:5]])}")

        d = self.judgment['sentiment']['distribution']
        print(f"情感: 正{d['positive']['ratio']:.1%} / 中{d['neutral']['ratio']:.1%} / 负{d['negative']['ratio']:.1%} → 总体【{self.judgment['sentiment']['overall'].upper()}】")
        print(f"负面强度: {self.judgment['sentiment']['intensity']['avg_negative']} (极端负面: {self.judgment['sentiment']['intensity']['extreme_negative_count']}条)")

        tl = self.judgment['temporal']
        if tl['spike_hours']:
            print(f"负面突变: {', '.join(tl['spike_hours'])} (加速度: {tl['trend_acceleration']:+.3f})")

        rf = self.judgment['risk_factors']
        print(f"敏感词密度: {rf['sensitive_word_density']:.1%} | 对立: {rf['derby_confrontation_density']:.1%} | 历史包袱: {rf['historical_baggage_density']:.1%}")

        pr = self.judgment['propagation']
        print(f"传播特征: {pr['concentration']['interpretation']} (集中度{pr['concentration']['top3_user_post_ratio']:.1%})")

        if "summary" in self.judgment.get("llm_synthesis", {}):
            print(f"\nLLM 综述: {self.judgment['llm_synthesis']['summary']}")
        print("=" * 60)
        print(f"研判文件: {out_path}")
        return str(out_path)

    def run(self):
        print("研判智能体启动...")
        print(f"加载 {len(self.posts)} 条感知数据")

        print("主题分析...")
        self.analyze_themes()

        print("情感分析（词典法）...")
        self.analyze_sentiment()

        print("时序演化...")
        self.analyze_temporal()

        print("传播特征...")
        self.analyze_propagation()

        print("风险因子...")
        self.analyze_risk_factors()

        print("LLM 综合研判...")
        self.llm_synthesis()

        return self.save()


if __name__ == "__main__":
    if len(sys.argv) >= 2:
        inp = sys.argv[1]
    else:
        candidates = sorted(
            [f for f in DATA_DIR.glob("*.json") 
            if "条.json" in f.name and not f.name.startswith(("judgment_", "warning_", "report_"))],
            key=lambda x: x.stat().st_mtime,
            reverse=True
        )
        if not candidates:
            print("未找到感知层 JSON，请指定路径")
            sys.exit(1)
        inp = candidates[0]
        print(f"自动加载最新数据: {inp.name}")

    analyzer = LobsterAnalyzer(str(inp))
    analyzer.run()