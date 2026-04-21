"""
微博足球舆情采集器（Playwright 版）
基于 Playwright + 已保存登录态，稳定采集微博搜索数据
"""

import asyncio
import json
import random
import urllib.parse
import argparse
import re
from datetime import datetime, timedelta
from pathlib import Path
from typing import List, Dict, Optional
from playwright.async_api import async_playwright, Page

# ============ 配置 ============
SCRIPT_DIR = Path(__file__).parent.resolve()
PROJECT_DIR = SCRIPT_DIR.parent
STATE_FILE = PROJECT_DIR / "data" / "weibo_auth.json"
OUTPUT_DIR = PROJECT_DIR / "data"
LOG_DIR = PROJECT_DIR / "logs"

OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
LOG_DIR.mkdir(parents=True, exist_ok=True)

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
]

VIEWPORTS = [
    {"width": 1920, "height": 1080},
    {"width": 1440, "height": 900},
]


class WeiboCollector:
    def __init__(self, keyword: str, start_date: str, end_date: str,
                 target_count: int = 150, proxy: Optional[str] = None):
        self.keyword = keyword
        self.start_date = start_date
        self.end_date = end_date
        self.target_count = target_count
        self.proxy = proxy
        self.data: List[Dict] = []
        self.seen_mids = set()
        self.log_file = LOG_DIR / f"collect_{datetime.now():%Y%m%d_%H%M%S}.log"

    def _log(self, msg: str):
        timestamp = datetime.now().strftime("%H:%M:%S")
        line = f"[{timestamp}] {msg}"
        print(line)
        with open(self.log_file, 'a', encoding='utf-8') as f:
            f.write(line + '\n')

    async def _init_browser(self, p):
        launch_args = {
            "headless": True,
            "channel": "chrome",
            "args": [
                '--disable-blink-features=AutomationControlled',
                '--disable-web-security',
                '--disable-features=IsolateOrigins,site-per-process',
            ]
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

        await context.add_init_script("""
            Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
            Object.defineProperty(navigator, 'plugins', {get: () => [1, 2, 3, 4, 5]});
            Object.defineProperty(navigator, 'languages', {get: () => ['zh-CN', 'zh', 'en']});
            window.chrome = { runtime: {} };
        """)

        return browser, context

    async def _human_scroll(self, page: Page):
        for i in range(random.randint(3, 5)):
            await page.evaluate(f"window.scrollBy(0, {random.randint(400, 800)})")
            await asyncio.sleep(random.uniform(2.0, 4.0))

    async def _expand_posts(self, page: Page):
        """多轮展开，处理动态加载的展开按钮"""
        for round_idx in range(3):
            try:
                buttons = await page.locator('''
                    a:has-text("展开"),
                    a:has-text("展开全文"),
                    span:has-text("展开"),
                    span:has-text("展开全文")
                ''').all()

                clicked = 0
                for btn in buttons:
                    try:
                        if await btn.is_visible() and await btn.is_enabled():
                            await btn.click(timeout=2000)
                            clicked += 1
                            await asyncio.sleep(0.5)
                    except:
                        continue

                if clicked == 0:
                    break
                await asyncio.sleep(1)
            except Exception as e:
                self._log(f"展开全文第{round_idx + 1}轮异常: {e}")
                break
        # 关键：展开后再滚动一次，确保新展开的内容进入DOM
        await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        await asyncio.sleep(2)

    def _clean_content(self, content: str) -> str:
        """清洗博文内容"""
        if not content:
            return ""
        content = content.replace("展开c", "").replace("收起d", "")
        content = content.replace("展开全文c", "").replace("收起全文d", "")
        content = content.replace("的微博视频", "")

        # 去掉视频引用前缀 L（通常是单独一个L后跟用户名）
        import re
        content = re.sub(r'(?<=\s)L([^\s]{2,20})(?=\s|$)', r'\1', content)
        content = re.sub(r'^L([^\s]{2,20})(?=\s|$)', r'\1', content)
        # 去掉零宽空格和截断标记
        content = content.replace('\u200b', '').replace('\u200c', '').replace('\u200d', '')
        
        content = content.replace("", "#")
        content = " ".join(content.split())
        return content.strip()

    def _is_relevant(self, content: str) -> bool:
        """严格过滤无关内容"""
        if not content or len(content) < 5:
            return False

        # 1. 排除"中超"作为"中国超市"的误命中
        if any(k in content for k in ["楼下中超", "现实中超","去中超买", "中超扛了", "中超买", "中超面粉"]):
            return False

        # 2. 排除超话签到、等级升级类
        if any(k in content for k in ["超话等级升级", "终于晋级啦", "LV.8", "LV.9", "LV.10", "忠实粉丝头衔"]):
            return False

        # 3. 排除明显非足球领域
        non_football = [
            "动漫", "小说", "电视剧", "综艺", "电影", "娱乐圈",
            "恒大标识", "私人飞机", "奢靡", "万亿债",
            "女神", "军火", "纸片人", "身材", "纯爱",
        ]
        if any(k in content for k in non_football):
            return False

        # 4. 必须包含足球相关实体词
        football_keywords = [
            "足球", "中超", "国足", "联赛", "球员", "球队", "俱乐部", "球场",
            "裁判", "VAR", "进球", "红牌", "黄牌", "越位", "点球", "角球", "任意球",
            "武磊", "张玉宁", "韦世豪", "克雷桑", "外援", "青训", "主教练", "主帅", "门将",
            "北京国安", "山东泰山", "上海申花", "上海海港", "成都蓉城",
            "天津津门虎", "大连英博", "浙江FC", "重庆铜梁龙", "深圳新鹏城",
            "武汉三镇", "青岛海牛", "云南玉昆", "辽宁铁人", "长春亚泰",
            "河南队", "梅州客家", "青岛西海岸", "南通支云",
            "亚冠", "足协杯", "中甲", "中乙", "中冠", "世预赛",
            "津门虎", "泰山队", "申花", "海港", "蓉城", "英博",
            "比赛", "客场", "主场", "对阵", "VS", "vs", "绝杀", "扳平", "逆转",
            "赛前发布会", "赛后", "积分榜", "射手榜", "最佳球员", "观赛",
        ]

        has_football = any(k in content for k in football_keywords)

        # 5. 排除纯广告
        ad_keywords = ["优惠券", "点击链接", "购买", "淘宝", "京东", "拼多多", "秒杀", "代购"]
        if any(k in content for k in ad_keywords) and not has_football:
            return False

        return has_football
    
    

    async def _extract_time(self, el) -> str:
        selectors = [
            'a[node-type="feed_list_item_date"]',
            '.from a',
            '[class*="time"]'
        ]
        raw_time = ""
        for sel in selectors:
            try:
                time_el = el.locator(sel).first
                if await time_el.count() > 0:
                    raw_time = await time_el.inner_text(timeout=1000)
                    if raw_time:
                        break
            except:
                continue
    
        if not raw_time:
            return ""
    
        now = datetime.now()
    
        # "32秒前"
        m = re.search(r'(\d+)\s*秒前', raw_time)
        if m:
            dt = now - timedelta(seconds=int(m.group(1)))
            return dt.strftime("%m月%d日 %H:%M")
    
        # "5分钟前"
        m = re.search(r'(\d+)\s*分钟前', raw_time)
        if m:
            dt = now - timedelta(minutes=int(m.group(1)))
            return dt.strftime("%m月%d日 %H:%M")
    
        # "今天19:33"
        m = re.search(r'今天\s*(\d{1,2}):(\d{2})', raw_time)
        if m:
            return now.strftime("%m月%d日") + f" {m.group(1).zfill(2)}:{m.group(2)}"
    
        # 标准格式直接返回
        return raw_time.strip()
   
    async def _extract_username(self, el) -> str:
        selectors = [
            'a.name',
            '[class*="name"]',
            '.txt a[href^="/u/"]',
            'a[usercard]',
            'a[href*="/u/"]'
        ]
        for sel in selectors:
            try:
                name_el = el.locator(sel).first
                if await name_el.count() > 0:
                    text = await name_el.inner_text(timeout=1000)
                    if text and text.strip():
                        return text.strip()
            except:
                continue
        return "未知用户"

    async def _extract_link(self, el) -> str:
        selectors = [
            'a[node-type="feed_list_item_date"]',
            '.from a',
            'a[href*="/status/"]'
        ]
        for sel in selectors:
            try:
                link_el = el.locator(sel).first
                if await link_el.count() > 0:
                    href = await link_el.get_attribute('href')
                    if href:
                        if href.startswith('//'):
                            return 'https:' + href
                        elif href.startswith('/'):
                            return 'https://weibo.com' + href
                        return href
            except:
                continue
        return ""

    async def _extract_page(self, page: Page) -> List[Dict]:
        posts = []

        try:
            await page.wait_for_selector(
                '[action-type="feed_list_item"], .card-wrap, .vue-recycle-scroller__item-view',
                timeout=10000
            )
        except:
            self._log("未检测到博文列表")
            return []

        selectors = [
            '[action-type="feed_list_item"]',
            '.card-wrap',
            '.vue-recycle-scroller__item-view'
        ]

        elements = []
        for sel in selectors:
            elements = await page.locator(sel).all()
            if elements:
                break

        self._log(f"本页找到 {len(elements)} 个博文元素")

        for el in elements:
            try:
                mid = await el.get_attribute('mid') or await el.get_attribute('data-mid')
                if not mid:
                    text = await el.inner_text()
                    mid = str(hash(text) % 100000000)

                if mid in self.seen_mids:
                    continue
                self.seen_mids.add(mid)

                raw_content = ""
                for content_sel in [
                    '[node-type="feed_list_content"]',
                    '[node-type="feed_list_content_full"]',
                    '.txt',
                    'p'
                ]:
                    try:
                        raw_content = await el.locator(content_sel).inner_text(timeout=1000)
                        if raw_content:
                            break
                    except:
                        continue

                content = self._clean_content(raw_content)
                if not self._is_relevant(content):
                    continue

                post = {
                    "mid": mid,
                    "content": content,
                    "time": await self._extract_time(el),
                    "username": await self._extract_username(el),
                    "link": await self._extract_link(el),
                    "crawl_time": datetime.now().isoformat(),
                }
                posts.append(post)

            except Exception:
                continue

        self._log(f"本页过滤后保留 {len(posts)} 条")
        return posts

    async def collect(self):
        async with async_playwright() as p:
            browser, context = await self._init_browser(p)
            page = await context.new_page()

            encoded_keyword = urllib.parse.quote(self.keyword.strip())
            url = (f"https://s.weibo.com/weibo?"
                   f"q={encoded_keyword}&"
                   f"timescope=custom:{self.start_date}-0:{self.end_date}-23&"
                   f"page=1")

            self._log(f"开始采集: {self.keyword}")
            self._log(f"日期范围: {self.start_date} 至 {self.end_date}")
            self._log(f"目标数量: {self.target_count}")
            self._log(f"起始URL: {url}")

            try:
                await page.goto(url, wait_until="domcontentloaded", timeout=30000)
                await asyncio.sleep(5)

                page_num = 1
                empty_pages = 0

                while len(self.data) < self.target_count and page_num <= 50:
                    self._log(f"处理第 {page_num} 页，当前已收集: {len(self.data)}/{self.target_count}")

                    await self._human_scroll(page)
                    await self._expand_posts(page)

                    posts = await self._extract_page(page)

                    if posts:
                        self.data.extend(posts)
                        empty_pages = 0
                        self._log(f"本页提取 {len(posts)} 条，总计 {len(self.data)}")
                    else:
                        empty_pages += 1
                        self._log(f"本页无数据（连续空页: {empty_pages}）")
                        if empty_pages >= 3:
                            self._log("连续3页无数据，停止采集")
                            break

                    delay = random.uniform(1, 3)
                    self._log(f"等待 {delay:.1f} 秒...")
                    await asyncio.sleep(delay)

                    try:
                        next_selectors = ['a.next', 'a:has-text("下一页")', '[class*="next"]']
                        next_btn = None
                        for sel in next_selectors:
                            btn = page.locator(sel).first
                            if await btn.count() > 0 and await btn.is_visible():
                                next_btn = btn
                                break

                        if not next_btn:
                            self._log("未找到下一页按钮，可能已到末尾")
                            break

                        if await page.locator('text=验证, text=安全中心, .verify').count() > 0:
                            self._log("触发反爬验证！保存当前进度...")
                            break

                        await next_btn.click(timeout=5000)
                        await asyncio.sleep(random.uniform(5, 10))
                        page_num += 1

                    except Exception as e:
                        self._log(f"翻页失败: {e}")
                        break

            except Exception as e:
                self._log(f"采集异常: {e}")
                import traceback
                self._log(traceback.format_exc())

            finally:
                await browser.close()

            return await self._save_results()

    async def _save_results(self):
        filename = OUTPUT_DIR / f"{self.keyword}_{self.start_date}-{self.end_date}_{len(self.data)}条.json"

        result = {
            "meta": {
                "keyword": self.keyword,
                "date_range": f"{self.start_date} to {self.end_date}",
                "target": self.target_count,
                "actual": len(self.data),
                "crawl_time": datetime.now().isoformat(),
                "source": "weibo_search"
            },
            "data": self.data[:self.target_count]
        }

        with open(filename, 'w', encoding='utf-8') as f:
            json.dump(result, f, ensure_ascii=False, indent=2)

        self._log(f"结果已保存: {filename}")
        return str(filename)


async def main():
    parser = argparse.ArgumentParser(description='微博足球舆情采集器（本地Playwright版）')
    parser.add_argument('--keyword', required=True, help='搜索关键词')
    parser.add_argument('--start-date', required=True, help='开始日期 YYYY-MM-DD')
    parser.add_argument('--end-date', required=True, help='结束日期 YYYY-MM-DD')
    parser.add_argument('--target-count', type=int, default=150, help='目标数量')
    parser.add_argument('--proxy', type=str, default=None, help='代理地址')

    args = parser.parse_args()

    collector = WeiboCollector(
        keyword=args.keyword,
        start_date=args.start_date,
        end_date=args.end_date,
        target_count=args.target_count,
        proxy=args.proxy
    )

    await collector.collect()


if __name__ == "__main__":
    asyncio.run(main())