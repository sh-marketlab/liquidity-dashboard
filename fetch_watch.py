"""
每日觀察儀表板 - 數據抓取與燈號判斷
來源: FRED (FRED_API_KEY)、Yahoo Finance (MOVE、USD/TWD)、CNN Fear & Greed、證交所、ICI (MMF，手動)
輸出: docs/watch_data.json  →  docs/watch.html 讀取

調整門檻: 只要改下面 RULES 區塊的數字即可 (GitHub 網頁上直接編輯)。
任何一個來源失敗都不會中斷整支程式，該指標會顯示「無資料」。
"""
import json
import os
import re
import time
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta, timezone

FRED_KEY = os.environ.get("FRED_API_KEY", "").strip()
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
TODAY = date.today()

# ============================================================
# 門檻設定 (起始值，回測後自行調整)
# watch = 黃燈「留意」, alert = 紅燈「觸發動作」
# ============================================================
RULES = {
    "hy_level":      {"watch": 4.0, "alert": 5.0},    # HY OAS 絕對值 (%)
    "hy_chg1m":      {"watch": 0.5, "alert": 1.0},    # HY OAS 一個月擴大 (百分點, 1.0 = 100bp)
    "sofr_iorb":     {"watch": 0.05, "alert": 0.10, "days": 3},  # SOFR−IORB (百分點), 非月底連續天數
    "srf":           {"watch": 5, "alert": 25},       # SRF 使用量 (十億美元), 非月底
    "y2_chg1w":      {"watch": 0.25, "alert": 0.40},  # 2Y 一週變動絕對值 (百分點)
    "vix":           {"watch": 30, "alert": 40},
    "move":          {"watch": 130, "alert": 160},
    "dxy_chg1m":     {"watch": 3.0, "alert": 5.0},    # 美元指數一個月變動絕對值 (%)
    "primary_credit_chg4w": {"watch": 5000, "alert": 20000},  # 百萬美元
    "reserves_chg4w": {"watch": -150000, "alert": -300000},   # 百萬美元 (負值=下降)
    "stlfsi_level":  {"watch": 0.0, "alert": 1.0},
    "stlfsi_rise4w": 0.5,                             # 大於0且4週上升超過此值 → 紅燈
    "claims_rise":   {"watch": 10.0, "alert": 20.0},  # 4週均值較52週低點上升 (%)
    "fg_low": 20, "fg_high": 80,                      # Fear & Greed 反向參考
    "sahm":          {"watch": 0.3, "alert": 0.5},
    "sloos":         {"watch": 20, "alert": 40},      # 淨緊縮比例 (%)
    "twd_chg1m":     {"watch": 3.0, "alert": 5.0},    # USD/TWD 一個月變動絕對值 (%)
    "margin_chg20d": {"watch": 10.0, "alert": 20.0},  # 融資餘額 20 日變動絕對值 (%)
}

SPARK = {"daily": 130, "weekly": 104, "monthly": 60, "quarterly": 40}


# ============================================================
# 抓資料工具
# ============================================================
def http_get(url, headers=None, timeout=40):
    h = {"User-Agent": UA}
    if headers:
        h.update(headers)
    req = urllib.request.Request(url, headers=h)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode("utf-8")


def fred(series_id, years=6):
    start = (TODAY - timedelta(days=365 * years)).isoformat()
    params = urllib.parse.urlencode({
        "series_id": series_id, "api_key": FRED_KEY,
        "file_type": "json", "observation_start": start,
    })
    data = json.loads(http_get(f"https://api.stlouisfed.org/fred/series/observations?{params}"))
    out = []
    for o in data.get("observations", []):
        if o.get("value") not in (".", "", None):
            out.append([o["date"], float(o["value"])])
    return out


def yahoo(symbol, rng="2y"):
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{urllib.parse.quote(symbol)}?range={rng}&interval=1d"
    data = json.loads(http_get(url))
    res = data["chart"]["result"][0]
    ts = res["timestamp"]
    closes = res["indicators"]["quote"][0]["close"]
    out = []
    for t, c in zip(ts, closes):
        if c is not None:
            out.append([datetime.fromtimestamp(t, tz=timezone.utc).strftime("%Y-%m-%d"), round(float(c), 4)])
    return out


def cnn_fear_greed():
    url = "https://production.dataviz.cnn.io/index/fearandgreed/graphdata"
    data = json.loads(http_get(url, {"Referer": "https://edition.cnn.com/", "Accept": "application/json"}))
    out = []
    for p in data["fear_and_greed_historical"]["data"]:
        d = datetime.fromtimestamp(p["x"] / 1000, tz=timezone.utc).strftime("%Y-%m-%d")
        out.append([d, round(float(p["y"]), 1)])
    return out


