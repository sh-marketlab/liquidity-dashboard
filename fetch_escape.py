"""
台美股「逃生／回升」檢查表 —— 每日自動判定
改編自 money_crazybird 的暴跌檢查清單（韓國版），把韓國專屬項目換成台股：
  #6 三星/海力士獲利共識 → 台積電獲利共識（TSM ADR，Yahoo 分析師預估）
  #9 外資 KOSPI 買賣超   → 外資台股現貨買賣超（證交所）
  輔助/h/R6 韓元、KOSPI   → 新台幣、加權指數

來源: FRED (FRED_API_KEY)、美國財政部殖利率、Yahoo Finance、yfinance(分析師預估/資本支出)、證交所
手動: docs/escape_manual.json（四大雲端業者明年資本支出指引、重要行程）
輸出: docs/escape_data.json、docs/escape_history.json  →  docs/escape.html 讀取

調整門檻: 只改下面 RULES 區塊的數字即可（GitHub 網頁上直接編輯）。
任何一個來源失敗都不會中斷程式，該指標顯示「無法確認」，且不計入紅燈數。
"""
import csv
import io
import json
import os
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone

from fetch_watch import (FRED_KEY, TODAY, _num, _rows, bdays_between, fred, http_get,
                         last_us_session, twse_days, yahoo)

HERE = os.path.dirname(os.path.abspath(__file__))
DOCS = os.path.join(HERE, "docs")
OUT = os.path.join(DOCS, "escape_data.json")
HIST = os.path.join(DOCS, "escape_history.json")
MANUAL = os.path.join(DOCS, "escape_manual.json")
SP500_CACHE = os.path.join(DOCS, "escape_sp500.json")
TW_CACHE = os.path.join(DOCS, "escape_tw_foreign.json")

# ============================================================
# 門檻設定（原版數字；改這裡就好）
# ============================================================
RULES = {
    # 11 項主訊號
    "us10y_red": 5.0,                 # 1. 10 年期 > 5% 紅
    "y2_ffr_red": 0.0,                # 2. 2 年期 − 利率上限 > 0 紅
    "hy_from_low": {"yellow": 0.3, "red": 0.5},   # 3. HY 較 6 個月低點擴大（百分點）
    "jpy_up_1m": {"yellow": 3.0, "red": 5.0},     # 4. 日圓 1 個月升值（%）
    "unrate_base_month": "2026-08",   # 5. 失業率比較基準月（原版：2026 年 8 月）
    "unrate_from_base_red": 0.3,      #    較基準月上升 ≥ 0.3 百分點 紅；較前月上升 黃
    "tsmc_rev_3m_red": -5.0,          # 6. 台積電明年 EPS 預估 3 個月下修 > 5% 紅
    "breadth_red": 60.0,              # 7. S&P 500 站上 200 日線比例 < 60% 紅
    "mag7_rev_30d_red": 0.0,          # 8. 七巨頭明年 EPS 預估合計 30 天變化 < 0（轉下修）紅
    "tw_foreign_sell_streak": 5,      # 9. 外資連續賣超 ≥ 5 日 → 黃
    "tw_foreign_sell_10d": -1000.0,   #    或近 10 日累計賣超 ≤ −1000 億 → 黃
    "core_cpi": {"green_below": 0.2, "red_at": 0.3},  # 10. 核心 CPI 月增率（四捨五入到 0.1，與官方公布一致）
    "capex_red": 20.0,                # 11. 四大雲端明年資本支出成長 < 20% 紅（四家平均）
    "capex_manual_stale_days": 120,   #     手動指引超過幾天沒更新就標「過期」

    # 第 3 階段惡化訊號
    "a_hy_level": 3.5, "a_hy_from_low": 1.0,
    "b_vix": 25.0, "b_days": 2,
    "c_spx_below_200_days": 3,
    "d_core_cpi": 0.3,
    "e_us10y_days": 10,
    "f_capex_count": 2,
    "g_sahm": 0.3, "g_claims": 250000,
    "h_twd_dep_1m": 3.0,              # 台股版：加權 < 200 日線且新台幣 1 個月貶值 ≥ 3%（原版韓元 5%，台幣波動小）

    # 第 4 階段危險訊號
    "d1_hy_level": 4.0, "d1_hy_from_low": 1.5,
    "d2_vix": 30.0, "d2_days": 3,
    "d3_below_200_pct": 5.0,
    "d4_sahm": 0.5,
    "d5_nfci": 0.0,

    # 回升訊號
    "r1_vix": 25.0, "r1_days": 5,
    "r2_from_high": 0.5,
    "r3_days": 3,
    "r4_breadth": 60.0,
    "r5_core_cpi": 0.2,
    "r6_buy_streak": 5,
}

MAG7 = ["AAPL", "MSFT", "GOOGL", "AMZN", "META", "NVDA", "TSLA"]
HYPERSCALERS = ["MSFT", "GOOGL", "META", "AMZN"]
SPARK_N = 130

# 正常最多落後幾個工作日；超過就標「資料過期」
MAX_LAG = {"daily": 1, "hy": 2, "weekly": 10, "monthly": 70, "est": 3, "tw": 2}


