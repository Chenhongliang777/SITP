"""
可视化报告生成器
读取 judgment + warning JSON，输出单页 HTML 报告
"""

import json
import sys
from datetime import datetime
from pathlib import Path

import matplotlib
matplotlib.use('Agg')  # 无头模式
import matplotlib.pyplot as plt

# 配置中文字体（Windows 优先尝试 SimHei / Microsoft YaHei）
plt.rcParams['font.sans-serif'] = ['SimHei', 'Microsoft YaHei', 'SimSun', 'Arial Unicode MS']
plt.rcParams['axes.unicode_minus'] = False

import numpy as np

SCRIPT_DIR = Path(__file__).parent.resolve()
PROJECT_DIR = SCRIPT_DIR.parent
DATA_DIR = PROJECT_DIR / "data"
REPORT_DIR = PROJECT_DIR / "reports"
REPORT_DIR.mkdir(parents=True, exist_ok=True)


class ReportGenerator:
    def __init__(self, judgment_path: str, warning_path: str):
        with open(judgment_path, 'r', encoding='utf-8') as f:
            self.j = json.load(f)
        with open(warning_path, 'r', encoding='utf-8') as f:
            self.w = json.load(f)
        
        self.keyword = self.j.get("meta", {}).get("keyword", "未知赛事")
        self.report_path = REPORT_DIR / f"report_{self.keyword}_{datetime.now():%Y%m%d_%H%M%S}.html"

    def _make_pie(self, data: dict, title: str, filename: str, colors=None):
        """生成饼图，返回相对路径"""
        labels = list(data.keys())
        sizes = list(data.values())
        if not any(sizes):
            return None
        
        fig, ax = plt.subplots(figsize=(6, 4))
        ax.pie(sizes, labels=labels, autopct='%1.1f%%', startangle=90, colors=colors)
        ax.set_title(title)
        img_path = REPORT_DIR / filename
        plt.tight_layout()
        plt.savefig(img_path, dpi=150)
        plt.close()
        return img_path.name

    def _make_line(self, x: list, y: list, title: str, filename: str, ylabel: str = "比率"):
        """生成折线图"""
        if len(x) <= 1:
            return None
        
        fig, ax = plt.subplots(figsize=(8, 4))
        ax.plot(x, y, marker='o', color='#e74c3c', linewidth=2)
        ax.set_title(title)
        ax.set_ylabel(ylabel)
        ax.set_ylim(0, 1)
        ax.grid(True, alpha=0.3)
        plt.xticks(rotation=45, ha='right')
        plt.tight_layout()
        img_path = REPORT_DIR / filename
        plt.savefig(img_path, dpi=150)
        plt.close()
        return img_path.name

    def generate(self):
        j, w = self.j, self.w
        
        # 1. 主题饼图
        topic_img = self._make_pie(
            j["themes"]["topic_distribution"],
            f"{self.keyword} - 议题分布",
            f"topic_{self.keyword}.png"
        )
        
        # 2. 情感饼图
        sent_dist = {
            "正面": j["sentiment"]["distribution"]["positive"]["ratio"],
            "中性": j["sentiment"]["distribution"]["neutral"]["ratio"],
            "负面": j["sentiment"]["distribution"]["negative"]["ratio"],
        }
        sent_img = self._make_pie(
            sent_dist,
            f"{self.keyword} - 情感分布",
            f"sentiment_{self.keyword}.png",
            colors=['#2ecc71', '#95a5a6', '#e74c3c']
        )
        
        # 3. 时间演化折线图
        timeline = j["temporal"]["hourly_curve"]
        time_img = None
        if timeline and len(timeline) > 1 and timeline[0]["hour"] != "unknown":
            hours = [t["hour"] for t in timeline]
            neg_ratios = [t["negative_ratio"] for t in timeline]
            time_img = self._make_line(
                hours, neg_ratios,
                f"{self.keyword} - 负面率时序演化",
                f"timeline_{self.keyword}.png",
                ylabel="负面率"
            )
        
        # 4. 组装 HTML
        html = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<title>龙虾舆情报告 | {self.keyword}</title>
