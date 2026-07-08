"""
美元流動性儀表板 - 數據抓取腳本
供給側: FRED (需要免費API key, 環境變數 FRED_API_KEY)
需求側: 財政部 FiscalData API (標售結果, 免key)
輸出: docs/data.json
"""
import json
import os
import sys
import urllib.request
import urllib.parse
from datetime import date, datetime

FRED_KEY = os.environ.get("FRED_API_KEY", "").strip()
START = "2020-01-01"
SPREAD_START = "2022-01-01"

# ---------- 工具 ----------

def http_get_json(url):
    req = urllib.request.Request(url, headers={"User-Agent": "liquidity-dashboard/1.0"})
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.loads(r.read().decode("utf-8"))

def fred_series(series_id, start=START):
    """回傳 [[date, value], ...]，略過缺值"""
    params = urllib.parse.urlencode({
        "series_id": series_id,
        "api_key": FRED_KEY,
        "file_type": "json",
        "observation_start": start,
    })
    url = f"https://api.stlouisfed.org/fred/series/observations?{params}"
    data = http_get_json(url)
    out = []
    for obs in data.get("observations", []):
        v = obs.get("value", ".")
        if v not in (".", "", None):
            out.append([obs["date"], float(v)])
    return out

def to_map(series):
    return {d: v for d, v in series}

def latest_on_or_before(sorted_dates, value_map, target):
    """找 target 當天或之前最近的值 (系列頻率不同時對齊用)"""
    best = None
    for d in sorted_dates:
        if d <= target:
            best = value_map[d]
        else:
            break
    return best

# ---------- 供給側 ----------

def build_supply():
    series = {}
    ids = {
        "WALCL": START,        # Fed 總資產
        "WTREGEN": START,      # TGA
        "WLRRAL": START,       # RRP (週均, Millions)
        "WRESBAL": START,      # 銀行準備金
        "SOFR": SPREAD_START,
        "IORB": SPREAD_START,
        "RPONTSYD": SPREAD_START,  # SRF 使用量
    }
    for sid, start in ids.items():
        try:
            series[sid] = fred_series(sid, start)
            print(f"  FRED {sid}: {len(series[sid])} 筆")
        except Exception as e:
            print(f"  FRED {sid} 失敗: {e}")
            series[sid] = []

    # 淨流動性 = WALCL - TGA - RRP，以 WALCL 的日期為準
    tga_map, rrp_map = to_map(series["WTREGEN"]), to_map(series["WLRRAL"])
    tga_dates, rrp_dates = sorted(tga_map), sorted(rrp_map)
    net_liq = []
    for d, walcl in series["WALCL"]:
        tga = latest_on_or_before(tga_dates, tga_map, d)
        rrp = latest_on_or_before(rrp_dates, rrp_map, d)
        if tga is not None and rrp is not None:
            net_liq.append([d, round(walcl - tga - rrp, 1)])

    # SOFR - IORB 日頻利差 + 週均線
    iorb_map = to_map(series["IORB"])
    spread_daily = [[d, round(v - iorb_map[d], 4)] for d, v in series["SOFR"] if d in iorb_map]
    spread_weekly, bucket, cur_week = [], [], None
    for d, v in spread_daily:
        wk = datetime.strptime(d, "%Y-%m-%d").isocalendar()[:2]
        if wk != cur_week and bucket:
            spread_weekly.append([bucket[-1][0], round(sum(x[1] for x in bucket) / len(bucket), 4)])
            bucket = []
        cur_week = wk
        bucket.append([d, v])
    if bucket:
        spread_weekly.append([bucket[-1][0], round(sum(x[1] for x in bucket) / len(bucket), 4)])

    return {
        "net_liquidity": net_liq,
        "reserves": series["WRESBAL"],
        "walcl": series["WALCL"],
        "tga": series["WTREGEN"],
        "rrp": series["WLRRAL"],
        "spread_daily": spread_daily,
        "spread_weekly": spread_weekly,
        "srf": series["RPONTSYD"],
    }

# ---------- 需求側 ----------

def build_demand():
    out = {}
    demand_ids = {
        "fed_treast": "TREAST",          # 情境1: Fed 持有國債
        "bank_holdings": "TASACBW027SBOG",  # 情境3: 銀行持有
        "foreign_custody": "WMTSECL1",   # 情境5: 海外官方 (Fed代管)
    }
    for key, sid in demand_ids.items():
        try:
            out[key] = fred_series(sid, START)
            print(f"  FRED {sid}: {len(out[key])} 筆")
        except Exception as e:
            print(f"  FRED {sid} 失敗: {e}")
            out[key] = []
    return out

def num(rec, *keys):
    for k in keys:
        v = rec.get(k)
        if v not in (None, "", "null"):
            try:
                return float(v)
            except (TypeError, ValueError):
                pass
    return None

def build_auctions():
    """情境4/6: 財政部標售結果 (Notes/Bonds 近30場)"""
    base = "https://api.fiscaldata.treasury.gov/services/api/fiscal_service/v1/accounting/od/auctions_query"
    url = f"{base}?sort=-auction_date&page[size]=120"
    try:
        data = http_get_json(url)
    except Exception as e:
        print(f"  FiscalData 失敗: {e}")
        return []
    rows = []
    for rec in data.get("data", []):
        stype = (rec.get("security_type") or "").strip()
        if stype not in ("Notes", "Bonds", "Note", "Bond"):
            continue
        total = num(rec, "total_accepted", "totl_accepted_amt", "offering_amt")
        dealer = num(rec, "primary_dealer_accepted", "pri_dlr_accepted_amt")
        indirect = num(rec, "indirect_bidder_accepted", "indr_bidder_accepted_amt")
        direct = num(rec, "direct_bidder_accepted", "drct_bidder_accepted_amt")
        row = {
            "date": rec.get("auction_date"),
            "term": rec.get("security_term"),
            "type": stype,
            "btc": num(rec, "bid_to_cover_ratio"),
            "high_yield": num(rec, "high_yield", "high_investment_rate"),
            "dealer_pct": round(dealer / total * 100, 1) if dealer and total else None,
            "indirect_pct": round(indirect / total * 100, 1) if indirect and total else None,
            "direct_pct": round(direct / total * 100, 1) if direct and total else None,
        }
        rows.append(row)
        if len(rows) >= 30:
            break
    print(f"  FiscalData 標售: {len(rows)} 場")
    return rows

# ---------- 主程式 ----------

def main():
    if not FRED_KEY:
        print("錯誤: 未設定 FRED_API_KEY 環境變數")
        print("申請免費 key: https://fred.stlouisfed.org/docs/api/api_key.html")
        sys.exit(1)

    print("抓取供給側...")
    supply = build_supply()
    print("抓取需求側...")
    demand = build_demand()
    print("抓取標售結果...")
    auctions = build_auctions()

    payload = {
        "updated": datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC"),
        "supply": supply,
        "demand": demand,
        "auctions": auctions,
    }
    out_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "docs", "data.json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False)
    print(f"完成 → {out_path}")

if __name__ == "__main__":
    main()
