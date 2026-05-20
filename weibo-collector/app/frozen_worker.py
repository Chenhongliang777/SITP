"""PyInstaller 打包后：用同一 exe 以 --run-script 执行流水线子脚本。"""
from __future__ import annotations

import runpy
import sys
import traceback
from pathlib import Path
from typing import List

from app.config import bootstrap
from app.paths import get_bundle_dir, get_script_path, get_weibo_collector_dir


def run_frozen_script(script_name: str, argv: List[str]) -> int:
    import os

    app_root = get_weibo_collector_dir()
    bundle = get_bundle_dir()
    os.environ["CSL_SENTINEL_ROOT"] = str(app_root)
    os.chdir(app_root)
    for p in (str(bundle), str(app_root)):
        if p not in sys.path:
            sys.path.insert(0, p)
    bootstrap()

    script_path = get_script_path(script_name)
    if not script_path.exists():
        bundle = get_bundle_dir() / script_name
        print(f"找不到脚本: {script_name} (bundle={bundle}, app={app_root / script_name})")
        return 2

    old_argv = sys.argv[:]
    try:
        sys.argv = [script_name] + list(argv)
        runpy.run_path(str(script_path), run_name="__main__")
        return 0
    except SystemExit as e:
        code = e.code
        if code is None:
            return 0
        if isinstance(code, int):
            return code
        return 1
    except Exception:
        traceback.print_exc()
        return 1
    finally:
        sys.argv = old_argv
