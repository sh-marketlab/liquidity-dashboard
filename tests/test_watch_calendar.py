"""fetch_watch 美國假日與資料過期判斷測試：python -m pytest tests/test_watch_calendar.py"""
import os
import sys
from datetime import date, datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import fetch_watch as fw  # noqa: E402


def test_thanksgiving_not_bday():
    assert fw.us_holidays(2026)[0] >= {date(2026, 11, 26)}
    assert not fw.is_us_bday(date(2026, 11, 26))
    assert fw.is_us_bday(date(2026, 11, 27))  # 感恩節隔天照常開市


def test_known_holidays_2026():
    fed, nyse = fw.us_holidays(2026)
    assert date(2026, 1, 1) in fed and date(2026, 1, 1) in nyse
    assert date(2026, 1, 19) in fed                  # MLK
    assert date(2026, 4, 3) in nyse                  # Good Friday
    assert date(2026, 4, 3) not in fed
    assert date(2026, 5, 25) in fed                  # Memorial
    assert date(2026, 7, 3) in fed                   # 7/4 週六 → 7/3 補假
    assert date(2026, 10, 12) in fed and date(2026, 10, 12) not in nyse  # Columbus
    assert date(2026, 11, 11) in fed                 # Veterans
    assert date(2026, 12, 25) in fed


def test_new_year_on_saturday():
    # 2028/1/1 是週六：聯邦在 2027/12/31 補假，美股不休
    fed, nyse = fw.us_holidays(2027)
    assert date(2027, 12, 31) in fed
    assert date(2027, 12, 31) not in nyse


def test_sofr_after_thanksgiving_not_stale():
    ref = date(2026, 11, 27)
    assert fw.bdays_between("2026-11-25", ref) == 1   # 11/26 不計
    assert fw.bdays_between("2026-11-25", ref) <= fw.MAX_LAG_BD["daily"]


def test_last_us_session_skips_holiday():
    from zoneinfo import ZoneInfo
    ny = ZoneInfo("America/New_York")
    # 感恩節晚上 → 最近收盤日是 11/25
    assert fw.last_us_session(datetime(2026, 11, 26, 20, 0, tzinfo=ny)) == date(2026, 11, 25)
    # 週六 → 11/27
    assert fw.last_us_session(datetime(2026, 11, 28, 10, 0, tzinfo=ny)) == date(2026, 11, 27)


# ---------------- 台灣休市日 ----------------
def _tw_offline(monkeypatch):
    """不連網：讓證交所 API 失敗，只用內建表"""
    def boom(*a, **k):
        raise OSError("offline")
    monkeypatch.setattr(fw, "http_get", boom)
    monkeypatch.setattr(fw, "_TW_HOL", None)


def test_parse_tw_date():
    assert fw._parse_tw_date("1150101") == date(2026, 1, 1)
    assert fw._parse_tw_date("115/02/16") == date(2026, 2, 16)
    assert fw._parse_tw_date("20261225") == date(2026, 12, 25)
    assert fw._parse_tw_date("") is None


def test_tw_holidays_builtin(monkeypatch):
    _tw_offline(monkeypatch)
    assert not fw.is_tw_bday(date(2026, 2, 12))   # 封關後無交易
    assert not fw.is_tw_bday(date(2026, 10, 9))   # 國慶補假
    assert fw.is_tw_bday(date(2026, 2, 11))       # 封關日有交易
    assert fw.is_tw_bday(date(2026, 2, 23))       # 開紅盤
    assert fw.is_tw_bday(date(2026, 11, 26))      # 美國感恩節台股照開


def test_tw_api_rows_merged(monkeypatch):
    import json
    rows = [{"Name": "中華民國開國紀念日", "Date": "1160101"},
            {"Name": "國曆新年開始交易日", "Date": "1160104"}]
    monkeypatch.setattr(fw, "http_get", lambda *a, **k: json.dumps(rows))
    monkeypatch.setattr(fw, "_TW_HOL", None)
    hol = fw.tw_holidays()
    assert date(2027, 1, 1) in hol
    assert date(2027, 1, 4) not in hol             # 開始交易日不是休市
    assert date(2026, 2, 16) in hol                # 內建表仍保留


def test_tw_stale_after_lunar_new_year(monkeypatch):
    _tw_offline(monkeypatch)
    # 封關 2/11 的資料，到 2/22 (週日) 都不算落後
    assert fw.tw_bdays_between("2026-02-11", date(2026, 2, 22)) == 0
    from zoneinfo import ZoneInfo
    tp = ZoneInfo("Asia/Taipei")
    assert fw.last_tw_session(datetime(2026, 2, 20, 10, 0, tzinfo=tp)) == date(2026, 2, 11)
    assert fw.last_tw_session(datetime(2026, 2, 23, 17, 0, tzinfo=tp)) == date(2026, 2, 23)
    assert fw.last_tw_session(datetime(2026, 2, 23, 9, 0, tzinfo=tp)) == date(2026, 2, 11)
