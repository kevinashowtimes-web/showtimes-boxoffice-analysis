"""
每日自動更新主程式，由 GitHub Actions 排程呼叫：
  python scripts/main.py

流程：
1. 算出台北時間「今天」的日期
2. 登入兩個系統，抓：今天單日、本月累計(MTD)、今年累計(YTD)
3. 檢查資料完整性（秀泰15/15、威秀22/22），不完整就直接失敗中止，不用估算值頂替
4. 讀舊的 index.html 抓出舊 DATA（拿 w7BO/lmtdBO 需要的 history、以及「前一天」比較基準）
5. 算出新的 mkt/st/wison/competitors/cinemas
6. 呼叫 Claude API 生成 lastUpdate 說明文字
7. 把新 DATA 寫回 index.html / 儀表板.html / 秀泰影城每日營運儀表板.html /
   秀泰影城每日營運儀表板_github.html（含登入版，只換 DATA 區塊，其餘不動）
8. 驗證 JS 語法
9. Git commit + push（用 Action 內建 GITHUB_TOKEN，不需要另外的 PAT）

⚠ 尚未跑過的部分：taipeitheater.org.tw / 內部系統的登入表單真實欄位名稱還沒有實際驗證過
（見 scrape.py 檔頭說明），第一次執行很可能會在登入這步失敗，需要照錯誤訊息調整。
去年同期(LY)的 14 館 vs 15 館口徑差異、以及 8/14 那種「跳過一天」的情境，這支自動化版本
假設「每天都會準時執行」，所以不會有跳日問題；如果哪天 Action 執行失敗，隔天要留意
mtdBO/ytdBO 是不是需要用「直接查詢累計範圍」而非「舊值+單日」來補救（做法可參考
2026-08-15 那次人工更新的 build_final_0815.py）。
"""
import json
import os
import sys
import zoneinfo
from datetime import date, datetime, timedelta

sys.path.insert(0, os.path.dirname(__file__))

import requests

from scrape import (
    NAME_MAP,
    taipeitheater_login,
    fetch_taipeitheater_range,
    parse_taipeitheater_table,
    internal_login,
    fetch_internal_range,
    parse_internal_table,
)
from compute import build_cinemas, build_competitors_and_market, build_wison_mtd, check_completeness
from narrative import generate_last_update
from inject import inject, validate_js

REPO_FILES = [
    "index.html",
    "儀表板.html",
    "秀泰影城每日營運儀表板.html",
    "秀泰影城每日營運儀表板_github.html",
]
WEEKDAY_CN = ["一", "二", "三", "四", "五", "六", "日"]


