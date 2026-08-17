"""
資料抓取模組：taipeitheater.org.tw（全台票房）+ 內部系統 60.251.126.13（秀泰15館 FB/來客數）

⚠ 重要提醒（首次上線前必讀）：
本檔案的 login() 函式所用的表單欄位名稱（USERNAME_FIELD / PASSWORD_FIELD 等）是根據常見
ASP.NET / PHP 登入表單慣例「推測」的預設值，尚未實際對照過兩個系統登入頁面的真實 HTML
（因為要看到真正的登入表單，必須先登出現有 session，避免中斷當天的人工作業，所以這次沒有
在對話中直接驗證）。第一次執行 GitHub Action 時，如果登入失敗，請打開瀏覽器開發者工具
(F12 -> Network)，登入一次，把登入 POST 請求的實際欄位名稱、目標網址回報回來，我再修正。

環境變數（由 GitHub Secrets 注入）：
  TAIPEITHEATER_USER, TAIPEITHEATER_PASS   -- taipeitheater.org.tw 秀泰帳號
  INTERNAL_REPORT_USER, INTERNAL_REPORT_PASS -- 內部系統 60.251.126.13 帳號
"""
import os
import re
import time
import requests
from datetime import date

TAIPEI_BASE = "http://www.taipeitheater.org.tw/taiwan"
INTERNAL_BASE = "http://60.251.126.13:81"

# 2026-08-17 第一次測試：GitHub Actions(Azure雲端IP) 連 taipeitheater.org.tw 20秒逾時，
# 懷疑對方防火牆擋雲端機房IP。這裡先拉長逾時＋加重試，確認是「慢」還是「真的擋」。
CONNECT_TIMEOUT = 45
RETRY_COUNT = 3
RETRY_WAIT_SEC = 8


def _request_with_retry(session, method, url, **kwargs):
    kwargs.setdefault("timeout", CONNECT_TIMEOUT)
    last_exc = None
    for attempt in range(1, RETRY_COUNT + 1):
        try:
            return session.request(method, url, **kwargs)
        except requests.exceptions.RequestException as exc:
            last_exc = exc
            print("第 {} 次嘗試連線 {} 失敗：{}".format(attempt, url, exc))
            if attempt < RETRY_COUNT:
                time.sleep(RETRY_WAIT_SEC)
    raise RuntimeError(
        "連線 {} 重試 {} 次後仍失敗，很可能是對方網站擋掉了這台伺服器的IP（常見於雲端機房IP被防火牆封鎖），"
        "而不是帳密或表單欄位的問題。原始錯誤：{}".format(url, RETRY_COUNT, last_exc)
    ) from last_exc


# ---- taipeitheater.org.tw ----

def taipeitheater_login(session: requests.Session):
    user = os.environ["TAIPEITHEATER_USER"]
    pwd = os.environ["TAIPEITHEATER_PASS"]
    # TODO 待驗證：真實登入表單的 method/action/欄位名稱
    # 目前假設是 POST 到 login.php，欄位名稱為 account / password
    resp = _request_with_retry(
        session, "POST", f"{TAIPEI_BASE}/login.php",
        data={"account": user, "password": pwd},
    )
    resp.raise_for_status()
    # 驗證登入成功：頁面應該出現「登出」字樣
    check = _request_with_retry(session, "GET", f"{TAIPEI_BASE}/tbopercentage2.php")
    if "登出" not in check.text:
        raise RuntimeError("taipeitheater.org.tw 登入失敗，請確認帳密或表單欄位名稱是否需要更新")
    return session


def fetch_taipeitheater_range(session: requests.Session, d1: str, d2: str, region_selected: int = 0):
    """d1/d2 格式 YYYY-MM-DD。回傳原始 HTML 字串，由 parse_taipeitheater_table 解析。"""
    url = f"{TAIPEI_BASE}/tbopercentage2.php"
    params = {"d1": d1, "d2": d2, "region_selected": region_selected, "query": 1}
    resp = _request_with_retry(session, "GET", url, params=params)
    resp.raise_for_status()
    return resp.text


