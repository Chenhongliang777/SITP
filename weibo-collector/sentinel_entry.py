#!/usr/bin/env python3
"""CSL Sentinel 打包入口（PyInstaller）与开发态可共用。"""
from __future__ import annotations

import multiprocessing
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def main() -> None:
    multiprocessing.freeze_support()

    if len(sys.argv) >= 3 and sys.argv[1] == "--run-script":
        from app.frozen_worker import run_frozen_script

        raise SystemExit(run_frozen_script(sys.argv[2], sys.argv[3:]))

    from app.config import bootstrap
    from app.paths import ensure_app_layout

    ensure_app_layout()
    bootstrap()

    from app.gui.main_app import run_app

    run_app()


if __name__ == "__main__":
    main()
