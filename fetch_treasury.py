"""
美債危機監控 - 數據抓取與燈號判斷
來源: FRED (FRED_API_KEY)、Yahoo Finance、財政部 FiscalData API
輸出: docs/treasury_data.json  →  docs/treasury.html 讀取
黑天鵝手動狀態: docs/treasury_manual.json (在 GitHub 網頁直接編輯，網頁會即時讀取)

調整門檻: 只要改下面 RULES 區塊的數字即可。
任何一個來源失敗都不會中斷整支程式，該指標會顯示「無資料」。
"""
import json
import os
import re
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta, timezone

FRED_KEY = os.environ.get("FRED_API_KEY", "").strip()
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
TODAY = date.today()
HERE = os.path.dirname(os.path.abspath(__file__))

# ============================================================
# 門檻設定 (watch = 黃燈「留意」, alert = 紅燈「觸發」)
# ============================================================
RULES = {
    "y30":        {"watch": 5.50, "alert": 5.73, "days": 3},   # 30Y 殖利率 (%)；alert = 2026 年高點，連續站上 days 天
    "y10":        {"watch": 5.20, "alert": 5.37, "days": 3},   # 10Y 殖利率 (%)；alert = 2026 年高點
    "term_prem":  {"watch": 1.30, "alert": 1.50},               # 10Y 期限溢酬 (百分點)
    "steepen":    {"watch": 0.25, "alert": 0.40, "spx_alert": -5.0},  # 5s30s 一個月擴大 (百分點) 且股市下跌
    "triple":     {"watch_days": 2, "alert_days": 3},           # 股、債(TLT)、美元 同日下跌的連續天數
    "move":       {"watch": 130, "alert": 150},
    "auction":    {"watch": 2, "alert": 3, "lookback": 6},      # 長債 (10/20/30Y) 標售連續偏弱場數
    "breakeven":  {"watch": 2.60, "alert": 2.90},               # 5y5y 遠期通膨預期 (%)
    "gold_dxy":   {"gold_3m": 10.0, "dxy_3m": -3.0},            # 金價 3 個月漲 ≥10% 且美元 3 個月跌 ≥3%
    "liq_crash":  {"spx_watch": -2.0, "tlt_watch": -1.5, "spx_alert": -3.0, "tlt_alert": -2.0},  # 同日跌幅 (%)
    "debt_room":  {"watch": 300, "alert": 100},                 # 距債務上限剩餘 (十億美元)
    "debt_limit_fallback": 41104,                               # 法定上限 (十億美元)，DTS 抓不到時用；上限調整後改這裡
    "jgb":        {"watch": 0.30, "alert": 0.50},               # 日本 10Y 三個月上升 (百分點)
    "oil":        {"watch": 15.0, "alert": 30.0},               # 油價一個月漲幅 (%)
    # 反向訊號 (衰退型)：沙姆法則 ≥ 0.5，或 (S&P 從高點跌 ≥15% 且 2Y 一個月降 ≥0.5)
    "recession":  {"sahm": 0.5, "spx_dd": 15.0, "y2_drop": -0.50},
}
# 存續期間 (殖利率變動 1% → 價格約變動 %)，查 iShares 官網更新
DURATION = {"TLT": 16.5, "IEF": 7.3}


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


def fred(series_id, years=3):
    start = (TODAY - timedelta(days=365 * years)).isoformat()
    params = urllib.parse.urlencode({"series_id": series_id, "api_key": FRED_KEY,
                                     "file_type": "json", "observation_start": start})
    data = json.loads(http_get(f"https://api.stlouisfed.org/fred/series/observations?{params}"))
    return [[o["date"], float(o["value"])] for o in data.get("observations", [])
            if o.get("value") not in (".", "", None)]


def yahoo(symbol, rng="2y"):
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{urllib.parse.quote(symbol)}?range={rng}&interval=1d"
    res = json.loads(http_get(url))["chart"]["result"][0]
    out = []
    for t, c in zip(res["timestamp"], res["indicators"]["quote"][0]["close"]):
        if c is not None:
            out.append([datetime.fromtimestamp(t, tz=timezone.utc).strftime("%Y-%m-%d"), round(float(c), 4)])
    return out


FD = "https://api.fiscaldata.treasury.gov/services/api/fiscal_service"


