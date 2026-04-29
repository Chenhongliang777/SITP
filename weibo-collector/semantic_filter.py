import argparse
import json
import os
import re
from datetime import datetime
from pathlib import Path

import requests
from dotenv import load_dotenv
from sklearn.metrics.pairwise import cosine_similarity

SCRIPT_DIR = Path(__file__).parent.resolve()
DATA_DIR = SCRIPT_DIR / "data"
DATA_DIR.mkdir(exist_ok=True)

ENV_PATH = SCRIPT_DIR / ".env"
if ENV_PATH.exists():
    load_dotenv(dotenv_path=ENV_PATH)

DS_KEY = os.getenv("DEEPSEEK_API_KEY")
DS_URL = os.getenv("DEEPSEEK_API_URL", "https://api.deepseek.com/chat/completions")

# ---------- 加载预训练语义模型 ----------
try:
    from sentence_transformers import SentenceTransformer
except ImportError as e:
    raise SystemExit(
        "缺少 sentence-transformers，请执行：pip install sentence-transformers\n"
        "该库会自动安装 PyTorch，首次运行需联网下载约 130MB 模型文件。"
    ) from e

MODEL_NAME = os.getenv("EMBEDDING_MODEL", "BAAI/bge-small-zh-v1.5")
DEVICE = os.getenv("DEVICE", "cpu")

# ---------- 语义锚点样例 ----------
POSITIVE_EXAMPLES = [
    "这场中超比赛裁判判罚有争议，球迷很激动",
    "本轮联赛进球精彩，球队战术调整得不错",
    "球员表现出色，球队晋级亚冠希望很大",
    "国足热身赛后球迷讨论纷纷，舆情主要关注阵容和状态",
    "中超赛程太密，球员体能下降是大家关注点",
    "足协杯决赛门票售罄，球迷期待巅峰对决",
    "青训梯队小将破格提拔进入一线队名单",
    "VAR介入后改判点球，引发全场嘘声",
    "海港前场压迫做得不错，三个边路轮转很流畅",
    "泰山后腰这场拦截太关键了，连续破坏对面反击",
    "成都蓉城边锋状态火热，单场完成多次成功过人",
    "申花这场传控节奏很稳，但禁区最后一脚处理差点意思",
    "青岛海牛反击效率太高，三脚射正进两球",
    "浙江队跑动数据第一，但临门一脚太急",
    "中超榜首争夺激烈，三队分差只有2分",
    "门将这次出击判断满分，单刀封堵直接救了3分",
    "深圳新鹏城后防站位不错，终于零封了一场",
    "中锋单场赢下多次对抗，支点作用太关键",
    "这场德比火药味十足，竞争激烈又有体育精神",
    "中超本周最佳进球，起脚突然角度刁钻",
]

NEGATIVE_EXAMPLES = [
    "今天股市涨幅明显，基金投资者很开心",
    "华晨宇演唱会门票已经售罄，歌迷纷纷打卡",
    "李佳琦直播带货销量再创新高，粉丝很关注优惠券",
    "这部电视剧剧情反转太多，网友讨论角色关系",
    "超市打折活动，很多人去买日用品和零食",
    "CBA季后赛广东宏远险胜辽宁本钢",
    "银行理财收益率持续走低，储户转向国债",
    "娱乐圈明星离婚事件登上热搜第一",
    "这支乐队前场配合不错，三个声部轮转很流畅",
    "他今天后腰这个位置坐得太稳了，连续两次拒绝客户",
    "公司防守严密，竞品根本打不进来，终于零封了对手",
    "周末去爬山，体能恢复得不错，最后20分钟冲刺很明显",
]

# ---------- 阈值 ----------
# 基于 sim_pos（与正例质心的相似度）直接判定。
# 当前数据分布：足球 0.63~0.82，非足球 0.42~0.57，0.60 是安全分界线。
POSITIVE_THRESHOLD = 0.60   # 直接保留
NEGATIVE_THRESHOLD = 0.55   # 直接拒绝
FALLBACK_THRESHOLD = 0.58   # LLM 不可用时保守线


def load_model():
    print(f"正在加载语义模型: {MODEL_NAME} (设备: {DEVICE})...")
    try:
        model = SentenceTransformer(MODEL_NAME, device=DEVICE, trust_remote_code=True)
        print("模型加载完成")
        return model
    except Exception as e:
        raise SystemExit(
            f"模型加载失败: {e}\n"
            f"请检查网络连接（首次需从 HuggingFace 下载模型）。\n"
            f"若下载缓慢，可设置镜像环境变量后重试：\n"
            f"  set HF_ENDPOINT=https://hf-mirror.com"
        )


def compute_centroid(model, texts):
    if not texts:
        return None
    embeddings = model.encode(
        texts,
        normalize_embeddings=True,
        convert_to_numpy=True,
        show_progress_bar=False,
    )
    centroid = embeddings.mean(axis=0)
    return centroid.reshape(1, -1)


def cosine_sim(a, b):
    if a is None or b is None:
        return 0.0
    return float(cosine_similarity(a, b)[0, 0])


