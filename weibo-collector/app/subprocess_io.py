"""Windows 子进程 stdout 编码与 UTF-8 环境。"""
from __future__ import annotations

import os
import sys
from typing import Dict, List


def subprocess_env() -> Dict[str, str]:
    from app.paths import get_weibo_collector_dir

    env = os.environ.copy()
    env["CSL_SENTINEL_ROOT"] = str(get_weibo_collector_dir())
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUTF8"] = "1"
    env["PYTHONUNBUFFERED"] = "1"
    if sys.platform == "win32":
        env["PYTHONLEGACYWINDOWSSTDIO"] = "utf-8"
    return env


def decode_subprocess_line(raw: bytes) -> str:
    if not raw:
        return ""
    candidates: List[str] = []
    if sys.platform == "win32":
        candidates.extend(["utf-8", "gbk", "cp936"])
    else:
        candidates.append("utf-8")
    for enc in candidates:
        try:
            return raw.decode(enc).rstrip("\r\n")
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace").rstrip("\r\n")