def _f(rec, *keys):
    for k in keys:
        v = rec.get(k)
        try:
            if v not in (None, "", "null"):
                return float(str(v).replace(",", ""))
        except ValueError:
            pass
    return None


def fd_auctions():
    """財政部標售 (近 400 筆，所有券種；之後只留 10/20/30 年)"""
    data = json.loads(http_get(f"{FD}/v1/accounting/od/auctions_query?sort=-auction_date&page[size]=400"))
    rows = []
    for r in data.get("data", []):
        stype = (r.get("security_type") or "").strip()
        if stype not in ("Note", "Notes", "Bond", "Bonds"):
            continue
        total = _f(r, "total_accepted", "offering_amt")
        dealer = _f(r, "primary_dealer_accepted")
        indirect = _f(r, "indirect_bidder_accepted")
        rows.append({
            "date": r.get("auction_date"),
            "term": (r.get("security_term") or "").strip(),
            "orig_term": (r.get("original_security_term") or "").strip(),
            "btc": _f(r, "bid_to_cover_ratio"),
            "high_yield": _f(r, "high_yield"),
            "dealer_pct": round(dealer / total * 100, 1) if dealer and total else None,
            "indirect_pct": round(indirect / total * 100, 1) if indirect and total else None,
        })
    return rows


def fd_debt():
    """每日財政報告 (DTS) 表 III-C：受上限約束的債務與法定上限 (百萬美元 → 十億)"""
    url = f"{FD}/v1/accounting/dts/debt_subject_to_limit?sort=-record_date&page[size]=600"
    data = json.loads(http_get(url))
    by_date = {}
    for r in data.get("data", []):
        cat = (r.get("debt_catg") or "").lower()
        v = _f(r, "close_today_bal")
        if v is None:
            continue
        d = by_date.setdefault(r["record_date"], {})
        if "statutory debt limit" in cat:
            d["limit"] = v / 1000
        elif "subject to limit" in cat and "total" in cat:
            d["subject"] = v / 1000
    hist = sorted([[k, v["subject"]] for k, v in by_date.items() if "subject" in v])
    lim = [v["limit"] for k, v in sorted(by_date.items()) if "limit" in v]
    return {"subject": hist, "limit": lim[-1] if lim else None, "source": "財政部 DTS 表 III-C"}


def fd_debt_fallback():
    """備援：Debt to the Penny 總額 (含少量不受上限約束的債務，略高估)"""
    url = f"{FD}/v2/accounting/od/debt_to_penny?sort=-record_date&page[size]=400"
    data = json.loads(http_get(url))
    hist = sorted([[r["record_date"], float(r["tot_pub_debt_out_amt"]) / 1e9] for r in data.get("data", [])])
    return {"subject": hist, "limit": None, "source": "財政部 Debt to the Penny（備援）"}


def fd_avg_rate():
    """可交易國債平均利率 (月)"""
    url = (f"{FD}/v2/accounting/od/avg_interest_rates?filter=security_desc:eq:Total%20Marketable"
           f"&sort=-record_date&page[size]=72")
    data = json.loads(http_get(url))
    return sorted([[r["record_date"], float(r["avg_interest_rate_amt"])] for r in data.get("data", [])
                   if r.get("avg_interest_rate_amt") not in (None, "", "null")])


# ============================================================
# 計算工具 (純函式，可單元測試)
# ============================================================
def last(s):
    return s[-1] if s else None


def lastv(s):
    return s[-1][1] if s else None


def chg(s, n):
    return round(s[-1][1] - s[-1 - n][1], 4) if s and len(s) > n else None


def pct(s, n):
    if not s or len(s) <= n or not s[-1 - n][1]:
        return None
    return round((s[-1][1] / s[-1 - n][1] - 1) * 100, 2)


def level(v, rule, key_w="watch", key_a="alert"):
    if v is None:
        return "na"
    return "alert" if v >= rule[key_a] else "watch" if v >= rule[key_w] else "ok"


def days_above(s, thr):
    """最後連續幾天 ≥ thr"""
    n = 0
    for _, v in reversed(s or []):
        if v >= thr:
            n += 1
        else:
            break
    return n


def daily_returns(s):
    return {s[i][0]: (s[i][1] / s[i - 1][1] - 1) * 100 for i in range(1, len(s)) if s[i - 1][1]}


