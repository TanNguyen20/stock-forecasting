import os
from datetime import date, datetime

import numpy as np
import pandas as pd
import pytest

from stockvn import market
from stockvn.forecast import future_business_days
from stockvn.indicators import add_indicators, bollinger, rsi, sma
from stockvn.market import (
    VN_TZ, fmt_change, fmt_pct, fmt_price, fmt_volume, holidays, is_trading_day, is_trading_time, parse_holidays,
    session_label, suggest_holidays,
)


def test_format_vietnamese_numbers():
    assert fmt_price(58200) == "58.200"
    assert fmt_price(1745.61, index=True) == "1.745,61"
    assert fmt_pct(-0.1655) == "-0,17%"
    assert fmt_pct(2.5) == "+2,50%"
    assert fmt_volume(1658000) == "1.658.000"
    assert fmt_price(None) == "—"
    assert fmt_price(float("nan")) == "—"
    assert fmt_change(1200) == "+1.200"
    assert fmt_change(-13.44, index=True) == "-13,44"
    assert fmt_change(0) == "0"


def test_trading_hours():
    tue = lambda h, m: datetime(2026, 10, 6, h, m, tzinfo=VN_TZ)  # noqa: E731 - thứ 3
    assert is_trading_time(tue(9, 30))
    assert not is_trading_time(tue(12, 0))
    assert is_trading_time(tue(14, 0))
    assert not is_trading_time(tue(15, 30))
    assert not is_trading_time(datetime(2026, 10, 10, 10, 0, tzinfo=VN_TZ))  # thứ 7
    assert session_label(tue(12, 0)) == "Nghỉ trưa"
    assert session_label(tue(8, 0)) == "Trước phiên"


def test_rsi_bounds_and_extremes(prices):
    r = rsi(prices["close"]).dropna()
    assert r.between(0, 100).all()
    rising = pd.Series(np.arange(1, 50, dtype=float))
    assert rsi(rising).iloc[-1] == 100.0
    falling = pd.Series(np.arange(50, 1, -1, dtype=float))
    assert rsi(falling).iloc[-1] < 1


def test_sma_and_bollinger(prices):
    c = prices["close"]
    assert np.isclose(sma(c, 5).iloc[-1], c.tail(5).mean())
    bb = bollinger(c).dropna()
    assert (bb["bb_upper"] >= bb["bb_mid"]).all() and (bb["bb_mid"] >= bb["bb_lower"]).all()


def test_add_indicators_columns(prices):
    out = add_indicators(prices)
    for col in ("sma20", "sma50", "sma200", "rsi14", "macd", "macd_signal", "macd_hist", "bb_upper", "vol_sma20"):
        assert col in out
    assert len(out) == len(prices)


@pytest.fixture
def holiday_file(tmp_path, monkeypatch):
    path = tmp_path / "holidays.txt"
    path.write_text("# chú thích\n2026-10-07   # một ngày\n2026-12-30..2027-01-01\nkhông-hợp-lệ\n", encoding="utf-8")
    monkeypatch.setattr(market, "HOLIDAYS_FILE", path)
    monkeypatch.setattr(market, "_holiday_cache", None)
    return path


def test_parse_holidays_days_ranges_and_bad_lines():
    days = parse_holidays("2026-02-16..2026-02-18  # Tết\n\n2026-04-27\nrác\n")
    assert days == {date(2026, 2, 16), date(2026, 2, 17), date(2026, 2, 18), date(2026, 4, 27)}


def test_holidays_are_not_trading_days(holiday_file):
    assert not is_trading_day(date(2026, 10, 7))
    assert is_trading_day(date(2026, 10, 8))
    assert not is_trading_day(date(2027, 1, 1))
    wed = datetime(2026, 10, 7, 10, 0, tzinfo=VN_TZ)
    assert not is_trading_time(wed)
    assert session_label(wed) == "Nghỉ lễ"
    assert session_label(datetime(2026, 10, 10, 10, 0, tzinfo=VN_TZ)) == "Nghỉ cuối tuần"


def test_holidays_reload_when_file_changes(holiday_file):
    assert date(2026, 10, 8) not in holidays()
    holiday_file.write_text("2026-10-08\n", encoding="utf-8")
    os.utime(holiday_file, (1, 1))  # đảm bảo mtime khác lần đọc trước
    assert holidays() == {date(2026, 10, 8)}


def test_future_business_days_skip_holidays(holiday_file):
    days = future_business_days(pd.Timestamp("2026-10-06"), 3)
    assert [d.date() for d in days] == [date(2026, 10, 8), date(2026, 10, 9), date(2026, 10, 12)]


def test_suggest_holidays_finds_weekday_gaps():
    traded = [date(2026, 4, 23), date(2026, 4, 24), date(2026, 4, 28)]  # thiếu thứ 2 27/4
    assert suggest_holidays(traded) == [date(2026, 4, 27)]
    assert suggest_holidays([]) == []


def test_shipped_holiday_file_parses():
    days = parse_holidays(market.HOLIDAYS_FILE.read_text(encoding="utf-8"))
    assert {date(2026, 1, 2), date(2026, 2, 17), date(2026, 4, 27), date(2026, 9, 1)} <= days


def test_last_session_skips_weekends_and_holidays(holiday_file):
    at = lambda *a: datetime(*a, tzinfo=VN_TZ)  # noqa: E731
    assert market.last_session(at(2026, 10, 6, 10, 0)) == date(2026, 10, 6)  # thứ 3 trong phiên
    assert market.last_session(at(2026, 10, 6, 8, 0)) == date(2026, 10, 5)  # trước giờ mở cửa
    assert market.last_session(at(2026, 10, 8, 8, 0)) == date(2026, 10, 6)  # 07/10 nghỉ lễ
    assert market.last_session(at(2026, 10, 11, 12, 0)) == date(2026, 10, 9)  # chủ nhật