MMF_MANUAL = os.path.join(os.path.dirname(os.path.abspath(__file__)), "docs", "mmf_manual.json")


def mmf_manual():
    """ICI 擋 GitHub 主機，MMF 改為手動：每週把 ICI 表格的 Total / Retail / Institutional
    加一行到 docs/mmf_manual.json (十億美元)"""
    with open(MMF_MANUAL, encoding="utf-8") as fh:
        rows = sorted(json.load(fh), key=lambda r: r["date"])
    return rows

def nyfed_sofr():
    """NY Fed 官方 SOFR (比 FRED 早幾小時)"""
    start = (TODAY - timedelta(days=365 * 5)).isoformat()
    data = json.loads(http_get("https://markets.newyorkfed.org/api/rates/secured/sofr/search.json"
                               f"?startDate={start}&endDate={TODAY.isoformat()}"))
    out = [[r["effectiveDate"], float(r["percentRate"])] for r in data["refRates"] if r.get("percentRate") is not None]
    return sorted(out)


def treasury_2y():
    """美國財政部每日殖利率曲線 (當天收盤後即公布，FRED 要等隔一個工作日)"""
    out = []
    for yr in (TODAY.year - 1, TODAY.year):
        url = ("https://home.treasury.gov/resource-center/data-chart-center/interest-rates/"
               f"daily-treasury-rates.csv/{yr}/all?type=daily_treasury_yield_curve&field_tdr_date_value={yr}&page&_format=csv")
        lines = http_get(url).strip().splitlines()
        head = [h.strip().strip('"') for h in lines[0].split(",")]
        i2 = head.index("2 Yr")
        for ln in lines[1:]:
            c = [x.strip().strip('"') for x in ln.split(",")]
            v = _num(c[i2]) if len(c) > i2 else None
            if v is not None:
                out.append([datetime.strptime(c[0], "%m/%d/%Y").strftime("%Y-%m-%d"), v])
    return sorted(out)


def prefer_fresh(primary, backup):
    """兩個來源取最新日期較晚的那個；回傳 (序列, 是否用主來源)"""
    if primary and (not backup or primary[-1][0] >= backup[-1][0]):
        return primary, True
    return backup, False


# ---------------- 美國假日 (純 Python，不需額外套件) ----------------
def _nth_weekday(y, m, wd, n):
    """y 年 m 月第 n 個星期 wd (0=週一)；n=-1 為最後一個"""
    if n > 0:
        d = date(y, m, 1)
        d += timedelta(days=(wd - d.weekday()) % 7)
        return d + timedelta(weeks=n - 1)
    d = date(y + (m == 12), m % 12 + 1, 1) - timedelta(days=1)
    return d - timedelta(days=(d.weekday() - wd) % 7)


def _observed(d):
    """週六的假日移到週五、週日移到週一"""
    if d.weekday() == 5:
        return d - timedelta(days=1)
    if d.weekday() == 6:
        return d + timedelta(days=1)
    return d


def _easter(y):
    a, b, c = y % 19, y // 100, y % 100
    d, e = b // 4, b % 4
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = c // 4, c % 4
    l = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * l) // 451
    month = (h + l - 7 * m + 114) // 31
    day = (h + l - 7 * m + 114) % 31 + 1
    return date(y, month, day)


_HOL_CACHE = {}


def us_holidays(y):
    """回傳 (fed, nyse) 兩組假日。
    fed  = 聯邦假日 (Fed / FRED / 債市大致依此休市，SOFR、H.4.1 不公布)
    nyse = 美股休市日 (無哥倫布日、退伍軍人節；多耶穌受難日)"""
    if y in _HOL_CACHE:
        return _HOL_CACHE[y]
    common = {
        _nth_weekday(y, 1, 0, 3),    # MLK
        _nth_weekday(y, 2, 0, 3),    # Presidents
        _nth_weekday(y, 5, 0, -1),   # Memorial
        _observed(date(y, 6, 19)),   # Juneteenth
        _observed(date(y, 7, 4)),    # Independence
        _nth_weekday(y, 9, 0, 1),    # Labor
        _nth_weekday(y, 11, 3, 4),   # Thanksgiving
        _observed(date(y, 12, 25)),  # Christmas
    }
    ny = date(y, 1, 1)
    fed = common | {
        _observed(ny), _observed(date(y + 1, 1, 1)),  # 隔年元旦若是週六，12/31 補假
        _nth_weekday(y, 10, 0, 2),     # Columbus
        _observed(date(y, 11, 11)),    # Veterans
    }
    fed = {d for d in fed if d.year == y}
    nyse = common | {_easter(y) - timedelta(days=2)}  # Good Friday
    if ny.weekday() != 5:              # 元旦逢週六時，美股不在 12/31 補休
        nyse.add(_observed(ny))
    _HOL_CACHE[y] = (fed, nyse)
    return fed, nyse


