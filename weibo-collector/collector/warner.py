"""
预警智能体
职责：读取研判层 judgment.json，基于五维评分卡生成分级预警
输出：warning_{keyword}_{timestamp}.json
"""

import json
import sys
from datetime import datetime
from pathlib import Path

import requests

SCRIPT_DIR = Path(__file__).parent.resolve()
PROJECT_DIR = SCRIPT_DIR.parent
DATA_DIR = PROJECT_DIR / "data"

DS_KEY = "sk-f1da75d5e90945daa3de76ad9791c8a4"
DS_URL = "https://api.deepseek.com/chat/completions"


class LobsterWarner:
    def __init__(self, judgment_path: str):
        with open(judgment_path, 'r', encoding='utf-8') as f:
            self.j = json.load(f)

        self.keyword = self.j.get("meta", {}).get("keyword", "unknown")
        self.warning = {
            "meta": self.j.get("meta", {}),
            "risk_level": "低",
            "risk_score": 0,
            "dimension_scores": {},
            "triggered_rules": [],
            "trend": "",
            "llm_report": {},
            "generated_at": datetime.now().isoformat(),
        }

    def _multi_dim_scoring(self):
        s = self.j.get("sentiment", {})
        t = self.j.get("themes", {})
        tl = self.j.get("temporal", {})
        p = self.j.get("propagation", {})
        r = self.j.get("risk_factors", {})

        scores = {}
        reasons = []

        # 1. 情感维度 (0-30)
        neg_ratio = s.get("distribution", {}).get("negative", {}).get("ratio", 0)
        neg_intensity = s.get("intensity", {}).get("avg_negative", 0)
        extreme = s.get("intensity", {}).get("extreme_negative_count", 0)
        total_posts = self.j.get("meta", {}).get("actual", 1)

        emo_score = min(30, (neg_ratio * 20) + (neg_intensity * 3) + (extreme / total_posts * 10))
        scores["emotion"] = round(emo_score, 2)
        if neg_ratio > 0.4:
            reasons.append(f"整体负面率{neg_ratio:.1%}")
        if neg_intensity > 2:
            reasons.append(f"负面强度高({neg_intensity:.2f})")

        # 2. 时序维度 (0-25)
        spikes = tl.get("spike_details", [])
        accel = tl.get("trend_acceleration", 0)
        time_score = 0
        if spikes:
            time_score += min(15, len(spikes) * 7)
            reasons.append(f"{len(spikes)}个时段负面突变")
        if accel > 0.15:
            time_score += min(10, accel * 30)
            reasons.append(f"负面率加速上升(加速度{accel:+.3f})")
        scores["temporal"] = round(time_score, 2)

        # 3. 主题维度 (0-20)
        topic_dist = t.get("topic_distribution", {})
        high_risk_topics = {"裁判/VAR": 0.25, "赛事组织": 0.15, "商业/品牌": 0.10}
        topic_score = 0
        for topic, weight in high_risk_topics.items():
            if topic in topic_dist and topic_dist[topic] > 0.15:
                topic_score += 10
                reasons.append(f"高风险议题'{topic}'占比{topic_dist[topic]:.1%}")
        scores["topic"] = round(min(20, topic_score), 2)

        # 4. 传播维度 (0-15)
        contagion = p.get("emotional_contagion", {})
        conc = p.get("concentration", {})
        prop_score = 0
        if contagion.get("exclamation_density", 0) > 0.05:
            prop_score += 5
            reasons.append("情绪标点密度高，传染性强")
        if conc.get("top3_user_post_ratio", 0) > 0.5:
            prop_score += 5
            reasons.append("讨论高度集中，领袖驱动")
        if len(p.get("leader_candidates", [])) > 0:
            prop_score += 5
        scores["propagation"] = round(prop_score, 2)

        # 5. 敏感因子 (0-10)
        sens = r.get("sensitive_word_density", 0)
        derby = r.get("derby_confrontation_density", 0)
        hist = r.get("historical_baggage_density", 0)
        risk_score = min(10, (sens * 20) + (derby * 15) + (hist * 10))
        scores["risk_factors"] = round(risk_score, 2)
        if sens > 0.05:
            reasons.append(f"敏感词密度{sens:.1%}")
        if derby > 0.05:
            reasons.append(f"对立情绪密度{derby:.1%}")

        total = sum(scores.values())
        self.warning["risk_score"] = round(total, 2)
        self.warning["dimension_scores"] = scores
        self.warning["triggered_rules"] = reasons

        if total >= 60:
            self.warning["risk_level"] = "极高"
        elif total >= 40:
            self.warning["risk_level"] = "高"
        elif total >= 20:
            self.warning["risk_level"] = "中"
        else:
            self.warning["risk_level"] = "低"

        if self.warning["risk_level"] in ("极高", "高"):
            self.warning["trend"] = f"负面情绪正在{'快速' if accel > 0.2 else ''}发酵，若赛事方或主管部门不介入，{'6' if total >= 60 else '12'}小时内可能引发媒体跟进与二次传播"
        elif self.warning["risk_level"] == "中":
            self.warning["trend"] = "局部负面聚集，需关注是否向裁判/组织类高风险议题扩散"
        else:
            self.warning["trend"] = "舆情整体平稳，偶发负面属正常赛后情绪，持续监测即可"

    def _llm_warning(self):
        j = self.j
        w = self.warning

        prompt = f"""你是足球赛事舆情预警专家。以下所有数据已由研判系统客观统计，请据此生成预警简报（严格JSON）。

【已确认事实】
赛事: {self.keyword}
风险等级: {w['risk_level']}（评分{w['risk_score']}/100）
维度评分: 情感{w['dimension_scores'].get('emotion',0)} 时序{w['dimension_scores'].get('temporal',0)} 主题{w['dimension_scores'].get('topic',0)} 传播{w['dimension_scores'].get('propagation',0)} 敏感{w['dimension_scores'].get('risk_factors',0)}
触发规则: {'; '.join(w['triggered_rules']) if w['triggered_rules'] else '无'}
情感总体: {j['sentiment'].get('overall','未知')}（负{j['sentiment']['distribution']['negative']['ratio']:.1%}）
主导议题: {j['themes'].get('dominant_topic','未知')}
突变时段: {', '.join(j['temporal'].get('spike_hours',[])) if j['temporal'].get('spike_hours') else '无'}
趋势判断: {w['trend']}

请输出JSON:
{{
  "summary": "一句话风险概述",
  "key_risks": ["风险点1", "风险点2"],
  "pr_suggestions": ["建议1", "建议2", "建议3"],
  "monitor_focus": "后续应重点监测的关键词或时段",
  "response_window": "建议响应时间窗口（如：立即/6小时内/24小时内）"
}}"""

        try:
            resp = requests.post(
                DS_URL,
                headers={"Authorization": f"Bearer {DS_KEY}", "Content-Type": "application/json"},
                json={"model": "deepseek-chat", "messages": [{"role": "user", "content": prompt}],
                      "temperature": 0.3, "max_tokens": 1000, "response_format": {"type": "json_object"}},
                timeout=30,
            )
            text = resp.json()["choices"][0]["message"]["content"]
            w["llm_report"] = json.loads(text)
        except Exception as e:
            w["llm_report"] = {"error": str(e)}

    def save_and_print(self):
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        out_path = DATA_DIR / f"warning_{self.keyword}_{datetime.now():%Y%m%d_%H%M%S}.json"

        with open(out_path, 'w', encoding='utf-8') as f:
            json.dump(self.warning, f, ensure_ascii=False, indent=2)

        print("\n" + "=" * 60)
        print(f"预警智能体 | {self.keyword}")
        print("=" * 60)
        print(f"风险等级: 【{self.warning['risk_level']}】 (评分: {self.warning['risk_score']}/100)")
        print(f"维度评分:")
        for k, v in self.warning["dimension_scores"].items():
            print(f"   • {k}: {v}")
        print(f"触发规则:")
        for r in self.warning["triggered_rules"]:
            print(f"   • {r}")
        print(f"趋势: {self.warning['trend']}")
        if "summary" in self.warning.get("llm_report", {}):
            print(f"\nLLM 简报: {self.warning['llm_report']['summary']}")
        if "pr_suggestions" in self.warning.get("llm_report", {}):
            print(f"\n干预建议:")
            for s in self.warning["llm_report"]["pr_suggestions"]:
                print(f"   • {s}")
        if "response_window" in self.warning.get("llm_report", {}):
            print(f"\n响应窗口: {self.warning['llm_report']['response_window']}")
        print("=" * 60)
        print(f"预警文件: {out_path}")
        return str(out_path)

    def run(self):
        print("预警智能体启动...")
        self._multi_dim_scoring()
        print(f"多维度评分完成: {self.warning['risk_score']}分 → {self.warning['risk_level']}")
        print("LLM 预警推理...")
        self._llm_warning()
        return self.save_and_print()


if __name__ == "__main__":
    if len(sys.argv) >= 2:
        inp = sys.argv[1]
    else:
        candidates = sorted(DATA_DIR.glob("judgment_*.json"), key=lambda x: x.stat().st_mtime, reverse=True)
        if not candidates:
            print("未找到研判层 JSON，请先运行 analyzer.py")
            sys.exit(1)
        inp = candidates[0]
        print(f"自动加载最新研判: {inp.name}")

    warner = LobsterWarner(str(inp))
    warner.run()