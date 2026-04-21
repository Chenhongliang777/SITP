#!/usr/bin/env python3
import asyncio
from pathlib import Path
from playwright.async_api import async_playwright

# 自动定位到项目根目录（不再写死 C 盘）
SCRIPT_DIR = Path(__file__).parent.resolve()
PROJECT_DIR = SCRIPT_DIR.parent
STATE_FILE = PROJECT_DIR / "data" / "weibo_auth.json"

async def save_login_state():
    # 自动创建 data 目录（解决 FileNotFoundError）
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    
    print("=" * 50)
    print("微博登录态保存工具")
    print("=" * 50)
    
    async with async_playwright() as p:
        # channel="chrome" 调用你本地已安装的 Chrome，而不是 Playwright 的 Testing 版
        # 这样微博反爬检测更弱，且能正常显示二维码
        try:
            browser = await p.chromium.launch(
                headless=False, 
                channel="chrome",  # 优先用系统 Chrome
                args=['--disable-blink-features=AutomationControlled']
            )
            print("已启动系统 Chrome")
        except Exception as e:
            print(f"未检测到系统 Chrome，回退到 Chromium: {e}")
            browser = await p.chromium.launch(
                headless=False,
                args=['--disable-blink-features=AutomationControlled']
            )
        
        context = await browser.new_context(
            viewport={"width": 1280, "height": 800},
            locale="zh-CN",
            timezone_id="Asia/Shanghai"
        )
        page = await context.new_page()
        
        # 访问微博并等待加载
        print("正在打开微博...")
        await page.goto("https://weibo.com", wait_until="domcontentloaded")
        await asyncio.sleep(5)  # 给足时间让登录页/二维码渲染
        
        current_url = page.url
        print(f"当前页面: {current_url}")
        
        if "login" in current_url or "newlogin" in current_url:
            print("请在新打开的浏览器窗口中扫码登录")
        else:
            print("如果未看到登录二维码，请手动刷新页面或访问 weibo.com/login")
        
        input("\n完成扫码登录后，按回车键保存状态...")
        
        # 保存
        await context.storage_state(path=str(STATE_FILE))
        print(f"\n登录态已保存: {STATE_FILE}")
        
        # 验证一下
        cookies = await context.cookies()
        key_names = {"SUB", "SUBP", "SCF", "ALF"}
        found = [c['name'] for c in cookies if c['name'] in key_names]
        print(f"检测到关键 Cookie: {found}")
        
        await browser.close()

if __name__ == "__main__":
    asyncio.run(save_login_state())