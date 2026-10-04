"""
流動性 × QE/QT 循環儀表板 - 數據抓取腳本
來源: FRED API (環境變數 FRED_API_KEY)
手動設定: cycle_config.json (階段切分、政策事件、HY 利差早期概略值)
輸出: docs/cycle_data.json  (docs/cycle.html 讀取)

每次執行會和既有的 docs/cycle_data.json 合併，不會覆蓋掉舊資料。
原因: FRED 對 BAMLH0A0HYM2 只給近 3 年、SP500 只給近 10 年，
      滾動出去的歷史要靠自己累積。某條序列抓取失敗時也會保留舊值。

離線/補資料模式: python fetch_cycle.py --csv <資料夾>
  資料夾內放 FRED 下載的 CSV: fredgraph.csv (WALCL,WLRRAL,WDTGAL,WRBWFRBL)、
  T10Y2Y.csv、BAMLH0A0HYM2.csv、SP500.csv、NASDAQCOM.csv、DFEDTARU.csv
"""
import csv
import json
import os
import sys
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta, timezone

FRED_KEY = os.environ.get("FRED_API_KEY", "").strip()
START = "2016-10-01"
ROOT = os.path.dirname(os.path.abspath(__file__))
OUT_PATH = os.path.join(ROOT, "docs", "cycle_data.json")
CONFIG_PATH = os.path.join(ROOT, "cycle_config.json")

WEEKLY = ["WALCL", "WLRRAL", "WDTGAL", "WRBWFRBL"]          # H.4.1 週三值, 百萬美元
DAILY = ["T10Y2Y", "BAMLH0A0HYM2", "SP500", "NASDAQCOM", "DFEDTARU"]

# FRED 的 DFEDTARU 在這一次是決議當天就變動 (其他各次都是隔天生效)
DECISION_DATE_OVERRIDE = {"2016-12-13": "2016-12-14"}

# ---------- 讀取 ----------

def fred_series(series_id, start=START):
    """回傳 [[date, value], ...]，略過缺值"""
    params = urllib.parse.urlencode({
        "series_id": series_id, "api_key": FRED_KEY,
        "file_type": "json", "observation_start": start,
    })
    url = f"https://api.stlouisfed.org/fred/series/observations?{params}"
    req = urllib.request.Request(url, headers={"User-Agent": "liquidity-dashboard/1.0"})
    with urllib.request.urlopen(req, timeout=60) as r:
        data = json.loads(r.read().decode("utf-8"))
    return [[o["date"], float(o["value"])] for o in data.get("observations", [])
            if o.get("value") not in (".", "", None)]

def csv_series(folder):
    """離線模式: 從 FRED 下載的 CSV 讀出 {series_id: [[date, value], ...]}"""
    out = {}
    for name in sorted(os.listdir(folder)):
        if not name.lower().endswith(".csv"):
            continue
        with open(os.path.join(folder, name), encoding="utf-8-sig") as f:
            for row in csv.DictReader(f):
                d = row.get("observation_date") or row.get("DATE")
                for k, v in row.items():
                    if k in ("observation_date", "DATE") or v in (".", "", None):
                        continue
                    try:
                        out.setdefault(k, {})[d] = float(v)
                    except ValueError:
                        pass
    return {k: sorted([d, v] for d, v in m.items() if d >= START) for k, m in out.items()}

# ---------- 合併與衍生 ----------

def merge(old, new):
    """以日期為鍵合併，新值覆蓋舊值"""
    m = {d: v for d, v in (old or [])}
    m.update({d: v for d, v in (new or [])})
    return [[d, m[d]] for d in sorted(m)]

def rate_changes(daily):
    """由 DFEDTARU 日資料找出每次變動，回傳 [[決議日, 新上限], ...]"""
    out = []
    for (d0, v0), (d1, v1) in zip(daily, daily[1:]):
        if v1 != v0:
            dec = (date.fromisoformat(d1) - timedelta(days=1)).isoformat()
            out.append([DECISION_DATE_OVERRIDE.get(dec, dec), v1])
    return out