def parse_taipeitheater_table(html: str):
    """解析各戲院票房市佔率表格，回傳 dict: {戲院名稱: {bo, visitors, ...}}。
    表格欄位：排行 戲院名稱 單日平均票房 天數 累積場次 累積票房 市佔率 累積人次 平均票價
    """
    from bs4 import BeautifulSoup
    soup = BeautifulSoup(html, "html.parser")
    rows = {}
    table = soup.find("table")
    if table is None:
        raise RuntimeError("找不到票房表格，頁面可能未正確渲染（可能是查詢按鈕未真正送出）")
    for tr in table.find_all("tr")[1:]:
        cells = [td.get_text(strip=True) for td in tr.find_all("td")]
        if len(cells) < 8:
            continue
        name = cells[1]
        if name in ("總戲院", "合計"):
            continue

        def num(s):
            return float(s.replace(",", "").replace("%", ""))

        rows[name] = {
            "avgBO": num(cells[2]),
            "days": int(num(cells[3])),
            "shows": int(num(cells[4])),
            "bo": int(num(cells[5])),
            "share": num(cells[6]),
            "visitors": int(num(cells[7])),
            "avgPrice": num(cells[8]) if len(cells) > 8 else None,
        }
    if not rows:
        raise RuntimeError("表格解析結果為空，請確認查詢日期區間有效")
    return rows


# ---- 內部系統 60.251.126.13 ----

def internal_login(session: requests.Session):
    user = os.environ["INTERNAL_REPORT_USER"]
    pwd = os.environ["INTERNAL_REPORT_PASS"]
    # TODO 待驗證：ASP.NET 登入表單常見有 __RequestVerificationToken 隱藏欄位，
    # 需要先 GET 登入頁拿到 token 再一併 POST，欄位名稱也需對照真實頁面確認
    login_page = _request_with_retry(session, "GET", f"{INTERNAL_BASE}/Account/Login")
    token_match = re.search(r'name="__RequestVerificationToken"[^>]*value="([^"]+)"', login_page.text)
    data = {"Email": user, "Password": pwd}
    if token_match:
        data["__RequestVerificationToken"] = token_match.group(1)
    resp = _request_with_retry(session, "POST", f"{INTERNAL_BASE}/Account/Login", data=data)
    resp.raise_for_status()
    check = _request_with_retry(session, "GET", f"{INTERNAL_BASE}/Report/Index")
    if "登出" not in check.text and "歡迎" not in check.text:
        raise RuntimeError("內部系統登入失敗，請確認帳密或表單欄位名稱是否需要更新")
    return session


def fetch_internal_range(session: requests.Session, d1: str, d2: str):
    """d1/d2 格式 YYYY-MM-DD。回傳 HistoryReport 頁面 HTML。"""
    resp = _request_with_retry(
        session, "GET", f"{INTERNAL_BASE}/Report/HistoryReport",
        params={"開始日": d1, "結束日": d2},
    )
    resp.raise_for_status()
    return resp.text


def parse_internal_table(html: str):
    """解析歷史影城統計表，回傳 dict: {內部影城名稱: {bo, visitors, fb, shows, ...}}"""
    from bs4 import BeautifulSoup
    soup = BeautifulSoup(html, "html.parser")
    tables = soup.find_all("table")
    if not tables:
        raise RuntimeError("找不到內部系統報表表格")
    table = tables[0]
    rows = {}
    trs = table.find_all("tr")
    header_cells = [td.get_text(strip=True) for td in trs[0].find_all(["td", "th"])]
    for tr in trs[1:]:
        cells = [td.get_text(strip=True) for td in tr.find_all("td")]
        if len(cells) < 14:
            continue
        name = cells[0]
        if name in ("合計",):
            continue

        def num(s):
            return float(s.replace(",", ""))

        rows[name] = {
            "bo": int(num(cells[1])),
            "visitors": int(num(cells[2])),
            "fb": int(num(cells[3])),
            "sellpct": num(cells[4]),
            "spp": num(cells[5]),
            "avgPrice": num(cells[6]),
            "occ": num(cells[7]),
            "hitrate": num(cells[8]),
            "perShowVisit": int(num(cells[9])),
            "fbCount": int(num(cells[10])),
            "fbItems": int(num(cells[11])),
            "fbAvg": int(num(cells[12])),
            "shows": int(num(cells[13])),
            "seats": int(num(cells[14])) if len(cells) > 14 else None,
        }
    return rows


NAME_MAP = {
    "台北大巨蛋": "秀泰巨蛋", "台北欣欣": "秀泰欣欣", "新北土城": "秀泰土城", "台北樹林": "秀泰樹林",
    "基隆": "秀泰基隆", "台中文心": "秀泰文心", "台中站前": "秀泰站前", "台中麗寶": "秀泰麗寶",
    "北港": "秀泰北港", "嘉義": "秀泰嘉義", "台南仁德": "秀泰仁德", "高雄夢時代": "秀泰夢時代",
    "高雄岡山": "秀泰岡山", "台東": "秀泰台東", "花蓮": "秀泰花蓮",
}