<style>
body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif; max-width: 900px; margin: 0 auto; padding: 20px; background: #f5f6fa; color: #2c3e50; }}
.card {{ background: white; border-radius: 12px; padding: 24px; margin-bottom: 20px; box-shadow: 0 2px 8px rgba(0,0,0,0.06); }}
h1 {{ color: #1a252f; margin-bottom: 8px; }}
h2 {{ color: #34495e; border-bottom: 2px solid #ecf0f1; padding-bottom: 8px; margin-top: 0; }}
.meta {{ color: #7f8c8d; font-size: 14px; margin-bottom: 24px; }}
.risk-badge {{ display: inline-block; padding: 6px 16px; border-radius: 20px; font-weight: bold; font-size: 18px; color: white; }}
.risk-high {{ background: #e74c3c; }}
.risk-medium {{ background: #f39c12; }}
.risk-low {{ background: #2ecc71; }}
.risk-extreme {{ background: #8e44ad; }}
.grid {{ display: grid; grid-template-columns: 1fr 1fr; gap: 20px; }}
.metric {{ text-align: center; padding: 16px; background: #f8f9fa; border-radius: 8px; }}
.metric-value {{ font-size: 28px; font-weight: bold; color: #2c3e50; }}
.metric-label {{ font-size: 12px; color: #7f8c8d; margin-top: 4px; }}
.sample {{ background: #f8f9fa; padding: 12px; border-radius: 6px; margin: 8px 0; font-size: 14px; border-left: 3px solid #e74c3c; }}
.sample-pos {{ border-left-color: #2ecc71; }}
img {{ max-width: 100%; border-radius: 8px; margin-top: 12px; }}
@media (max-width: 600px) {{ .grid {{ grid-template-columns: 1fr; }} }}
</style>
</head>
<body>

<div class="card">
<h1>龙虾舆情风险报告</h1>
<div class="meta">
赛事：{self.keyword} | 样本量：{j['meta']['actual']} 条 | 采集时段：{j['meta']['date_range']}<br>
生成时间：{datetime.now().strftime('%Y-%m-%d %H:%M')}
</div>
<div style="display:flex; align-items:center; gap:12px; margin-top:12px;">
<span class="risk-badge risk-{w['risk_level'].replace('极高','extreme').replace('高','high').replace('中','medium').replace('低','low')}">
风险等级：{w['risk_level']}
</span>
<span style="font-size:16px; color:#7f8c8d;">评分：{w['risk_score']}/100</span>
</div>
<p style="margin-top:12px; color:#34495e;"><strong>趋势判断：</strong>{w['trend']}</p>
</div>

<div class="grid">
<div class="card">
<h2>主导议题</h2>
<p style="font-size:18px; font-weight:bold; color:#2c3e50;">{j['themes']['dominant_topic']}</p>
<p style="color:#7f8c8d; font-size:13px;">高频词：{', '.join([k['word'] for k in j['themes']['top_keywords'][:5]])}</p>
{ f'<img src="{topic_img}" alt="议题分布">' if topic_img else '<p style="color:#95a5a6;">暂无图表数据</p>' }
</div>

<div class="card">
<h2>情感分布</h2>
<div style="display:flex; gap:8px; margin:12px 0;">
<div class="metric" style="flex:1;"><div class="metric-value" style="color:#2ecc71;">{j['sentiment']['distribution']['positive']['ratio']:.1%}</div><div class="metric-label">正面</div></div>
<div class="metric" style="flex:1;"><div class="metric-value" style="color:#95a5a6;">{j['sentiment']['distribution']['neutral']['ratio']:.1%}</div><div class="metric-label">中性</div></div>
<div class="metric" style="flex:1;"><div class="metric-value" style="color:#e74c3c;">{j['sentiment']['distribution']['negative']['ratio']:.1%}</div><div class="metric-label">负面</div></div>
</div>
<p style="font-size:13px; color:#7f8c8d;">负面强度均值：{j['sentiment']['intensity']['avg_negative']} | 极端负面：{j['sentiment']['intensity']['extreme_negative_count']} 条</p>
{ f'<img src="{sent_img}" alt="情感分布">' if sent_img else '' }
</div>
</div>

<div class="card">
<h2>时序演化</h2>
{ f'<img src="{time_img}" alt="负面率时序">' if time_img else '<p style="color:#e74c3c;">时间解析失败，所有数据落入 unknown 时段。请修复感知层时间提取后重跑。</p>' }
<p style="font-size:13px; color:#7f8c8d;">突变时段：{', '.join(j['temporal']['spike_hours']) if j['temporal']['spike_hours'] else '无'}</p>
</div>

<div class="card">
<h2>风险因子</h2>
<div class="grid">
<div class="metric"><div class="metric-value">{j['risk_factors']['sensitive_word_density']:.1%}</div><div class="metric-label">敏感词密度</div></div>
<div class="metric"><div class="metric-value">{j['risk_factors']['derby_confrontation_density']:.1%}</div><div class="metric-label">对立情绪密度</div></div>
<div class="metric"><div class="metric-value">{j['risk_factors']['historical_baggage_density']:.1%}</div><div class="metric-label">历史包袱密度</div></div>
<div class="metric"><div class="metric-value">{len(j['propagation']['leader_candidates'])}</div><div class="metric-label">意见领袖候选</div></div>
</div>
</div>

<div class="card">
<h2>干预建议（LLM）</h2>
<ul style="padding-left: 20px;">
{ ''.join([f'<li>{s}</li>' for s in w.get('llm_report', {}).get('pr_suggestions', ['暂无建议'])]) }
</ul>
</div>

<div class="card">
<h2>典型博文</h2>
<h3 style="color:#2ecc71; font-size:14px;">正面样本</h3>
{ ''.join([f'<div class="sample sample-pos">{s["text"]} <span style="color:#7f8c8d;">(得分{s["score"]})</span></div>' for s in j['sentiment']['positive_samples']]) }
<h3 style="color:#e74c3c; font-size:14px; margin-top:16px;">负面样本</h3>
{ ''.join([f'<div class="sample">{s["text"]} <span style="color:#7f8c8d;">(得分{s["score"]})</span></div>' for s in j['sentiment']['negative_samples']]) }
</div>

</body>
</html>"""
        
        with open(self.report_path, 'w', encoding='utf-8') as f:
            f.write(html)
        
        print(f"\n报告已生成: {self.report_path}")
        print(f"图表目录: {REPORT_DIR}")
        print("用浏览器打开 HTML 文件查看完整报告")
        return str(self.report_path)


if __name__ == "__main__":
    if len(sys.argv) >= 3:
        j_path, w_path = sys.argv[1], sys.argv[2]
    else:
        # 自动找最新的
        j_candidates = sorted(DATA_DIR.glob("judgment_*.json"), key=lambda x: x.stat().st_mtime, reverse=True)
        w_candidates = sorted(DATA_DIR.glob("warning_*.json"), key=lambda x: x.stat().st_mtime, reverse=True)
        if not j_candidates or not w_candidates:
            print("未找到 judgment 或 warning 文件")
            sys.exit(1)
        j_path, w_path = str(j_candidates[0]), str(w_candidates[0])
        print(f"自动加载: {j_candidates[0].name} + {w_candidates[0].name}")
    
    gen = ReportGenerator(j_path, w_path)
    gen.generate()