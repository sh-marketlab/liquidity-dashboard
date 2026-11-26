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

## 美債危機監控頁（docs/treasury.html）

追蹤「美債崩潰」最可能的五種形式（長債暴動、標售失敗、財政主導、流動性崩潰、債務上限）＋黑天鵝，給出紅／黃／綠／藍總燈號與 TLT、IEF 的對應動作；頁面最下方有完整的底層邏輯說明。

- `fetch_treasury.py` → `docs/treasury_data.json`（Actions 每個工作日跑；門檻在檔案上方 `RULES`，存續期間在 `DURATION`）
- `docs/treasury_manual.json`：黑天鵝手動狀態，在 GitHub 網頁直接改 `level`（green／yellow／red）、`note`、`updated`，存檔後重新整理網頁就生效。`exit_trigger: true` 的項目變 red，總燈號直接升紅燈。
- 債務上限調整後：DTS 會自動抓新上限；抓不到時改 `RULES["debt_limit_fallback"]`。
- 測試：`python -m pytest tests/test_treasury.py`

## 台美股逃生檢查表（docs/escape.html，可裝成手機 App）

改編自 money_crazybird 的暴跌檢查清單（韓國版），韓國項目換成台股（台積電獲利預估、外資台股買賣超、新台幣、加權指數）。每天判定：11 個燈號 → 情境（信用型／2022 型／2023 多頭型）→ 逃生 1～4 階段、回升 0～3 階段。

- `fetch_escape.py` → `docs/escape_data.json`、`docs/escape_history.json`（每天累積一筆階段，之後可回測）。門檻都在檔案上方 `RULES`。
- `notify_escape.py`：逃生或回升階段**改變時**開一則 GitHub Issue → GitHub 寄 email；手機裝 GitHub App 會直接推播。階段沒變不通知。
- `docs/escape_manual.json`（手動，在 GitHub 網頁直接改）：
  - `capex_guidance`：四大雲端業者「明年」資本支出成長率指引，每季財報後填 `growth_pct` 與 `updated`。沒填時用最新一季實際年增率當近似值（頁面標 *）。
  - `events`：FOMC、CPI、就業報告日期，可自行加台積電法說會等。
- 資料來源：FRED、美國財政部、Yahoo Finance（指數、匯率、S&P 500 成分股日線自算市場寬度）、yfinance（分析師 EPS 預估、資本支出）、證交所 BFI82U。任何來源失敗 → 該項「無法確認」，不計入紅燈。
- **裝成手機 App**：手機瀏覽器打開 `https://sh-marketlab.github.io/liquidity-dashboard/escape.html` → iPhone Safari「分享 → 加入主畫面」；Android Chrome 點頁面上的「安裝」或選單「加到主畫面」。
- 測試：`python -m pytest tests/test_escape.py`
