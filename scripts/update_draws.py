#!/usr/bin/env python3
"""Lotto Max 自動更新程式 (DrawNight)

每次執行會：
  1. 由兩個公開資料來源讀取最近的攪珠結果
  2. 檢查每一期（7 個不重複號碼 + 特別號碼、號碼範圍、星期二/五、不是未來日期）
  3. 把 data/draws.json 未有的期數全部補上（不止最新一期）
  4. 兩個來源結果不一致時，不寫入該期，並以錯誤結束（GitHub 會電郵通知你）
  5. 數據已經係最新時，唔會去讀來源網站（減少被封鎖機會）
  6. 電郵通知：每個攪珠日最多一封。只有 workflow 指定嘅「檢查」排程
     （攪珠後 3 小時以上）發現仍然缺少最新一期，先會變紅色寄電郵。
     其他排程讀唔到都只會留低提示，等下一次再試。

RUN_MODE（由 workflow 設定）：
  quiet  ＝ 一般排程：有問題都唔變紅色
  alert  ＝ 檢查排程：仍然缺少最新一期或數據不一致就變紅色（寄一封電郵）
  manual ＝ 手動執行：一定去讀來源，有任何問題都變紅色

用法：
  python scripts/update_draws.py              # 正常更新
  python scripts/update_draws.py --verify-all # 逐年覆核全部歷史數據（只報告，不修改）
  python scripts/update_draws.py --force      # 手動模式：一定去讀來源，有問題就報錯
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

DATA = pathlib.Path(__file__).resolve().parent.parent / "data" / "draws.json"
SOURCES = [
    "https://www.lottomaxnumbers.com/past-numbers",
    "https://ca.lottonumbers.com/lotto-max/past-numbers",
]
RESULT_DELAY_MIN = 30  # 攪珠後大約幾多分鐘先有結果
YEAR_URL = "https://www.lottomaxnumbers.com/numbers/{year}"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/126.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml",
    "Accept-Language": "en-CA,en;q=0.8",
}
MONTHS = {m: i + 1 for i, m in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"])}
# 接受 "November 13 2026"、"Nov 13, 2026"、"Sept. 5, 2026" 等寫法
DATE_RE = re.compile(
    r"\b(jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|june?|july?|aug(?:ust)?|"
    r"sep(?:t(?:ember)?)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)\.?\s+(\d{1,2})(?:st|nd|rd|th)?,?\s+(\d{4})\b",
    re.I)


def pool_for(date):
    """號碼範圍：2026-04-14 起 1-52；2019-05-14 起 1-50；之前 1-49。"""
    if date >= "2026-04-14":
        return 52
    if date >= "2019-05-14":
        return 50
    return 49


def now_eastern():
    try:
        from zoneinfo import ZoneInfo
        return dt.datetime.now(ZoneInfo("America/Toronto")).replace(tzinfo=None)
    except Exception:
        return dt.datetime.utcnow() - dt.timedelta(hours=5)


def today_eastern():
    return now_eastern().date()


def expected_latest(now=None):
    """最近一期「應該已經有結果」的攪珠日期，及距離攪珠過了幾多個鐘。"""
    now = now or now_eastern()
    d = now.date()
    for _ in range(8):
        if d.weekday() in (1, 4):  # 星期二、五
            draw_at = dt.datetime.combine(d, dt.time(22, 30))
            if now >= draw_at + dt.timedelta(minutes=RESULT_DELAY_MIN):
                return d.isoformat(), (now - draw_at).total_seconds() / 3600
        d -= dt.timedelta(days=1)
    return None, 0.0


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
        date = "%04d-%02d-%02d" % (int(m.group(3)), MONTHS[m.group(1).lower()[:3]], int(m.group(2)))
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
    for attempt in range(2):
        try:
            r = requests.get(url, headers=HEADERS, timeout=(10, 25))
            if r.status_code == 200:
                return r.text
            last = "HTTP %s" % r.status_code
        except requests.RequestException as e:
            last = str(e)
        time.sleep(3)
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


def finish(mode, problem):
    """有問題時決定紅色定唔紅色。每個攪珠日只有 alert 排程會紅色。"""
    if not problem:
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
    if exp in have and mode != "manual":
        print("數據已是最新（已有 %s 一期），今次唔使讀取來源。" % exp)
        return 0
    results = []
    for url in SOURCES:
        print("讀取", url)
        html = fetch(url)
        found = parse(html) if html else {}
        print("  讀到 %d 期" % len(found))
        results.append(found)
    if not any(results):
        return finish(mode, "所有來源都讀不到數據，歷史記錄維持不變")
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
        draws.append({"date": date, "numbers": nums, "bonus": bonus})
        added.append("%s%s" % (date, "" if len(seen) > 1 else "（單一來源）"))
    if added:
        save(draws)
        print("已新增 %d 期：%s" % (len(added), "、".join(added)))
    else:
        print("沒有新的攪珠結果。")
    if conflicts:
        return finish(mode, "以下日期各來源或現有記錄不一致，未有寫入，請人手核對：" + "、".join(conflicts))
    if exp and exp not in {d["date"] for d in draws}:
        return finish(mode, "攪珠後 %.1f 小時，來源網站仍未有 %s 的結果" % (hours, exp))
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
    if "--verify-all" in sys.argv:
        sys.exit(verify_all())
    mode = "manual" if "--force" in sys.argv else os.environ.get("RUN_MODE", "manual").strip().lower()
    if mode not in ("quiet", "alert", "manual"):
        mode = "manual"
    sys.exit(update(mode))
