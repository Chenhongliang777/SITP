import argparse
import json
from datetime import datetime
from pathlib import Path

SCRIPT_DIR = Path(__file__).parent.resolve()
PROJECT_DIR = SCRIPT_DIR
DATA_DIR = PROJECT_DIR / "data"
DATA_DIR.mkdir(exist_ok=True)


def build_output_path(keyword: str):
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    return DATA_DIR / f"deduped_{keyword}_{stamp}.json"


def dedupe_posts(posts):
    seen_mids = set()
    seen_text_prefix = set()
    output = []

    for post in posts:
        mid = post.get("mid")
        if mid and mid in seen_mids:
            continue
        seen_mids.add(mid)

        text = (post.get("clean_text") or post.get("raw_text") or "").strip()
        prefix = text[:50]
        if prefix in seen_text_prefix:
            continue
        seen_text_prefix.add(prefix)
        output.append(post)

    return output


def main():
    parser = argparse.ArgumentParser(description="去重脚本，先按 mid 再按 clean_text 前50字去重")
    parser.add_argument("--input", required=True, help="上游 timecleaned JSON 文件")
    args = parser.parse_args()

    input_path = Path(args.input)
    if not input_path.exists():
        raise SystemExit(f"未找到输入文件: {input_path}")

    with open(input_path, 'r', encoding='utf-8') as f:
        payload = json.load(f)

    keyword = payload.get("meta", {}).get("keyword") or input_path.stem
    posts = payload.get("data", [])

    before_count = len(posts)
    filtered = dedupe_posts(posts)
    after_count = len(filtered)

    output_path = build_output_path(keyword)
    out_payload = {"meta": {"keyword": keyword, "date_range": payload.get("meta", {}).get("date_range", ""), "actual": after_count}, "data": filtered}
    with open(output_path, 'w', encoding='utf-8') as f:
        json.dump(out_payload, f, ensure_ascii=False, indent=2)

    print(f"去重前: {before_count} 条, 去重后: {after_count} 条")
    print(f"结果已写入: {output_path}")


if __name__ == "__main__":
    main()
