import argparse
import json
import os
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
from dotenv import load_dotenv

from utils.llm_client import try_llm_client
from utils.runtime import get_llm_max_workers, get_semantic_encode_batch

SCRIPT_DIR = Path(__file__).parent.resolve()
DATA_DIR = SCRIPT_DIR / "data"
DATA_DIR.mkdir(exist_ok=True)

ENV_PATH = SCRIPT_DIR / ".env"
if ENV_PATH.exists():
    load_dotenv(dotenv_path=ENV_PATH)

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
POSITIVE_THRESHOLD = 0.60
NEGATIVE_THRESHOLD = 0.55
FALLBACK_THRESHOLD = 0.58


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


def prompt_llm_football(text: str, client) -> Optional[Dict[str, Any]]:
    system_prompt = (
        "你是一个足球舆情内容审核助手。请判断给定文本是否属于中国足球、中超、国足、"
        "足协杯、亚冠、青训、球员转会、裁判争议等足球相关舆情。"
        "只需输出 JSON，包含三个字段：football_related（布尔值）、"
        "confidence（0.0-1.0 浮点数，表示确信度）、"
        "reason（不超过20字的判定理由）。"
    )
    parsed = client.chat_json(
        system_prompt,
        f"待审核文本：\n{text}\n",
        temperature=0.0,
        max_tokens=200,
        response_format={"type": "json_object"},
    )
    if not isinstance(parsed, dict):
        return None
    if "football_related" not in parsed or "confidence" not in parsed:
        return None
    try:
        parsed["confidence"] = max(0.0, min(1.0, float(parsed["confidence"])))
    except (TypeError, ValueError):
        parsed["confidence"] = 0.5
    return parsed


def build_output_path(keyword: str, prefix: str):
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    return DATA_DIR / f"{prefix}_{keyword}_{stamp}.json"


