"""
任务二：采集后端完整文本获取
基于原有 weibo_collector 的稳定页面抓取流程改造。
"""

import argparse
import asyncio
import json
import os
import random
import re
import urllib.parse
from abc import ABC, abstractmethod
from datetime import datetime
from html import unescape
from pathlib import Path
from typing import Dict, List, Optional

import requests
from playwright.async_api import Page, async_playwright

from utils.project_root import get_project_root

SCRIPT_DIR = get_project_root()
DATA_DIR = SCRIPT_DIR / "data"
LOG_DIR = SCRIPT_DIR / "logs"
STATE_FILE = DATA_DIR / "weibo_auth.json"

DATA_DIR.mkdir(parents=True, exist_ok=True)
LOG_DIR.mkdir(parents=True, exist_ok=True)


def _weibo_fast_collect() -> bool:
    """环境变量 WEIBO_FAST_COLLECT=1 时缩短等待（略增风控风险，可明显加快采集）。"""
    return os.getenv("WEIBO_FAST_COLLECT", "").lower() in ("1", "true", "yes")


def _weibo_turbo_collect() -> bool:
    """WEIBO_TURBO_COLLECT=1（或 launcher --turbo-collect）时进一步缩短滚动/翻页等待；风控风险高于 fast。"""
    return os.getenv("WEIBO_TURBO_COLLECT", "").lower() in ("1", "true", "yes")


def _use_mobile_api() -> bool:
    """默认关闭 m.weibo.cn 移动端接口；设 WEIBO_USE_MOBILE_API=1 时仍可作为补充。"""
    return os.getenv("WEIBO_USE_MOBILE_API", "").lower() in ("1", "true", "yes")


def _mobile_api_max_pages() -> int:
    """移动端分页上限（仅当 WEIBO_USE_MOBILE_API=1 时生效）。"""
    raw = os.getenv("WEIBO_MOBILE_MAX_PAGES", "").strip()
    try:
        if raw:
            return max(1, min(30, int(raw)))
    except ValueError:
        pass
    if _weibo_turbo_collect():
        return 15
    if _weibo_fast_collect():
        return 10
    return 5

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
]

VIEWPORTS = [
    {"width": 1920, "height": 1080},
    {"width": 1440, "height": 900},
]


class CollectorBackend(ABC):
    @abstractmethod
    async def collect(self) -> List[Dict]:
        """执行采集并返回记录列表。"""


