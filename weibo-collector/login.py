#!/usr/bin/env python3
"""命令行登录入口（GUI 请用 gui_main.py）。"""
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.weibo_login import WeiboLoginSession


def main() -> None:
    session = WeiboLoginSession()

    def on_log(msg: str) -> None:
        print(msg)

    session.start(on_log)
    time.sleep(2)
    input("\n完成扫码登录后，按回车键保存状态...")
    ok, msg = session.confirm_save(on_log)
    print(msg)
    if not ok:
        sys.exit(1)


if __name__ == "__main__":
    main()