def is_us_bday(d):
    """聯邦或美股任一休市就不算工作日 (判斷資料是否過期時寧鬆勿嚴)"""
    if d.weekday() >= 5:
        return False
    fed, nyse = us_holidays(d.year)
    return d not in fed and d not in nyse


def bdays_between(d1, d2):
    """d1 之後到 d2 為止的美國工作日數 (不含 d1；跳過週末與美國假日)"""
    a = datetime.strptime(d1, "%Y-%m-%d").date()
    n = 0
    while a < d2:
        a += timedelta(days=1)
        if is_us_bday(a):
            n += 1
    return n


def last_us_session(now=None):
    """程式執行當下，美股最近一個已收盤的交易日 (美東 17:00 後才算當天；跳過美股休市日)"""
    from zoneinfo import ZoneInfo
    now = now or datetime.now(ZoneInfo("America/New_York"))
    d = now.date() if now.hour >= 17 else now.date() - timedelta(days=1)
    while d.weekday() >= 5 or d in us_holidays(d.year)[1]:
        d -= timedelta(days=1)
    return d


# ---------------- 台灣休市日 ----------------
# 台灣假日有農曆、補假、颱風假，無法用公式算，所以：
#   1) 先抓證交所 OpenAPI 的「開(休)市日期」(通常每年 12 月前公布隔年)
#   2) 抓不到時用下面的內建表 (每年證交所公告後，到這裡補一行即可)
# 「開始交易日」「最後交易日」是有交易的日子，不算休市；「市場無交易，僅辦理結算交割」算休市。
TW_HOLIDAYS_BUILTIN = {
    2026: ["01-01", "02-12", "02-13", "02-16", "02-17", "02-18", "02-19", "02-20",
           "02-27", "04-03", "04-06", "05-01", "06-19", "09-25", "09-28",
           "10-09", "10-26", "12-25"],
}
TWSE_HOLIDAY_API = "https://openapi.twse.com.tw/v1/holidaySchedule/holidaySchedule"
_TW_HOL = None


def _parse_tw_date(s):
    """'1150101'(民國) / '20260101' / '2026-01-01' / '115/01/01' → date"""
    digits = re.sub(r"\D", "", str(s))
    try:
        if len(digits) == 8:
            return date(int(digits[:4]), int(digits[4:6]), int(digits[6:]))
        if len(digits) in (6, 7):  # 民國年 2~3 位
            return date(int(digits[:-4]) + 1911, int(digits[-4:-2]), int(digits[-2:]))
    except ValueError:
        pass
    return None


def tw_holidays():
    """回傳台灣集中市場休市日 (set of date)；平日才有意義，週末另外判斷"""
    global _TW_HOL
    if _TW_HOL is not None:
        return _TW_HOL
    hol = {date(y, int(md[:2]), int(md[3:])) for y, mds in TW_HOLIDAYS_BUILTIN.items() for md in mds}
    try:
        rows = json.loads(http_get(TWSE_HOLIDAY_API, timeout=20))
        n = 0
        for r in rows if isinstance(rows, list) else []:
            name = str(r.get("Name") or r.get("名稱") or "")
            d = _parse_tw_date(r.get("Date") or r.get("日期") or "")
            if not d or "開始交易" in name or "最後交易" in name:
                continue
            hol.add(d)
            n += 1
        print(f"[tw_holidays] 證交所休市日 {n} 筆")
    except Exception as e:
        print(f"[tw_holidays] 證交所休市日抓取失敗，只用內建表: {e}")
    if TODAY.year not in TW_HOLIDAYS_BUILTIN and not any(d.year == TODAY.year for d in hol):
        print(f"[tw_holidays] ⚠ 沒有 {TODAY.year} 年的台灣休市日，請在 TW_HOLIDAYS_BUILTIN 補上")
    _TW_HOL = hol
    return hol


def is_tw_bday(d):
    return d.weekday() < 5 and d not in tw_holidays()


def tw_bdays_between(d1, d2):
    """d1 之後到 d2 為止的台股交易日數 (不含 d1)"""
    a = datetime.strptime(d1, "%Y-%m-%d").date()
    n = 0
    while a < d2:
        a += timedelta(days=1)
        if is_tw_bday(a):
            n += 1
    return n


def last_tw_session(now=None):
    """台股最近一個已收盤且盤後資料已公布的交易日 (台北 16:00 後才算當天)"""
    from zoneinfo import ZoneInfo
    now = now or datetime.now(ZoneInfo("Asia/Taipei"))
    d = now.date() if now.hour >= 16 else now.date() - timedelta(days=1)
    while not is_tw_bday(d):
        d -= timedelta(days=1)
    return d