class WeiboCollectorBackend(CollectorBackend):
    def __init__(
        self,
        keyword: str,
        start_date: str,
        end_date: str,
        target_count: int = 80,
        headless: bool = True,
        proxy: Optional[str] = None,
    ) -> None:
        self.keyword = keyword
        self.start_date = start_date
        self.end_date = end_date
        self.target_count = target_count
        self.headless = headless
        self.proxy = proxy
        self.records: List[Dict] = []
        self.seen_mids = set()
        self.log_file = LOG_DIR / f"collector_backend_{datetime.now():%Y%m%d_%H%M%S}.log"

    def _log(self, msg: str) -> None:
        line = f"[{datetime.now():%H:%M:%S}] {msg}"
        print(line)
        with open(self.log_file, "a", encoding="utf-8") as f:
            f.write(line + "\n")

    @staticmethod
    def _clean_html_text(raw_text: str) -> str:
        if not raw_text:
            return ""
        text = unescape(raw_text)
        text = re.sub(r"<br\s*/?>", "\n", text, flags=re.IGNORECASE)
        text = re.sub(r"<[^>]+>", "", text)
        text = text.replace("\u200b", "").replace("\u200c", "").replace("\u200d", "")
        text = text.replace("展开全文c", "").replace("收起d", "").replace("的微博视频", "")
        text = " ".join(text.split())
        return text.strip()

    async def _init_browser(self, p):
        launch_args = {
            "headless": self.headless,
            "channel": "chrome",
            "args": [
                "--disable-blink-features=AutomationControlled",
                "--disable-web-security",
                "--disable-features=IsolateOrigins,site-per-process",
            ],
        }
        if self.proxy:
            launch_args["proxy"] = {"server": self.proxy}

        browser = await p.chromium.launch(**launch_args)
        context_kwargs = {
            "viewport": random.choice(VIEWPORTS),
            "user_agent": random.choice(USER_AGENTS),
        }
        if STATE_FILE.exists():
            context_kwargs["storage_state"] = str(STATE_FILE)
            self._log(f"已加载登录态: {STATE_FILE}")
        else:
            self._log("未找到登录态文件，将以未登录状态运行")

        context = await browser.new_context(**context_kwargs)
        await context.add_init_script(
            """
            Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
            Object.defineProperty(navigator, 'plugins', {get: () => [1, 2, 3, 4, 5]});
            Object.defineProperty(navigator, 'languages', {get: () => ['zh-CN', 'zh', 'en']});
            window.chrome = { runtime: {} };
            """
        )
        return browser, context

    async def _human_scroll(self, page: Page):
        turbo = _weibo_turbo_collect()
        fast = _weibo_fast_collect()
        if turbo:
            n = random.randint(1, 2)
            lo, hi = (0.22, 0.55)
        elif fast:
            n = random.randint(2, 4)
            lo, hi = (0.7, 1.5)
        else:
            n = random.randint(3, 5)
            lo, hi = (2.0, 4.0)
        for _ in range(n):
            await page.evaluate(f"window.scrollBy(0, {random.randint(400, 800)})")
            await asyncio.sleep(random.uniform(lo, hi))

    async def _fast_scroll_feed_for_ajax(self, page: Page) -> None:
        """尽量触发列表 XHR：优先滚到底，比多段小步滚动更快。"""
        turbo = _weibo_turbo_collect()
        fast = _weibo_fast_collect()
        if turbo:
            await page.evaluate("window.scrollTo(0, Math.max(document.body.scrollHeight, document.documentElement.scrollHeight))")
            await asyncio.sleep(0.18)
            await page.evaluate("window.scrollBy(0, -400); window.scrollTo(0, document.documentElement.scrollHeight)")
            await asyncio.sleep(0.15)
            return
        if fast:
            for _ in range(2):
                await page.evaluate(
                    "window.scrollTo(0, Math.max(document.body.scrollHeight, document.documentElement.scrollHeight))"
                )
                await asyncio.sleep(0.4)
            return
        await self._human_scroll(page)

    async def _wait_statuses_xhr(self, page: Page, timeout_ms: int) -> None:
        """等待搜索列表相关 ajax，减少固定盲等时间。"""
        try:
            await page.wait_for_response(
                lambda r: "/ajax/statuses/" in r.url and r.status == 200,
                timeout=timeout_ms,
            )
        except Exception:
            pass

    async def _expand_posts(self, page: Page):
        turbo = _weibo_turbo_collect()
        gap = 0.12 if turbo else 0.3
        round_sleep = 0.45 if turbo else 1.0
        for _ in range(3):
            buttons = await page.locator(
                'a:has-text("展开"), a:has-text("展开全文"), span:has-text("展开"), span:has-text("展开全文")'
            ).all()
            clicked = 0
            for btn in buttons:
                try:
                    if await btn.is_visible() and await btn.is_enabled():
                        await btn.click(timeout=2000)
                        clicked += 1
                        await asyncio.sleep(gap)
                except Exception:
                    continue
            if clicked == 0:
                break
            await asyncio.sleep(round_sleep)

    async def _extract_time(self, el) -> str:
        selectors = ['a[node-type="feed_list_item_date"]', ".from a", '[class*="time"]']
        for sel in selectors:
            try:
                time_el = el.locator(sel).first
                if await time_el.count() > 0:
                    t = await time_el.inner_text(timeout=1000)
                    if t:
                        return t.strip()
            except Exception:
                continue
        return ""

    async def _extract_username(self, el) -> str:
        for sel in ["a.name", '[class*="name"]', "a[usercard]", 'a[href*="/u/"]']:
            try:
                name_el = el.locator(sel).first
                if await name_el.count() > 0:
                    name = await name_el.inner_text(timeout=1000)
                    if name and name.strip():
                        return name.strip()
            except Exception:
                continue
        return "未知用户"

    def _append_record(self, mid: str, raw_text: str, time_text: str, username: str, source_backend: str) -> None:
        if not mid or mid in self.seen_mids:
            return
        clean_text = self._clean_html_text(raw_text)
        if not clean_text:
            return
        self.seen_mids.add(mid)
        self.records.append(
            {
                "mid": str(mid),
                "raw_text": raw_text,
                "clean_text": clean_text,
                "time": time_text,
                "source_backend": source_backend,
                "username": username,
                "crawl_time": datetime.now().isoformat(),
            }
        )

    async def _handle_ajax_statuses_response(self, resp) -> None:
        """解析单条 XHR 响应中的微博列表（须在 page 上只注册一次 listener）。"""
        if "/ajax/statuses/" not in resp.url:
            return
        if len(self.records) >= self.target_count:
            return
        try:
            data = await resp.json()
        except Exception:
            return
        statuses = data.get("data", {}).get("list", []) or data.get("data", {}).get("statuses", [])
        for item in statuses:
            if len(self.records) >= self.target_count:
                break
            mblog = item.get("mblog", item)
            raw_text = mblog.get("text") or (mblog.get("longText", {}) or {}).get("longTextContent", "")
            self._append_record(
                mid=str(mblog.get("mid", "")),
                raw_text=raw_text,
                time_text=mblog.get("created_at", ""),
                username=(mblog.get("user", {}) or {}).get("screen_name", "未知用户"),
                source_backend="api",
            )

    async def _scroll_and_wait_ajax_capture(self, page: Page) -> int:
        """滚动触发列表 XHR；listener 已由 DOM 主链路单次注册，此处不再 page.on。"""
        before = len(self.records)
        await self._fast_scroll_feed_for_ajax(page)
        if _weibo_turbo_collect():
            await asyncio.sleep(0.28)
            await self._wait_statuses_xhr(page, 2200)
        elif _weibo_fast_collect():
            await asyncio.sleep(0.55)
            await self._wait_statuses_xhr(page, 3500)
        else:
            await asyncio.sleep(1.0)
            await self._wait_statuses_xhr(page, 7000)
        return len(self.records) - before

    def _collect_via_mobile_api(self) -> int:
        before = len(self.records)
        sess = requests.Session()
        sess.headers.update(
            {
                "User-Agent": "Mozilla/5.0 (Linux; Android 13) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Mobile Safari/537.36"
            }
        )
        max_pages = _mobile_api_max_pages()
        for page_idx in range(1, max_pages + 1):
            if len(self.records) >= self.target_count:
                break
            try:
                resp = sess.get(
                    "https://m.weibo.cn/api/container/getIndex",
                    params={
                        "containerid": f"100103type=1&q={self.keyword}",
                        "page_type": "searchall",
                        "page": page_idx,
                    },
                    timeout=12,
                )
                cards = (resp.json().get("data", {}) or {}).get("cards", [])
                for card in cards:
                    mblog = card.get("mblog")
                    if not mblog:
                        continue
                    self._append_record(
                        mid=str(mblog.get("mid", "")),
                        raw_text=mblog.get("text", ""),
                        time_text=mblog.get("created_at", ""),
                        username=(mblog.get("user", {}) or {}).get("screen_name", "未知用户"),
                        source_backend="mweibo",
                    )
            except Exception:
                break
        return len(self.records) - before

    def _collect_via_local_cache(self) -> int:
        """
        基于原有 collector 历史输出做兜底，避免线上页面结构变化导致 0 条。
        仅在前面链路拿不到足够数据时触发。
        """
        before = len(self.records)
        candidates = sorted(
            [
                p
                for p in DATA_DIR.glob("*.json")
                if p.is_file()
                and p.name.startswith(f"{self.keyword}_")
                and not p.name.startswith("raw_")
                and "warning_" not in p.name
                and "judgment_" not in p.name
                and p.name != "weibo_auth.json"
            ],
            key=lambda x: x.stat().st_mtime,
            reverse=True,
        )
        if not candidates:
            return 0

        for path in candidates:
            if len(self.records) >= self.target_count:
                break
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
                rows = payload.get("data", [])
                for row in rows:
                    if len(self.records) >= self.target_count:
                        break
                    content = row.get("content", "")
                    self._append_record(
                        mid=str(row.get("mid", "")),
                        raw_text=content,
                        time_text=row.get("time", ""),
                        username=row.get("username", "未知用户"),
                        source_backend="local_cache",
                    )
            except Exception:
                continue
        return len(self.records) - before

    async def _extract_page_dom(self, page: Page) -> int:
        before = len(self.records)
        try:
            await page.wait_for_selector(
                '[action-type="feed_list_item"], .card-wrap, .vue-recycle-scroller__item-view, .card',
                timeout=12000,
            )
        except Exception:
            self._log("当前页未检测到微博列表元素，跳过本页")
            return 0
        elements = []
        for sel in ['[action-type="feed_list_item"]', ".card-wrap", ".vue-recycle-scroller__item-view", ".card"]:
            elements = await page.locator(sel).all()
            if elements:
                break

        for el in elements:
            if len(self.records) >= self.target_count:
                break
            try:
                mid = await el.get_attribute("mid") or await el.get_attribute("data-mid")
                if not mid:
                    mid = str(hash(await el.inner_text()) % 1000000000)

                raw_html = ""
                for sel in ['[node-type="feed_list_content_full"]', '[node-type="feed_list_content"]', ".txt", "p"]:
                    try:
                        node = el.locator(sel).first
                        if await node.count() > 0:
                            raw_html = await node.inner_html(timeout=1000)
                            if raw_html:
                                break
                    except Exception:
                        continue

                if not raw_html:
                    continue

                self._append_record(
                    mid=mid,
                    raw_text=raw_html,
                    time_text=await self._extract_time(el),
                    username=await self._extract_username(el),
                    source_backend="dom_fallback",
                )
            except Exception:
                continue
        return len(self.records) - before

    async def _collect_via_dom_mainflow(self) -> int:
        before = len(self.records)
        url = (
            "https://s.weibo.com/weibo?"
            f"q={urllib.parse.quote(self.keyword)}&"
            f"timescope=custom:{self.start_date}-0:{self.end_date}-23&"
            "page=1"
        )
        async with async_playwright() as p:
            browser, context = await self._init_browser(p)
            page = await context.new_page()

            async def on_response(resp):
                await self._handle_ajax_statuses_response(resp)

            page.on("response", on_response)

            await page.goto(url, wait_until="domcontentloaded", timeout=30000)
            if _weibo_turbo_collect():
                await self._wait_statuses_xhr(page, 4000)
                await asyncio.sleep(0.35)
            elif _weibo_fast_collect():
                await self._wait_statuses_xhr(page, 6000)
                await asyncio.sleep(0.75)
            else:
                await self._wait_statuses_xhr(page, 10000)
                await asyncio.sleep(1.6)

            page_num = 1
            empty_pages = 0
            try:
                max_dom_pages = max(1, min(50, int(os.getenv("WEIBO_MAX_DOM_PAGES", "30"))))
            except ValueError:
                max_dom_pages = 30
            empty_patience = 2 if _weibo_turbo_collect() else 3
            while len(self.records) < self.target_count and page_num <= max_dom_pages:
                self._log(f"DOM主链路第{page_num}页，当前 {len(self.records)}/{self.target_count}")
                count_before_page = len(self.records)

                await self._scroll_and_wait_ajax_capture(page)
                if len(self.records) >= self.target_count:
                    self._log("Ajax 捕获已满足目标条数，跳过展开与 DOM 提取并结束翻页。")
                    break

                await self._expand_posts(page)
                if len(self.records) >= self.target_count:
                    self._log("展开后已满足目标条数，跳过 DOM 提取并结束翻页。")
                    break

                dom_added = await self._extract_page_dom(page)
                page_growth = len(self.records) - count_before_page
                self._log(f"本页合计新增 {page_growth} 条（其中 DOM 路径约 {dom_added} 条）")
                if page_growth == 0:
                    empty_pages += 1
                else:
                    empty_pages = 0
                if empty_pages >= empty_patience:
                    break

                try:
                    next_btn = None
                    for sel in ["a.next", 'a:has-text("下一页")', '[class*="next"]']:
                        btn = page.locator(sel).first
                        if await btn.count() > 0 and await btn.is_visible():
                            next_btn = btn
                            break
                    if not next_btn:
                        break
                    await next_btn.click(timeout=5000)
                    if _weibo_turbo_collect():
                        await self._wait_statuses_xhr(page, 3500)
                        await asyncio.sleep(random.uniform(0.45, 1.1))
                    elif _weibo_fast_collect():
                        await self._wait_statuses_xhr(page, 5000)
                        await asyncio.sleep(random.uniform(1.2, 2.8))
                    else:
                        await self._wait_statuses_xhr(page, 8000)
                        await asyncio.sleep(random.uniform(2.5, 5.5))
                    page_num += 1
                except Exception:
                    break
            await browser.close()
        return len(self.records) - before

    def _save_outputs(self) -> None:
        output = {
            "meta": {
                "keyword": self.keyword,
                "date_range": f"{self.start_date} to {self.end_date}",
                "target": self.target_count,
                "actual": min(len(self.records), self.target_count),
                "crawl_time": datetime.now().isoformat(),
                "source": "collector_backend",
            },
            "data": self.records[: self.target_count],
        }
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        output_file = DATA_DIR / f"raw_{self.keyword}_{ts}.json"
        output_file.write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")
        self._log(f"已输出: {output_file}")

        sample = {
            "meta": {**output["meta"], "sample": 20},
            "data": output["data"][:20],
        }
        sample_file = DATA_DIR / "raw_sample.json"
        sample_file.write_text(json.dumps(sample, ensure_ascii=False, indent=2), encoding="utf-8")
        self._log(f"已输出样例: {sample_file}")

        lengths = [len(x.get("clean_text", "")) for x in output["data"] if x.get("clean_text")]
        avg_len = sum(lengths) / len(lengths) if lengths else 0.0
        self._log(f"clean_text 平均长度: {avg_len:.2f}")

    async def collect(self) -> List[Dict]:
        if _use_mobile_api():
            self._log(f"已开启 WEIBO_USE_MOBILE_API：先拉移动端（最多 {_mobile_api_max_pages()} 页）")
            mobile_first = self._collect_via_mobile_api()
            self._log(f"移动端 API {mobile_first} 条，累计 {len(self.records)}/{self.target_count}")
            if len(self.records) >= self.target_count:
                self._save_outputs()
                return self.records

        dom_added = await self._collect_via_dom_mainflow()
        self._log(f"DOM主链路累计 {dom_added} 条")
        if len(self.records) < self.target_count and _use_mobile_api():
            mobile_added = self._collect_via_mobile_api()
            self._log(f"移动端 API 第二轮补充 {mobile_added} 条")
        if len(self.records) < self.target_count:
            cache_added = self._collect_via_local_cache()
            self._log(f"本地缓存补充 {cache_added} 条")
        self._save_outputs()
        return self.records


async def _main() -> None:
    parser = argparse.ArgumentParser(description="采集后端完整文本获取（T2）")
    parser.add_argument("--keyword", required=True, help="关键词")
    parser.add_argument("--start-date", required=True, help="开始日期 YYYY-MM-DD")
    parser.add_argument("--end-date", required=True, help="结束日期 YYYY-MM-DD")
    parser.add_argument("--target-count", type=int, default=80, help="目标条数")
    parser.add_argument("--no-headless", action="store_true", help="关闭无头模式")
    parser.add_argument("--proxy", default=None, help="代理地址")
    args = parser.parse_args()

    collector = WeiboCollectorBackend(
        keyword=args.keyword,
        start_date=args.start_date,
        end_date=args.end_date,
        target_count=args.target_count,
        headless=not args.no_headless,
        proxy=args.proxy,
    )
    await collector.collect()


if __name__ == "__main__":
    asyncio.run(_main())
