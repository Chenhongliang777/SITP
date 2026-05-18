"""报告文件查找与打开。"""
from __future__ import annotations

import re
import webbrowser
from pathlib import Path

from app.paths import get_data_dir, get_reports_dir


def find_latest_report(keyword: str) -> Path:
    escaped_kw = re.escape(keyword)
    pattern = re.compile(rf"^report_{escaped_kw}_\d{{8}}_\d{{6}}\.html$")
    candidates = [
        f
        for f in get_reports_dir().iterdir()
        if f.is_file() and pattern.match(f.name)
    ]
    if not candidates:
        raise FileNotFoundError(f"未找到关键词「{keyword}」的报告 HTML")
    candidates.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    return candidates[0]


def find_latest_file(
    directory: Path, prefix: str, keyword: str, ext: str = "json"
) -> Path:
    escaped_kw = re.escape(keyword)
    pattern = re.compile(
        rf"^{re.escape(prefix)}_{escaped_kw}_\d{{8}}_\d{{6}}\.{ext}$"
    )
    candidates = [f for f in directory.iterdir() if f.is_file() and pattern.match(f.name)]
    if not candidates:
        raise FileNotFoundError(
            f"在 [{directory}] 中未找到 {prefix}_{keyword}_*.{ext}"
        )
    candidates.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    return candidates[0]


def open_report(path: Path) -> None:
    webbrowser.open(path.resolve().as_uri())