def prompt_llm(text: str):
    if not DS_KEY:
        return None
    system_prompt = (
        "你是一个足球舆情内容审核助手。请判断给定文本是否属于中国足球、中超、国足、"
        "足协杯、亚冠、青训、球员转会、裁判争议等足球相关舆情。"
        "只需输出 JSON，包含三个字段：football_related（布尔值）、"
        "confidence（0.0-1.0 浮点数，表示确信度）、"
        "reason（不超过20字的判定理由）。"
    )
    try:
        resp = requests.post(
            DS_URL,
            headers={"Authorization": f"Bearer {DS_KEY}", "Content-Type": "application/json"},
            json={
                "model": "deepseek-chat",
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": f"待审核文本：\n{text}\n"},
                ],
                "temperature": 0.0,
                "max_tokens": 200,
                "response_format": {"type": "json_object"},
            },
            timeout=25,
        )
        resp.raise_for_status()
        data = resp.json()
        content = data.get("choices", [{}])[0].get("message", {}).get("content", "")
        if not content:
            return None
        result = json.loads(content)
        if "football_related" not in result or "confidence" not in result:
            return None
        result["confidence"] = max(0.0, min(1.0, float(result["confidence"])))
        return result
    except Exception:
        return None


def score_text(text: str, model, pos_centroid, neg_centroid):
    if not text or not text.strip():
        return 0.0, 0.0, "空文本"
    emb = model.encode(
        text,
        normalize_embeddings=True,
        convert_to_numpy=True,
        show_progress_bar=False,
    ).reshape(1, -1)
    sim_pos = cosine_sim(emb, pos_centroid)
    sim_neg = cosine_sim(emb, neg_centroid)
    # 核心修复：用 sim_pos 作为决策分数，而非 sim_pos - sim_neg
    score = float(sim_pos)
    confidence = float(sim_pos)
    detail = f"pos={sim_pos:.3f}, neg={sim_neg:.3f}, score={score:.3f}"
    return score, confidence, detail


def build_output_path(keyword: str, prefix: str):
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    return DATA_DIR / f"{prefix}_{keyword}_{stamp}.json"


def main():
    parser = argparse.ArgumentParser(description="足球相关性语义过滤脚本（预训练语义模型 + LLM 回退）")
    parser.add_argument("--input", required=True, help="上游 deduped JSON 文件路径")
    parser.add_argument("--no-llm", action="store_true", help="禁用 LLM 回退，仅使用语义模型")
    args = parser.parse_args()

    input_path = Path(args.input)
    if not input_path.exists():
        raise SystemExit(f"未找到输入文件: {input_path}")

    with open(input_path, "r", encoding="utf-8") as f:
        payload = json.load(f)

    keyword = payload.get("meta", {}).get("keyword") or input_path.stem
    posts = payload.get("data", [])

    print(f"加载 {len(posts)} 条帖子，初始化语义模型...")
    model = load_model()
    print("构建正负例语义质心...")
    pos_centroid = compute_centroid(model, POSITIVE_EXAMPLES)
    neg_centroid = compute_centroid(model, NEGATIVE_EXAMPLES)

    filtered = []
    rejected = []
    llm_called = 0
    llm_failed = 0

    for idx, post in enumerate(posts, 1):
        text = (post.get("clean_text") or post.get("raw_text") or "").strip()
        score, confidence, detail = score_text(text, model, pos_centroid, neg_centroid)

        post["relevance_confidence"] = round(confidence, 4)
        post["football_related"] = False
        reason = ""

        if score >= POSITIVE_THRESHOLD:
            post["football_related"] = True
            reason = f"语义模型正相似度高于阈值 {POSITIVE_THRESHOLD} ({detail})"
        elif score <= NEGATIVE_THRESHOLD:
            post["football_related"] = False
            reason = f"语义模型相似度低于阈值 {NEGATIVE_THRESHOLD}，判定为非足球 ({detail})"
        else:
            # 中间地带：0.55 ~ 0.60，理论上当前数据不会落入，留给未来数据或 LLM
            if not args.no_llm and DS_KEY:
                llm_called += 1
                llm_result = prompt_llm(text)
                if llm_result:
                    post["football_related"] = bool(llm_result["football_related"])
                    post["relevance_confidence"] = round(llm_result["confidence"], 4)
                    reason = f"LLM 判定: {llm_result.get('reason', '无理由')} ({detail})"
                else:
                    llm_failed += 1
                    post["football_related"] = score >= FALLBACK_THRESHOLD
                    reason = f"LLM 调用失败，保守启发式回退 (score={score:.3f})"
            else:
                post["football_related"] = score >= FALLBACK_THRESHOLD
                reason = f"语义模型模糊区域，保守启发式回退 (score={score:.3f})"

        post["rejection_reason"] = reason

        if post["football_related"]:
            filtered.append(post)
        else:
            rejected.append(post)

        if idx % 500 == 0:
            print(f"  已处理 {idx}/{len(posts)} 条...")

    # ---------- 输出 ----------
    filtered_path = build_output_path(keyword, "filtered")
    rejected_path = build_output_path(keyword, "rejected")

    with open(filtered_path, "w", encoding="utf-8") as f:
        json.dump(
            {
                "meta": {
                    "keyword": keyword,
                    "date_range": payload.get("meta", {}).get("date_range", ""),
                    "actual": len(filtered),
                    "method": "pretrained_embedding+llm_fallback",
                },
                "data": filtered,
            },
            f,
            ensure_ascii=False,
            indent=2,
        )

    with open(rejected_path, "w", encoding="utf-8") as f:
        json.dump(
            {
                "meta": {
                    "keyword": keyword,
                    "date_range": payload.get("meta", {}).get("date_range", ""),
                    "actual": len(rejected),
                    "llm_called": llm_called,
                    "llm_failed": llm_failed,
                },
                "data": rejected,
            },
            f,
            ensure_ascii=False,
            indent=2,
        )

    print(f"\n过滤完成：保留 {len(filtered)} 条，拒绝 {len(rejected)} 条")
    if llm_called:
        print(f"LLM 回退调用: {llm_called} 次，失败: {llm_failed} 次")
    print(f"filtered  -> {filtered_path}")
    print(f"rejected  -> {rejected_path}")


if __name__ == "__main__":
    main()