def triple_sell(spx, tlt, dxy):
    """股、長債、美元同日下跌：回傳 (連續天數, 近10日發生天數, 5日報酬dict)"""
    rs, rt, rd = daily_returns(spx), daily_returns(tlt), daily_returns(dxy)
    common = sorted(set(rs) & set(rt) & set(rd))
    flags = [(d, rs[d] < 0 and rt[d] < 0 and rd[d] < 0) for d in common]
    streak = 0
    for _, f in reversed(flags):
        if f:
            streak += 1
        else:
            break
    recent = sum(1 for _, f in flags[-10:] if f)
    r5 = {"spx": pct(spx, 5), "tlt": pct(tlt, 5), "dxy": pct(dxy, 5)}
    hist = [[d, 1 if f else 0] for d, f in flags[-60:]]
    return streak, recent, r5, hist


def term_years(term):
    """'30-Year' → 30；'29-Year 11-Month' → 29.92"""
    m = re.match(r"(\d+)-Year(?:\s+(\d+)-Month)?", term or "")
    if not m:
        return None
    return int(m.group(1)) + (int(m.group(2)) / 12 if m.group(2) else 0)


def bucket(row):
    y = term_years(row.get("orig_term") or "") or term_years(row.get("term"))
    if y is None:
        return None
    for b in (10, 20, 30):
        if b - 1.01 <= y <= b + 0.01:
            return f"{b}Y"
    return None


def analyse_auctions(rows, lookback=6):
    """長債標售：每場與同天期前 lookback 場平均比較。
    偏弱 = 投標倍數低於平均 且 交易商承接高於平均 (財政部資料沒有 WI 殖利率，無法算尾差，以此代替)"""
    long_rows = [dict(r, bucket=bucket(r)) for r in rows]
    long_rows = [r for r in long_rows if r["bucket"] and r["btc"]]
    long_rows.sort(key=lambda r: r["date"])
    out = []
    for i, r in enumerate(long_rows):
        prev = [p for p in long_rows[:i] if p["bucket"] == r["bucket"]][-lookback:]
        btcs = [p["btc"] for p in prev if p["btc"]]
        dls = [p["dealer_pct"] for p in prev if p["dealer_pct"] is not None]
        avg_btc = sum(btcs) / len(btcs) if btcs else None
        avg_dl = sum(dls) / len(dls) if dls else None
        low_btc = avg_btc is not None and r["btc"] < avg_btc
        high_dl = avg_dl is not None and r["dealer_pct"] is not None and r["dealer_pct"] > avg_dl
        out.append(dict(r, avg_btc=round(avg_btc, 2) if avg_btc else None,
                        avg_dealer=round(avg_dl, 1) if avg_dl is not None else None,
                        weak=bool(low_btc and high_dl), soft=bool(low_btc or high_dl) and not (low_btc and high_dl),
                        enough=len(prev) >= 3))
    out = [r for r in out if r["enough"]]
    streak = 0
    for r in reversed(out):
        if r["weak"]:
            streak += 1
        else:
            break
    return out[::-1][:15], streak


def drawdown(s, n=252):
    if not s:
        return None
    hi = max(v for _, v in s[-n:])
    return round((s[-1][1] / hi - 1) * 100, 2) if hi else None


def thin(s, step):
    """長序列抽樣給迷你圖用 (保留最後一點)"""
    if not s:
        return []
    out = s[::-1][::step][::-1]
    return out


def ind(id_, name, s, unit, status, rule, what, why, source, chg_label=None, chg_value=None,
        digits=2, note=None, spark=None, forms=(), value=None):
    lv = last(s)
    return {
        "id": id_, "name": name, "unit": unit, "status": status, "rule": rule,
        "what": what, "why": why, "source": source, "forms": list(forms),
        "value": value if value is not None else (lv[1] if lv else None),
        "date": lv[0] if lv else None, "digits": digits,
        "chg_label": chg_label, "chg": chg_value, "note": note,
        "spark": (spark if spark is not None else (s or [])[-130:]),
    }


ORDER = {"na": -1, "ok": 0, "info": 0, "watch": 1, "alert": 2}


def worst(statuses):
    st = [s for s in statuses if s in ORDER]
    if not st:
        return "na"
    return max(st, key=lambda s: ORDER[s])


