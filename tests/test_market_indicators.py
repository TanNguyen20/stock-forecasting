from datetime import datetime

import numpy as np
import pandas as pd

from stockvn.indicators import add_indicators, bollinger, rsi, sma
from stockvn.market import VN_TZ, fmt_change, fmt_pct, fmt_price, fmt_volume, is_trading_time, session_label


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
