import os
import threading
import json
import re
import time
import random
import traceback
import urllib.parse
from datetime import datetime
from bs4 import BeautifulSoup
import requests
# 禁用不安全请求限制的警告（因为我们要设置 verify=False 来跳过证书检测）
import urllib3

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

#################### 代理配置区 (已修复格式) ####################
tunnel = "c820.kdltps.com:15818"
username = "t13846961737653"
password = "vs64jp24"

# 对账号密码进行 URL 编码，防止特殊字符导致 FileNotFoundError
encoded_user = urllib.parse.quote(username)
encoded_pwd = urllib.parse.quote(password)

# 统一使用 http 协议作为隧道入口（这是隧道代理的标准用法）
proxy_url = f"http://{encoded_user}:{encoded_pwd}@{tunnel}"
proxies = {
    "http": proxy_url,
    "https": proxy_url
}

# 微博Cookie
ck = """XSRF-TOKEN=KbvK0Gntpc_AdFI5TWOLl5eR; SCF=AktbArwGHtC8wcwdMFpvuTSuNScT6EIJUh-Tc_A4wFF6xMQ75NXw9JG4bH8NxwzBBhOQx8hv7zrE2guugy5gHNw.; _s_tentry=weibo.com; Apache=6960325501635.0625.1773022418354; SINAGLOBAL=6960325501635.0625.1773022418354; ULV=1773022418356:1:1:1:6960325501635.0625.1773022418354:; PC_TOKEN=8869d55f03; SUB=_2A25EqkLbDeRhGeBH41UX8ivKyj6IHXVnxtoTrDV8PUNbmtAYLUH4kW9NQbD6lFsAnzRr6PrGUuP5f23y4kMs48oJ; SUBP=0033WrSXqPxfM725Ws9jqgMF55529P9D9WWinS.zh3Jyhv0FJwZCJJeE5NHD95Qc1KnNSozfSo2EWs4Dqcj_i--ciKLWiKnEi--fiKnpiKLhi--ciK.fi-z7i--ciK.RiKLsi--ciK.RiKLs; ALF=02_1775615883; WBPSESS=Dt2hbAUaXfkVprjyrAZT_L-WP8D6_zTeujhLqCtcEMllruR5jXCQ-_uF6-ShfvhR8Z8svNWfXW-nVmvRJDxNfJHr6kqm07G8Ow8v6w7pP8C3YYAEZcZyqQVx6VxCMI-mwRtdEjnfk7kXEvlBu5Fp7XCaxFpjTLsw0Jtbh12UmY8x1fXP_mzbCpoKO2qqw_uUKne7htNWVmRLnLYjsZ4yzQ=="""
ck = ck.replace("\n", "").strip()

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
    "Mozilla/5.0 (iPhone; CPU iPhone OS 16_5 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.5 Mobile/15E148 Safari/604.1"
]

req_map = {}


def get_req() -> requests.Session:
    name = threading.current_thread().name
    if name not in req_map:
        session = requests.Session()
        # 将代理直接绑定到 Session
        session.proxies = proxies
        req_map[name] = session
    return req_map[name]


def update_req():
    name = threading.current_thread().name
    print(f"🔄 正在更新会话并重试...")
    session = requests.Session()
    session.proxies = proxies
    req_map[name] = session


def proxy_get(url, params=None, referer=None, retry=3):
    for i in range(retry):
        try:
            # 随机延迟：非常重要，模拟真人操作
            time.sleep(random.uniform(2.5, 5.0))

            session = get_req()
            headers = {
                'cookie': ck,
                'accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8',
                'user-agent': random.choice(USER_AGENTS),
                'referer': referer if referer else "https://weibo.com/",
                'sec-fetch-mode': 'navigate',
                'x-requested-with': 'XMLHttpRequest'
            }

            # verify=False 解决 'FileNotFoundError' 相关的 SSL 证书查找问题
            resp = session.get(url, params=params, headers=headers, timeout=15, verify=False, allow_redirects=False)

            if resp.status_code == 200:
                # 检查是否返回了“未登录”或“验证码”关键字（微博有时在200状态下返回错误提示）
                if "passport.weibo.com" in resp.text or "login.php" in resp.text:
                    print("⚠️ Cookie失效或触发验证码，建议更换Cookie")
                    return None
                return resp

            if resp.status_code in [301, 302]:
                print(f"🚧 遇到重定向 {resp.status_code}，可能是触发了人机验证")
                update_req()
                continue

            print(f"请求失败，状态码：{resp.status_code}，正在尝试第 {i + 1} 次重试...")
        except (requests.exceptions.ProxyError, requests.exceptions.ConnectionError) as e:
            print(f"🔥 代理连接失败: {e}。正在检查代理设置...")
            update_req()  # 强制更新 Session
        except Exception as e:
            print(f"❌ 未知异常: {str(e)}")

    return None


#################### 业务逻辑区 ####################

def to_int(s_num: str):
    s_num = s_num.strip()
    if not s_num or any(x in s_num for x in ["转发", "评论", "赞"]): return 0
    if '万' in s_num:
        return int(float(s_num.replace('万', '')) * 10000)
    return int(s_num) if s_num.isdigit() else 0


def parse_search_result(html):
    if not html: return []
    soup = BeautifulSoup(html, 'html.parser')
    cards = soup.find_all(class_="card-wrap")
    results = []
    for item in cards:
        mid = item.get("mid")
        if not mid: continue
        try:
            # 基础信息解析
            detail_link = item.select_one('[class="from"]').find("a")["href"]
            results.append({
                "mid": mid,
                "博文内容": item.select_one('[node-type="feed_list_content"]').text.strip() if item.select_one(
                    '[node-type="feed_list_content"]') else "",
                "时间": item.select_one('[class="from"]').find("a").text.strip(),
                "详情链接": "https:" + detail_link,
                "用户昵称": item.find(class_="name").text if item.find(class_="name") else "未知"
            })
        except:
            continue
    return results


def start_crawl(keyword, start_date, end_date, pages=1):
    print(f"🚀 开始爬取关键词: {keyword}")
    all_results = []

    for page in range(1, pages + 1):
        search_url = "https://s.weibo.com/weibo"
        params = {
            'q': keyword,
            'page': page,
            'timescope': f'custom:{start_date}:{end_date}',
            'Refer': 'g'
        }

        print(f"📄 正在请求第 {page} 页...")
        resp = proxy_get(search_url, params=params)

        if resp:
            data = parse_search_result(resp.text)
            print(f"✅ 成功提取 {len(data)} 条微博")
            all_results.extend(data)

            # 为了绕开反爬，每页之后多休息一会
            time.sleep(random.uniform(5, 10))
        else:
            print(f"❌ 第 {page} 页请求失败，跳过")

    # 保存结果
    if all_results:
        output_file = f"weibo_data_{int(time.time())}.json"
        with open(output_file, 'w', encoding='utf-8') as f:
            json.dump(all_results, f, ensure_ascii=False, indent=2)
        print(f"💾 任务完成，数据已存入: {output_file}")
    else:
        print("📭 未采集到任何数据，请检查Cookie或代理是否有效")


if __name__ == "__main__":
    # 执行爬取
    start_crawl(
        keyword="苏超",
        start_date="2024-10-01",
        end_date="2024-10-10",
        pages=2
    )