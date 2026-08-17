"""
呼叫 Claude API，把當天算好的數字交給模型，產生跟人工撰寫風格一致的
「lastUpdate」異常波動說明文字（例如：主因新片上映帶動熱潮 / 屬正常週間波動 等）。

需要環境變數 ANTHROPIC_API_KEY（GitHub Secret）。
"""
import json
import os

from anthropic import Anthropic

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


def generate_last_update(payload: dict) -> str:
    client = Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
    msg = client.messages.create(
        model="claude-sonnet-5",
        max_tokens=1024,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": json.dumps(payload, ensure_ascii=False, indent=2)}],
    )
    return "".join(block.text for block in msg.content if block.type == "text").strip()