# ============================================================
# 抓資料
# ============================================================
def safe(label, fn, *a, default=None, **kw):
    try:
        v = fn(*a, **kw)
        n = len(v) if hasattr(v, "__len__") else "ok"
        print(f"  {label}: {n}")
        return v
    except Exception as e:  # noqa: BLE001
        print(f"  {label} 失敗: {e}")
        return [] if default is None else default


def treasury_yields(cols=("2 Yr", "10 Yr")):
    """美國財政部每日殖利率（收盤當天公布，比 FRED 早一天）"""
    out = {c: [] for c in cols}
    for yr in (TODAY.year - 1, TODAY.year):
        url = ("https://home.treasury.gov/resource-center/data-chart-center/interest-rates/"
               f"daily-treasury-rates.csv/{yr}/all?type=daily_treasury_yield_curve&field_tdr_date_value={yr}&page&_format=csv")
        rows = list(csv.reader(io.StringIO(http_get(url))))
        head = [h.strip() for h in rows[0]]
        idx = {c: head.index(c) for c in cols}
        for r in rows[1:]:
            d = datetime.strptime(r[0].strip(), "%m/%d/%Y").strftime("%Y-%m-%d")
            for c, i in idx.items():
                v = _num(r[i]) if len(r) > i else None
                if v is not None:
                    out[c].append([d, v])
    return {c: sorted(v) for c, v in out.items()}


def fresher(a, b):
    """兩個來源取最新日期較晚者"""
    if a and (not b or a[-1][0] >= b[-1][0]):
        return a
    return b


def sp500_tickers():
    try:
        txt = http_get("https://raw.githubusercontent.com/datasets/s-and-p-500-companies/main/data/constituents.csv")
        t = sorted(r["Symbol"].strip().replace(".", "-") for r in csv.DictReader(io.StringIO(txt)) if r.get("Symbol"))
        if len(t) > 450:
            with open(SP500_CACHE, "w", encoding="utf-8") as fh:
                json.dump({"source": "github.com/datasets/s-and-p-500-companies",
                           "updated": TODAY.isoformat(), "tickers": t}, fh, ensure_ascii=False)
            return t
    except Exception as e:  # noqa: BLE001
        print(f"  成分股清單更新失敗，用快取: {e}")
    with open(SP500_CACHE, encoding="utf-8") as fh:
        return json.load(fh)["tickers"]


def breadth_200():
    """S&P 500 成分股收盤 > 200 日均線的比例（自己用 Yahoo 日線算）"""
    tickers = sp500_tickers()

    def one(t):
        for attempt in range(2):
            try:
                s = yahoo(t, "1y")
                if len(s) >= 200:
                    return s[-1][1] > sum(x[1] for x in s[-200:]) / 200, s[-1][0]
                return None
            except Exception:  # noqa: BLE001
                time.sleep(1.5 * (attempt + 1))
        return None

    with ThreadPoolExecutor(max_workers=6) as ex:
        res = [r for r in ex.map(one, tickers) if r is not None]
    if not res:
        return None
    dates = sorted(r[1] for r in res)
    latest = dates[-1]
    res = [r for r in res if r[1] == latest]   # 只算最新交易日有資料的
    return {"pct": round(sum(1 for r in res if r[0]) / len(res) * 100, 1),
            "n": len(res), "total": len(tickers), "date": latest}


def _yf():
    import yfinance as yf  # 只有這段需要 yfinance
    return yf


def eps_trend(tk, back_col):
    """回傳 (明年 EPS 預估現值, back_col 時的值)；back_col = '30daysAgo' / '90daysAgo'"""
    df = _yf().Ticker(tk).get_eps_trend()
    row = df.loc["+1y"]
    cur, back = float(row["current"]), float(row[back_col])
    if cur != cur or back != back:   # NaN
        raise ValueError("缺值")
    return cur, back


def capex_auto(tk):
    """實際資本支出年增率（最新一季 vs 去年同季；不是指引，只當近似值）"""
    cf = _yf().Ticker(tk).quarterly_cashflow
    label = next((i for i in cf.index if str(i).replace(" ", "").lower() == "capitalexpenditure"), None)
    if label is None:
        raise ValueError("找不到 Capital Expenditure")
    s = cf.loc[label].dropna()
    s = s.sort_index(ascending=False)
    if len(s) < 5 or float(s.iloc[4]) == 0:
        raise ValueError("季資料不足 5 季")
    return round((abs(float(s.iloc[0])) / abs(float(s.iloc[4])) - 1) * 100, 1), str(s.index[0])[:10]


