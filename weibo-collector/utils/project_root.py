"""工程根目录：开发态 / PyInstaller 打包态 / 子进程均指向 exe 旁可写目录。"""
from __future__ import annotations

import os
import sys
from pathlib import Path

_cached: Path | None = None


def get_project_root() -> Path:
    global _cached
    if _cached is not None:
        return _cached

    env = os.environ.get("CSL_SENTINEL_ROOT", "").strip()
    if env:
        _cached = Path(env).resolve()
        return _cached

    if getattr(sys, "frozen", False):
        _cached = Path(sys.executable).resolve().parent
        return _cached

    _cached = Path(__file__).resolve().parents[1]
    return _cached
