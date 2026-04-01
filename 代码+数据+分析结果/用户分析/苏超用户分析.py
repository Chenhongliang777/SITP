# -*- coding: utf-8 -*-
"""
Created on Thu Jul 31 11:05:37 2025

@author: GIO
"""


import json
import re
import pandas as pd
import matplotlib.pyplot as plt
from datetime import datetime
import os

from collections import Counter

# 设置文件路径
FILE_PATH = r"C:\Users\GIO\Desktop\苏超_weibo.json"
OUTPUT_DIR = r"C:\Users\GIO\Desktop\苏超分析结果"
os.makedirs(OUTPUT_DIR, exist_ok=True)

# 1. 数据加载与预处理
def load_data(file_path):
    """加载并解析JSON数据"""
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception as e:
        print(f"数据加载失败: {e}")
        return []

# 2. 用户信息提取
def extract_user_info(data):
    users = []
    user_ids = set()  # 用于去重
    
    for weibo in data:
        # 提取微博作者信息
        user_info_str = weibo.get('user_info', '{}')
        try:
            user_info = json.loads(user_info_str).get('data', {})
            if user_info and user_info.get('id') not in user_ids:
                users.append({
                    'id': user_info.get('id') or weibo.get('mid'),
                    'type': 'author',
                    'gender': user_info.get('gender'),
                    'birthday': user_info.get('birthday'),
                    'location': user_info.get('location') or user_info.get('ip_location'),
                    'followers_count': user_info.get('followers', {}).get('total_number', 0),
                    'verified_type': user_info.get('verified_type', -1),
                    'description': user_info.get('description', '')
                })
                user_ids.add(user_info.get('id') or weibo.get('mid'))
        except:
            pass
        
        # 提取评论用户信息
        for comment in weibo.get('comments', []):
            user = comment.get('user', {})
            user_id = user.get('id')
            if user_id and user_id not in user_ids:
                users.append({
                    'id': user_id,
                    'type': 'commenter',
                    'gender': user.get('gender'),
                    'birthday': user.get('birthday'),
                    'location': user.get('location') or comment.get('source'),
                    'followers_count': parse_followers(user.get('followers_count_str', '0')),
                    'verified_type': user.get('verified_type', -1),
                    'description': user.get('description', '')
                })
                user_ids.add(user_id)
    
    return pd.DataFrame(users)

def parse_followers(follower_str):
    """解析粉丝量字符串 (处理'万'单位)"""
    if not isinstance(follower_str, str):
        return 0
    
    follower_str = follower_str.strip()
    
    # 处理带逗号的数字 (如"1,234")
    if re.match(r'^\d{1,3}(,\d{3})+$', follower_str):
        return int(follower_str.replace(',', ''))
    
    # 处理带"万"的数字 (如"1.2万")
    if '万' in follower_str:
        num_part = re.sub(r'[^\d.]', '', follower_str)
        try:
            return int(float(num_part) * 10000)
        except:
            return 0
    
    try:
        return int(follower_str)
    except:
        return 0

