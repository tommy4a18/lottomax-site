#!/usr/bin/env python3
"""Lotto Max 自動更新程式 (DrawNight)

每次執行會：
  1. 由兩個公開資料來源讀取最近的攪珠結果
  2. 檢查每一期（7 個不重複號碼 + 特別號碼、號碼範圍、星期二/五、不是未來日期）
  3. 把 data/draws.json 未有的期數全部補上（不止最新一期）
  4. 兩個來源結果不一致時，不寫入該期，並以錯誤結束（GitHub 會電郵通知你）

用法：
  python scripts/update_draws.py              # 正常更新
  python scripts/update_draws.py --verify-all # 逐年覆核全部歷史數據（只報告，不修改）
"""
import datetime as dt
import json
import pathlib
import re
import sys
import time

import requests
from bs4 import BeautifulSoup

DATA = pathlib.Path(__file__).resolve().parent.parent / "data" / "draws.json"
SOURCES = [
    "https://www.lottomaxnumbers.com/past-numbers",
    "https://ca.lottonumbers.com/lotto-max/past-numbers",
]
YEAR_URL = "https://www.lottomaxnumbers.com/numbers/{year}"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/126.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml",
    "Accept-Language": "en-CA,en;q=0.8",
}
MONTHS = {m: i + 1 for i, m in enumerate(
    ["january", "february", "march", "april", "may", "june", "july",
     "august", "september", "october", "november", "december"])}
DATE_RE = re.compile(r"(" + "|".join(MONTHS) + r")\s+(\d{1,2})(?:st|nd|rd|th)?,?\s+(\d{4})", re.I)


def pool_for(date):
    """號碼範圍：2026-04-14 起 1-52；2019-05-14 起 1-50；之前 1-49。"""
    if date >= "2026-04-14":
        return 52
    if date >= "2019-05-14":
        return 50
    return 49


def today_eastern():
    try:
        from zoneinfo import ZoneInfo
        return dt.datetime.now(ZoneInfo("America/Toronto")).date()
    except Exception:
        return (dt.datetime.utcnow() - dt.timedelta(hours=5)).date()


def valid(date, nums, bonus):
    try:
        d = dt.date.fromisoformat(date)
    except ValueError:
        return False
    if d > today_eastern() or date < "2009-09-25":
        return False
    days = (1, 4) if date >= "2019-05-14" else (4,)   # 1 = 星期二, 4 = 星期五
    if d.weekday() not in days:
        return False
    pool = pool_for(date)
    allv = nums + [bonus]
    return len(nums) == 7 and len(set(allv)) == 8 and all(1 <= v <= pool for v in allv)


def ints(text):
    return [int(x) for x in re.findall(r"(?<![\d$,.])\b\d{1,2}\b(?![\d,.xX%])", text)]


def parse(html):
    """由網頁表格讀出 {日期: (7 個號碼, 特別號碼)}。不依賴個別網站的固定版面。"""
    soup = BeautifulSoup(html, "html.parser")
    out = {}
    for row in soup.find_all("tr"):
        m = DATE_RE.search(row.get_text(" ", strip=True))
        if not m:
            continue
        date = "%04d-%02d-%02d" % (int(m.group(3)), MONTHS[m.group(1).lower()], int(m.group(2)))
        cands = []
        balls = [el.get_text(strip=True) for el in row.select("li, [class*=ball] > span, span[class*=ball], div[class*=ball]")]
        balls = [int(b) for b in balls if re.fullmatch(r"\d{1,2}", b)]
        cands.append(balls)
        for cell in row.find_all(["td", "th"]):
            txt = cell.get_text(" ", strip=True)
            if re.fullmatch(r"(\d{1,2}\D{0,12}){8}", txt + " "):
                cands.append(ints(txt))
        for c in cands:
            if len(c) == 8 and valid(date, sorted(c[:7]), c[7]):
                out.setdefault(date, (sorted(c[:7]), c[7]))
                break
    return out


def fetch(url):
    last = None
    for attempt in range(3):
        try:
            r = requests.get(url, headers=HEADERS, timeout=25)
            if r.status_code == 200:
                return r.text
            last = "HTTP %s" % r.status_code
        except requests.RequestException as e:
            last = str(e)
        time.sleep(4)
    print("  無法讀取 %s (%s)" % (url, last))
    return None


def load():
    if not DATA.exists():
        return []
    return json.loads(DATA.read_text(encoding="utf-8"))


def save(draws):
    draws.sort(key=lambda d: d["date"], reverse=True)
    lines = [json.dumps(d, separators=(", ", ": ")) for d in draws]
    DATA.write_text("[\n" + ",\n".join(lines) + "\n]\n", encoding="utf-8")


def update():
    draws = load()
    have = {d["date"]: d for d in draws}
    results = []
    for url in SOURCES:
        print("讀取", url)
        html = fetch(url)
        found = parse(html) if html else {}
        print("  讀到 %d 期" % len(found))
        results.append(found)
    if not any(results):
        print("錯誤：所有來源都讀不到數據，歷史記錄維持不變。")
        return 1
    conflicts, added = [], []
    for date in sorted(set().union(*results)):
        seen = [r[date] for r in results if date in r]
        if any(s != seen[0] for s in seen):
            conflicts.append(date)
            continue
        nums, bonus = seen[0]
        if date in have:
            if have[date]["numbers"] != nums or have[date]["bonus"] != bonus:
                conflicts.append(date)
            continue
        draws.append({"date": date, "numbers": nums, "bonus": bonus})
        added.append("%s%s" % (date, "" if len(seen) > 1 else "（單一來源）"))
    if added:
        save(draws)
        print("已新增 %d 期：%s" % (len(added), "、".join(added)))
    else:
        print("沒有新的攪珠結果。")
    if conflicts:
        print("警告：以下日期各來源或現有記錄不一致，未有寫入，請人手核對：", "、".join(conflicts))
        return 2
    return 0


def verify_all():
    have = {d["date"]: (d["numbers"], d["bonus"]) for d in load()}
    bad = checked = 0
    for year in range(2009, today_eastern().year + 1):
        html = fetch(YEAR_URL.format(year=year))
        found = parse(html) if html else {}
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
    sys.exit(verify_all() if "--verify-all" in sys.argv else update())