def main():
    tz = zoneinfo.ZoneInfo("Asia/Taipei")
    today = datetime.now(tz).date()
    today_s = today.isoformat()
    month_start = today.replace(day=1).isoformat()
    year_start = today.replace(month=1, day=1).isoformat()
    last_week = (today - timedelta(days=7)).isoformat()
    ly_today = today.replace(year=today.year - 1)
    ly_month_start = ly_today.replace(day=1).isoformat()
    ly_year_start = ly_today.replace(month=1, day=1).isoformat()
    ly_today_s = ly_today.isoformat()

    with open("index.html", encoding="utf-8") as f:
        old_html = f.read()
    idx = old_html.find("const DATA")
    from inject import find_data_block
    start, end = find_data_block(old_html)
    old_data = json.loads(old_html[start:end])

    if today_s in old_data.get("history", {}):
        print("今天({})已經更新過了，不重複執行".format(today_s))
        return

    ext_session = requests.Session()
    taipeitheater_login(ext_session)
    int_session = requests.Session()
    internal_login(int_session)

    # ---- 抓資料 ----
    today_ext_html = fetch_taipeitheater_range(ext_session, today_s, today_s)
    ext_today = parse_taipeitheater_table(today_ext_html)

    today_int_html = fetch_internal_range(int_session, today_s, today_s)
    today_internal = parse_internal_table(today_int_html)

    mtd_int_html = fetch_internal_range(int_session, month_start, today_s)
    mtd_internal = parse_internal_table(mtd_int_html)

    ytd_int_html = fetch_internal_range(int_session, year_start, today_s)
    ytd_internal = parse_internal_table(ytd_int_html)

    mtd_ext_html = fetch_taipeitheater_range(ext_session, month_start, today_s)
    ext_mtd = parse_taipeitheater_table(mtd_ext_html)

    ly_mtd_ext_html = fetch_taipeitheater_range(ext_session, ly_month_start, ly_today_s)
    ext_ly_mtd = parse_taipeitheater_table(ly_mtd_ext_html)

    ly_ytd_ext_html = fetch_taipeitheater_range(ext_session, ly_year_start, ly_today_s)
    ext_ly_ytd = parse_taipeitheater_table(ly_ytd_ext_html)

    # ---- 資料完整性檢查（不完整就中止，不用估算值）----
    problems = check_completeness(today_internal, ext_today)
    if problems:
        raise RuntimeError("資料不完整，中止更新，不使用估算值：\n" + "\n".join(problems))

    # ---- 上週同期 / 上月同期基準（history 查表；沒有就留空並在敘述裡註明）----
    history = old_data.get("history", {})
    w7_entry = history.get(last_week, {})
    w7_cinemas = {c["name"]: c for c in w7_entry.get("cinemas", [])}
    lmtd_month_ref = (today.replace(month=today.month - 1) if today.month > 1
                      else today.replace(year=today.year - 1, month=12)).isoformat()
    # 簡化：直接找「上月同一天」的 history entry 當作 lmtd 對照（若不存在則跳過，不補估算）
    lmtd_entry = history.get(lmtd_month_ref, {})
    lmtd_cinemas = {c["name"]: c for c in lmtd_entry.get("cinemas", [])}

    old_cinemas_by_name = {c["name"]: c for c in old_data["cinemas"]}

    new_cinemas = build_cinemas(today_internal, mtd_internal, ytd_internal, old_cinemas_by_name, w7_cinemas, lmtd_cinemas)

    mkt_comp = build_competitors_and_market(ext_today)
    wison_mtd = build_wison_mtd(ext_mtd)

    st_bo_today = sum(today_internal[k]["bo"] for k in NAME_MAP) / 10000
    st_fb_today = sum(today_internal[k]["fb"] for k in NAME_MAP) / 10000
    st_vis_today = sum(today_internal[k]["visitors"] for k in NAME_MAP)
    st_mtdBO = sum(mtd_internal[k]["bo"] for k in NAME_MAP) / 10000
    st_ytdBO = sum(ytd_internal[k]["bo"] for k in NAME_MAP) / 10000

    def st_sum_ly(rows):
        # 2025年「秀泰巨蛋」尚未開幕，14館口徑
        return sum(v["bo"] for k, v in rows.items() if "秀泰" in k and k != "台北大巨蛋秀泰") / 10000

    st_mtdLastYearBO = round(st_sum_ly(ext_ly_mtd), 1)
    st_ytdLastYearBO = round(st_sum_ly(ext_ly_ytd), 1)

    new_data = dict(old_data)
    new_data["date"] = today_s
    new_data["weekday"] = WEEKDAY_CN[today.weekday()]
    new_data["mkt"] = dict(mkt_comp["mkt"], lastWeekBO=w7_entry.get("mktBO"))
    new_data["st"] = {
        "bo": round(st_bo_today, 1),
        "fb": round(st_fb_today, 1),
        "visitors": st_vis_today,
        "ystBO": old_data["st"]["bo"],
        "mtdBO": round(st_mtdBO, 1),
        "mtdLastMonthBO": lmtd_entry.get("mtdBO", old_data["st"].get("mtdLastMonthBO")),
        "mtdLastYearBO": st_mtdLastYearBO,
        "ytdBO": round(st_ytdBO, 1),
        "ytdLastYearBO": st_ytdLastYearBO,
    }
    new_data["wison"] = dict(mkt_comp["wison_today"], **wison_mtd)
    new_data["competitors"] = mkt_comp["competitors"]
    new_data["cinemas"] = new_cinemas

    mkt_share = round(new_data["st"]["bo"] / new_data["mkt"]["bo"] * 100, 1) if new_data["mkt"]["bo"] else 0

    narrative_payload = {
        "date": today_s, "weekday": new_data["weekday"],
        "mkt": new_data["mkt"], "st": new_data["st"], "wison": new_data["wison"],
        "mktShare": mkt_share,
        "last_week_reference_missing": not bool(w7_entry),
        "last_month_reference_missing": not bool(lmtd_entry),
    }
    last_update = generate_last_update(narrative_payload)
    new_data["lastUpdate"] = last_update

    new_data.setdefault("history", {})[today_s] = {
        "date": today_s,
        "mktBO": new_data["mkt"]["bo"], "mktVisitors": new_data["mkt"]["visitors"], "mktATP": new_data["mkt"]["atp"],
        "stBO": new_data["st"]["bo"], "stFB": new_data["st"]["fb"], "stVisitors": new_data["st"]["visitors"],
        "ystBO": new_data["st"]["ystBO"], "mtdBO": new_data["st"]["mtdBO"], "ytdBO": new_data["st"]["ytdBO"],
        "wisonBO": new_data["wison"]["bo"], "mktShare": mkt_share,
        "wisonVisitors": new_data["wison"]["visitors"], "wisonATP": new_data["wison"]["atp"],
        "competitors": new_data["competitors"], "mktNote": last_update,
        "cinemas": [{"name": c["name"], "bo": c["bo"], "fb": c["fb"], "visitors": c["visitors"],
                     "shows": c["shows"], "avgPrice": c["avgPrice"]} for c in new_cinemas],
    }

    for filename in REPO_FILES:
        if not os.path.exists(filename):
            print("跳過不存在的檔案：{}".format(filename))
            continue
        with open(filename, encoding="utf-8") as f:
            html = f.read()
        new_html = inject(html, new_data)
        with open(filename, "w", encoding="utf-8") as f:
            f.write(new_html)
        validate_js(filename)
        print("已更新並驗證：{}".format(filename))

    print("完成，日期：{}，秀泰{}萬，全台{}萬，市占{}%".format(
        today_s, new_data["st"]["bo"], new_data["mkt"]["bo"], mkt_share))


if __name__ == "__main__":
    main()