def twse_foreign(n=25):
    """外資及陸資(不含外資自營商)買賣差額，億元；有快取，每天只補新的日子"""
    try:
        with open(TW_CACHE, encoding="utf-8") as fh:
            cache = json.load(fh)
    except (OSError, ValueError):
        cache = {"data": {}, "closed": []}
    data, closed = cache.get("data", {}), set(cache.get("closed", []))
    recent = {d.isoformat() for d in twse_days(3)}
    for d in twse_days(n):
        k = d.isoformat()
        if (k in data or k in closed) and k not in recent:
            continue
        try:
            j = json.loads(http_get("https://www.twse.com.tw/rwd/zh/fund/BFI82U?type=day&dayDate="
                                    f"{d.strftime('%Y%m%d')}&response=json"))
            if j.get("stat") == "OK":
                for r in _rows(j.get("data", [])):
                    if str(r[0]).strip().startswith("外資及陸資"):
                        v = _num(r[-1])
                        if v is not None:
                            data[k] = round(v / 1e8, 1)
                        break
            elif k not in recent:
                closed.add(k)
        except Exception as e:  # noqa: BLE001
            print(f"  證交所 {k} 失敗: {e}")
        time.sleep(1.2)
    keep = sorted(data)[-80:]
    cache = {"data": {k: data[k] for k in keep}, "closed": sorted(c for c in closed if c >= keep[0])[-40:] if keep else []}
    with open(TW_CACHE, "w", encoding="utf-8") as fh:
        json.dump(cache, fh, ensure_ascii=False)
    return [[k, cache["data"][k]] for k in keep]


def load_manual():
    try:
        with open(MANUAL, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError) as e:
        print(f"  手動檔讀取失敗: {e}")
        return {}


# ============================================================
# 計算工具
# ============================================================
def lastv(s):
    return s[-1][1] if s else None


def lastd(s):
    return s[-1][0] if s else None


def sma_series(s, n):
    out, run = [], 0.0
    for i, (d, v) in enumerate(s):
        run += v
        if i >= n:
            run -= s[i - n][1]
        if i >= n - 1:
            out.append([d, run / n])
    return out


def streak(vals, cond):
    """從最新往回數，連續符合 cond 的筆數"""
    n = 0
    for v in reversed(vals):
        if cond(v):
            n += 1
        else:
            break
    return n


def pct_back(s, back):
    if len(s) <= back or not s[-1 - back][1]:
        return None
    return (s[-1][1] / s[-1 - back][1] - 1) * 100


def step_join(series, steps):
    """把階梯序列（利率上限）往前補值對到 series 的每一天，回傳 series − steps"""
    steps, out, k, cur = sorted(steps), [], 0, None
    for d, v in series:
        while k < len(steps) and steps[k][0] <= d:
            cur = steps[k][1]
            k += 1
        if cur is not None:
            out.append([d, round(v - cur, 3)])
    return out


def above_ma(px, n):
    """回傳與 MA 對齊的 [(date, close, ma)]"""
    ma = sma_series(px, n)
    m = dict((d, v) for d, v in ma)
    return [(d, v, m[d]) for d, v in px if d in m]


def mom_core_cpi(idx):
    """CPILFESL 指數 → 月增率 %（兩位小數與四捨五入一位）"""
    out = []
    for i in range(1, len(idx)):
        if idx[i - 1][1]:
            out.append([idx[i][0], round((idx[i][1] / idx[i - 1][1] - 1) * 100, 3)])
    return out


def r1(x):
    """官方公布的四捨五入（0.25 → 0.3）"""
    return None if x is None else float(f"{x + 1e-9:.1f}")


def stale_of(d, kind, ref_us, ref_tw):
    if not d:
        return False
    ref = ref_tw if kind == "tw" else ref_us
    return bdays_between(d[:10], ref) > MAX_LAG[kind]


