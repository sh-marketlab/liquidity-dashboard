"""fetch_treasury 燈號邏輯測試：python -m pytest tests/test_treasury.py"""
import math
import os
import sys
from datetime import date, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import fetch_treasury as ft  # noqa: E402


def days(n, start=date(2025, 1, 1)):
    out, d = [], start
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d.isoformat())
        d += timedelta(days=1)
    return out


def series(vals):
    return [[d, v] for d, v in zip(days(len(vals)), vals)]


def wave(n, base, amp, period=60, drift=0.0):
    return [round(base + amp * math.sin(i / period * 2 * math.pi) + drift * i, 4) for i in range(n)]


def auctions(weak_last=0):
    rows, d = [], date(2025, 1, 10)
    for i in range(30):
        b = ("10-Year", "30-Year", "20-Year")[i % 3]
        weak = i >= 30 - weak_last
        rows.append({"date": (d + timedelta(days=10 * i)).isoformat(), "term": b, "orig_term": b,
                     "btc": 2.30 if weak else 2.55 + 0.01 * (i % 2), "high_yield": 4.8,
                     "dealer_pct": 20.0 if weak else 10.0, "indirect_pct": 65.0})
    return rows


def base_raw(n=300, **over):
    raw = {
        "y2": series(wave(n, 4.5, 0.05)), "y5": series(wave(n, 4.6, 0.05)),
        "y10": series(wave(n, 4.85, 0.05)), "y30": series(wave(n, 5.2, 0.05)),
        "term_prem": series(wave(n, 0.9, 0.05)), "breakeven": series(wave(n, 2.3, 0.02)),
        "sahm": series([0.2] * n), "jgb": series([1.5] * 40),
        "spx": series(wave(n, 6000, 50, drift=1)), "tlt": series(wave(n, 85, 1)),
        "ief": series(wave(n, 93, 0.5)), "dxy": series(wave(n, 100, 0.5, period=37)),
        "move": series([100] * n), "gold": series([3000] * n), "oil": series([70] * n),
        "twd": series([31] * n), "auctions": auctions(0),
        "debt": {"subject": series([40000 + i for i in range(n)]), "limit": 41104, "source": "test"},
        "avg_rate": series([3.3 + 0.01 * i for i in range(40)]),
    }
    raw.update(over)
    return raw


def by_id(p):
    return {i["id"]: i for i in p["indicators"]}


def test_green_baseline():
    p = ft.compute(base_raw())
    assert p["overall"]["level"] == "green", p["overall"]
    assert all(f["status"] in ("ok", "na") for f in p["forms"].values()), p["forms"]


def test_y30_watch_gives_yellow():
    p = ft.compute(base_raw(y30=series([5.2] * 299 + [5.6])))
    assert by_id(p)["y30"]["status"] == "watch"
    assert p["overall"]["level"] == "yellow"


def test_y30_needs_three_days_for_red():
    p = ft.compute(base_raw(y30=series([5.2] * 298 + [5.8, 5.8])))
    assert by_id(p)["y30"]["status"] == "watch"
    p = ft.compute(base_raw(y30=series([5.2] * 297 + [5.8, 5.8, 5.75])))
    assert by_id(p)["y30"]["status"] == "alert"
    assert p["overall"]["level"] == "red"
    assert p["forms"]["f1"]["status"] == "alert"


def test_triple_sell_streak():
    n = 300
    down = lambda b: series([b] * (n - 3) + [b * 0.99, b * 0.98, b * 0.97])
    p = ft.compute(base_raw(spx=down(6000), tlt=down(85), dxy=down(100)))
    assert by_id(p)["triple"]["value"] == 3
    assert p["overall"]["level"] == "red"


def test_auction_streak():
    p = ft.compute(base_raw(auctions=auctions(2)))
    assert by_id(p)["auction"]["value"] == 2
    assert by_id(p)["auction"]["status"] == "watch"
    assert p["overall"]["level"] == "yellow"
    assert p["auctions"][0]["weak"] is True


def test_term_bucket_reopening():
    assert ft.bucket({"term": "29-Year 11-Month", "orig_term": ""}) == "30Y"
    assert ft.bucket({"term": "9-Year 10-Month", "orig_term": "10-Year"}) == "10Y"
    assert ft.bucket({"term": "5-Year", "orig_term": ""}) is None


def test_recession_blue():
    p = ft.compute(base_raw(sahm=series([0.2] * 299 + [0.6])))
    assert p["overall"]["level"] == "blue"


