#!/usr/bin/env python3
"""Lotto Max 自動更新程式 (DrawNight)

每次執行會：
  1. 由三個公開資料來源讀取最近的攪珠結果
     （兩個 lottonumbers 網站 + BCLC 官方 PlayNow 的 JSON 數據）
     下期頭獎金額：PlayNow 官方優先，否則用 lottomaxnumbers 嗰行「?」旁邊嘅金額
  2. 檢查每一期（7 個不重複號碼 + 特別號碼、號碼範圍、星期二/五、不是未來日期）
  3. 把 data/draws.json 未有的期數全部補上（不止最新一期）
  4. 把下期攪珠日期同頭獎金額寫入 data/next.json
  5. 各來源結果不一致時，不寫入該期
  6. 數據已經係最新時，唔會去讀來源網站（減少被封鎖機會）
  7. 所有來源都連唔到：自動叫 GitHub 開過一部新機再試（最多 4 次）
  8. 電郵通知：每個攪珠日最多一封，只有「檢查」排程仍然缺少最新一期先寄

RUN_MODE（由 workflow 設定）：
  quiet  ＝ 一般排程：有問題都唔變紅色
  alert  ＝ 檢查排程：仍然缺少最新一期或數據不一致就變紅色（寄一封電郵）
  manual ＝ 手動執行：一定去讀來源，有任何問題都變紅色

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
DRAW_TIME = dt.time(22, 30)   # 東岸時間晚上 10:30 攪珠
RESULT_DELAY_MIN = 30         # 攪珠後大約幾多分鐘先有結果
MONTHS = {m: i + 1 for i, m in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"])}
# 接受 "November 13 2026"、"Nov 13, 2026"、"Friday, November 13, 2026" 等寫法
DATE_RE = re.compile(
    r"\b(jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|june?|july?|aug(?:ust)?|"
    r"sep(?:t(?:ember)?)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)\.?\s+(\d{1,2})(?:st|nd|rd|th)?,?\s+(\d{4})\b",
    re.I)
AMOUNT_RE = re.compile(r"([\d,]+(?:\.\d+)?)\s*(million|m\b)?", re.I)


# ---------------------------------------------------------------- 時間

def now_eastern():
    try:
        from zoneinfo import ZoneInfo
        return dt.datetime.now(ZoneInfo("America/Toronto")).replace(tzinfo=None)
    except Exception:
        return dt.datetime.utcnow() - dt.timedelta(hours=5)


def today_eastern():
    return now_eastern().date()


def is_draw_day(d):
    return d.weekday() in (1, 4)   # 星期二、五


def expected_latest(now=None):
    """最近一期「應該已經有結果」的攪珠日期，及距離攪珠過了幾多個鐘。"""
    now = now or now_eastern()
    d = now.date()
    for _ in range(8):
        if is_draw_day(d):
            draw_at = dt.datetime.combine(d, DRAW_TIME)
            if now >= draw_at + dt.timedelta(minutes=RESULT_DELAY_MIN):
                return d.isoformat(), (now - draw_at).total_seconds() / 3600
        d -= dt.timedelta(days=1)
    return None, 0.0


def upcoming_draw(now=None):
    """下一期（未攪珠）的日期。攪珠日 10:30pm 之前就係當日。"""
    now = now or now_eastern()
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
    if d > today_eastern() or date < "2009-09-25":
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


SOURCES = [
    ("lottomaxnumbers.com", "https://www.lottomaxnumbers.com/past-numbers", parse_table),
    ("ca.lottonumbers.com", "https://ca.lottonumbers.com/lotto-max/past-numbers", parse_table),
    # 網址入面嘅 {date} 會換成最近一期攪珠日期
    ("PlayNow (BCLC 官方)", PLAYNOW_URL, parse_playnow),
    ("PlayNow 頭獎 (BCLC 官方)", PLAYNOW_JACKPOT_URL, parse_playnow_jackpot),
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


def github_output(key, value):
    path = os.environ.get("GITHUB_OUTPUT")
    if path:
        with open(path, "a", encoding="utf-8") as f:
            f.write("%s=%s\n" % (key, value))


# ---------------------------------------------------------------- 主流程

def finish(mode, problem, unreachable=False):
    """有問題時決定：開新機再試、紅色、定係只留提示。"""
    if not problem:
        return 0
    attempt = int(os.environ.get("ATTEMPT", "1") or 1)
    max_attempts = int(os.environ.get("MAX_ATTEMPTS", "1") or 1)
    if unreachable and attempt < max_attempts:
        github_output("retry", "true")
        print("提示：%s。會自動開過一部新機再試（第 %d / %d 次）。" % (problem, attempt + 1, max_attempts))
        return 0
    if mode in ("manual", "alert"):
        print("錯誤：" + problem)
        return 1
    print("提示：%s。下一次排程會再試，今次唔當失敗。" % problem)
    return 0


def update(mode="quiet"):
    draws = load()
    have = {d["date"]: d for d in draws}
    exp, hours = expected_latest()
    upcoming = upcoming_draw()
    nxt = load_next()
    if mode != "manual" and exp in have and nxt.get("date") == upcoming:
        print("數據已是最新（已有 %s 一期，同 %s 嘅頭獎金額），今次唔使讀取來源。" % (exp, upcoming))
        return 0

    results, jackpots, reached = [], [], 0
    for name, url, parser in SOURCES:
        print("讀取", name)
        html, connected = fetch(url.format(date=exp))
        if connected:
            reached += 1
        found, jp = parser(html) if html else ({}, [])
        if jp:
            print("  下期頭獎 %s：$%s" % (jp[0]["date"], format(jp[0]["jackpot"], ",")))
        if parser is not parse_playnow_jackpot:
            print("  讀到 %d 期" % len(found))
        results.append(found)
        jackpots += [dict(j, source=name) for j in jp]

    if jackpots:
        # 官方 PlayNow 優先，否則用第一個讀到嘅
        best = next((j for j in jackpots if j["source"].startswith("PlayNow")), jackpots[0])
        save_next(dict(best, updated=now_eastern().strftime("%Y-%m-%d %H:%M")))

    if not reached:
        return finish(mode, "所有來源都連唔到，歷史記錄維持不變", unreachable=True)

    numbers_seen = {tuple(d["numbers"]): d["date"] for d in draws}
    conflicts, added = [], []
    for date in sorted(set().union(*results)):
        seen = [r[date] for r in results if date in r]
        if any(x != seen[0] for x in seen):
            conflicts.append(date)
            continue
        nums, bonus = seen[0]
        if date in have:
            if have[date]["numbers"] != nums or have[date]["bonus"] != bonus:
                conflicts.append(date)
            continue
        if tuple(nums) in numbers_seen:   # 同舊一期一模一樣，多數係網站未更新
            conflicts.append("%s（號碼同 %s 一樣）" % (date, numbers_seen[tuple(nums)]))
            continue
        draws.append({"date": date, "numbers": nums, "bonus": bonus})
        added.append("%s%s" % (date, "" if len(seen) > 1 else "（單一來源）"))
    if added:
        save(draws)
        print("已新增 %d 期：%s" % (len(added), "、".join(added)))
    else:
        print("沒有新的攪珠結果。")
    if not jackpots:
        print("提示：今次未讀到下期頭獎金額。")
    if conflicts:
        return finish(mode, "以下日期各來源或現有記錄不一致，未有寫入，請人手核對：" + "、".join(conflicts))
    if exp and exp not in {d["date"] for d in draws}:
        return finish(mode, "攪珠後 %.1f 小時，來源網站仍未有 %s 的結果" % (hours, exp))
    return 0


def verify_all():
    have = {d["date"]: (d["numbers"], d["bonus"]) for d in load()}
    bad = checked = 0
    for year in range(2009, today_eastern().year + 1):
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
