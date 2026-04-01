import time
from datetime import datetime, timedelta

import json
import os


import pandas


import weibo as wb

def add(date_str,hour=12):

    if len(date_str)==10:
        date_str = date_str +"-00"


    date_obj = datetime.strptime(date_str, "%Y-%m-%d-%H")


    new_date_obj = date_obj + timedelta(hours=hour)


    new_date_str = new_date_obj.strftime("%Y-%m-%d-%H")
    return new_date_str

def search(date,key):

    rows = []
    for i in range(1, 51):
        time.sleep(1)
        r = wb.pc_search_time(key, i, f"{date}", f"{add(date)}")
        if len(r) == 0:
            return rows
        print(i,key,date,len(rows))
        if len(r) == 0:
            continue
        rows = rows + r
    return rows

if __name__=="__main__":


    os.makedirs("xlsx",exist_ok=True)
    os.makedirs("j",exist_ok=True)

    keys = "中国男足,中国 男足".split(",")
    time_range = "2023-01-01,2025-05-09"



    for key in keys:
        rows = []
        day = time_range.split(",")[0]
        key = key.strip()
        for i in range(0,30000):
            r = search(day,key)
            print(f"搜索结果，日期{day}，个数{len(r)}")
            rows = rows + r
            if day > time_range.split(",")[1]:
                break
            day = add(day)
        with open(f"j/{key}.json", mode='w', encoding="utf-8") as f:
            f.write(json.dumps(rows))
    # with open("data.json",mode='w',encoding="utf-8") as f:
    #     f.write(json.dumps(rows))
    # pandas.DataFrame(rows).to_excel("data.xlsx",index=False)