# ============================================================
# 判定（純函式，tests/test_escape.py 會直接測這段）
# ============================================================
def evaluate(x, R=RULES):
    """x: 已整理好的數值 dict（見 build_inputs）。回傳 signals / deter / danger / recov / 階段"""
    sig = {}

    def put(id_, status, **kw):
        sig[id_] = {"status": status if status else "na", **kw}

    # 1. 10 年期
    v = x.get("us10y")
    put("us10y", None if v is None else ("red" if v > R["us10y_red"] else "green"),
        detail=f"連續 >5% 共 {x.get('us10y_days_above', 0)} 個交易日")
    # 2. 2Y − 利率上限
    v = x.get("y2_ffr")
    put("y2_ffr", None if v is None else ("red" if v > R["y2_ffr_red"] else "green"))
    # 3. HY
    v, lo = x.get("hy"), x.get("hy_low6m")
    if v is None or lo is None:
        put("hy", None)
    else:
        dd = v - lo
        put("hy", "red" if dd >= R["hy_from_low"]["red"] else "yellow" if dd >= R["hy_from_low"]["yellow"] else "green",
            detail=f"6 個月低點 {lo:.2f}%，近 3 個月高點 {x.get('hy_high3m', v):.2f}%，較低點 {dd:+.2f}")
    # 4. 日圓
    v = x.get("jpy_up_1m")
    put("usdjpy", None if v is None else ("red" if v >= R["jpy_up_1m"]["red"] else
                                          "yellow" if v >= R["jpy_up_1m"]["yellow"] else "green"),
        detail=None if v is None else f"日圓 1 個月 {'升值' if v >= 0 else '貶值'} {abs(v):.1f}%")
    # 5. 失業率
    u, up, base = x.get("unrate"), x.get("unrate_prev"), x.get("unrate_base")
    if u is None:
        put("unrate", None)
    else:
        st = "green"
        if up is not None and u > up:
            st = "yellow"
        if base is not None and u - base >= R["unrate_from_base_red"] - 1e-9:
            st = "red"
        put("unrate", st, detail=f"前月 {up}%、基準月 {R['unrate_base_month']} {base}%")
    # 6. 台積電
    v = x.get("tsmc_rev_3m")
    put("tsmc", None if v is None else ("red" if v < R["tsmc_rev_3m_red"] else "green"))
    # 7. 市場寬度
    v = x.get("breadth")
    put("breadth", None if v is None else ("red" if v < R["breadth_red"] else "green"),
        detail=x.get("breadth_note"))
    # 8. 七巨頭
    v = x.get("mag7_rev_30d")
    put("mag7", None if v is None else ("red" if v < R["mag7_rev_30d_red"] else "green"),
        detail=x.get("mag7_note"))
    # 9. 外資台股
    s_sell, s_buy, cum10 = x.get("tw_sell_streak"), x.get("tw_buy_streak"), x.get("tw_cum10")
    if s_sell is None:
        put("tw_foreign", None)
    else:
        big = s_sell >= R["tw_foreign_sell_streak"] or (cum10 is not None and cum10 <= R["tw_foreign_sell_10d"])
        put("tw_foreign", "yellow" if big else "green",
            detail=(f"連續賣超 {s_sell} 日" if s_sell else f"連續買超 {s_buy} 日")
            + (f"，近 10 日累計 {cum10:+,.0f} 億" if cum10 is not None else ""))
    # 10. 核心 CPI
    v = x.get("core_cpi_r")
    put("core_cpi", None if v is None else ("red" if v >= R["core_cpi"]["red_at"] else
                                            "green" if v < R["core_cpi"]["green_below"] else "yellow"))
    # 11. 資本支出
    v = x.get("capex_avg")
    put("capex", None if v is None else ("red" if v < R["capex_red"] else "green"), detail=x.get("capex_note"))

    reds = [k for k, s in sig.items() if s["status"] == "red"]
    hy_red = sig["hy"]["status"] == "red"

    def chk(cond_list):
        """cond_list: [(id, 文字, True/False/None, 細節)]"""
        return [{"id": i, "text": t, "on": on, "detail": dt} for i, t, on, dt in cond_list]

    hy, lo, hi = x.get("hy"), x.get("hy_low6m"), x.get("hy_high3m")
    vix = x.get("vix_recent") or []
    below = x.get("spx_below200_recent") or []   # True=收盤在 200 日線下
    sahm, claims, nfci = x.get("sahm"), x.get("claims4w"), x.get("nfci")
    cpi = x.get("core_cpi_r")

    def all_last(lst, k, f):
        return None if len(lst) < k else all(f(v) for v in lst[-k:])

    def any_true(*v):
        if any(z is True for z in v):
            return True
        return None if all(z is None for z in v) else False

    deter = chk([
        ("a", f"高收益債利差 ≥ {R['a_hy_level']}% 或較 6 個月低點 ≥ +{R['a_hy_from_low']}",
         None if hy is None else any_true(hy >= R["a_hy_level"], lo is not None and hy - lo >= R["a_hy_from_low"]), None),
        ("b", f"VIX ≥ {R['b_vix']:.0f} 連續 {R['b_days']} 日", all_last(vix, R["b_days"], lambda v: v >= R["b_vix"]), None),
        ("c", f"S&P 500 收盤低於 200 日線連續 {R['c_spx_below_200_days']} 日",
         all_last(below, R["c_spx_below_200_days"], bool), None),
        ("d", f"核心 CPI 月增率 ≥ {R['d_core_cpi']}%", None if cpi is None else cpi >= R["d_core_cpi"], None),
        ("e", f"10 年期 > 5% 連續 {R['e_us10y_days']} 日",
         None if x.get("us10y") is None else x.get("us10y_days_above", 0) >= R["e_us10y_days"], None),
        ("f", f"{R['f_capex_count']} 家以上雲端業者資本支出成長 < {R['capex_red']:.0f}%",
         None if x.get("capex_below_n") is None else x["capex_below_n"] >= R["f_capex_count"], None),
        ("g", f"薩姆規則 ≥ {R['g_sahm']} 或初領失業金 4 週均 ≥ {R['g_claims']/1e4:.0f} 萬",
         any_true(None if sahm is None else sahm >= R["g_sahm"], None if claims is None else claims >= R["g_claims"]), None),
        ("h", f"加權指數 < 200 日線 且 新台幣 1 個月貶值 ≥ {R['h_twd_dep_1m']}%",
         None if x.get("twii_below200") is None or x.get("twd_dep_1m") is None
         else bool(x["twii_below200"] and x["twd_dep_1m"] >= R["h_twd_dep_1m"]), None),
    ])
    spx, ma50, ma200 = x.get("spx"), x.get("spx_ma50"), x.get("spx_ma200")
    danger = chk([
        ("①", f"高收益債利差 ≥ {R['d1_hy_level']}% 或較 6 個月低點 ≥ +{R['d1_hy_from_low']}",
         None if hy is None else any_true(hy >= R["d1_hy_level"], lo is not None and hy - lo >= R["d1_hy_from_low"]), None),
        ("②", f"VIX ≥ {R['d2_vix']:.0f} 連續 {R['d2_days']} 日", all_last(vix, R["d2_days"], lambda v: v >= R["d2_vix"]), None),
        ("③", f"S&P 500 死亡交叉，或低於 200 日線 {R['d3_below_200_pct']:.0f}% 以上",
         None if None in (spx, ma50, ma200) else bool(ma50 < ma200 or spx <= ma200 * (1 - R["d3_below_200_pct"] / 100)), None),
        ("④", f"薩姆規則 ≥ {R['d4_sahm']}", None if sahm is None else sahm >= R["d4_sahm"], None),
        ("⑤", f"NFCI ≥ {R['d5_nfci']:.0f}", None if nfci is None else nfci >= R["d5_nfci"], None),
    ])
    above = [not b for b in below]
    recov = chk([
        ("R1", f"VIX < {R['r1_vix']:.0f} 連續 {R['r1_days']} 日", all_last(vix, R["r1_days"], lambda v: v < R["r1_vix"]), None),
        ("R2", f"高收益債較近期高點收斂 ≥ {R['r2_from_high']}，或較 6 個月低點 < +{R['hy_from_low']['red']}",
         None if hy is None else any_true(hi is not None and hi - hy >= R["r2_from_high"],
                                         lo is not None and hy - lo < R["hy_from_low"]["red"]), None),
        ("R3", f"S&P 500 收盤站上 200 日線連續 {R['r3_days']} 日", all_last(above, R["r3_days"], bool), None),
        ("R4", f"站上 200 日線比例 ≥ {R['r4_breadth']:.0f}%",
         None if x.get("breadth") is None else x["breadth"] >= R["r4_breadth"], None),
        ("R5", f"2 年期 ≤ 利率上限，或核心 CPI ≤ {R['r5_core_cpi']}%",
         any_true(None if x.get("y2_ffr") is None else x["y2_ffr"] <= 0,
                  None if cpi is None else cpi <= R["r5_core_cpi"]), None),
        ("R6", f"外資連續 {R['r6_buy_streak']} 日買超台股，或加權指數站上 200 日線",
         any_true(None if x.get("tw_buy_streak") is None else x["tw_buy_streak"] >= R["r6_buy_streak"],
                  None if x.get("twii_below200") is None else not x["twii_below200"]), None),
    ])

    n_det = sum(1 for c in deter if c["on"])
    n_dan = sum(1 for c in danger if c["on"])
    n_rec = sum(1 for c in recov if c["on"])
    r3 = next(c for c in recov if c["id"] == "R3")["on"] is True

    esc = 1
    if len(reds) >= 3 or hy_red:
        esc = 2
    if esc == 2 and n_det >= 2:
        esc = 3
    if n_dan >= 2:
        esc = 4
    if n_rec >= 5 and r3 and esc <= 2:
        rec = 3
    elif n_rec >= 4 and n_dan == 0:
        rec = 2
    elif n_rec >= 2:
        rec = 1
    else:
        rec = 0

    # 情境
    cpi_green = sig["core_cpi"]["status"] == "green"
    y2f = x.get("y2_ffr")
    if hy_red:
        scen = ("credit", "2007 次貸／2000 網路泡沫型", "信用市場出問題，歷史上跌幅大")
    elif y2f is not None and y2f > 0 and sig["core_cpi"]["status"] in ("yellow", "red"):
        scen = ("rate", "2022 空頭型", "公司債還穩，但市場在定價升息、通膨壓不下來：跌得淺但拖很久")
    elif y2f is not None and y2f <= 0 and cpi_green:
        scen = ("bull", "2023 年 10 月型", "市場定價降息、通膨低：偏多頭環境")
    else:
        scen = ("mixed", "無明確對應", "三種歷史情境的條件都不完全成立")

    return {"signals": sig, "reds": reds, "deter": deter, "danger": danger, "recov": recov,
            "n_det": n_det, "n_dan": n_dan, "n_rec": n_rec, "r3": r3,
            "escape": esc, "recovery": rec, "scenario": scen}


