"""
產生跟人工撰寫風格一致的「lastUpdate」異常波動說明文字。

兩種模式，自動判斷：
- 有設定 ANTHROPIC_API_KEY（GitHub Secret）就呼叫 Claude API，寫出具體的「可能原因」推測
  （例如：主因新片上映帶動熱潮）
- 沒有設定就自動退回「規則模板」：只依數字公式化組句（例如：較上週上升127.5%，達異常波動線，
  待人工複核），完全免費、不需要任何 API key

之後想從規則模板升級成 AI 版本，只要在 GitHub Secrets 補上 ANTHROPIC_API_KEY 即可，
不需要改任何程式碼。
"""
import json
import os

SYSTEM_PROMPT_TEMPLATE = None  # 見下方函式內定義，避免沒安裝 anthropic 套件時就報錯

SYSTEM_PROMPT = """你是秀泰集團影城的資深營運數據分析師，任務是根據給定的當日/累計數字，
寫出一段繁體中文的「lastUpdate」報表說明文字，格式與語氣要跟下面的範例一致：

範例：
2026-08-13 更新（週四）秀泰497.5萬/15,578人；全台100家4744場次2610.6萬；威秀1410.3萬；
秀泰市占19.1%；較前日(8/12)全台下滑8.5%、秀泰下滑8.2%（正常範圍）；
🔴較上週四(8/6)全台下滑15.0%，達±15%異常波動警示線！秀泰同期下滑11.7%（未達警示線，跌幅小於大盤）；
可能原因：暑假強片熱潮持續消退且本週無同量級新片挹注，全台整體場次減少，屬市場自然回落非秀泰個別因素，
惟波動幅度已達警示標準建議留意

規則：
- 任何 dd(較前日)、ww(較上週同期)、mtd(本月vs上月同期)、ytd(今年vs去年同期) 只要絕對值 >= 15%，
  就在該句前面加上 🔴，並給出一句「可能原因」的合理解讀（可以參考熱門影片、假期效應、新片上映、
  淡旺季等一般常識，但不要編造沒有根據的具體事件名稱，除非輸入資料裡有提供片名）
- 未達 15% 就正常敘述，不用加符號，可以用 🟢 或不加符號皆可
- 所有百分比四捨五入到小數點第一位
- 有任何資料缺口或用回推值代替直接查詢的情況，必須用 ⚠ 開頭清楚註明，不能含糊帶過
- 整段輸出一句連貫的敘述文字（用「；」分隔子句），不要用條列、不要用 markdown 標題
- 只輸出這段文字本身，不要加任何其他說明或引號
"""


SYSTEM_PROMPT = """你是秀泰集團影城的資深營運數據分析師，任務是根據給定的當日/累計數字，
寫出一段繁體中文的「lastUpdate」報表說明文字，格式與語氣要跟下面的範例一致：

範例：
2026-08-13 更新（週四）秀泰497.5萬/15,578人；全台100家4744場次2610.6萬；威秀1410.3萬；
秀泰市占19.1%；較前日(8/12)全台下滑8.5%、秀泰下滑8.2%（正常範圍）；
🔴較上週四(8/6)全台下滑15.0%，達±15%異常波動警示線！秀泰同期下滑11.7%（未達警示線，跌幅小於大盤）；
可能原因：暑假強片熱潮持續消退且本週無同量級新片挹注，全台整體場次減少，屬市場自然回落非秀泰個別因素，
惟波動幅度已達警示標準建議留意

規則：
- 任何 dd(較前日)、ww(較上週同期)、mtd(本月vs上月同期)、ytd(今年vs去年同期) 只要絕對值 >= 15%，
  就在該句前面加上 🔴，並給出一句「可能原因」的合理解讀（可以參考熱門影片、假期效應、新片上映、
  淡旺季等一般常識，但不要編造沒有根據的具體事件名稱，除非輸入資料裡有提供片名）
- 未達 15% 就正常敘述，不用加符號，可以用 🟢 或不加符號皆可
- 所有百分比四捨五入到小數點第一位
- 有任何資料缺口或用回推值代替直接查詢的情況，必須用 ⚠ 開頭清楚註明，不能含糊帶過
- 整段輸出一句連貫的敘述文字（用「；」分隔子句），不要用條列、不要用 markdown 標題
- 只輸出這段文字本身，不要加任何其他說明或引號
"""


def _rule_based_last_update(payload: dict) -> str:
    """免費、不需要 API key 的規則模板版本。只依數字公式化組句，不推測具體原因。"""
    d = payload["date"]
    weekday = payload["weekday"]
    mkt = payload["mkt"]
    st = payload["st"]
    wison = payload["wison"]
    share = payload["mktShare"]

    parts = [
        "{} 更新（週{}）秀泰{}萬/{:,}人；全台{}家{}萬；威秀{}萬；秀泰市占{}%".format(
            d, weekday, st["bo"], st["visitors"], mkt.get("cinemaCount", "?"), mkt["bo"], wison["bo"], share
        )
    ]

    def pct(cur, ref):
        if not ref:
            return None
        return round((cur - ref) / ref * 100, 1)

    dd = pct(st["bo"], st.get("ystBO"))
    if dd is not None:
        flag = "🔴" if abs(dd) >= 15 else ""
        note = "達異常波動線，待人工複核" if abs(dd) >= 15 else "正常範圍"
        parts.append("{}較前日{}{:+.1f}%（{}）".format(flag, "秀泰", dd, note))

    mtd_ref = st.get("mtdLastMonthBO")
    mtd_pct = pct(st["mtdBO"], mtd_ref)
    if mtd_pct is not None:
        flag = "🔴" if abs(mtd_pct) >= 15 else ""
        note = "達異常波動線，待人工複核" if abs(mtd_pct) >= 15 else "正常範圍"
        parts.append("{}本月累計較上月同期{:+.1f}%（{}）".format(flag, mtd_pct, note))

    ytd_ref = st.get("ytdLastYearBO")
    ytd_pct = pct(st["ytdBO"], ytd_ref)
    if ytd_pct is not None:
        flag = "🔴" if abs(ytd_pct) >= 15 else ""
        note = "達異常波動線，待人工複核" if abs(ytd_pct) >= 15 else "正常範圍"
        parts.append("{}今年累計較去年同期{:+.1f}%（{}）".format(flag, ytd_pct, note))

    if payload.get("last_week_reference_missing"):
        parts.append("⚠缺少上週同期比較基準")
    if payload.get("last_month_reference_missing"):
        parts.append("⚠缺少上月同期比較基準")

    return "；".join(parts)


def generate_last_update(payload: dict) -> str:
    if not os.environ.get("ANTHROPIC_API_KEY"):
        return _rule_based_last_update(payload)

    from anthropic import Anthropic

    client = Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
    msg = client.messages.create(
        model="claude-sonnet-5",
        max_tokens=1024,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": json.dumps(payload, ensure_ascii=False, indent=2)}],
    )
    return "".join(block.text for block in msg.content if block.type == "text").strip()
