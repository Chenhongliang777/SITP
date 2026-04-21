"""
情感分析引擎（基于词典法，继承前期研究成果）
支持：情感词典、否定词、6级程度副词、情绪标点
"""

import re
import jieba

DEFAULT_DICTS = {
    "positive": {
        "精彩", "激动", "成功", "加油", "胜利", "绝杀", "逆转", "拼搏", "信心", "捍卫",
        "荣耀", "不错", "好", "棒", "优秀", "期待", "支持", "赞", "牛", "强", "稳",
        "漂亮", "夺冠", "出线", "晋级", "功臣", "神扑", "世界波", "碾压", "完胜",
        "好球", "给力", "热血", "振奋", "高兴", "开心", "满意", "认可", "希望", "信任",
        "喜欢", "爱", "敬佩", "尊重", "欣赏", "欢呼", "喝彩", "祝贺"
    },
    "negative": {
        "垃圾", "菜", "烂队", "弱队", "丢人", "废物", "菜鸡", "辣鸡",
        "抑郁", "绝望", "心疼", "疼", "难受", "心酸", "泪目", "哭了",
        "降级", "垫底", "倒数", "连败", "惨案", "血洗", "屠杀",
        "乱七八糟", "一塌糊涂", "稀烂", "稀碎", "完犊子", "拉胯",
        "退钱", "rnm", "RNM", "傻逼", "煞笔", "智障", "脑残",
        "失误", "失败", "痛苦", "失望", "糟糕", "垃圾", "黑哨", "假球", "下课", "滚",
        "烂", "差", "输", "败", "遗憾", "争议", "不公", "愤怒", "恶心", "无语", "退钱",
        "腐败", "暗箱", "内定", "崩盘", "低迷", "惨案", "丢人", "离谱", "偏袒", "漏判",
        "错判", "废物", "菜", "弱", "惨", "冤", "气", "恨", "骂", "喷", "烂队", "解散"
    },
    "not": {
        "不", "没", "无", "未", "别", "不要", "不是", "不能", "不会", "没有", "从未",
        "并非", "决不", "绝不", "并未", "尚无", "勿", "甭", "不必"
    },
    "most": {"极其", "万分", "极度", "最", "太", "绝对", "完全", "彻底", "百分之百", "万分"},
    "very": {"非常", "十分", "特别", "相当", "很", "挺", "怪", "老", "多么", "那么"},
    "more": {"比较", "较为", "相当", "更", "越发", "愈发", "越加", "愈加"},
    "over": {"过于", "过分", "太过", "过", "太"},
    "ish": {"有点", "稍微", "略显", "多少", "稍稍", "有些", "一点儿", "些许"},
    "insufficiently": {"欠缺", "不够", "稍欠", "不大", "不怎么", "不甚", "不太"},
    "inverse": {"反倒", "却", "反而", "竟然", "居然", "谁知", "不料"},
}

DEGREE_WEIGHTS = {
    "most": 2.0, "very": 1.5, "more": 1.25, "over": 1.3,
    "ish": 0.8, "insufficiently": 0.5, "inverse": 0.8,
}


class SentimentEngine:
    def __init__(self, dict_dir: str = None):
        self.dicts = {}
        if dict_dir:
            import os
            for name in DEFAULT_DICTS.keys():
                path = os.path.join(dict_dir, f"{name}.txt")
                if os.path.exists(path):
                    with open(path, 'r', encoding='utf-8') as f:
                        self.dicts[name] = set(line.strip() for line in f if line.strip())
                else:
                    self.dicts[name] = DEFAULT_DICTS[name]
        else:
            self.dicts = {k: set(v) for k, v in DEFAULT_DICTS.items()}

        for w in self.dicts["positive"]:
            jieba.add_word(w, freq=1000)
        for w in self.dicts["negative"]:
            jieba.add_word(w, freq=1000)

    def _score_sentence(self, sent: str):
        if not sent:
            return 0.0, 0.0

        words = list(jieba.cut(sent.strip()))
        pos_score, neg_score = 0.0, 0.0

        for i, word in enumerate(words):
            if word in self.dicts["positive"]:
                score = 1.0
                window = words[max(0, i - 5):i]
                neg_count = sum(1 for w in window if w in self.dicts["not"])
                if neg_count % 2 == 1:
                    score *= -1
                degree = 1.0
                for w in window:
                    for cat, weight in DEGREE_WEIGHTS.items():
                        if w in self.dicts[cat]:
                            degree = max(degree, weight)
                            break
                score *= degree
                if score > 0:
                    pos_score += score
                else:
                    neg_score += abs(score)

            elif word in self.dicts["negative"]:
                score = 1.0
                window = words[max(0, i - 5):i]
                neg_count = sum(1 for w in window if w in self.dicts["not"])
                if neg_count % 2 == 1:
                    score *= -1
                degree = 1.0
                for w in window:
                    for cat, weight in DEGREE_WEIGHTS.items():
                        if w in self.dicts[cat]:
                            degree = max(degree, weight)
                            break
                score *= degree
                if score > 0:
                    neg_score += score
                else:
                    pos_score += abs(score)

        if pos_score > 0 and ('!' in sent or '！' in sent):
            pos_score += 1.5
        if '?' in sent or '？' in sent:
            neg_score += 1.0

        return pos_score, neg_score

    def analyze(self, text: str) -> dict:
        if not text:
            return {"pos_mean": 0, "neg_mean": 0, "polarity": "neutral", "pos_total": 0, "neg_total": 0}

        sentences = re.split(r'[。！？!?\n]', text)
        pos_scores, neg_scores = [], []

        for sent in sentences:
            if not sent.strip():
                continue
            p, n = self._score_sentence(sent.strip())
            pos_scores.append(p)
            neg_scores.append(n)

        pos_mean = sum(pos_scores) / len(pos_scores) if pos_scores else 0
        neg_mean = sum(neg_scores) / len(neg_scores) if neg_scores else 0

        if pos_mean > neg_mean + 0.5:
            polarity = "positive"
        elif neg_mean > pos_mean + 0.5:
            polarity = "negative"
        else:
            polarity = "neutral"

        return {
            "pos_mean": round(pos_mean, 3),
            "neg_mean": round(neg_mean, 3),
            "polarity": polarity,
            "pos_total": round(sum(pos_scores), 3),
            "neg_total": round(sum(neg_scores), 3),
        }