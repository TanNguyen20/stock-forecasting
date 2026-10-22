"""Bộ lọc cổ phiếu theo điều kiện kỹ thuật, chạy trên dữ liệu giá đã lưu trong CSDL."""
from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import dataclass

import pandas as pd

from .indicators import add_indicators

LOOKBACK = 300  # số phiên đọc mỗi mã: đủ cho SMA200 và 52 tuần
MIN_SESSIONS = 60
STALE_DAYS = 10  # phiên cuối cũ hơn mã mới nhất quá số ngày này -> coi như ngừng giao dịch


def _crossed_up(a: pd.Series, b: pd.Series, within: int) -> bool:
    """`a` vừa cắt lên `b` trong `within` phiên gần nhất và hiện vẫn ở trên."""
    above = (a > b) & a.notna() & b.notna()
    return bool(above.iloc[-1] and (above.astype(int).diff().iloc[-within:] == 1).any())


def _prior(d: pd.DataFrame, n: int = 252) -> pd.DataFrame:
    return d.iloc[:-1].tail(n)


@dataclass(frozen=True)
class Rule:
    label: str
    test: Callable[[pd.DataFrame], bool]
    min_sessions: int = MIN_SESSIONS


RULES: dict[str, Rule] = {
    "rsi_oversold": Rule("RSI(14) < 30 (quá bán)", lambda d: d["rsi14"].iloc[-1] < 30),
    "rsi_overbought": Rule("RSI(14) > 70 (quá mua)", lambda d: d["rsi14"].iloc[-1] > 70),
    "above_sma200": Rule("Giá trên SMA200", lambda d: d["close"].iloc[-1] > d["sma200"].iloc[-1], 200),
    "below_sma200": Rule("Giá dưới SMA200", lambda d: d["close"].iloc[-1] < d["sma200"].iloc[-1], 200),
    "uptrend": Rule("Xu hướng tăng (SMA20 > SMA50 > SMA200)",
                    lambda d: d["sma20"].iloc[-1] > d["sma50"].iloc[-1] > d["sma200"].iloc[-1], 200),
    "golden_cross": Rule("SMA20 cắt lên SMA50 (5 phiên gần nhất)", lambda d: _crossed_up(d["sma20"], d["sma50"], 5)),
    "death_cross": Rule("SMA20 cắt xuống SMA50 (5 phiên gần nhất)", lambda d: _crossed_up(d["sma50"], d["sma20"], 5)),
    "macd_cross": Rule("MACD cắt lên Signal (3 phiên gần nhất)",
                       lambda d: _crossed_up(d["macd"], d["macd_signal"], 3)),
    "volume_spike": Rule("KL phiên cuối ≥ 2 lần TB20",
                         lambda d: d["volume"].iloc[-1] >= 2 * d["volume"].iloc[-21:-1].mean()),
    "high_52w": Rule("Phá đỉnh 52 tuần", lambda d: d["close"].iloc[-1] >= _prior(d)["high"].max()),
    "near_low_52w": Rule("Cách đáy 52 tuần ≤ 5%", lambda d: d["close"].iloc[-1] <= _prior(d)["low"].min() * 1.05),
    "bb_lower": Rule("Đóng cửa dưới dải Bollinger dưới", lambda d: d["close"].iloc[-1] < d["bb_lower"].iloc[-1]),
}


def _ret(close: pd.Series, n: int) -> float:
    return (close.iloc[-1] / close.iloc[-n - 1] - 1) * 100 if len(close) > n else math.nan


def snapshot(d: pd.DataFrame) -> dict:
    """Chỉ số tóm tắt của một mã (d đã có cột chỉ báo)."""
    c, v = d["close"], d["volume"]
    prev_vol = v.iloc[-21:-1].mean()
    sma200 = d["sma200"].iloc[-1]
    return {
        "date": d.index[-1],
        "price": float(c.iloc[-1]),
        "change_pct": _ret(c, 1),
        "ret_1m": _ret(c, 21),
        "rsi": float(d["rsi14"].iloc[-1]),
        "vol_ratio": float(v.iloc[-1] / prev_vol) if prev_vol else math.nan,
        "value_bn": float((c * v).tail(20).mean() / 1e9),  # giá trị giao dịch TB 20 phiên (tỷ VND)
        "dist_sma200": float((c.iloc[-1] / sma200 - 1) * 100) if pd.notna(sma200) else math.nan,
    }


def screen(data: dict[str, pd.DataFrame], rules: list[str], min_value_bn: float = 0.0) -> pd.DataFrame:
    """Các mã thỏa TẤT CẢ điều kiện. data: lịch sử giá theo mã (tăng dần theo ngày)."""
    latest = max((df.index[-1] for df in data.values() if not df.empty), default=None)
    rows = []
    for sym, raw in data.items():
        if len(raw) < MIN_SESSIONS or (latest - raw.index[-1]).days > STALE_DAYS:
            continue
        d = add_indicators(raw.tail(LOOKBACK))
        snap = snapshot(d)
        if snap["value_bn"] < min_value_bn:
            continue
        if all(len(d) >= RULES[r].min_sessions and RULES[r].test(d) for r in rules):
            rows.append({"symbol": sym, **snap})
    return pd.DataFrame(rows, columns=["symbol", "date", "price", "change_pct", "ret_1m", "rsi", "vol_ratio",
                                       "value_bn", "dist_sma200"])