# 3. 特征工程
def process_features(df):
    # 性别处理
    gender_mapping = {'m': '男', 'f': '女', '男': '男', '女': '女'}
    df['gender'] = df['gender'].map(gender_mapping).fillna('未知')
    
    # 年龄处理
    current_year = 2025
    def calculate_age(birthday):
        if not birthday or not isinstance(birthday, str) or birthday.strip() == '':
            return None
        
        # 匹配格式: 1990-01-01 或 1990-01-01 水瓶座
        date_match = re.match(r'(\d{4})-\d{2}-\d{2}', birthday)
        if date_match:
            birth_year = int(date_match.group(1))
            return current_year - birth_year
        
        # 处理星座格式: 水瓶座
        return None
    
    df['age'] = df['birthday'].apply(calculate_age)
    
    # 地域处理
    provinces = r'(北京|天津|上海|重庆|辽宁|吉林|黑龙江|河北|河南|湖北|湖南|江苏|浙江|安徽|福建|江西|山东|广东|海南|四川|贵州|云南|陕西|甘肃|青海|台湾|山西|西藏|内蒙古|广西|宁夏|新疆|香港|澳门)'
    def extract_province(location):
        if not location or not isinstance(location, str):
            return '未知'
        match = re.search(provinces, location)
        return match.group(0) if match else '未知'
    
    df['province'] = df['location'].apply(extract_province)
    
    # 粉丝分级
    def followers_level(count):
        if count == 0:
            return '0'
        elif count <= 1000:
            return '0-1000'
        elif count <= 10000:
            return '1001-1万'
        elif count <= 100000:
            return '1万-10万'
        elif count <= 1000000:
            return '10万-100万'
        else:
            return '100万+'
    
    df['fans_level'] = df['followers_count'].apply(followers_level)
    
    # 用户类型标签
    def user_type(row):
        followers_count = row['followers_count']
        verified_type = row['verified_type']
        
        if followers_count > 1000000:
            return '顶流博主'
        elif followers_count > 100000:
            return '头部大V'
        elif followers_count > 10000:
            return '腰部大V'
        elif followers_count > 1000:
            return '初级KOL'
        elif verified_type > 0 or verified_type == 0:  # 认证用户
            return '认证用户'
        return '普通用户'
    
    df['user_type'] = df.apply(user_type, axis=1)
    
    return df


# 主分析函数
def analyze_weibo_data(file_path, output_dir):
    """执行完整的用户画像分析流程"""
    print(f"开始分析微博数据: {file_path}")
    
    # 加载数据
    data = load_data(file_path)
    if not data:
        print("未加载到有效数据，程序终止")
        return
    
    print(f"成功加载 {len(data)} 条微博数据")
    
    # 提取用户信息
    user_df = extract_user_info(data)
    print(f"共提取 {len(user_df)} 个用户信息")
    
    # 特征工程
    processed_df = process_features(user_df)
    
   
    
    # 分析报告
    total_users = len(processed_df)
    print(f"\n{' 用户画像分析报告 ':=^50}")
    print(f"分析时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"用户总数: {total_users} (微博作者 + 评论用户)")
    
    # 地域分析
    top_provinces = processed_df['province'].value_counts().head(3)
    print("\n[地域分布]")
    print(f"前三省份: {', '.join(top_provinces.index)}")
    print(f"江苏用户占比: {top_provinces.get('江苏', 0)/total_users:.1%}")
    
    # 性别分析
    male_count = (processed_df['gender'] == '男').sum()
    female_count = (processed_df['gender'] == '女').sum()
    print("\n[性别分布]")
    print(f"男性用户: {male_count} ({male_count/total_users:.1%})")
    print(f"女性用户: {female_count} ({female_count/total_users:.1%})")
    
    # 年龄分析
    age_df = processed_df.dropna(subset=['age'])
    if not age_df.empty:
        avg_age = age_df['age'].mean()
        age_dist = age_df['age'].value_counts().sort_index()
        print("\n[年龄分布]")
        print(f"平均年龄: {avg_age:.1f}岁")
        print(f"主要年龄段: {age_dist.idxmax()}岁 ({age_dist.max()}人)")
    
    # 粉丝分析
    fans_stats = processed_df['followers_count'].describe()
    print("\n[粉丝量统计]")
    print(f"最大粉丝量: {fans_stats['max']:,}")
    print(f"平均粉丝量: {fans_stats['mean']:,.0f}")
    print(f"粉丝>10万: {(processed_df['followers_count'] > 100000).sum()}人")
    
    # 用户类型分析
    user_type_dist = processed_df['user_type'].value_counts()
    print("\n[用户类型]")
    for utype, count in user_type_dist.items():
        print(f"{utype}: {count}人 ({count/total_users:.1%})")
    
    # 保存结果
    csv_path = os.path.join(output_dir, '用户画像详情.csv')
    processed_df.to_csv(csv_path, index=False, encoding='utf-8-sig')
    print(f"\n用户画像详情已保存到: {csv_path}")
    
   
# 运行分析
if __name__ == "__main__":
    analyze_weibo_data(FILE_PATH, OUTPUT_DIR)
    print("\n分析完成！")
