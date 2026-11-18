#!/usr/bin/env python3
"""Lotto Max 自動更新程式 (DrawNight)

每次執行會：
  1. 官方先：先讀 BCLC 官方 PlayNow 的 JSON 數據（攪珠結果 + 下期頭獎）。
     兩樣都攞到就完成，唔使讀後備網站。
  2. 官方未有或者連唔到，先讀後備：lottomaxnumbers.com、ca.lottonumbers.com
  3. 官方話事：官方同後備數字唔一樣，以官方為準，log 留低警告。
     冇官方數字嗰期，後備網站之間要一致先寫入。
  4. 檢查每一期（7 個不重複號碼 + 特別號碼、號碼範圍、星期二/五、不是未來日期）
  5. 把 data/draws.json 未有的期數全部補上；下期攪珠日期、頭獎金額同 MaxPlus 寫入 data/next.json
  6. 攪珠結果同頭獎都已經係最新時，唔會去讀來源網站

時間：一律用溫哥華（太平洋）時間。攪珠：星期二、五晚 8:30pm

結束代碼（workflow 會按呢個決定下一步）：
  0 ＝ 最新一期已經有（頭獎未出都算，頭獎有自己嘅排程）
  3 ＝ 來源連到，但最新一期結果仲未出 → workflow 5 分鐘後再問
  4 ＝ 所有來源都連唔到 → workflow 開過一部新機再試
  1 ＝ 有問題要通知你（只會喺「檢查」排程或者手動執行出現）

RUN_MODE（由 workflow 設定）：
  quiet  ＝ 一般排程：唔會報錯
  alert  ＝ 檢查排程：仍然缺少最新一期或數據不一致就報錯（寄一封電郵）
  manual ＝ 手動執行：一定去讀來源，有任何問題都報錯

用法：
  python scripts/update_draws.py              # 按 RUN_MODE 執行（預設 manual）
  python scripts/update_draws.py --verify-all # 逐年覆核全部歷史數據（只報告，不修改）
"""
import datetime as dt
import json
import os
import pathlib
import re
import sys
import time

import requests
from bs4 import BeautifulSoup

