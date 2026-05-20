#!/usr/bin/env python3
"""
合并步骤：时间清洗 + 去重。
在同一 Python 进程中完成；时间解析与去重逻辑内联于本文件（原 time_cleaner / deduper），减少顶层脚本数量。
"""

from __future__ import annotations

import argparse
import json
import re
from datetime import datetime, timedelta
from pathlib import Path

from utils.project_root import get_project_root

SCRIPT_DIR = get_project_root()
DATA_DIR = SCRIPT_DIR / "data"
DATA_DIR.mkdir(exist_ok=True)


# ----- 时间清洗（原 time_cleaner.py） -----


def parse_time_text(text: str, now: datetime | None = None):
    if not text or not isinstance(text, str):
        return None
    now = now or datetime.now()
    text = text.strip()

    for fmt in ["%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y/%m/%d %H:%M:%S", "%Y/%m/%d %H:%M"]:
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            pass

    if text == "刚刚":
        return now

    m = re.match(r"(\d+)\s*分钟前", text)
    if m:
        return now - timedelta(minutes=int(m.group(1)))
    m = re.match(r"(\d+)\s*秒前", text)
    if m:
        return now - timedelta(seconds=int(m.group(1)))

    m = re.match(r"今天\s*(\d{1,2}):(\d{2})", text)
    if m:
        return now.replace(hour=int(m.group(1)), minute=int(m.group(2)), second=0, microsecond=0)

    m = re.match(r"(\d{1,2})月(\d{1,2})日\s*(\d{1,2}):(\d{2})", text)
    if m:
        year = now.year
        return datetime(year, int(m.group(1)), int(m.group(2)), int(m.group(3)), int(m.group(4)))

    m = re.search(r"(\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2})", text)
    if m:
        try:
            return datetime.strptime(m.group(1), "%Y-%m-%d %H:%M")
        except ValueError:
            pass

    return None


def normalize_time(post: dict, now: datetime | None = None):
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


def _timecleaned_output_path(keyword: str) -> Path:
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    return DATA_DIR / f"timecleaned_{keyword}_{stamp}.json"


# ----- 去重（原 deduper.py） -----


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


def _deduped_output_path(keyword: str) -> Path:
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    return DATA_DIR / f"deduped_{keyword}_{stamp}.json"


def run_preprocess(input_path: Path, start_date: str, end_date: str) -> tuple[Path, Path]:
    if not input_path.exists():
        raise SystemExit(f"未找到输入文件: {input_path}")

    with open(input_path, "r", encoding="utf-8") as f:
        payload = json.load(f)

    meta = payload.get("meta", {})
    keyword = meta.get("keyword") or input_path.stem
    posts = payload.get("data", [])

    before_count = len(posts)
    time_filtered = []
    removed = 0

    for post in posts:
        normalize_time(post)
        if post.get("parsed_time") and not in_range(post["parsed_time"], start_date, end_date):
            removed += 1
            continue
        time_filtered.append(post)

    timecleaned_path = _timecleaned_output_path(keyword)
    time_payload = {
        "meta": {
            "keyword": keyword,
            "date_range": f"{start_date} to {end_date}",
            "actual": len(time_filtered),
        },
        "data": time_filtered,
    }
    with open(timecleaned_path, "w", encoding="utf-8") as f:
        json.dump(time_payload, f, ensure_ascii=False, indent=2)

    after_time = len(time_filtered)
    deduped_list = dedupe_posts(time_filtered)
    deduped_path = _deduped_output_path(keyword)
    deduped_payload = {
        "meta": {
            "keyword": keyword,
            "date_range": f"{start_date} to {end_date}",
            "actual": len(deduped_list),
        },
        "data": deduped_list,
    }
    with open(deduped_path, "w", encoding="utf-8") as f:
        json.dump(deduped_payload, f, ensure_ascii=False, indent=2)

    print(f"预处理完成：原始 {before_count} 条 → 时间过滤后 {after_time} 条（剔除超范围 {removed}）→ 去重后 {len(deduped_list)} 条")
    print(f"timecleaned -> {timecleaned_path.name}")
    print(f"deduped     -> {deduped_path.name}")

    return timecleaned_path, deduped_path


def main():
    parser = argparse.ArgumentParser(description="时间清洗 + 去重（合并步骤）")
    parser.add_argument("--input", required=True, help="上游 raw JSON 文件路径")
    parser.add_argument("--start-date", required=True, help="开始日期 YYYY-MM-DD")
    parser.add_argument("--end-date", required=True, help="结束日期 YYYY-MM-DD")
    args = parser.parse_args()

    run_preprocess(Path(args.input), args.start_date, args.end_date)


if __name__ == "__main__":
    main()