# 與「最近收盤日」相比，正常最多落後幾個工作日；超過就標「資料過期」
# HY OAS、SOFR 來源本身就是 T+1 公布，所以日資料容許落後 1 天
MAX_LAG_BD = {"daily": 1, "weekly": 8, "monthly": 70, "quarterly": 75, "taiwan": 1}
SLOW_DAILY = {"dxy": 6}   # 廣義美元指數備援來源約落後一週


def _num(s):
    try:
        return float(str(s).replace(",", "").strip())
    except (TypeError, ValueError):
        return None


def _rows(obj):
    """把證交所各種版本的 JSON 結構攤平成一列一列"""
    if isinstance(obj, list):
        if obj and all(not isinstance(x, (list, dict)) for x in obj):
            yield obj
        else:
            for x in obj:
                yield from _rows(x)
    elif isinstance(obj, dict):
        for v in obj.values():
            yield from _rows(v)


def twse_days(n_days=30):
    """回傳最近的台股交易日 (由新到舊)，週末與台灣休市日跳過"""
    d, out = TODAY, []
    while len(out) < n_days:
        if is_tw_bday(d):
            out.append(d)
        d -= timedelta(days=1)
    return out


def twse_foreign_and_margin(n=25):
    foreign, margin = [], []
    for d in twse_days(n):
        ds = d.strftime("%Y%m%d")
        try:
            j = json.loads(http_get(f"https://www.twse.com.tw/rwd/zh/fund/BFI82U?type=day&dayDate={ds}&response=json"))
            if j.get("stat") == "OK":
                for r in _rows(j.get("data", [])):
                    if str(r[0]).strip().startswith("外資及陸資"):  # 「外資及陸資(不含外資自營商)」
                        v = _num(r[-1])
                        if v is not None:
                            foreign.append([d.isoformat(), round(v / 1e8, 1)])  # 元 → 億元
                        break
            time.sleep(1.5)
            j = json.loads(http_get(f"https://www.twse.com.tw/rwd/zh/marginTrading/MI_MARGN?date={ds}&selectType=MS&response=json"))
            if j.get("stat") == "OK":
                for r in _rows(j):
                    if "融資金額" in str(r[0]) and len(r) >= 6:
                        v = _num(r[5])
                        if v is not None:
                            margin.append([d.isoformat(), round(v / 1e5, 1)])  # 仟元 → 億元
                        break
            time.sleep(1.5)
        except Exception as e:
            print(f"  證交所 {ds} 失敗: {e}")
    return sorted(foreign), sorted(margin)


def safe(label, fn, *a, **kw):
    try:
        s = fn(*a, **kw)
        print(f"  {label}: {len(s)} 筆")
        return s
    except Exception as e:
        print(f"  {label} 失敗: {e}")
        return []


# ============================================================
# 計算工具
# ============================================================
def last(s, k=1):
    return s[-k] if len(s) >= k else None


def chg(s, back):
    """最新值 − back 筆之前的值"""
    if len(s) <= back:
        return None
    return s[-1][1] - s[-1 - back][1]


def pct_chg(s, back):
    if len(s) <= back or s[-1 - back][1] == 0:
        return None
    return (s[-1][1] / s[-1 - back][1] - 1) * 100


def yoy(s):
    """月資料年增率序列"""
    out = []
    for i in range(12, len(s)):
        if s[i - 12][1]:
            out.append([s[i][0], round((s[i][1] / s[i - 12][1] - 1) * 100, 2)])
    return out


def rolling_mean(s, n):
    out = []
    for i in range(n - 1, len(s)):
        out.append([s[i][0], round(sum(x[1] for x in s[i - n + 1:i + 1]) / n, 1)])
    return out


def diff_series(s):
    return [[s[i][0], round(s[i][1] - s[i - 1][1], 1)] for i in range(1, len(s))]


def month_end_window(dstr):
    """月底/月初資金面常態性緊張的日子，不算訊號 (28 日後或 2 日前)"""
    d = int(dstr[8:10])
    return d >= 28 or d <= 2


def level(v, rule, higher_is_worse=True):
    if v is None:
        return "na"
    if not higher_is_worse:
        v = -v
        rule = {"watch": -rule["watch"], "alert": -rule["alert"]}
    if v >= rule["alert"]:
        return "alert"
    if v >= rule["watch"]:
        return "watch"
    return "ok"


def worst(*states):
    order = ["na", "info", "ok", "watch", "alert"]
    states = [s for s in states if s != "na"] or ["na"]
    return max(states, key=order.index)


def ind(id_, group, name, s, unit, status, rule, action, source, chg_label=None, chg_value=None,
        decimals=2, note=None, spark=None):
    lv = last(s)
    sp = spark if spark is not None else s
    return {
        "id": id_, "group": group, "name": name, "unit": unit,
        "value": round(lv[1], decimals) if lv else None,
        "date": lv[0] if lv else None,
        "chg_label": chg_label,
        "chg": round(chg_value, decimals) if chg_value is not None else None,
        "status": status if lv else "na",
        "rule": rule, "action": action, "source": source, "note": note,
        "spark": sp[-SPARK[group if group in SPARK else "daily"]:],
    }