def phase_return(series, a, b):
    seg = [v for d, v in series if a <= d <= b]
    return round((seg[-1] / seg[0] - 1) * 100) if len(seg) >= 2 else None

def main():
    with open(CONFIG_PATH, encoding="utf-8") as f:
        cfg = json.load(f)
    old = {}
    if os.path.exists(OUT_PATH):
        with open(OUT_PATH, encoding="utf-8") as f:
            old = json.load(f)

    # 1. 取得新資料
    fresh = {}
    if len(sys.argv) >= 3 and sys.argv[1] == "--csv":
        fresh = csv_series(sys.argv[2])
        print(f"離線模式: 讀到 {', '.join(sorted(fresh))}")
    else:
        if not FRED_KEY:
            print("錯誤: 未設定 FRED_API_KEY 環境變數")
            sys.exit(1)
        for sid in WEEKLY + DAILY:
            try:
                fresh[sid] = fred_series(sid)
                print(f"  FRED {sid}: {len(fresh[sid])} 筆")
            except Exception as e:
                print(f"  FRED {sid} 失敗，沿用舊資料: {e}")

    # 2. 與既有資料合併 (新值覆蓋同日舊值；抓不到的序列沿用舊資料)
    raw = dict(old.get("raw", {}))
    raw.update({"T10Y2Y": old.get("ty"), "BAMLH0A0HYM2": old.get("hy"),
                "SP500": old.get("sp"), "NASDAQCOM": old.get("nq")})
    for sid in WEEKLY + DAILY[:4]:
        raw[sid] = merge(raw.get(sid), fresh.get(sid))
        if not raw[sid]:
            print(f"錯誤: {sid} 沒有任何資料 (首次執行請確認 API key，或用 --csv 補資料)")
            sys.exit(1)

    # 3. 資產負債表 (兆美元): [日期, 總資產, 淨流動性, 準備金, TGA, RRP]
    w = {sid: dict(raw[sid]) for sid in WEEKLY}
    bs = []
    for d, walcl in raw["WALCL"]:
        if all(d in w[s] for s in WEEKLY):
            tga, rrp, res = w["WDTGAL"][d], w["WLRRAL"][d], w["WRBWFRBL"][d]
            bs.append([d, round(walcl / 1e6, 3), round((walcl - tga - rrp) / 1e6, 3),
                       round(res / 1e6, 3), round(tga / 1e6, 3), round(rrp / 1e6, 3)])

    # 4. 升降息: 由 DFEDTARU 推出，並與既有清單合併
    dfed = fresh.get("DFEDTARU") or []
    ff = merge(old.get("ff"), rate_changes(dfed))
    ff0 = old.get("ff0", dfed[0][1] if dfed else None)
    if ff0 is None:
        print("錯誤: 首次執行需要 DFEDTARU 才能建立利率起始值")
        sys.exit(1)

    # 5. 各階段的指數漲跌 (階段結束日空白 = 進行中)
    last_day = raw["SP500"][-1][0]
    phases = []
    for p in cfg["phases"]:
        b = p.get("b") or last_day
        phases.append({**p, "pr": [phase_return(raw["SP500"], p["a"], b),
                                   phase_return(raw["NASDAQCOM"], p["a"], b)]})

    payload = {
        "updated": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
        "bs": bs,
        "ty": raw["T10Y2Y"],
        "hy": raw["BAMLH0A0HYM2"],
        "sp": raw["SP500"],
        "nq": raw["NASDAQCOM"],
        "ff0": ff0,
        "ff": ff,
        "phases": phases,
        "events": cfg["events"],
        "hy_approx": cfg["hy_approx"],
        "raw": {sid: raw[sid] for sid in WEEKLY},
    }
    os.makedirs(os.path.dirname(OUT_PATH), exist_ok=True)
    with open(OUT_PATH, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, separators=(",", ":"))
    print(f"完成 → {OUT_PATH}  (資產負債表至 {bs[-1][0]}，日資料至 {raw['T10Y2Y'][-1][0]})")

if __name__ == "__main__":
    main()