ESC_NAME = {1: "觀察", 2: "警戒", 3: "防禦準備", 4: "迴避"}
ESC_ACT = {1: "持續觀察", 2: "減少新買進、檢視現金比重", 3: "擬定降低風險資產的計畫", 4: "實際評估減碼風險資產"}
REC_NAME = {0: "下跌進行中", 1: "探底", 2: "評估分批重新進場", 3: "趨勢回升"}


def reading(e, r):
    if e >= 3:
        return "風險升高、尚未見底，照計畫降低風險資產" if r <= 1 else "仍在高風險區，但出現回升跡象，分辨是反彈還是轉折"
    if e == 2:
        return "警戒中：暫停新買進、檢查現金" if r <= 1 else "警戒但結構轉好，等紅燈收斂"
    if r == 3:
        return "多頭常態，紅燈不多且趨勢完好"
    if r == 2:
        return "風險低，回升訊號多數成立"
    return "紅燈不多，但價格與波動面偏弱，持續觀察"


def next_steps(ev):
    e, r, nd, nn, nr = ev["escape"], ev["recovery"], ev["n_det"], ev["n_dan"], ev["n_rec"]
    if e == 1:
        en = f"升到 2：紅燈 ≥ 3（目前 {len(ev['reds'])}）或高收益債轉紅"
    elif e == 2:
        en = f"升到 3：惡化訊號 ≥ 2（目前 {nd}）"
    elif e == 3:
        en = f"升到 4：危險訊號 ≥ 2（目前 {nn}）"
    else:
        en = "已是最高階段"
    if e < 3 and nn > 0:
        en += f"；危險訊號已有 {nn}/2"
    if r == 0:
        rn = f"到 1：回升訊號 ≥ 2（目前 {nr}）"
    elif r == 1:
        rn = f"到 2：回升訊號 ≥ 4（目前 {nr}）且危險訊號 0（目前 {nn}）"
    elif r == 2:
        rn = f"到 3：回升訊號 ≥ 5（目前 {nr}）、含 R3{'（已成立）' if ev['r3'] else '（未成立）'}、逃生 ≤ 2"
    else:
        rn = "已是最高階段"
    return en, rn


