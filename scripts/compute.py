"""
彙整計算模組：把 scrape.py 抓回來的原始表格，換算成 index.html 裡 `const DATA` 需要的結構。

邏輯完全比照 2026-08-15 人工更新時使用的方法（見對話紀錄 / update_08xx.py 系列）：
- 秀泰15館的 bo/fb/visitors/shows/avgPrice 一律採用「內部系統」數字（公司自己的售票系統，
  比 taipeitheater.org.tw 更準）
- 全台大盤、競品(威秀/國賓/美麗華/in89/新光/美麗新台茂/喜樂/其他) 一律採用 taipeitheater.org.tw
- 上週同期(w7BO/w7FB) 與上月同期(lmtdBO/lmtdFB) 直接從既有 history 字典查表，不用重新查詢
- MTD/YTD 一律用「直接查詢當期累計範圍」而非「舊累計+單日相加」，避免中間有缺漏日期造成誤差
- 去年同期(LY) 用 taipeitheater.org.tw 查 2025 年同一天範圍，並注意 2025 年時「秀泰巨蛋」尚未開幕，
  只有 14 館可比，回傳的加總需要另外標註口徑不同
"""
import json
from datetime import date, timedelta

from scrape import NAME_MAP


def _sum_st(rows_by_internal_name, field):
    return sum(rows_by_internal_name[k][field] for k in NAME_MAP if k in rows_by_internal_name)


def build_cinemas(today_internal, mtd_internal, ytd_internal, old_cinemas_by_name, w7_cinemas_by_name, lmtd_cinemas_by_name):
    """回傳新的 cinemas 陣列（15 個 dict），欄位與既有 DATA.cinemas 完全相同。"""
    new_cinemas = []
    for internal_name, dash_name in NAME_MAP.items():
        t = today_internal[internal_name]
        m = mtd_internal[internal_name]
        y = ytd_internal[internal_name]
        old = old_cinemas_by_name[dash_name]
        w7 = w7_cinemas_by_name.get(dash_name, {})
        lm = lmtd_cinemas_by_name.get(dash_name, {})

        new_cinemas.append({
            "name": dash_name,
            "bo": round(t["bo"] / 10000, 2),
            "fb": round(t["fb"] / 10000, 2),
            "visitors": t["visitors"],
            "shows": t["shows"],
            "avgPrice": t["avgPrice"],
            "seats": old["seats"],
            "halls": old["halls"],
            "totalSeats": old["totalSeats"],
            "w7BO": w7.get("bo", old.get("w7BO")),
            "w7FB": w7.get("fb", old.get("w7FB")),
            "ystBO": old["bo"],  # 前一天的 bo，即「舊 DATA」裡今天更新前的 bo（=真正的前一日，因為每天都跑，不會有跳日）
            "ytdBO": round(y["bo"] / 10000, 2),
            "ytdVisitors": y["visitors"],
            "mtdBO": round(m["bo"] / 10000, 2),
            "mtdVisitors": m["visitors"],
            "mtdFB": round(m["fb"] / 10000, 2),
            "lmtdBO": round(old["lmtdBO"] + lm.get("bo", 0), 2),
            "lmtdFB": round(old["lmtdFB"] + lm.get("fb", 0), 2),
        })
    return new_cinemas


def build_competitors_and_market(ext_today_rows):
    """ext_today_rows: dict {戲院名稱: {bo, visitors, ...}} 來自 taipeitheater 單日查詢。"""
    mkt_bo = sum(r["bo"] for r in ext_today_rows.values())
    mkt_vis = sum(r["visitors"] for r in ext_today_rows.values())
    mkt_atp = round(mkt_bo / mkt_vis, 1) if mkt_vis else 0
    n_theaters = len(ext_today_rows)

    def grp(pred):
        return {k: v for k, v in ext_today_rows.items() if pred(k)}

    groups = {
        "秀泰": grp(lambda n: "秀泰" in n),
        "威秀": grp(lambda n: "威秀" in n or n == "MuvieCinemas"),
        "國賓": grp(lambda n: "國賓" in n),
        "美麗華": grp(lambda n: n == "台北美麗華"),
        "in89": grp(lambda n: "in89" in n),
        "新光": grp(lambda n: "新光" in n),
        "美麗新台茂": grp(lambda n: "美麗新台茂" in n or "美麗新宏匯" in n),
        "喜樂": grp(lambda n: "喜樂時代" in n),
    }
    named_total = sum(sum(v["bo"] for v in g.values()) for g in groups.values())
    competitors = {k: round(sum(v["bo"] for v in g.values()) / 10000, 1) for k, g in groups.items()}
    competitors["其他"] = round((mkt_bo - named_total) / 10000, 1)

    wison_rows = groups["威秀"]
    wison_bo = sum(v["bo"] for v in wison_rows.values())
    wison_vis = sum(v["visitors"] for v in wison_rows.values())
    wison_atp = round(wison_bo / wison_vis, 1) if wison_vis else 0

    return {
        "mkt": {"bo": round(mkt_bo / 10000, 1), "visitors": mkt_vis, "atp": mkt_atp, "cinemaCount": n_theaters},
        "competitors": competitors,
        "wison_today": {"bo": round(wison_bo / 10000, 1), "visitors": wison_vis, "atp": wison_atp},
    }


def build_wison_mtd(ext_mtd_rows):
    wison_rows = {k: v for k, v in ext_mtd_rows.items() if "威秀" in k or k == "MuvieCinemas"}
    bo = sum(v["bo"] for v in wison_rows.values())
    vis = sum(v["visitors"] for v in wison_rows.values())
    return {"mtdBO": round(bo / 10000, 1), "mtdVisitors": vis, "mtdAtp": round(bo / vis, 1) if vis else 0}


def check_completeness(today_internal, ext_today_rows):
    """秀泰15/15 + 威秀22/22 才算資料完整，否則要中止不更新（依專案規則：資料不完整不可用估算值）"""
    missing_st = [k for k in NAME_MAP if k not in today_internal]
    wison_count = sum(1 for n in ext_today_rows if "威秀" in n or n == "MuvieCinemas")
    problems = []
    if missing_st:
        problems.append("秀泰內部系統缺少：{}".format(", ".join(missing_st)))
    if wison_count < 22:
        problems.append("威秀僅 {}/22 家，資料可能不完整".format(wison_count))
    return problems
