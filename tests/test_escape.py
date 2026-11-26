"""fetch_escape 判定邏輯測試：python -m pytest tests/test_escape.py"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import fetch_escape as fe  # noqa: E402


def calm():
    """多頭常態：全部綠燈"""
    return {
        "us10y": 4.2, "us10y_days_above": 0, "y2_ffr": -0.3,
        "hy": 3.0, "hy_low6m": 2.9, "hy_high3m": 3.1,
        "jpy_up_1m": 0.5, "unrate": 4.1, "unrate_prev": 4.1, "unrate_base": 4.1,
        "tsmc_rev_3m": 3.0, "breadth": 72.0, "mag7_rev_30d": 1.2,
        "tw_sell_streak": 0, "tw_buy_streak": 6, "tw_cum10": 500.0,
        "core_cpi": 0.15, "core_cpi_r": 0.1, "capex_avg": 30.0, "capex_below_n": 0,
        "vix_recent": [14] * 10, "spx_below200_recent": [False] * 10,
        "spx": 6000, "spx_ma50": 5900, "spx_ma200": 5600,
        "sahm": 0.1, "claims4w": 220000, "nfci": -0.5,
        "twii": 23000, "twii_ma200": 21000, "twii_below200": False, "twd_dep_1m": -0.5,
    }


def test_calm_is_bull():
    ev = fe.evaluate(calm())
    assert ev["reds"] == []
    assert ev["escape"] == 1
    assert ev["recovery"] == 3          # 6 個回升訊號全成立、含 R3
    assert ev["n_rec"] == 6
    assert ev["scenario"][0] == "bull"


def test_hy_red_alone_moves_to_stage2_and_credit_scenario():
    x = calm()
    x["hy"] = 3.45                      # 較低點 +0.55
    ev = fe.evaluate(x)
    assert ev["signals"]["hy"]["status"] == "red"
    assert ev["escape"] == 2
    assert ev["scenario"][0] == "credit"


def test_hy_yellow_band():
    x = calm()
    x["hy"] = 3.3                       # +0.4
    assert fe.evaluate(x)["signals"]["hy"]["status"] == "yellow"


def test_three_reds_stage2_then_deterioration_stage3():
    x = calm()
    x.update(breadth=50, mag7_rev_30d=-0.5, tsmc_rev_3m=-6)   # 3 個紅燈
    ev = fe.evaluate(x)
    assert len(ev["reds"]) == 3 and ev["escape"] == 2
    x.update(vix_recent=[20] * 8 + [26, 27], spx_below200_recent=[False] * 7 + [True] * 3)  # b, c
    ev = fe.evaluate(x)
    assert ev["n_det"] == 2 and ev["escape"] == 3


def test_deterioration_without_stage2_stays_1():
    x = calm()
    x.update(vix_recent=[26] * 10, spx_below200_recent=[True] * 10)
    ev = fe.evaluate(x)
    assert ev["n_det"] >= 2 and ev["escape"] == 1   # 第 3 階段必須先滿足第 2 階段


def test_danger_two_goes_stage4_regardless():
    x = calm()
    x.update(vix_recent=[31] * 10, nfci=0.1)        # ②、⑤
    ev = fe.evaluate(x)
    assert ev["n_dan"] == 2 and ev["escape"] == 4


def test_death_cross_and_below_5pct():
    x = calm()
    x.update(spx=5300, spx_ma50=5700, spx_ma200=5600)
    on = {c["id"]: c["on"] for c in fe.evaluate(x)["danger"]}
    assert on["③"] is True


def test_rate_scenario_2022():
    x = calm()
    x.update(y2_ffr=0.2, core_cpi_r=0.3)
    ev = fe.evaluate(x)
    assert ev["signals"]["y2_ffr"]["status"] == "red"
    assert ev["signals"]["core_cpi"]["status"] == "red"
    assert ev["scenario"][0] == "rate"


def test_core_cpi_bands():
    for v, st in [(0.1, "green"), (0.2, "yellow"), (0.3, "red"), (0.4, "red")]:
        x = calm()
        x["core_cpi_r"] = v
        assert fe.evaluate(x)["signals"]["core_cpi"]["status"] == st


def test_round_like_bls():
    assert fe.r1(0.25) == 0.3
    assert fe.r1(0.249) == 0.2


def test_unrate_rules():
    x = calm()
    x.update(unrate=4.2, unrate_prev=4.1, unrate_base=4.1)
    assert fe.evaluate(x)["signals"]["unrate"]["status"] == "yellow"
    x.update(unrate=4.4, unrate_prev=4.4)
    assert fe.evaluate(x)["signals"]["unrate"]["status"] == "red"


def test_jpy_bands():
    for v, st in [(2.9, "green"), (3.0, "yellow"), (5.0, "red"), (-6, "green")]:
        x = calm()
        x["jpy_up_1m"] = v
        assert fe.evaluate(x)["signals"]["usdjpy"]["status"] == st


def test_tw_foreign_yellow():
    x = calm()
    x.update(tw_sell_streak=5, tw_buy_streak=0, tw_cum10=-300)
    assert fe.evaluate(x)["signals"]["tw_foreign"]["status"] == "yellow"
    x.update(tw_sell_streak=1, tw_cum10=-1200)
    assert fe.evaluate(x)["signals"]["tw_foreign"]["status"] == "yellow"


def test_h_taiwan():
    x = calm()
    x.update(twii_below200=True, twd_dep_1m=3.2)
    on = {c["id"]: c["on"] for c in fe.evaluate(x)["deter"]}
    assert on["h"] is True


def test_missing_data_is_na_not_red():
    x = calm()
    for k in ("tsmc_rev_3m", "breadth", "mag7_rev_30d", "capex_avg", "capex_below_n"):
        x.pop(k)
    ev = fe.evaluate(x)
    for k in ("tsmc", "breadth", "mag7", "capex"):
        assert ev["signals"][k]["status"] == "na"
    assert ev["reds"] == []
    on = {c["id"]: c["on"] for c in ev["recov"]}
    assert on["R4"] is None


def test_recovery_ladder():
    x = calm()
    # 下跌中：VIX 高、在 200 日線下、寬度差、HY 高
    x.update(vix_recent=[32] * 10, spx_below200_recent=[True] * 10, breadth=35,
             hy=4.2, hy_low6m=3.0, hy_high3m=4.3, y2_ffr=0.1, core_cpi_r=0.3,
             tw_buy_streak=0, tw_sell_streak=4, twii_below200=True)
    ev = fe.evaluate(x)
    assert ev["recovery"] == 0
    # 開始回穩：VIX 回落 5 日、HY 從高點回落 0.6
    x.update(vix_recent=[30] * 5 + [22] * 5, hy=3.7)
    ev = fe.evaluate(x)
    assert ev["n_rec"] == 2 and ev["recovery"] == 1


def test_step_join_and_streak():
    y2 = [["2026-01-01", 4.0], ["2026-01-02", 4.1], ["2026-01-05", 4.2]]
    ffr = [["2025-12-10", 4.0], ["2026-01-05", 3.75]]
    assert fe.step_join(y2, ffr) == [["2026-01-01", 0.0], ["2026-01-02", 0.1], ["2026-01-05", 0.45]]
    assert fe.streak([1, -1, -2, -3], lambda v: v < 0) == 3


def test_reading_and_next():
    ev = fe.evaluate(calm())
    en, rn = fe.next_steps(ev)
    assert "紅燈" in en and "最高" in rn
    assert "多頭" in fe.reading(1, 3)