def test_red_beats_blue():
    p = ft.compute(base_raw(sahm=series([0.6] * 300), y30=series([5.2] * 297 + [5.8] * 3)))
    assert p["overall"]["level"] == "red"


def test_debt_room():
    p = ft.compute(base_raw(debt={"subject": series([41050] * 100), "limit": 41104, "source": "t"}))
    assert by_id(p)["debt_room"]["status"] == "alert"
    assert p["forms"]["f5"]["status"] == "alert"


def test_missing_sources_do_not_crash():
    p = ft.compute({"_errors": ["all down"]})
    assert p["overall"]["level"] == "green"
    assert by_id(p)["y30"]["status"] == "na"


# ---------- 3M 國庫券 − 政策上限、水管連動、TGA 回補期 ----------
def crash_raw(gap):
    """S&P 從高點跌 20% 且 2Y 一個月降 0.6 → 藍燈第二路徑成立；gap = 3M − 上限"""
    n = 300
    return base_raw(spx=series([6000] * (n - 1) + [4800]),
                    y2=series([4.6] * (n - 1) + [4.0]),
                    bill3m=series([4.0 + gap] * n), fed_upper=series([4.0] * 5))


def watch_payload(sofr="ok", srf="ok"):
    return {"stage": {"level": 0, "name": "常態"},
            "indicators": [{"id": "sofr_iorb", "status": sofr, "value": 0.12, "date": "2026-11-19",
                            "spark": [["2026-11-18", 0.02], ["2026-11-19", 0.12]]},
                           {"id": "srf", "status": srf, "value": 0.0}]}


def test_gap_ffill():
    g = ft.gap_ffill([["2026-01-02", 4.2], ["2026-01-05", 4.3]], [["2025-12-01", 4.0]])
    assert g == [["2026-01-02", 0.2], ["2026-01-05", 0.3]]


def test_blue_when_no_hike_priced():
    p = ft.compute(crash_raw(-0.20))
    assert p["overall"]["level"] == "blue"
    assert by_id(p)["y2"]["status"] == "rev"


def test_hike_priced_blocks_blue_path():
    p = ft.compute(crash_raw(0.23))
    assert p["overall"]["level"] != "blue"
    assert by_id(p)["y2"]["status"] == "info"
    assert by_id(p)["bill_gap"]["value"] == 23.0
    assert any("不算衰退型" in o for o in p["overall"]["other"])


def test_sahm_blue_not_blocked_by_hike_pricing():
    raw = crash_raw(0.23)
    raw["sahm"] = series([0.6] * 300)
    assert ft.compute(raw)["overall"]["level"] == "blue"


def test_plumb_links_watch_into_form4():
    p = ft.compute(base_raw(watch=watch_payload(sofr="watch")))
    assert by_id(p)["plumb"]["status"] == "watch"
    assert p["forms"]["f4"]["status"] == "watch"
    assert p["overall"]["level"] == "green"   # 水管單獨亮不改總燈號
    assert by_id(p)["plumb"]["value"] == 12.0


def test_tga_rebuild_window_plus_plumb_gives_yellow():
    d = (ft.TODAY - timedelta(days=9)).isoformat()
    p = ft.compute(base_raw(watch=watch_payload(srf="alert")), {"debt_limit_resolved": d})
    t = by_id(p)["tga_rebuild"]
    assert t["status"] == "watch" and t["value"] == 10
    assert p["overall"]["level"] == "yellow"


def test_tga_rebuild_window_without_plumb_is_trend_only():
    d = (ft.TODAY - timedelta(days=9)).isoformat()
    p = ft.compute(base_raw(watch=watch_payload()), {"debt_limit_resolved": d})
    assert by_id(p)["tga_rebuild"]["status"] == "info"
    assert p["overall"]["level"] == "green"


def test_tga_rebuild_auto_detect_limit_raise():
    d0, d1 = (ft.TODAY - timedelta(days=40)).isoformat(), (ft.TODAY - timedelta(days=20)).isoformat()
    raw = base_raw(watch=watch_payload(sofr="watch"))
    raw["debt"] = dict(raw["debt"], limit_hist=[[d0, 41104.0], [d1, 46104.0]], limit=46104.0)
    t = by_id(ft.compute(raw))["tga_rebuild"]
    assert t["status"] == "watch" and t["value"] == 21


def test_tga_rebuild_expired_window():
    d = (ft.TODAY - timedelta(days=90)).isoformat()
    p = ft.compute(base_raw(watch=watch_payload(sofr="alert")), {"debt_limit_resolved": d})
    assert by_id(p)["tga_rebuild"]["status"] == "ok"
