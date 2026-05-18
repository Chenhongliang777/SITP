#!/usr/bin/env python3
"""CSL Sentinel 桌面 GUI 入口。"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.gui.main_app import run_app

if __name__ == "__main__":
    run_app()
