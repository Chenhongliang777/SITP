import argparse
import json
import re
from datetime import datetime, timedelta
from pathlib import Path


SCRIPT_DIR = Path(__file__).parent.resolve()
PROJECT_DIR = SCRIPT_DIR
DATA_DIR = PROJECT_DIR / "data"
DATA_DIR.mkdir(exist_ok=True)


def parse_time_text(text: str, now: datetime = None):
    if not text or not isinstance(text, str):
        return None
    now = now or datetime.now()
    text = text.strip()

    # 已经标准格式
    for fmt in ["%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y/%m/%d %H:%M:%S", "%Y/%m/%d %H:%M"]:
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            pass

    # 刚刚
    if text == "刚刚":
        return now

    # N分钟前、N秒前
    m = re.match(r"(\d+)\s*分钟前", text)
    if m:
        return now - timedelta(minutes=int(m.group(1)))
    m = re.match(r"(\d+)\s*秒前", text)
    if m:
        return now - timedelta(seconds=int(m.group(1)))

    # 今天 HH:MM
    m = re.match(r"今天\s*(\d{1,2}):(\d{2})", text)
    if m:
        return now.replace(hour=int(m.group(1)), minute=int(m.group(2)), second=0, microsecond=0)

    # MM月DD日 HH:MM
    m = re.match(r"(\d{1,2})月(\d{1,2})日\s*(\d{1,2}):(\d{2})", text)
    if m:
        year = now.year
        return datetime(year, int(m.group(1)), int(m.group(2)), int(m.group(3)), int(m.group(4)))

    # 可能是 ISO 片段
    m = re.search(r"(\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2})", text)
    if m:
        try:
            return datetime.strptime(m.group(1), "%Y-%m-%d %H:%M")
        except ValueError:
            pass

    return None


def normalize_time(post: dict, now: datetime = None):
    now = now or datetime.now()
    raw = post.get("raw_time") or post.get("time") or post.get("parsed_time") or ""
    parsed = parse_time_text(raw, now=now)
    if parsed:
        post["parsed_time"] = parsed.strftime("%Y-%m-%d %H:%M:%S")
        post.pop("time", None)
        post.pop("raw_time", None)
        post.pop("time_unknown", None)
        return True
    post["parsed_time"] = None
    post["time_unknown"] = True
    return False


def in_range(parsed_time: str, start_date: str, end_date: str):
    if not parsed_time:
        return True
    try:
        dt = datetime.strptime(parsed_time, "%Y-%m-%d %H:%M:%S")
        start = datetime.strptime(start_date, "%Y-%m-%d")
        end = datetime.strptime(end_date, "%Y-%m-%d")
        return start <= dt <= end + timedelta(days=1) - timedelta(seconds=1)
    except ValueError:
        return False


def build_output_path(keyword: str):
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    return DATA_DIR / f"timecleaned_{keyword}_{stamp}.json"


def main():
    parser = argparse.ArgumentParser(description="时间清洗脚本，将 raw JSON 中时间字段统一为 parsed_time")
    parser.add_argument("--input", required=True, help="上游 raw JSON 文件路径")
    parser.add_argument("--start-date", required=True, help="开始日期 YYYY-MM-DD")
    parser.add_argument("--end-date", required=True, help="结束日期 YYYY-MM-DD")
    args = parser.parse_args()

    input_path = Path(args.input)
    if not input_path.exists():
        raise SystemExit(f"未找到输入文件: {input_path}")

    with open(input_path, 'r', encoding='utf-8') as f:
        payload = json.load(f)

    meta = payload.get("meta", {})
    keyword = meta.get("keyword") or input_path.stem
    posts = payload.get("data", [])

    before_count = len(posts)
    filtered = []
    removed = 0

    for post in posts:
        normalize_time(post)
        if post.get("parsed_time") and not in_range(post["parsed_time"], args.start_date, args.end_date):
            removed += 1
            continue
        filtered.append(post)

    output_path = build_output_path(keyword)
    out_payload = {
        "meta": {"keyword": keyword, "date_range": f"{args.start_date} to {args.end_date}", "actual": len(filtered)},
        "data": filtered,
    }
    with open(output_path, 'w', encoding='utf-8') as f:
        json.dump(out_payload, f, ensure_ascii=False, indent=2)

    print(f"输入记录: {before_count}, 输出记录: {len(filtered)}, 删除超范围: {removed}")
    print(f"时间清洗结果已写入: {output_path}")


if __name__ == "__main__":
    main()