# ============================================================
# 主程式
# ============================================================
def main():
    if not FRED_KEY:
        print("錯誤: 未設定 FRED_API_KEY")
        raise SystemExit(1)

    print("FRED...")
    f = {sid: safe(sid, fred, sid) for sid in [
        "BAMLH0A0HYM2", "SOFR", "IORB", "RPONTSYD", "DGS2", "VIXCLS", "T10Y2Y",
        "WLCFLPCL", "WRBWFRBL", "WDTGAL", "WLRRAL", "WALCL", "STLFSI4", "ICSA",
        "CPIAUCSL", "CPILFESL", "PPIFIS", "PCEPILFE", "PAYEMS", "UNRATE", "SAHMREALTIME",
        "UMCSENT", "MICH", "DRTSCILM", "THREEFYTP10", "DEXTAUS",
    ]}
    print("其他來源...")
    move = safe("MOVE", yahoo, "^MOVE")
    dxy_s = safe("DXY", yahoo, "DX-Y.NYB")
    twd = safe("USD/TWD", yahoo, "TWD=X") or f["DEXTAUS"]
    fg = safe("Fear & Greed", cnn_fear_greed)
    sofr, sofr_fast = prefer_fresh(safe("SOFR (NY Fed)", nyfed_sofr), f["SOFR"])
    y2, y2_fast = prefer_fresh(safe("2Y (財政部)", treasury_2y), f["DGS2"])
    vix, vix_fast = prefer_fresh(safe("VIX (Yahoo)", yahoo, "^VIX"), f["VIXCLS"])
    try:
        mmf_rows = mmf_manual()
        print(f"  MMF 手動: {len(mmf_rows)} 筆")
    except (OSError, ValueError, KeyError) as e:
        print(f"  MMF 手動檔讀取失敗: {e}")
        mmf_rows = []
    foreign, margin = twse_foreign_and_margin()
    print(f"  證交所: 外資 {len(foreign)} 天, 融資 {len(margin)} 天")

    R = RULES
    out = []

    # ---------------- 每日 ----------------
    hy = f["BAMLH0A0HYM2"]
    hy_c = chg(hy, 21)
    st = worst(level(last(hy)[1] if hy else None, R["hy_level"]), level(hy_c, R["hy_chg1m"]))
    out.append(ind("hy", "daily", "HY OAS 高收益債利差", hy, "%", st,
        f"一個月擴大 ≥ {R['hy_chg1m']['alert']*100:.0f}bp 或 ≥ {R['hy_level']['alert']}%",
        "紅隊暫停加碼，檢查持股的負債結構", "FRED BAMLH0A0HYM2", "1 個月", hy_c))

    # IORB 只在 FOMC 調整，往後補值，才不會因 FRED 的 IORB 晚一天而少算最新的 SOFR
    iorb = sorted(f["IORB"])
    spread, k, cur = [], 0, None
    for d, v in sofr:
        while k < len(iorb) and iorb[k][0] <= d:
            cur = iorb[k][1]; k += 1
        if cur is not None:
            spread.append([d, round(v - cur, 4)])
    streak = 0
    for d, v in reversed(spread):
        if month_end_window(d):
            continue
        if v > R["sofr_iorb"]["watch"]:
            streak += 1
        else:
            break
    st = "ok"
    if spread:
        d0, v0 = spread[-1]
        if not month_end_window(d0) and v0 > R["sofr_iorb"]["alert"]:
            st = "alert"
        elif streak >= R["sofr_iorb"]["days"]:
            st = "watch"
    out.append(ind("sofr_iorb", "daily", "SOFR − IORB", spread, "百分點", st,
        f"非月底連續 {R['sofr_iorb']['days']} 天 > {R['sofr_iorb']['watch']*100:.0f}bp；單日 > {R['sofr_iorb']['alert']*100:.0f}bp 為紅燈",
        "留意水管壓力，對照稅務日與公債交割日", ("NY Fed SOFR" if sofr_fast else "FRED SOFR") + ", FRED IORB", None, None, 3,
        note=f"非月底連續偏高 {streak} 天"))

    srf = f["RPONTSYD"]
    st = "ok"
    if srf and not month_end_window(srf[-1][0]):
        st = level(srf[-1][1], R["srf"])
    out.append(ind("srf", "daily", "SRF 常設附買回使用量", srf, "十億美元", st,
        f"非月底 ≥ {R['srf']['watch']}B 黃燈、≥ {R['srf']['alert']}B 紅燈",
        "銀行在向 Fed 借短錢，搭配 SOFR−IORB 一起看", "FRED RPONTSYD", None, None, 2,
        note="月底、月初使用量偏高屬常態"))

    c = chg(y2, 5)
    out.append(ind("y2", "daily", "2 年期美債殖利率", y2, "%", level(abs(c) if c is not None else None, R["y2_chg1w"]),
        f"一週變動 ≥ {R['y2_chg1w']['watch']*100:.0f}bp", "對照 FedWatch，記錄市場預期改變的原因",
        "美國財政部" if y2_fast else "FRED DGS2", "1 週", c))

    out.append(ind("vix", "daily", "VIX", vix, "", level(last(vix)[1] if vix else None, R["vix"]),
        f"≥ {R['vix']['watch']} 壓力、≥ {R['vix']['alert']} 極端",
        "≥30 不追高不殺低；≥40 準備紅隊進場清單（見階段規則）", "Yahoo ^VIX" if vix_fast else "FRED VIXCLS", "1 週", chg(vix, 5), 1))

    out.append(ind("move", "daily", "MOVE 美債波動", move, "", level(last(move)[1] if move else None, R["move"]),
        f"≥ {R['move']['watch']}", "債市不穩會傳導到抵押品與融資，提高現金意識",
        "Yahoo ^MOVE", "1 週", chg(move, 5), 1))

    dxy = dxy_s
    c = pct_chg(dxy, 21)
    out.append(ind("dxy", "daily", "美元指數 DXY", dxy, "", level(abs(c) if c is not None else None, R["dxy_chg1m"]),
        f"一個月變動 ≥ {R['dxy_chg1m']['watch']}%", "檢查新台幣匯率與外資買賣超",
        "Yahoo DX-Y.NYB（ICE 美元指數）", "1 個月 %", c, 2))

    # ---------------- 每週 ----------------
    pc = f["WLCFLPCL"]
    c = chg(pc, 4)
    out.append(ind("primary_credit", "weekly", "Primary credit 貼現窗口", pc, "百萬美元",
        level(c, R["primary_credit_chg4w"]), "4 週增加 ≥ 50 億美元",
        "視為銀行壓力訊號，對照區域銀行股與存款資料", "FRED WLCFLPCL", "4 週", c, 0))

    rs = f["WRBWFRBL"]
    c = chg(rs, 4)
    out.append(ind("reserves", "weekly", "銀行準備金", rs, "百萬美元",
        level(c, R["reserves_chg4w"], higher_is_worse=False), "4 週減少 ≥ 1,500 億美元",
        "留意 QT 放緩或結束的政策訊號", "FRED WRBWFRBL", "4 週", c, 0))

    tga_m, rrp_m = dict(f["WDTGAL"]), dict(f["WLRRAL"])
    netliq = [[d, round(v - tga_m[d] - rrp_m[d], 0)] for d, v in f["WALCL"] if d in tga_m and d in rrp_m]
    c = chg(netliq, 4)
    regime = "擴張" if (c or 0) > 0 else "收縮"
    out.append(ind("netliq", "weekly", "淨流動性 WALCL − TGA − RRP", netliq, "百萬美元", "info",
        "只判斷環境，不抓買賣點", f"近 4 週{regime}", "FRED WALCL, WDTGAL, WLRRAL", "4 週", c, 0,
        note=f"近 4 週{regime}"))

    out.append(ind("tga", "weekly", "TGA 財政部帳戶", f["WDTGAL"], "百萬美元", "info",
        "上升＝從市場抽水", "對照稅務日", "FRED WDTGAL", "4 週", chg(f["WDTGAL"], 4), 0))
    out.append(ind("rrp", "weekly", "ON RRP 隔夜逆回購", f["WLRRAL"], "百萬美元", "info",
        "接近零代表緩衝已用完", "", "FRED WLRRAL", "4 週", chg(f["WLRRAL"], 4), 0))

    mmf = [[r["date"], r["total"]] for r in mmf_rows]
    mmf_note = None
    if len(mmf_rows) >= 2:
        p0, p1 = mmf_rows[-2], mmf_rows[-1]
        mmf_note = (f"零售 {p1['retail']:,.2f}（{p1['retail'] - p0['retail']:+.2f}）｜"
                    f"機構 {p1['institutional']:,.2f}（{p1['institutional'] - p0['institutional']:+.2f}）")
    out.append(ind("mmf", "weekly", "MMF 貨幣基金總資產", mmf, "十億美元", "info",
        "與 RRP、淨流動性一起看；週變動主要反映資金淨流入/流出",
        "MMF 增加而 RRP 沒增加＝錢停在 T-bill；機構型大減＝資金流回風險資產或被提領",
        "ICI（手動輸入，docs/mmf_manual.json）", "1 週", chg(mmf, 1), 2, note=mmf_note))

    sf = f["STLFSI4"]
    c = chg(sf, 4)
    v = last(sf)[1] if sf else None
    st = "na" if v is None else ("alert" if (v >= R["stlfsi_level"]["alert"] or (v > 0 and (c or 0) >= R["stlfsi_rise4w"]))
                                 else "watch" if v > R["stlfsi_level"]["watch"] else "ok")
    out.append(ind("stlfsi", "weekly", "STLFSI4 金融壓力指數", sf, "", st,
        "> 0 黃燈；> 0 且 4 週上升 ≥ 0.5 紅燈", "紅隊暫停加碼；急升後見頂才考慮進場",
        "FRED STLFSI4", "4 週", c, 2))

    ic = f["ICSA"]
    ic4 = rolling_mean(ic, 4)
    rise = None
    if len(ic4) >= 52:
        low = min(x[1] for x in ic4[-52:])
        rise = (ic4[-1][1] / low - 1) * 100 if low else None
    out.append(ind("claims", "weekly", "初領失業金（4 週均值）", ic4, "人", level(rise, R["claims_rise"]),
        "較 52 週低點上升 ≥ 20%", "加強觀察 NFP 與沙姆法則", "FRED ICSA", "較低點 %", rise, 0,
        note=f"較 52 週低點 +{rise:.1f}%" if rise is not None else None))

    cv = f["T10Y2Y"]
    st = "info"
    if len(cv) > 63 and cv[-64][1] < 0 <= cv[-1][1]:
        st = "watch"
    out.append(ind("curve", "weekly", "10Y − 2Y 利差", cv, "百分點", st,
        "三個月內由負轉正標黃燈", "區分是降息預期還是期限溢酬上升造成的",
        "FRED T10Y2Y", "4 週", chg(cv, 20), 2, spark=cv[::-5][::-1]))

    fv = last(fg)[1] if fg else None
    st = "na" if fv is None else ("watch" if (fv <= R["fg_low"] or fv >= R["fg_high"]) else "ok")
    out.append(ind("fg", "daily", "CNN Fear & Greed", fg, "", st,
        f"≤ {R['fg_low']} 或 ≥ {R['fg_high']}", "與 VIX、HY 重疊，只當反向參考",
        "CNN", "1 週", chg(fg, 5), 0, spark=fg[-90:]))

    # ---------------- 每月 ----------------
    cpi_y, core_y = yoy(f["CPIAUCSL"]), yoy(f["CPILFESL"])
    out.append(ind("cpi", "monthly", "CPI 年增率", cpi_y, "%", "info", "", "看核心與服務，不看單月整體",
        "FRED CPIAUCSL", "較上月", chg(cpi_y, 1), 2))
    out.append(ind("core_cpi", "monthly", "核心 CPI 年增率", core_y, "%", "info", "", "",
        "FRED CPILFESL", "較上月", chg(core_y, 1), 2))
    ppi_y = yoy(f["PPIFIS"])
    out.append(ind("ppi", "monthly", "PPI 最終需求年增率", ppi_y, "%", "info", "",
        "只對商品與能源通膨有領先性", "FRED PPIFIS", "較上月", chg(ppi_y, 1), 2))
    pce_y = yoy(f["PCEPILFE"])
    out.append(ind("core_pce", "monthly", "核心 PCE 年增率", pce_y, "%", "info", "Fed 目標 2%",
        "Fed 實際使用的通膨指標", "FRED PCEPILFE", "較上月", chg(pce_y, 1), 2))
    nfp = diff_series(f["PAYEMS"])
    nfp3 = rolling_mean(nfp, 3)
    out.append(ind("nfp", "monthly", "非農新增（3 個月均值）", nfp3, "千人", "info", "",
        "看 3 個月均值與修正", "FRED PAYEMS", "最新單月", nfp[-1][1] if nfp else None, 0))
    out.append(ind("unrate", "monthly", "失業率", f["UNRATE"], "%", "info", "", "",
        "FRED UNRATE", "較上月", chg(f["UNRATE"], 1), 1))
    sahm = f["SAHMREALTIME"]
    out.append(ind("sahm", "monthly", "沙姆法則", sahm, "百分點", level(last(sahm)[1] if sahm else None, R["sahm"]),
        "≥ 0.5 為衰退訊號", "就業轉弱，檢查景氣循環股部位", "FRED SAHMREALTIME", "較上月", chg(sahm, 1), 2))
    out.append(ind("umich", "monthly", "密西根消費者信心", f["UMCSENT"], "", "info", "", "",
        "FRED UMCSENT", "較上月", chg(f["UMCSENT"], 1), 1))
    out.append(ind("umich_inf", "monthly", "密西根 1 年通膨預期", f["MICH"], "%", "info", "", "",
        "FRED MICH", "較上月", chg(f["MICH"], 1), 1))

    # ---------------- 每季 ----------------
    sl = f["DRTSCILM"]
    out.append(ind("sloos", "quarterly", "SLOOS 企業放款淨緊縮比例", sl, "%", level(last(sl)[1] if sl else None, R["sloos"]),
        f"≥ {R['sloos']['watch']}% 黃燈、≥ {R['sloos']['alert']}% 紅燈",
        "放款條件惡化：防禦優先", "FRED DRTSCILM", "較上季", chg(sl, 1), 1))
    tp = f["THREEFYTP10"]
    out.append(ind("term_premium", "quarterly", "10 年期限溢酬（Kim-Wright）", tp, "百分點", "info", "",
        "區分長端利率上升是經濟好還是要求更多補償", "FRED THREEFYTP10", "3 個月", chg(tp, 63), 2, spark=tp[::-21][::-1]))

    # ---------------- 台灣 ----------------
    c = pct_chg(twd, 21)
    out.append(ind("twd", "taiwan", "USD/TWD", twd, "", level(abs(c) if c is not None else None, R["twd_chg1m"]),
        f"一個月變動 ≥ {R['twd_chg1m']['watch']}%", "對照外資買賣超與壽險避險動向",
        "Yahoo TWD=X", "1 個月 %", c, 3))
    s5 = sum(x[1] for x in foreign[-5:]) if foreign else None
    s20 = sum(x[1] for x in foreign[-20:]) if foreign else None
    out.append(ind("foreign", "taiwan", "外資買賣超（上市）", foreign, "億元", "info", "",
        "看連續方向是否與匯率同向", "證交所 BFI82U", "近 5 日合計", s5, 1,
        note=f"近 20 日合計 {s20:+.0f} 億" if s20 is not None else None))
    c = pct_chg(margin, 20)
    out.append(ind("margin", "taiwan", "融資餘額（上市）", margin, "億元", level(abs(c) if c is not None else None, R["margin_chg20d"]),
        f"20 日變動 ≥ {R['margin_chg20d']['watch']}%", "大增＝散戶槓桿升溫；大減＝斬倉",
        "證交所 MI_MARGN", "20 日 %", c, 1))

    # ---------------- 資料新鮮度 ----------------
    ref_day = last_us_session()
    tw_ref = last_tw_session()
    for i in out:
        if not i["date"]:
            i["stale"], i["age_bd"] = False, None
            continue
        if i["group"] == "taiwan":
            age = tw_bdays_between(i["date"], tw_ref)
        else:
            age = bdays_between(i["date"], ref_day)
        lim = SLOW_DAILY.get(i["id"], MAX_LAG_BD.get(i["group"], 2))
        i["age_bd"], i["stale"] = age, age > lim

    # ---------------- 階段建議 ----------------
    by = {i["id"]: i for i in out}
    reasons2, reasons1 = [], []
    if by["vix"]["value"] is not None and by["vix"]["value"] >= R["vix"]["alert"]:
        reasons2.append("VIX ≥ 40")
    if by["srf"]["status"] == "alert":
        reasons2.append("SRF 非月底大量使用")
    if by["sofr_iorb"]["status"] == "alert":
        reasons2.append("SOFR−IORB 非月底急升")
    if by["hy"]["status"] == "alert":
        reasons1.append("HY OAS 快速擴大")
    if by["stlfsi"]["status"] == "alert":
        reasons1.append("STLFSI4 > 0 且上升")
    if by["vix"]["value"] is not None and by["vix"]["value"] >= R["vix"]["watch"]:
        reasons1.append("VIX ≥ 30")
    if reasons2:
        stage = {"level": 2, "name": "急性壓力", "action": "不接刀；確認現金與流動性；不做不可逆的決定", "reasons": reasons2 + reasons1}
    elif reasons1:
        stage = {"level": 1, "name": "壓力上升", "action": "紅隊暫停加碼；藍隊不動；整理進場清單", "reasons": reasons1}
    else:
        stage = {"level": 0, "name": "常態", "action": "照常規配置，紅隊等均線", "reasons": []}

    stage_ids = ["hy", "vix", "sofr_iorb", "srf", "stlfsi"]
    stale_stage = [i["name"] for i in out if i["id"] in stage_ids and i.get("stale")]
    stage["stale"] = stale_stage
    core_dates = [i["date"] for i in out if i["id"] in ("hy", "vix", "sofr_iorb", "fg") and i["date"]]
    payload = {
        "updated": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
        "core_asof": min(core_dates) if core_dates else None,
        "last_session": ref_day.isoformat(),
        "stage": stage,
        "indicators": out,
    }
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "docs", "watch_data.json")
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False)
    print(f"完成 → {path}  階段 {stage['level']}")


if __name__ == "__main__":
    main()