# ============================================================
# 主計算：raw → payload
# ============================================================
def compute(raw, manual=None):
    R = raw
    out = []
    g = lambda k: R.get(k) or []

    # ---------- ① 長債暴動 ----------
    y30 = g("y30")
    v = lastv(y30)
    above = days_above(y30, RULES["y30"]["alert"])
    st = "na" if v is None else ("alert" if above >= RULES["y30"]["days"] else
                                 "watch" if v >= RULES["y30"]["watch"] else "ok")
    out.append(ind("y30", "30 年期殖利率", y30, "%", st,
        f"≥ {RULES['y30']['watch']}% 黃燈；連續 {RULES['y30']['days']} 天 ≥ {RULES['y30']['alert']}%（2026 高點）紅燈",
        "政府借 30 年要付的利息。", "財政信任的溫度計：大家不信任政府，最先被賣的就是 30 年債。TLT 主要就是這一段。",
        "FRED DGS30", "1 個月", chg(y30, 21), 2,
        note=f"已連續 {above} 天站上 {RULES['y30']['alert']}%" if above else None, forms=["f1"]))

    y10 = g("y10")
    v = lastv(y10)
    above10 = days_above(y10, RULES["y10"]["alert"])
    st = "na" if v is None else ("alert" if above10 >= RULES["y10"]["days"] else
                                 "watch" if v >= RULES["y10"]["watch"] else "ok")
    out.append(ind("y10", "10 年期殖利率", y10, "%", st,
        f"≥ {RULES['y10']['watch']}% 黃燈；連續 {RULES['y10']['days']} 天 ≥ {RULES['y10']['alert']}% 紅燈",
        "政府借 10 年的利息，IEF 主要就是這一段。", "房貸、企業借款都看它，升太高會直接傷到經濟和股市。",
        "FRED DGS10", "1 個月", chg(y10, 21), 2, forms=["f1"]))

    tp = g("term_prem")
    out.append(ind("term_prem", "10 年期限溢酬（Kim-Wright）", tp, "百分點",
        level(lastv(tp), RULES["term_prem"]),
        f"≥ {RULES['term_prem']['watch']} 黃燈；≥ {RULES['term_prem']['alert']} 紅燈",
        "借錢給政府 10 年、而不是每 3 個月借一次，額外要求的「擔心費」。",
        "它升，代表長期利率上漲是因為「不放心」，不是因為經濟好。這是財政型危機最核心的指標。",
        "FRED THREEFYTP10（Fed 理事會模型；紐約 Fed ACM 模型數值會略有不同）", "1 個月", chg(tp, 21), 2,
        forms=["f1"]))

    y5 = g("y5")
    s530 = []
    if y5 and y30:
        d5 = dict(y5)
        s530 = [[d, round(v - d5[d], 3)] for d, v in y30 if d in d5]
    c530 = chg(s530, 21)
    spx = g("spx")
    spx1m = pct(spx, 21)
    st = "info"
    if c530 is not None and spx1m is not None:
        if c530 >= RULES["steepen"]["alert"] and spx1m <= RULES["steepen"]["spx_alert"]:
            st = "alert"
        elif c530 >= RULES["steepen"]["watch"] and spx1m < 0:
            st = "watch"
        else:
            st = "ok"
    out.append(ind("steepen", "5 年－30 年利差（熊市陡峭化）", s530, "百分點", st,
        f"一個月擴大 ≥ {RULES['steepen']['watch']} 且股市下跌 黃燈；≥ {RULES['steepen']['alert']} 且股市跌 ≥ 5% 紅燈",
        "30 年利率減 5 年利率，看長債是不是被「單獨」拋售。",
        "利差擴大＋股市也跌＝市場在逃離長債，不是在等降息。若是利差擴大但股市漲，通常只是經濟好。",
        "FRED DGS30 − DGS5", "1 個月", c530, 2,
        note=f"同期 S&P 500 {spx1m:+.1f}%" if spx1m is not None else None, forms=["f1"]))

    tlt, dxy = g("tlt"), g("dxy")
    streak, recent, r5, thist = triple_sell(spx, tlt, dxy) if (spx and tlt and dxy) else (None, None, {}, [])
    st = "na" if streak is None else ("alert" if streak >= RULES["triple"]["alert_days"] else
                                     "watch" if streak >= RULES["triple"]["watch_days"] else "ok")
    note = None
    if r5 and None not in r5.values():
        note = f"近 5 日：股 {r5['spx']:+.1f}%、TLT {r5['tlt']:+.1f}%、美元 {r5['dxy']:+.1f}%；近 10 日三殺 {recent} 天"
    out.append(ind("triple", "股、債、匯三殺（連續天數）", thist, "天", st,
        f"連續 {RULES['triple']['watch_days']} 天 黃燈；連續 {RULES['triple']['alert_days']} 天 紅燈",
        "S&P 500、TLT、美元指數「同一天」一起跌。",
        "正常時股票跌，錢會躲進美債。三個一起跌＝全世界在逃離美國資產，是財政信任崩潰最典型的樣子。",
        "Yahoo ^GSPC、TLT、DX-Y.NYB", None, None, 0, note=note, value=streak, forms=["f1", "f3"],
        spark=thist))

    # ---------- ② 標售失敗 ----------
    auc_tbl, auc_streak = analyse_auctions(g("auctions"), RULES["auction"]["lookback"]) if g("auctions") else ([], None)
    st = "na" if auc_streak is None else ("alert" if auc_streak >= RULES["auction"]["alert"] else
                                         "watch" if auc_streak >= RULES["auction"]["watch"] else "ok")
    note = None
    if auc_tbl:
        a = auc_tbl[0]
        note = (f"最新 {a['date']} {a['bucket']}：倍數 {a['btc']:.2f}（均 {a['avg_btc']}）、"
                f"交易商 {a['dealer_pct']}%（均 {a['avg_dealer']}%）→ {'偏弱' if a['weak'] else '單項偏弱' if a['soft'] else '正常'}")
    out.append(ind("auction", "長債標售連續偏弱（10/20/30 年）", [], "場", st,
        f"連續 {RULES['auction']['watch']} 場偏弱 黃燈；{RULES['auction']['alert']} 場 紅燈",
        "政府拍賣新長債時，買的人夠不夠多。",
        "偏弱＝投標倍數低於前 6 場平均，而且造市商被迫吃下的比例高於平均（代表沒人要）。連續偏弱＝買家在罷工。",
        "財政部 FiscalData auctions_query", None, None, 0, note=note, value=auc_streak, forms=["f2"], spark=[]))

    # ---------- ③ 財政主導 ----------
    be = g("breakeven")
    out.append(ind("breakeven", "5y5y 遠期通膨預期", be, "%", level(lastv(be), RULES["breakeven"]),
        f"≥ {RULES['breakeven']['watch']}% 黃燈；≥ {RULES['breakeven']['alert']}% 紅燈",
        "市場猜「5 年後開始的那 5 年」平均通膨多少。",
        "它往上跑＝市場開始擔心央行會用印鈔幫政府還債，你拿回來的錢會變薄。",
        "FRED T5YIFR", "1 個月", chg(be, 21), 2, forms=["f3"]))

    gold = g("gold")
    g3, d3 = pct(gold, 63), pct(dxy, 63)
    st = "na" if g3 is None or d3 is None else (
        "watch" if g3 >= RULES["gold_dxy"]["gold_3m"] and d3 <= RULES["gold_dxy"]["dxy_3m"] else "ok")
    out.append(ind("gold_dxy", "金價漲＋美元跌（3 個月）", gold, "美元/盎司", st,
        f"金價 3 個月 ≥ +{RULES['gold_dxy']['gold_3m']:.0f}% 且美元 ≤ {RULES['gold_dxy']['dxy_3m']:.0f}% 黃燈",
        "黃金 vs 美元的拔河。", "大家搶黃金、丟美元＝不信任美元的購買力，是「錢變薄」的早期訊號。",
        "Yahoo GC=F、DX-Y.NYB", "3 個月", g3, 0,
        note=f"美元指數 3 個月 {d3:+.1f}%" if d3 is not None else None, forms=["f3"]))

    # ---------- ④ 流動性崩潰 ----------
    mv = g("move")
    out.append(ind("move", "MOVE 債市恐慌指數", mv, "", level(lastv(mv), RULES["move"]),
        f"≥ {RULES['move']['watch']} 黃燈；≥ {RULES['move']['alert']} 紅燈",
        "債市版的 VIX，衡量美債價格預期會晃多大。",
        "急升代表債市劇烈波動，高槓桿基金可能被迫賣債，連美債都會被拋售換現金。",
        "Yahoo ^MOVE", "1 個月", chg(mv, 21), 0, forms=["f4"]))

    rs, rt = daily_returns(spx), daily_returns(tlt)
    crash_days = []
    for d in sorted(set(rs) & set(rt))[-5:]:
        if rs[d] <= RULES["liq_crash"]["spx_alert"] and rt[d] <= RULES["liq_crash"]["tlt_alert"]:
            crash_days.append((d, "alert"))
        elif rs[d] <= RULES["liq_crash"]["spx_watch"] and rt[d] <= RULES["liq_crash"]["tlt_watch"]:
            crash_days.append((d, "watch"))
    st = "na" if not rs or not rt else worst([s for _, s in crash_days] or ["ok"])
    out.append(ind("liq_crash", "股債同日重挫（近 5 日）", [], "天", st,
        f"同日 S&P ≤ {RULES['liq_crash']['spx_watch']}% 且 TLT ≤ {RULES['liq_crash']['tlt_watch']}% 黃燈；"
        f"≤ {RULES['liq_crash']['spx_alert']}% 且 ≤ {RULES['liq_crash']['tlt_alert']}% 紅燈",
        "股票和長債在同一天大跌。", "恐慌時大家什麼都賣、只要現金（2020 年 3 月、2025 年 4 月都發生過），通常是急跌後反彈。",
        "Yahoo ^GSPC、TLT", None, None, 0, value=len(crash_days),
        note="、".join(d for d, _ in crash_days) or None, forms=["f4"], spark=[]))

    # ---------- ⑤ 債務上限 ----------
    debt = R.get("debt") or {}
    sub = debt.get("subject") or []
    limit = debt.get("limit") or RULES["debt_limit_fallback"]
    room = round(limit - lastv(sub), 1) if sub else None
    note = None
    if sub and len(sub) > 60:
        span = (date.fromisoformat(sub[-1][0]) - date.fromisoformat(sub[-61][0])).days or 1
        per_day = (sub[-1][1] - sub[-61][1]) / span
        if room is not None and room > 0 and per_day > 0:
            note = f"照近期速度約 {room / per_day:.0f} 天後碰頂（之後動用非常措施，真正 X-date 通常再晚幾個月）"
        elif room is not None and room <= 0:
            note = "已碰頂：財政部正在動用非常措施，注意 X-date 前後到期的短期國庫券"
    st = "na" if room is None else ("alert" if room <= RULES["debt_room"]["alert"] else
                                   "watch" if room <= RULES["debt_room"]["watch"] else "ok")
    out.append(ind("debt_room", "距債務上限剩餘", [[d, round(limit - v, 1)] for d, v in sub][-130:], "十億美元", st,
        f"≤ {RULES['debt_room']['watch']} 黃燈；≤ {RULES['debt_room']['alert']} 紅燈",
        f"法定借錢上限（{limit/1000:.3f} 兆）減掉已借的錢。",
        "用完又沒提高上限，少數快到期的短債可能延遲付款（技術性違約）。長債反而常因避險上漲。",
        debt.get("source", "財政部 DTS"), "1 個月", chg([[d, limit - v] for d, v in sub], 21) if sub else None, 0,
        value=room, note=note, forms=["f5"]))

    # ---------- 背景：財政負擔 ----------
    ar = g("avg_rate")
    out.append(ind("avg_rate", "國債平均利率（可交易債）", ar, "%", "info" if ar else "na", "只看趨勢",
        "政府所有借條加起來，平均每年付多少利息。",
        "舊的低利借條到期、換成新的高利借條，這個數字會慢慢爬升＝利息負擔越來越重（惡性循環的燃料）。",
        "財政部 avg_interest_rates", "12 個月", chg(ar, 12), 3, spark=ar[-60:], forms=["f1"]))

    # ---------- 黑天鵝自動代理 ----------
    jgb = g("jgb")
    out.append(ind("jgb", "日本 10 年期公債殖利率", jgb, "%", level(chg(jgb, 3), RULES["jgb"]),
        f"3 個月上升 ≥ {RULES['jgb']['watch']} 黃燈；≥ {RULES['jgb']['alert']} 紅燈",
        "日本自己國家的長期利率。", "日本是美債最大的外國債主。日本利率夠高，日本資金就會賣美債搬回家。",
        "FRED IRLTLT01JPM156N（月資料，晚約 1 個月）", "3 個月", chg(jgb, 3), 2, spark=jgb[-36:], forms=["f1"]))

    oil = g("oil")
    o1 = pct(oil, 21)
    out.append(ind("oil", "原油價格（戰爭／通膨風險）", oil, "美元/桶", level(o1, RULES["oil"]),
        f"一個月漲 ≥ {RULES['oil']['watch']:.0f}% 黃燈；≥ {RULES['oil']['alert']:.0f}% 紅燈",
        "WTI 原油期貨。", "油價暴漲推升通膨，長債最怕通膨。戰爭風險會先反映在這裡。",
        "Yahoo CL=F", "1 個月 %", o1, 1, forms=["f3"]))

    # ---------- 反向訊號 (衰退型) ----------
    sahm = g("sahm")
    y2 = g("y2")
    y2c = chg(y2, 21)
    dd = drawdown(spx)
    rec_reasons = []
    if lastv(sahm) is not None and lastv(sahm) >= RULES["recession"]["sahm"]:
        rec_reasons.append(f"沙姆法則 {lastv(sahm):.2f} ≥ {RULES['recession']['sahm']}")
    if dd is not None and y2c is not None and dd <= -RULES["recession"]["spx_dd"] and y2c <= RULES["recession"]["y2_drop"]:
        rec_reasons.append(f"S&P 從高點 {dd:.1f}% 且 2Y 一個月 {y2c:+.2f}")
    out.append(ind("sahm", "沙姆法則（衰退訊號）", sahm, "百分點",
        "rev" if lastv(sahm) is not None and lastv(sahm) >= RULES["recession"]["sahm"] else ("ok" if sahm else "na"),
        f"≥ {RULES['recession']['sahm']} ＝衰退型（藍燈）", "失業率 3 個月均值比過去一年低點高多少。",
        "衰退時 Fed 降息、錢湧進美債，TLT 反而會漲。用來分辨下一場危機是哪一型。",
        "FRED SAHMREALTIME", "較上月", chg(sahm, 1), 2, spark=sahm[-36:], forms=[]))
    out.append(ind("y2", "2 年期殖利率", y2, "%",
        "rev" if y2c is not None and y2c <= RULES["recession"]["y2_drop"] else ("info" if y2 else "na"),
        f"一個月下降 ≥ {abs(RULES['recession']['y2_drop'])} 搭配股市大跌＝衰退型",
        "跟 Fed 政策利率最貼近的利率。", "2 年期急降＝市場在押 Fed 要大降息，通常是衰退型危機（對長債有利）。",
        "FRED DGS2", "1 個月", y2c, 2, note=f"S&P 500 距 52 週高點 {dd:.1f}%" if dd is not None else None))

    # ---------- 我的部位 ----------
    ief = g("ief")
    twd = g("twd")
    positions = []
    for sym, s in (("TLT", tlt), ("IEF", ief)):
        positions.append({"symbol": sym, "price": lastv(s), "date": last(s)[0] if s else None,
                          "ret1m": pct(s, 21), "ret3m": pct(s, 63), "dd52": drawdown(s),
                          "duration": DURATION[sym], "spark": s[-130:]})
    twd_info = {"value": lastv(twd), "chg1m": pct(twd, 21), "spark": twd[-130:]}

    # ---------- 五種形式 ----------
    by = {i["id"]: i for i in out}
    forms = {}
    for f in ("f1", "f2", "f3", "f4", "f5"):
        drivers = [i for i in out if f in i["forms"]]
        forms[f] = {"status": worst([i["status"] for i in drivers]),
                    "lit": [i["name"] for i in drivers if i["status"] in ("watch", "alert")],
                    "drivers": [i["id"] for i in drivers]}

    # ---------- 總燈號 (依「紅綠燈出場規則」表) ----------
    red, yellow = [], []
    if by["y30"]["status"] == "alert":
        red.append(f"30 年期連續 {RULES['y30']['days']} 天站上前高 {RULES['y30']['alert']}%")
    if by["triple"]["status"] == "alert":
        red.append(f"股債匯三殺連續 {by['triple']['value']} 天")
    if lastv(y30) is not None and lastv(y30) >= RULES["y30"]["watch"] and by["y30"]["status"] != "alert":
        yellow.append(f"30 年期 {lastv(y30):.2f}% ≥ {RULES['y30']['watch']}%")
    if by["auction"]["status"] in ("watch", "alert"):
        yellow.append(f"長債標售連續 {by['auction']['value']} 場偏弱")
    if lastv(tp) is not None and lastv(tp) >= RULES["term_prem"]["watch"]:
        yellow.append(f"期限溢酬 {lastv(tp):.2f} ≥ {RULES['term_prem']['watch']}")
    other = [f"{i['name']}（{'紅' if i['status'] == 'alert' else '黃'}）" for i in out
             if i["status"] in ("watch", "alert") and i["id"] not in ("y30", "triple", "auction", "term_prem")]

    ACT = {
        "red": ("紅燈：財政信任崩潰型", "TLT 大部分出清，轉入短債（SGOV、BIL）或抗通膨債 TIPS（SCHP）；IEF 減碼。分批執行。"),
        "yellow": ("黃燈：長債壓力升溫", "先把約 1/3 的 TLT 換成 IEF 或短債，縮短存續期間；其餘部位設好紅燈條件。"),
        "blue": ("藍燈：衰退型訊號", "衰退型危機中 TLT 通常會漲（Fed 降息、資金避險）。不要恐慌賣出；這是長債表現最好的時候。"),
        "green": ("綠燈：正常", "維持部位，每週看一次即可。"),
    }
    if red:
        lv, reasons = "red", red + yellow
    elif rec_reasons and not red:
        lv, reasons = "blue", rec_reasons + yellow
    elif yellow:
        lv, reasons = "yellow", yellow
    else:
        lv, reasons = "green", []
    overall = {"level": lv, "name": ACT[lv][0], "action": ACT[lv][1], "reasons": reasons,
               "other": other, "recession": rec_reasons}

    return {
        "updated": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
        "overall": overall,
        "forms": forms,
        "indicators": out,
        "auctions": auc_tbl,
        "positions": positions,
        "twd": twd_info,
        "charts": {
            "y2": y2[-520:], "y10": y10[-520:], "y30": y30[-520:],
            "term_prem": tp[-520:], "s530": s530[-520:],
        },
        "rules": RULES,
        "errors": R.get("_errors", []),
    }


