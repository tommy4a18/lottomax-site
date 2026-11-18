# DrawNight 上線教學（唔使識寫程式）

呢個資料夾就係成個網站。跟住以下步驟，大約 20 分鐘就可以免費上線，之後逢星期二、五攪珠後會自動更新數據。

## 資料夾入面有乜

| 檔案 | 用途 |
|---|---|
| `index.html` | 成個網站（版面、6 種語言、分析、生成器全部喺入面） |
| `data/draws.json` | 攪珠歷史數據，每行一期，最新喺最上面 |
| `scripts/update_draws.py` | 自動更新程式：去兩個公開網站攞新結果、檢查、寫入 `draws.json` |
| `.github/workflows/update.yml` | 定時器：叫 GitHub 逢攪珠日之後自動行上面個程式 |
| `workflow-update.yml.txt` | 同上面個定時器一模一樣嘅副本（步驟 3 會用到） |

## 步驟 1：開 GitHub 帳戶同儲存庫

1. 去 <https://github.com> 免費註冊。
2. 右上角 **+** → **New repository**。
3. Repository name 填 `drawnight`（或者你鍾意嘅名），揀 **Public**，撳 **Create repository**。

## 步驟 2：上載網站檔案

1. 喺新儲存庫頁面撳 **uploading an existing file**。
2. 將 `index.html`、`data` 資料夾、`scripts` 資料夾、`README.md` 一齊拖入去。
3. 撳 **Commit changes**。

## 步驟 3：加入定時器

`.github` 係隱藏資料夾，拖放好多時會漏咗，所以用人手開：

1. 儲存庫頁面撳 **Add file** → **Create new file**。
2. 檔名一格打 `.github/workflows/update.yml`（打 `/` 時佢會自動變資料夾）。
3. 用記事本打開 `workflow-update.yml.txt`，全選複製，貼入去。（呢個副本同 `.github/workflows/update.yml` 內容完全一樣。）
4. 撳 **Commit changes**。

## 步驟 4：開啟網站

1. 儲存庫上面撳 **Settings** → 左邊 **Pages**。
2. Source 揀 **Deploy from a branch**，Branch 揀 **main**、資料夾揀 **/ (root)**，撳 **Save**。
3. 等一兩分鐘，頁面頂會出現網址，例如 `https://你的帳戶名.github.io/drawnight/`。

## 步驟 5：測試自動更新

1. 儲存庫上面撳 **Actions** → 左邊 **Update Lotto Max draws** → 右邊 **Run workflow** → **Run workflow**。
2. 等大約一分鐘。綠色剔號代表成功；撳入去可以睇到「已新增幾多期」或者「沒有新的攪珠結果」。
3. 如果出紅色交叉，撳入去睇訊息。最常見原因係資料來源網站暫時封鎖 GitHub 或者改咗版面。將嗰段訊息貼畀 Claude，叫佢幫你改 `scripts/update_draws.py`。

**呢一步一定要做。** 更新程式喺製作時只用模擬網頁測試過，未對住真網站行過，第一次手動執行就係實測。

想一次過覆核全部歷史數據：**Run workflow** 時剔選「逐年覆核全部歷史數據」。佢只會報告差異，唔會改數據。

## 之後點運作

- 攪珠喺星期二、五晚 **8:30pm（溫哥華時間）**。
- **攪珠排程**：攪珠時段每 15 分鐘觸發一次。觸發咗之後，如果結果未出，會喺同一個 run 入面**每 5 分鐘再問一次**，直到攞到為止（最多等到攪珠後 4 小時），唔使靠 GitHub 下一次排程。
- **頭獎排程**：攪珠後兩日內每 2 小時一次，攞下期頭獎金額同 MaxPlus，寫入 `data/next.json`。官方 PlayNow 優先。
- **官方先**：每次先讀 BCLC 官方 PlayNow（攪珠結果同頭獎）。官方兩樣都有，就唔使讀其他網站。
- 官方未有或者連唔到，先讀後備網站：lottomaxnumbers.com、ca.lottonumbers.com。
- 官方同後備數字唔一樣，以官方為準；冇官方數字嗰期，後備網站之間要一致先會寫入。
- 如果一部機連唔到所有來源，會自動開過另一部機再試，最多 4 部。
- 數據同頭獎都已經係最新，就唔會再讀來源網站，幾秒完成。
- **電郵通知：每個攪珠日最多一封。** 只有凌晨嘅「檢查」排程（冬令 12:47am／夏令 1:47am）發現仍然冇最新一期，先會寄。
- 手動撳 Run workflow 會即刻讀一次來源，有問題就變紅色，方便你測試。畫面上「mode」「watch」「attempt」幾格係內部用，唔使改。
- 如果儲存庫 60 日完全冇更新，GitHub 會暫停定時器；萬一停咗，去 **Actions** 撳 **Enable workflow** 就得。

## 人手加一期（後備方法）

打開 `data/draws.json` → 撳鉛筆圖示 → 喺第一行 `[` 下面加一行（留意行尾有逗號）：

```
{"date": "2026-11-17", "numbers": [1, 2, 3, 4, 5, 6, 7], "bonus": 8},
```

## 想改設計或者加功能

將 `index.html` 上載畀 Claude，直接講你想改乜，例如「將金色改成紅色」、「加西班牙文」、「網站名改做 XXX」，再將新嘅 `index.html` 上載返去 GitHub 覆蓋舊檔就得。

- 網站名：搜尋 `DrawNight`。
- 顏色：`index.html` 最頂 `:root{` 嗰幾行。
- 文字翻譯：搜尋 `const I18N`，每種語言一組。旁遮普文同印地文建議搵母語人士睇一次。

## 自訂網址（可選）

買一個域名（例如喺 Namecheap 或 Cloudflare，大約每年十幾加元），喺 **Settings → Pages → Custom domain** 填入，再跟 GitHub 指示設定 DNS。

## 重要聲明

本網站係獨立網站，同 OLG、BCLC、WCLC、Loto-Québec、Atlantic Lottery 無關。請勿使用官方 Lotto Max 商標或者標誌。攪珠係隨機，任何分析或生成器都唔可以提高中獎機會。如果日後想放廣告或者收費，請先了解你所在省份對彩票相關網站嘅規定。