# ============================================================
# 主程式
# ============================================================
def main():
    if not FRED_KEY:
        print("錯誤: 未設定 FRED_API_KEY")
        raise SystemExit(1)
    ref_us = last_us_session()
    ref_tw = TODAY if TODAY.weekday() < 5 else TODAY - timedelta(days=TODAY.weekday() - 4)
    manual = load_manual()

    print("FRED...")
    f = {sid: safe(sid, fred, sid, years=3) for sid in [
        "DGS10", "DGS2", "DFEDTARU", "BAMLH0A0HYM2", "UNRATE", "CPILFESL", "SAHMREALTIME",
        "IC4WSA", "NFCI", "VIXCLS", "SP500", "DEXJPUS", "DEXTAUS"]}
    print("財政部 / Yahoo...")
    ty = safe("財政部殖利率", treasury_yields, default={})
    y10 = fresher(ty.get("10 Yr", []), f["DGS10"])
    y2 = fresher(ty.get("2 Yr", []), f["DGS2"])
    spx = fresher(safe("^GSPC", yahoo, "^GSPC", "2y"), f["SP500"])
    vix = fresher(safe("^VIX", yahoo, "^VIX", "1y"), f["VIXCLS"])
    jpy = fresher(safe("JPY=X", yahoo, "JPY=X", "1y"), f["DEXJPUS"])
    twd = fresher(safe("TWD=X", yahoo, "TWD=X", "1y"), f["DEXTAUS"])
    twii = safe("^TWII", yahoo, "^TWII", "2y")
    print("市場寬度（S&P 500 成分股）...")
    br = safe("breadth", breadth_200, default={}) or {}
    print("分析師預估 / 資本支出 (yfinance)...")
    tsmc = safe("TSM eps", eps_trend, "TSM", "90daysAgo", default=())
    mag = {t: safe(f"{t} eps", eps_trend, t, "30daysAgo", default=()) for t in MAG7}
    cap_auto = {t: safe(f"{t} capex", capex_auto, t, default=()) for t in HYPERSCALERS}
    print("證交所外資...")
    twf = safe("外資", twse_foreign, default=[])

    R = RULES
    x = {}
    # 1 / e
    x["us10y"] = lastv(y10)
    x["us10y_days_above"] = streak([v for _, v in y10], lambda v: v > R["us10y_red"])
    # 2
    y2f = step_join(y2, f["DFEDTARU"])
    x["y2_ffr"] = lastv(y2f)
    # 3
    hy = f["BAMLH0A0HYM2"]
    if hy:
        x["hy"] = hy[-1][1]
        x["hy_low6m"] = min(v for _, v in hy[-126:])
        x["hy_high3m"] = max(v for _, v in hy[-63:])
    # 4（USD/JPY 下跌 = 日圓升值）
    p = pct_back(jpy, 21)
    x["jpy_up_1m"] = None if p is None else round(-p, 2)
    # 5
    un = f["UNRATE"]
    if un:
        x["unrate"] = un[-1][1]
        x["unrate_prev"] = un[-2][1] if len(un) > 1 else None
        base = [v for d, v in un if d.startswith(R["unrate_base_month"])]
        x["unrate_base"] = base[0] if base else min(v for _, v in un[-12:])
    # 6
    if tsmc:
        x["tsmc_rev_3m"] = round((tsmc[0] / tsmc[1] - 1) * 100, 2)
    # 7
    if br and br.get("n", 0) >= 0.8 * br.get("total", 1):
        x["breadth"] = br["pct"]
        x["breadth_note"] = f"{br['n']}/{br['total']} 檔有資料"
    # 8
    ok = {t: v for t, v in mag.items() if v}
    if len(ok) >= 5:
        cur, back = sum(v[0] for v in ok.values()), sum(v[1] for v in ok.values())
        x["mag7_rev_30d"] = round((cur / back - 1) * 100, 2)
        downs = [t for t, v in ok.items() if v[0] < v[1]]
        x["mag7_note"] = f"{len(ok)} 家合計；下修中：{', '.join(downs) if downs else '無'}"
    # 9
    if twf:
        vals = [v for _, v in twf]
        x["tw_sell_streak"] = streak(vals, lambda v: v < 0)
        x["tw_buy_streak"] = streak(vals, lambda v: v > 0)
        x["tw_cum10"] = round(sum(vals[-10:]), 1)
    # 10
    cm = mom_core_cpi(f["CPILFESL"])
    x["core_cpi"] = lastv(cm)
    x["core_cpi_r"] = r1(x["core_cpi"])
    # 11 / f：手動指引優先，沒有就用實際季增率當近似
    guide = (manual.get("capex_guidance") or {}).get("items") or {}
    rates, parts, approx, stale_manual = {}, [], False, []
    for t in HYPERSCALERS:
        g = guide.get(t) or {}
        if g.get("growth_pct") is not None:
            rates[t] = float(g["growth_pct"])
            parts.append(f"{t} {rates[t]:+.0f}%")
            upd = g.get("updated")
            if upd and (TODAY - date.fromisoformat(upd)).days > R["capex_manual_stale_days"]:
                stale_manual.append(t)
        elif cap_auto.get(t):
            rates[t] = cap_auto[t][0]
            parts.append(f"{t} {rates[t]:+.0f}%*")
            approx = True
    if len(rates) >= 3:
        x["capex_avg"] = round(sum(rates.values()) / len(rates), 1)
        x["capex_below_n"] = sum(1 for v in rates.values() if v < R["capex_red"])
        x["capex_note"] = "、".join(parts) + ("（*＝最新一季實際年增率，非明年指引，近似值）" if approx else "") \
            + (f"；手動指引過期：{', '.join(stale_manual)}" if stale_manual else "")
    # 輔助
    vix_v = [v for _, v in vix]
    x["vix_recent"] = vix_v[-10:]
    sx = above_ma(spx, 200)
    x["spx_below200_recent"] = [c < m for _, c, m in sx[-10:]]
    if sx:
        x["spx"], x["spx_ma200"] = sx[-1][1], sx[-1][2]
    m50 = sma_series(spx, 50)
    x["spx_ma50"] = lastv(m50)
    x["sahm"], x["claims4w"], x["nfci"] = lastv(f["SAHMREALTIME"]), lastv(f["IC4WSA"]), lastv(f["NFCI"])
    tw = above_ma(twii, 200)
    if tw:
        x["twii"], x["twii_ma200"] = tw[-1][1], tw[-1][2]
        x["twii_below200"] = tw[-1][1] < tw[-1][2]
    p = pct_back(twd, 21)
    x["twd_dep_1m"] = None if p is None else round(p, 2)   # USD/TWD 上升 = 台幣貶值

    ev = evaluate(x)
    en, rn = next_steps(ev)

    # -------- 組輸出 --------
    meta = {
        "us10y": ("1", "美 10 年期殖利率", y10, "%", "daily", "> 5% 紅", "財政部 / FRED DGS10", 2),
        "y2_ffr": ("2", "2 年期 − 利率上限", y2f, "百分點", "daily", "> 0 紅（市場在定價升息）", "財政部 2Y − FRED DFEDTARU", 2),
        "hy": ("3", "高收益債利差", hy, "%", "hy", "較 6 個月低點 +0.5 紅、+0.3~0.5 黃", "FRED BAMLH0A0HYM2", 2),
        "usdjpy": ("4", "USD/JPY（日圓）", jpy, "", "daily", "日圓 1 個月升值 ≥5% 紅、3~5% 黃", "Yahoo JPY=X / FRED", 2),
        "unrate": ("5", "美國失業率", un, "%", "monthly", f"較前月上升 黃；較 {R['unrate_base_month']} +0.3 紅", "FRED UNRATE", 1),
        "tsmc": ("6", "台積電明年 EPS 預估", None, "%（3 個月變化）", "est", "3 個月下修 > 5% 紅", "Yahoo 分析師預估（TSM）", 2),
        "breadth": ("7", "S&P 500 站上 200 日線比例", None, "%", "daily", "< 60% 紅", "Yahoo 成分股日線自算", 1),
        "mag7": ("8", "七巨頭明年 EPS 預估", None, "%（30 天變化）", "est", "轉為下修 紅", "Yahoo 分析師預估", 2),
        "tw_foreign": ("9", "外資台股買賣超", twf, "億元", "tw", "連續賣超 ≥5 日或 10 日累計 ≤ −1000 億 黃", "證交所 BFI82U", 1),
        "core_cpi": ("10", "美國核心 CPI 月增率", cm, "%", "monthly", "≥ 0.3 紅、< 0.2 綠", "FRED CPILFESL 自算", 2),
        "capex": ("11", "四大雲端明年資本支出成長", None, "%（平均）", "est", "< 20% 紅", "手動指引 / yfinance", 1),
    }
    single = {"tsmc": x.get("tsmc_rev_3m"), "breadth": x.get("breadth"), "mag7": x.get("mag7_rev_30d"),
              "capex": x.get("capex_avg")}
    single_date = {"tsmc": TODAY.isoformat() if tsmc else None, "breadth": br.get("date"),
                   "mag7": TODAY.isoformat() if x.get("mag7_rev_30d") is not None else None,
                   "capex": TODAY.isoformat() if x.get("capex_avg") is not None else None}
    signals = []
    for id_, (no, name, s, unit, kind, rule, src, dec) in meta.items():
        s_ = s or []
        val = single[id_] if id_ in single else lastv(s_)
        d = single_date[id_] if id_ in single else lastd(s_)
        if id_ == "y2_ffr" and val is not None:
            val = round(val, 2)
        sg = ev["signals"][id_]
        signals.append({
            "id": id_, "no": no, "name": name, "unit": unit,
            "value": None if val is None else round(val, dec),
            "date": d, "status": sg["status"] if val is not None else "na",
            "rule": rule, "detail": sg.get("detail"), "source": src,
            "stale": stale_of(d, kind, ref_us, ref_tw),
            "spark": [[a, round(b, 3)] for a, b in s_[-SPARK_N:]] if s_ else [],
        })
    for sgl in signals:
        if sgl["id"] == "core_cpi" and sgl["value"] is not None:
            sgl["detail"] = f"官方口徑（四捨五入）{x['core_cpi_r']:.1f}%"

    def aux(id_, name, s, unit, dec, detail=None, kind="daily"):
        return {"id": id_, "name": name, "value": None if lastv(s) is None else round(lastv(s), dec),
                "unit": unit, "date": lastd(s), "detail": detail, "stale": stale_of(lastd(s), kind, ref_us, ref_tw),
                "spark": [[a, round(b, 3)] for a, b in s[-SPARK_N:]]}

    spx_detail = None
    if x.get("spx_ma200"):
        spx_detail = (f"50 日 {x['spx_ma50']:,.0f}／200 日 {x['spx_ma200']:,.0f}（"
                      f"{(x['spx'] / x['spx_ma200'] - 1) * 100:+.1f}% vs 200 日）")
    tw_detail = None
    if x.get("twii_ma200"):
        tw_detail = f"200 日 {x['twii_ma200']:,.0f}（{(x['twii'] / x['twii_ma200'] - 1) * 100:+.1f}%）"
    auxs = [
        aux("vix", "VIX", vix, "", 1),
        aux("spx", "S&P 500", spx, "", 0, spx_detail),
        aux("sahm", "薩姆規則", f["SAHMREALTIME"], "", 2, kind="monthly"),
        aux("claims", "初領失業金 4 週均", f["IC4WSA"], "件", 0, kind="weekly"),
        aux("nfci", "芝加哥聯準會 NFCI", f["NFCI"], "", 2, "≥ 0 代表金融條件偏緊", kind="weekly"),
        aux("twii", "台股加權指數", twii, "", 0, tw_detail, kind="tw"),
        aux("twd", "USD/TWD", twd, "", 3, None if x.get("twd_dep_1m") is None else
            f"1 個月 台幣{'貶' if x['twd_dep_1m'] >= 0 else '升'} {abs(x['twd_dep_1m']):.1f}%"),
    ]

    events = sorted([e for e in manual.get("events", []) if e.get("d", "") >= TODAY.isoformat()],
                    key=lambda e: e["d"])[:8]

    hist = []
    try:
        with open(HIST, encoding="utf-8") as fh:
            hist = json.load(fh)
    except (OSError, ValueError):
        pass
    key = ref_us.isoformat()
    entry = {"d": key, "esc": ev["escape"], "rec": ev["recovery"], "reds": len(ev["reds"]),
             "det": ev["n_det"], "dan": ev["n_dan"], "nrec": ev["n_rec"], "scen": ev["scenario"][0],
             "spx": x.get("spx"), "twii": x.get("twii")}
    hist = [h for h in hist if h["d"] != key] + [entry]
    hist = sorted(hist, key=lambda h: h["d"])[-750:]
    with open(HIST, "w", encoding="utf-8") as fh:
        json.dump(hist, fh, ensure_ascii=False)

    core_ids = ["hy", "us10y", "y2_ffr", "breadth"]
    na = [s["name"] for s in signals if s["status"] == "na"]
    data = {
        "updated": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
        "last_session": key,
        "summary": {
            "line": f"逃生 {ev['escape']}・{ESC_NAME[ev['escape']]}／回升 {ev['recovery']}・{REC_NAME[ev['recovery']]}："
                    + reading(ev["escape"], ev["recovery"]),
            "escape": {"stage": ev["escape"], "name": ESC_NAME[ev["escape"]], "action": ESC_ACT[ev["escape"]],
                       "next": en},
            "recovery": {"stage": ev["recovery"], "name": REC_NAME[ev["recovery"]], "next": rn},
            "scenario": {"key": ev["scenario"][0], "name": ev["scenario"][1], "desc": ev["scenario"][2]},
            "count": {c: sum(1 for s in signals if s["status"] == c) for c in ("red", "yellow", "green", "na")},
            "na": na,
            "stale": [s["name"] for s in signals if s["stale"] and s["id"] in core_ids],
        },
        "signals": signals,
        "deter": ev["deter"], "danger": ev["danger"], "recov": ev["recov"],
        "aux": auxs,
        "events": events,
        "history": hist[-260:],
        "rules": RULES,
    }
    with open(OUT, "w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=1)
    print(f"完成 → {OUT}\n{data['summary']['line']}")


if __name__ == "__main__":
    main()