# ============================================================
# 抓取 → 計算 → 輸出
# ============================================================
def gather():
    raw, errors = {}, []

    def safe(key, fn, *a):
        try:
            raw[key] = fn(*a)
            print(f"  ✓ {key}: {len(raw[key]) if hasattr(raw[key], '__len__') else 'ok'}")
        except Exception as e:  # noqa: BLE001
            raw[key] = None
            errors.append(f"{key}: {e}")
            print(f"  ✗ {key}: {e}")

    if FRED_KEY:
        for key, sid in (("y2", "DGS2"), ("y5", "DGS5"), ("y10", "DGS10"), ("y30", "DGS30"),
                         ("term_prem", "THREEFYTP10"), ("breakeven", "T5YIFR"),
                         ("sahm", "SAHMREALTIME"), ("jgb", "IRLTLT01JPM156N")):
            safe(key, fred, sid)
    else:
        errors.append("未設定 FRED_API_KEY")
    for key, sym in (("spx", "^GSPC"), ("tlt", "TLT"), ("ief", "IEF"), ("dxy", "DX-Y.NYB"),
                     ("move", "^MOVE"), ("gold", "GC=F"), ("oil", "CL=F"), ("twd", "TWD=X")):
        safe(key, yahoo, sym)
    safe("auctions", fd_auctions)
    safe("debt", fd_debt)
    if not raw.get("debt") or not raw["debt"].get("subject"):
        safe("debt", fd_debt_fallback)
    safe("avg_rate", fd_avg_rate)
    raw["_errors"] = errors
    return raw


def main():
    print("抓取美債監控資料…")
    payload = compute(gather())
    path = os.path.join(HERE, "docs", "treasury_data.json")
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False)
    print(f"完成 → {path}  總燈號 {payload['overall']['level']}")


if __name__ == "__main__":
    main()
