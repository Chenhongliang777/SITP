"""微博登录：供 GUI 调用的异步 Playwright 会话。"""
from __future__ import annotations

import asyncio
import json
import threading
from typing import Callable, List, Optional, Tuple

from app.paths import get_weibo_auth_path, get_weibo_collector_dir

LogFn = Callable[[str], None]

KEY_COOKIES = {"SUB", "SUBP", "SCF", "ALF"}


def is_login_file_present() -> bool:
    path = get_weibo_auth_path()
    if not path.exists() or path.stat().st_size < 10:
        return False
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        cookies = data.get("cookies") or []
        names = {c.get("name") for c in cookies}
        return "SUB" in names
    except (json.JSONDecodeError, OSError):
        return False


class WeiboLoginSession:
    """在后台线程维持 Playwright，直到用户确认保存或取消。"""

    def __init__(self) -> None:
        self._thread: Optional[threading.Thread] = None
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._ready = threading.Event()
        self._confirm_event: Optional[asyncio.Event] = None
        self._result: Optional[Tuple[bool, str, List[str]]] = None
        self._error: Optional[str] = None
        self._running = False

    @property
    def is_active(self) -> bool:
        return self._running

    def start(self, on_log: LogFn) -> None:
        if self._running:
            on_log("登录窗口已在运行，请在浏览器中完成扫码后点击「确认已登录」。")
            return

        self._result = None
        self._error = None
        self._ready.clear()

        def _thread_main() -> None:
            self._loop = asyncio.new_event_loop()
            asyncio.set_event_loop(self._loop)
            try:
                self._loop.run_until_complete(self._async_login_flow(on_log))
            except Exception as e:
                self._error = str(e)
                on_log(f"登录流程异常: {e}")
            finally:
                self._running = False
                self._loop.close()
                self._loop = None

        self._thread = threading.Thread(target=_thread_main, daemon=True)
        self._thread.start()
        self._ready.wait(timeout=30)

    def confirm_save(self, on_log: LogFn) -> Tuple[bool, str]:
        if not self._running or not self._loop or not self._confirm_event:
            return False, "请先点击「打开微博登录窗口」"
        future = asyncio.run_coroutine_threadsafe(
            self._signal_confirm(), self._loop
        )
        try:
            future.result(timeout=120)
        except Exception as e:
            return False, f"保存登录态失败: {e}"
        if self._thread:
            self._thread.join(timeout=30)
        if self._error:
            return False, self._error
        if self._result:
            ok, msg, found = self._result
            if ok and found:
                on_log(f"关键 Cookie: {found}")
            return ok, msg
        return False, "未知错误"

    async def _signal_confirm(self) -> None:
        if self._confirm_event:
            self._confirm_event.set()

    async def _async_login_flow(self, on_log: LogFn) -> None:
        from playwright.async_api import async_playwright

        self._running = True
        state_file = get_weibo_auth_path()
        state_file.parent.mkdir(parents=True, exist_ok=True)
        self._confirm_event = asyncio.Event()

        on_log("正在启动浏览器…")
        async with async_playwright() as p:
            browser = None
            try:
                browser = await p.chromium.launch(
                    headless=False,
                    channel="chrome",
                    args=["--disable-blink-features=AutomationControlled"],
                )
                on_log("已启动系统 Chrome")
            except Exception as e:
                on_log(f"未检测到系统 Chrome，使用 Chromium: {e}")
                browser = await p.chromium.launch(
                    headless=False,
                    args=["--disable-blink-features=AutomationControlled"],
                )

            context = await browser.new_context(
                viewport={"width": 1280, "height": 800},
                locale="zh-CN",
                timezone_id="Asia/Shanghai",
            )
            page = await context.new_page()
            on_log("正在打开微博…")
            await page.goto("https://weibo.com", wait_until="domcontentloaded")
            await asyncio.sleep(3)
            on_log("请在浏览器窗口中扫码登录，完成后点击「确认已登录」。")
            self._ready.set()

            await self._confirm_event.wait()

            await context.storage_state(path=str(state_file))
            cookies = await context.cookies()
            found = [c["name"] for c in cookies if c["name"] in KEY_COOKIES]
            ok = "SUB" in set(found)
            if ok:
                self._result = (
                    True,
                    f"登录态已保存: {state_file}",
                    found,
                )
                on_log(self._result[1])
            else:
                self._result = (
                    False,
                    "未检测到有效登录 Cookie（缺少 SUB），请重新登录",
                    found,
                )
                on_log(self._result[1])

            await browser.close()


_login_session: Optional[WeiboLoginSession] = None


def get_login_session() -> WeiboLoginSession:
    global _login_session
    if _login_session is None:
        _login_session = WeiboLoginSession()
    return _login_session
