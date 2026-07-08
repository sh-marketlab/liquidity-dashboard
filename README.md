# 美元流動性儀表板（供給側 × 需求側）

自動抓取 FRED 與美國財政部公開數據，每個工作日更新一次，免費託管在 GitHub Pages。

## 架構

```
fetch_data.py            ← Python腳本：抓數據 → 寫入 docs/data.json
.github/workflows/       ← GitHub Actions：每個工作日23:00 UTC自動執行上面的腳本
docs/index.html          ← 儀表板網頁：讀取 data.json 畫圖
```

## 設定步驟（一次性，約15分鐘）

### 1. 申請 FRED API key（免費）
到 https://fred.stlouisfed.org/docs/api/api_key.html → 登入你的FRED帳號（就是你做dashboard那個）→ Request API Key → 複製那串32字元的key。

### 2. 建立 GitHub repo
1. 註冊/登入 https://github.com
2. 右上角 ＋ → New repository → 名稱例如 `liquidity-dashboard` → 選 **Public** → Create
3. 把本專案的所有檔案上傳（Add file → Upload files，注意 `.github/workflows/update.yml` 要保持資料夾路徑）

### 3. 設定 Secret 與啟用功能
1. Repo → Settings → Secrets and variables → Actions → **New repository secret**
   - Name: `FRED_API_KEY`
   - Secret: 貼上你的key
2. Repo → Settings → Pages → Source 選 **Deploy from a branch** → Branch 選 `main`、資料夾選 `/docs` → Save
3. Repo → Actions 頁籤 → 若有提示則啟用 workflows → 點 `Update dashboard data` → **Run workflow**（手動跑第一次）

跑完後（約1分鐘），你的儀表板網址就是：
`https://<你的帳號>.github.io/liquidity-dashboard/`

之後每個工作日自動更新，不需任何維護。

## 本機測試（可選）

```bash
export FRED_API_KEY=你的key        # Windows PowerShell: $env:FRED_API_KEY="你的key"
python fetch_data.py
# 然後在 docs/ 資料夾啟動簡易伺服器預覽：
cd docs && python -m http.server 8000
# 瀏覽器開 http://localhost:8000
```

## 涵蓋的指標

| 區塊 | 指標 | 來源 |
|---|---|---|
| 供給側 | 淨流動性 WALCL−TGA−RRP、銀行準備金、SOFR−IORB利差（日頻＋週均）、SRF使用量 | FRED |
| 需求側 | Fed持有國債4週變化（情境1）、銀行持有（情境3）、海外官方代管（情境5） | FRED |
| 需求側 | Notes/Bonds標售：BTC、間接/直接/Dealer比例、自動警示 | 財政部 FiscalData API |

**不自動化的兩項**（低頻，維持手動）：情境2退休基金看季度Z.1；情境7基差交易沿用你的CFTC COT槓桿基金淨空單分析。

## 判讀順序（每週五分鐘）

1. **訊號摘要列**：六張卡片，綠=正常、黃=留意、紅=警報
2. **水位計**：淨流動性目前位置 vs 5.2兆警戒線
3. 有黃紅燈 → 看對應圖表找原因（供給側=誰在搬水；需求側=誰不接債）
4. 結論餵入日常戰術層（2Y、DXY、VIX、F&G）

## 常見問題

- **Actions跑失敗**：九成是 `FRED_API_KEY` secret 沒設或打錯，看Actions的log。
- **標售表某些欄位是「—」**：財政部API對部分歷史場次欄位缺漏，屬正常。
- **想改更新頻率**：編輯 `.github/workflows/update.yml` 的 cron。
- **想加指標**：在 `fetch_data.py` 的series清單加FRED代碼，再到 `index.html` 加一張卡片。