ROOT = pathlib.Path(__file__).resolve().parent.parent
DATA = ROOT / "data" / "draws.json"
NEXT = ROOT / "data" / "next.json"
YEAR_URL = "https://www.lottomaxnumbers.com/numbers/{year}"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/126.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml",
    "Accept-Language": "en-CA,en;q=0.8",
}
TZ_NAME = "America/Vancouver"
DRAW_TIME = dt.time(20, 30)   # 溫哥華時間晚上 8:30 攪珠（PlayNow 公佈）
RESULT_DELAY_MIN = 5          # 攪珠後幾多分鐘開始當「應該有結果」
WATCH_HOURS = 4               # 攪珠後最多等幾多個鐘（之後交俾檢查排程）
MONTHS = {m: i + 1 for i, m in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"])}
# 接受 "November 13 2026"、"Nov 13, 2026"、"Friday, November 13, 2026" 等寫法
DATE_RE = re.compile(
    r"\b(jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|june?|july?|aug(?:ust)?|"
    r"sep(?:t(?:ember)?)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)\.?\s+(\d{1,2})(?:st|nd|rd|th)?,?\s+(\d{4})\b",
    re.I)
AMOUNT_RE = re.compile(r"([\d,]+(?:\.\d+)?)\s*(million|m\b)?", re.I)


# ---------------------------------------------------------------- 時間

def now_local():
    try:
        from zoneinfo import ZoneInfo
        return dt.datetime.now(ZoneInfo(TZ_NAME)).replace(tzinfo=None)
    except Exception:
        return dt.datetime.utcnow() - dt.timedelta(hours=8)


def today_local():
    return now_local().date()


def is_draw_day(d):
    return d.weekday() in (1, 4)   # 星期二、五


def expected_latest(now=None):
    """最近一期「應該已經有結果」的攪珠日期，及距離攪珠過了幾多個鐘。"""
    now = now or now_local()
    d = now.date()
    for _ in range(8):
        if is_draw_day(d):
            draw_at = dt.datetime.combine(d, DRAW_TIME)
            if now >= draw_at + dt.timedelta(minutes=RESULT_DELAY_MIN):
                return d.isoformat(), (now - draw_at).total_seconds() / 3600
        d -= dt.timedelta(days=1)
    return None, 0.0


def upcoming_draw(now=None):
    """下一期（未攪珠）的日期。攪珠日 8:30pm 之前就係當日。"""
    now = now or now_local()
    d = now.date()
    for _ in range(8):
        if is_draw_day(d) and now < dt.datetime.combine(d, DRAW_TIME):
            return d.isoformat()
        d += dt.timedelta(days=1)
    return None


# ---------------------------------------------------------------- 檢查

def pool_for(date):
    """號碼範圍：2026-04-14 起 1-52；2019-05-14 起 1-50；之前 1-49。"""
    if date >= "2026-04-14":
        return 52
    if date >= "2019-05-14":
        return 50
    return 49


def valid(date, nums, bonus):
    try:
        d = dt.date.fromisoformat(date)
    except ValueError:
        return False
    if d > today_local() or date < "2009-09-25":
        return False
    days = (1, 4) if date >= "2019-05-14" else (4,)
    if d.weekday() not in days:
        return False
    pool = pool_for(date)
    allv = nums + [bonus]
    return len(nums) == 7 and len(set(allv)) == 8 and all(1 <= v <= pool for v in allv)


def to_date(m):
    return "%04d-%02d-%02d" % (int(m.group(3)), MONTHS[m.group(1).lower()[:3]], int(m.group(2)))


def ints(text):
    return [int(x) for x in re.findall(r"(?<![\d$,.])\b\d{1,2}\b(?![\d,.xX%])", text)]


def parse_amount(text):
    """'$20,000,000'、'20 Million'、'$1.5M' → 整數金額；唔合理就回傳 None。"""
    m = AMOUNT_RE.search(text.replace("$", " "))
    if not m:
        return None
    try:
        v = float(m.group(1).replace(",", ""))
    except ValueError:
        return None
    if m.group(2):
        v *= 1_000_000
    v = int(round(v))
    return v if 1_000_000 <= v <= 200_000_000 else None


# ---------------------------------------------------------------- 來源解析
# 每個解析器回傳 (draws, jackpots)
#   draws    = {日期: (7 個號碼, 特別號碼)}
#   jackpots = [{"date": 日期, "jackpot": 金額, ...}]  只包括未攪珠嘅期數

def parse_table(html):
    """lottomaxnumbers.com / ca.lottonumbers.com 的表格頁。"""
    soup = BeautifulSoup(html, "html.parser")
    draws, jackpots = {}, []
    upcoming = upcoming_draw()
    for row in soup.find_all("tr"):
        text = row.get_text(" ", strip=True)
        m = DATE_RE.search(text)
        if not m:
            continue
        date = to_date(m)
        cands = []
        balls = [el.get_text(strip=True) for el in row.select("li, [class*=ball] > span, span[class*=ball], div[class*=ball]")]
        cands.append([int(b) for b in balls if re.fullmatch(r"\d{1,2}", b)])
        for cell in row.find_all(["td", "th"]):
            txt = cell.get_text(" ", strip=True)
            if re.fullmatch(r"(\d{1,2}\D{0,12}){8}", txt + " "):
                cands.append(ints(txt))
        found = False
        for c in cands:
            if len(c) == 8 and valid(date, sorted(c[:7]), c[7]):
                draws.setdefault(date, (sorted(c[:7]), c[7]))
                found = True
                break
        # 未攪珠嗰行（波仔係「?」）：攞頭獎金額
        if not found and date == upcoming and "$" in text:
            amount = parse_amount(text[text.index("$"):])
            if amount:
                jackpots.append({"date": date, "jackpot": amount})
    return draws, jackpots


PLAYNOW_URL = "https://www.playnow.com/services2/lotto/draw/lmax/{date}"


def parse_playnow(text):
    """BCLC 官方 PlayNow 的 JSON 數據（網頁 JavaScript 用嘅同一份）。
    例子：{"drawDate": "Nov 13, 2026", "drawNbrs": [4, 9, ...], "bonusNbr": 29, ...}"""
    draws = {}
    try:
        data = json.loads(text)
    except ValueError:
        print("  PlayNow：回應唔係 JSON（可能網站改咗或者被擋）")
        return draws, []
    if not isinstance(data, dict):
        return draws, []
    m = DATE_RE.search(str(data.get("drawDate", "")))
    nums, bonus = data.get("drawNbrs"), data.get("bonusNbr")
    if not m or not isinstance(nums, list) or not isinstance(bonus, int):
        print("  PlayNow：今期結果未公佈")
        return draws, []
    try:
        nums = sorted(int(x) for x in nums)
    except (TypeError, ValueError):
        return draws, []
    date = to_date(m)
    if valid(date, nums, bonus):
        draws[date] = (nums, bonus)
    return draws, []


PLAYNOW_JACKPOT_URL = "https://www.playnow.com/services2/lotto/jackpot/lmax"


def parse_playnow_jackpot(text):
    """BCLC 官方 PlayNow 的下期頭獎 JSON。
    例子：{"jackpot": 20000000, "nextDrawDate": "2026-11-17T00:00",
           "additionalDrawsCount": 20, "additionalDrawsAmount": 100000, ...}"""
    try:
        data = json.loads(text)
    except ValueError:
        print("  PlayNow 頭獎：回應唔係 JSON（可能網站改咗或者被擋）")
        return {}, []
    if not isinstance(data, dict):
        return {}, []
    date = str(data.get("nextDrawDate", ""))[:10]
    amount = data.get("jackpot")
    if not isinstance(amount, (int, float)) or not 1_000_000 <= amount <= 200_000_000:
        return {}, []
    if date != upcoming_draw():
        print("  PlayNow 頭獎：網站仍然顯示 %s 嘅頭獎，未轉到下一期" % (date or "未知日期"))
        return {}, []
    info = {"date": date, "jackpot": int(amount)}
    cnt, amt = data.get("additionalDrawsCount"), data.get("additionalDrawsAmount")
    if isinstance(cnt, int) and isinstance(amt, (int, float)) and cnt > 0:
        info["maxplus"] = {"count": cnt, "amount": int(amt)}
    return {}, [info]


# 官方來源：先讀。網址入面嘅 {date} 會換成最近一期攪珠日期
OFFICIAL = [
    ("PlayNow (BCLC 官方)", PLAYNOW_URL, parse_playnow),
    ("PlayNow 頭獎 (BCLC 官方)", PLAYNOW_JACKPOT_URL, parse_playnow_jackpot),
]
# 後備來源：官方未齊數據先讀
BACKUP = [
    ("lottomaxnumbers.com", "https://www.lottomaxnumbers.com/past-numbers", parse_table),
    ("ca.lottonumbers.com", "https://ca.lottonumbers.com/lotto-max/past-numbers", parse_table),
]


# ---------------------------------------------------------------- 讀寫

def fetch(url):
    """回傳 (內容, 有冇連到網站)。網站有回應但唔係 200（例如未公佈）都算連到。"""
    last, connected = None, False
    headers = dict(HEADERS)
    if "/services2/" in url:
        headers["Accept"] = "application/json, text/plain, */*"
    for attempt in range(2):
        try:
            r = requests.get(url, headers=headers, timeout=(10, 25))
            connected = True
            if r.status_code == 200:
                return r.text, True
            last = "HTTP %s" % r.status_code
            if r.status_code == 404:
                break
        except requests.RequestException as e:
            last = str(e)
        time.sleep(3)
    print("  無法讀取 %s (%s)" % (url, last))
    return None, connected


def load():
    if not DATA.exists():
        return []
    return json.loads(DATA.read_text(encoding="utf-8"))


def save(draws):
    draws.sort(key=lambda d: d["date"], reverse=True)
    lines = [json.dumps(d, separators=(", ", ": ")) for d in draws]
    DATA.write_text("[\n" + ",\n".join(lines) + "\n]\n", encoding="utf-8")


def load_next():
    try:
        return json.loads(NEXT.read_text(encoding="utf-8"))
    except Exception:
        return {}


def save_next(info):
    old = {k: v for k, v in load_next().items() if k != "updated"}
    if old != {k: v for k, v in info.items() if k != "updated"}:
        NEXT.write_text(json.dumps(info, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print("已更新下期頭獎：%s $%s（來源：%s）" % (info["date"], format(info["jackpot"], ","), info["source"]))


# ---------------------------------------------------------------- 主流程

OK, PROBLEM, WAITING, UNREACHABLE = 0, 1, 3, 4


def update(mode="quiet"):
    draws = load()
    have = {d["date"]: d for d in draws}
    exp, hours = expected_latest()
    upcoming = upcoming_draw()
    if mode != "manual" and exp in have and load_next().get("date") == upcoming:
        print("數據已是最新（已有 %s 一期，同 %s 嘅頭獎金額），今次唔使讀取來源。" % (exp, upcoming))
        return OK

    def read(sources):
        found_all, jps, ok = {}, [], 0
        for name, url, parser in sources:
            print("讀取", name)
            html, connected = fetch(url.format(date=exp))
            ok += connected
            found, jp = parser(html) if html else ({}, [])
            if jp:
                print("  下期頭獎 %s：$%s" % (jp[0]["date"], format(jp[0]["jackpot"], ",")))
            if parser is not parse_playnow_jackpot:
                print("  讀到 %d 期" % len(found))
            found_all[name] = found
            jps += [dict(j, source=name) for j in jp]
        return found_all, jps, ok

    # 1) 官方先
    official, jackpots, reached = read(OFFICIAL)
    official_draws = {}
    for found in official.values():
        official_draws.update(found)
    draw_ok = exp in have or exp in official_draws
    jackpot_ok = load_next().get("date") == upcoming or any(j["date"] == upcoming for j in jackpots)

    # 2) 官方未齊先讀後備
    backup = {}
    if draw_ok and jackpot_ok:
        print("官方數據已齊，唔使讀後備網站。")
    else:
        print("官方數據未齊（%s），讀後備網站……" % "、".join(
            x for x, ok in (("攪珠結果", draw_ok), ("頭獎", jackpot_ok)) if not ok))
        backup, jp_b, ok_b = read(BACKUP)
        jackpots += jp_b
        reached += ok_b

    if jackpots:
        # 官方 PlayNow 優先，否則用第一個讀到嘅
        best = next((j for j in jackpots if j["source"].startswith("PlayNow")), jackpots[0])
        save_next(dict(best, updated=now_local().strftime("%Y-%m-%d %H:%M")))

    if not reached:
        print("所有來源都連唔到，歷史記錄維持不變。")
        return UNREACHABLE

    # 3) 合併：官方話事；冇官方數字嗰期，後備之間要一致
    numbers_seen = {tuple(d["numbers"]): d["date"] for d in draws}
    conflicts, added = [], []
    all_dates = set(official_draws)
    for found in backup.values():
        all_dates |= set(found)
    for date in sorted(all_dates):
        others = [found[date] for found in backup.values() if date in found]
        if date in official_draws:
            value, src = official_draws[date], "官方"
            for name, found in backup.items():
                if date in found and found[date] != value:
                    print("警告：%s 一期 %s 嘅數字同官方唔一樣，以官方為準。" % (date, name))
        else:
            if any(x != others[0] for x in others):
                conflicts.append(date)
                continue
            value, src = others[0], "後備" if len(others) > 1 else "單一後備來源"
        nums, bonus = value
        if date in have:
            if have[date]["numbers"] != nums or have[date]["bonus"] != bonus:
                conflicts.append("%s（同現有記錄唔一樣）" % date)
            continue
        if tuple(nums) in numbers_seen:   # 同舊一期一模一樣，多數係網站未更新
            conflicts.append("%s（號碼同 %s 一樣）" % (date, numbers_seen[tuple(nums)]))
            continue
        draws.append({"date": date, "numbers": nums, "bonus": bonus})
        added.append("%s（%s）" % (date, src))
    if added:
        save(draws)
        print("已新增 %d 期：%s" % (len(added), "、".join(added)))
    else:
        print("沒有新的攪珠結果。")
    if load_next().get("date") != upcoming:
        print("提示：%s 嘅頭獎金額仲未公佈，頭獎排程會繼續試。" % upcoming)

    loud = mode in ("manual", "alert")
    if conflicts:
        print(("錯誤：" if loud else "提示：") + "以下日期各來源或現有記錄不一致，未有寫入，請人手核對："
              + "、".join(conflicts))
        if loud:
            return PROBLEM
    if exp and exp not in {d["date"] for d in draws}:
        msg = "攪珠後 %.1f 小時，來源網站仍未有 %s 的結果" % (hours, exp)
        if loud:
            print("錯誤：" + msg)
            return PROBLEM
        if hours > WATCH_HOURS:
            print("提示：%s。已超過等候時間，交俾檢查排程處理。" % msg)
            return OK
        print("提示：%s，稍後再試。" % msg)
        return WAITING
    return OK


def verify_all():
    have = {d["date"]: (d["numbers"], d["bonus"]) for d in load()}
    bad = checked = 0
    for year in range(2009, today_local().year + 1):
        html, _ = fetch(YEAR_URL.format(year=year))
        found = parse_table(html)[0] if html else {}
        for date, val in sorted(found.items()):
            checked += 1
            if date not in have:
                bad += 1
                print("  缺少 %s：%s + %s" % (date, val[0], val[1]))
            elif have[date] != val:
                bad += 1
                print("  不一致 %s：檔案 %s，來源 %s" % (date, have[date], val))
        missing = [d for d in have if d.startswith(str(year)) and d not in found]
        if found and missing:
            print("  %d 年有 %d 期在來源找不到：%s" % (year, len(missing), "、".join(sorted(missing)[:5])))
        print("%d 年：來源 %d 期" % (year, len(found)))
        time.sleep(1.5)
    print("覆核完成：對照 %d 期，發現 %d 個問題。" % (checked, bad))
    return 1 if bad or not checked else 0


if __name__ == "__main__":
    if "--verify-all" in sys.argv:
        sys.exit(verify_all())
    mode = os.environ.get("RUN_MODE", "manual").strip().lower()
    if mode not in ("quiet", "alert", "manual"):
        mode = "manual"
    sys.exit(update(mode))