def run_semantic_filter(
    input_path: Path,
    no_semantic_llm: bool = False,
    semantic_gray_reject: bool = False,
) -> Tuple[Path, Path]:
    with open(input_path, "r", encoding="utf-8") as f:
        payload = json.load(f)

    keyword = payload.get("meta", {}).get("keyword") or input_path.stem
    posts: List[Dict] = payload.get("data", [])

    print(f"加载 {len(posts)} 条帖子，初始化语义模型...")
    if semantic_gray_reject:
        print("已启用语义灰区严弃：相似度在正负阈值之间的帖子一律判为非足球并丢弃（不调 LLM）。")
    model = load_model()
    print("构建正负例语义质心...")
    pos_centroid = compute_centroid(model, POSITIVE_EXAMPLES)
    neg_centroid = compute_centroid(model, NEGATIVE_EXAMPLES)

    texts = [(post.get("clean_text") or post.get("raw_text") or "").strip() for post in posts]
    n = len(posts)
    sim_pos_arr = np.zeros(n, dtype=np.float64)
    sim_neg_arr = np.zeros(n, dtype=np.float64)
    nonempty_idx = [i for i, t in enumerate(texts) if t]
    enc_batch = get_semantic_encode_batch()
    if nonempty_idx:
        to_encode = [texts[i] for i in nonempty_idx]
        emb = model.encode(
            to_encode,
            batch_size=enc_batch,
            normalize_embeddings=True,
            convert_to_numpy=True,
            show_progress_bar=False,
        )
        # 质心是「已归一化向量」的均值，本身一般不是单位向量；必须与原版 sklearn.cosine_similarity 一致：
        # cos(u, c) = (u·c) / ||c||（u 已为 sentence-transformers 行归一化，||u||=1）
        pc = pos_centroid.reshape(-1)
        nc = neg_centroid.reshape(-1)
        pc_norm = float(np.linalg.norm(pc)) + 1e-12
        nc_norm = float(np.linalg.norm(nc)) + 1e-12
        sp = (emb @ pc) / pc_norm
        sn = (emb @ nc) / nc_norm
        for row, global_i in enumerate(nonempty_idx):
            sim_pos_arr[global_i] = float(sp[row])
            sim_neg_arr[global_i] = float(sn[row])

    llm_client = None if no_semantic_llm else try_llm_client()

    pending_gray: List[Tuple[int, str, float, str]] = []
    for i in range(n):
        t = texts[i]
        score = float(sim_pos_arr[i])
        snv = float(sim_neg_arr[i])
        detail = f"pos={score:.3f}, neg={snv:.3f}, score={score:.3f}"
        if not t:
            continue
        if NEGATIVE_THRESHOLD < score < POSITIVE_THRESHOLD and not semantic_gray_reject:
            pending_gray.append((i, t, score, detail))

    gray_llm: Dict[int, Optional[Dict[str, Any]]] = {}
    llm_called = 0
    llm_failed = 0
    if pending_gray and llm_client is not None and not no_semantic_llm:
        llm_called = len(pending_gray)
        workers = min(get_llm_max_workers(), len(pending_gray))

        def _gray_llm(item: Tuple[int, str, float, str]) -> Tuple[int, Optional[Dict[str, Any]]]:
            gi, gtext, _, _ = item
            return gi, prompt_llm_football(gtext, llm_client)

        with ThreadPoolExecutor(max_workers=workers) as pool:
            for gi, llm_result in pool.map(_gray_llm, pending_gray):
                gray_llm[gi] = llm_result
                if llm_result is None:
                    llm_failed += 1
    elif pending_gray:
        for gi, _, _, _ in pending_gray:
            gray_llm[gi] = None

    filtered: List[Dict] = []
    rejected: List[Dict] = []

    for idx, post in enumerate(posts, 1):
        text = texts[idx - 1]
        score = float(sim_pos_arr[idx - 1])
        snv = float(sim_neg_arr[idx - 1])
        detail = f"pos={score:.3f}, neg={snv:.3f}, score={score:.3f}"

        post["relevance_confidence"] = round(score, 4)
        post["football_related"] = False
        reason = ""

        if not text:
            post["football_related"] = False
            reason = f"空文本 ({detail})"
        elif score >= POSITIVE_THRESHOLD:
            post["football_related"] = True
            reason = f"语义模型正相似度高于阈值 {POSITIVE_THRESHOLD} ({detail})"
        elif score <= NEGATIVE_THRESHOLD:
            post["football_related"] = False
            reason = f"语义模型相似度低于阈值 {NEGATIVE_THRESHOLD}，判定为非足球 ({detail})"
        elif semantic_gray_reject:
            post["football_related"] = False
            reason = f"语义模型灰区，严弃模式一律判定为非足球 ({detail})"
        else:
            if not no_semantic_llm and llm_client is not None:
                llm_result = gray_llm.get(idx - 1)
                if llm_result:
                    post["football_related"] = bool(llm_result["football_related"])
                    post["relevance_confidence"] = round(float(llm_result["confidence"]), 4)
                    reason = f"LLM 判定: {llm_result.get('reason', '无理由')} ({detail})"
                else:
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

    if semantic_gray_reject:
        method = "pretrained_embedding+gray_reject_strict"
    else:
        method = "pretrained_embedding+llm_fallback"

    filtered_path = build_output_path(keyword, "filtered")
    rejected_path = build_output_path(keyword, "rejected")

    with open(filtered_path, "w", encoding="utf-8") as f:
        json.dump(
            {
                "meta": {
                    "keyword": keyword,
                    "date_range": payload.get("meta", {}).get("date_range", ""),
                    "actual": len(filtered),
                    "method": method,
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

    return filtered_path, rejected_path


def main():
    parser = argparse.ArgumentParser(description="足球相关性语义过滤脚本（预训练语义模型 + LLM 回退，与旧版一致）")
    parser.add_argument("--input", required=True, help="上游 deduped JSON 文件路径")
    parser.add_argument(
        "--no-llm",
        action="store_true",
        help="禁用 LLM 回退，模糊区域仅用启发式阈值（省 API，与旧版 --no-llm 一致）",
    )
    parser.add_argument(
        "--semantic-gray-reject",
        action="store_true",
        help="灰区（正负阈值之间）一律判为非足球并丢弃，不调 LLM、不用启发式捞回",
    )
    args = parser.parse_args()

    input_path = Path(args.input)
    if not input_path.exists():
        raise SystemExit(f"未找到输入文件: {input_path}")

    run_semantic_filter(
        input_path,
        no_semantic_llm=args.no_llm,
        semantic_gray_reject=args.semantic_gray_reject,
    )


if __name__ == "__main__":
    main()
