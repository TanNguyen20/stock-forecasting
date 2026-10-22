"""Backtest chiến lược giao dịch đơn giản (chỉ mua/bán, không bán khống) có tính phí, thuế và T+2.

Tín hiệu tính trên giá đóng cửa phiên t, lệnh khớp ở giá mở cửa phiên t+1 -> không nhìn trước dữ liệu.
"""
from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from .indicators import macd, rsi, sma


def _hold(enter: pd.Series, leave: pd.Series) -> pd.Series:
    """Tín hiệu có trạng thái: 1 từ khi `enter` đến khi `leave`."""
    state = pd.Series(np.nan, index=enter.index)
    state[leave.fillna(False)] = 0.0
    state[enter.fillna(False)] = 1.0
    return state.ffill().fillna(0.0)


def sma_cross(df: pd.DataFrame, fast: int = 20, slow: int = 50) -> pd.Series:
    c = df["close"]
    return (sma(c, fast) > sma(c, slow)).astype(float)


def rsi_reversion(df: pd.DataFrame, low: int = 30, high: int = 70) -> pd.Series:
    r = rsi(df["close"])
    return _hold(r < low, r > high)


def macd_trend(df: pd.DataFrame) -> pd.Series:
    m = macd(df["close"])
    return (m["macd"] > m["macd_signal"]).astype(float)


def breakout(df: pd.DataFrame, entry: int = 20, leave: int = 10) -> pd.Series:
    """Donchian: mua khi đóng cửa vượt đỉnh `entry` phiên trước, bán khi thủng đáy `leave` phiên trước."""
    c = df["close"]
    return _hold(c > df["high"].rolling(entry).max().shift(1), c < df["low"].rolling(leave).min().shift(1))


def buy_and_hold(df: pd.DataFrame) -> pd.Series:
    return pd.Series(1.0, index=df.index)


@dataclass(frozen=True)
class Strategy:
    label: str
    signal: Callable[..., pd.Series]
    params: dict[str, tuple[int, int, int]] = field(default_factory=dict)  # tên -> (mặc định, nhỏ nhất, lớn nhất)
    help: str = ""


STRATEGIES: dict[str, Strategy] = {
    "sma_cross": Strategy("Giao cắt SMA", sma_cross, {"fast": (20, 2, 100), "slow": (50, 5, 250)},
                          "Nắm giữ khi SMA nhanh nằm trên SMA chậm."),
    "rsi_reversion": Strategy("RSI đảo chiều", rsi_reversion, {"low": (30, 5, 50), "high": (70, 50, 95)},
                              "Mua khi RSI(14) dưới ngưỡng thấp, bán khi vượt ngưỡng cao."),
    "macd_trend": Strategy("MACD theo xu hướng", macd_trend, {},
                           "Nắm giữ khi đường MACD(12,26) nằm trên Signal(9)."),
    "breakout": Strategy("Phá vỡ kênh Donchian", breakout, {"entry": (20, 5, 120), "leave": (10, 3, 60)},
                         "Mua khi đóng cửa vượt đỉnh N phiên, bán khi thủng đáy M phiên."),
}


PARAM_LABELS = {
    "fast": "SMA nhanh (phiên)", "slow": "SMA chậm (phiên)", "low": "Mua khi RSI dưới", "high": "Bán khi RSI trên",
    "entry": "Mua khi vượt đỉnh N phiên", "leave": "Bán khi thủng đáy M phiên",
}


@dataclass
class BacktestResult:
    equity: pd.Series  # giá trị tài khoản cuối mỗi phiên (VND)
    position: pd.Series  # 1 = đang nắm giữ
    trades: pd.DataFrame
    stats: dict[str, float]


def _max_drawdown(equity: pd.Series) -> float:
    return float((equity / equity.cummax() - 1).min() * 100)


def run_backtest(df: pd.DataFrame, signal: pd.Series, capital: float = 100_000_000, fee_pct: float = 0.15,
                 tax_pct: float = 0.1, min_hold: int = 2) -> BacktestResult:
    """Mua/bán toàn bộ vốn (cho phép lẻ cổ phiếu). min_hold: số phiên tối thiểu từ ngày mua đến ngày bán (T+2)."""
    if len(df) < 2:
        raise ValueError("Cần ít nhất 2 phiên dữ liệu")
    close = df["close"].to_numpy(dtype=float)
    open_ = df["open"].to_numpy(dtype=float)
    open_ = np.where(np.isfinite(open_) & (open_ > 0), open_, close)
    want = signal.reindex(df.index).fillna(0.0).to_numpy()
    fee, tax = fee_pct / 100, tax_pct / 100

    cash, shares, entry_i, entry_px, entry_cost = capital, 0.0, -1, 0.0, 0.0
    equity = np.empty(len(df))
    held = np.zeros(len(df))
    equity[0] = capital
    trades, fees_paid = [], 0.0
    for i in range(1, len(df)):
        px = open_[i]
        if want[i - 1] > 0 and shares == 0:
            shares = cash * (1 - fee) / px
            fees_paid += cash * fee
            entry_i, entry_px, entry_cost, cash = i, px, cash, 0.0
        elif want[i - 1] <= 0 and shares > 0 and i - entry_i >= min_hold:
            gross = shares * px
            cash = gross * (1 - fee - tax)
            fees_paid += gross * (fee + tax)
            trades.append((df.index[entry_i], entry_px, df.index[i], px, i - entry_i,
                           (cash / entry_cost - 1) * 100, False))
            shares = 0.0
        held[i] = float(shares > 0)
        equity[i] = cash + shares * close[i]
    if shares > 0:  # vị thế còn mở: định giá theo giá đóng cửa cuối, chưa trừ phí bán
        trades.append((df.index[entry_i], entry_px, df.index[-1], close[-1], len(df) - 1 - entry_i,
                       (shares * close[-1] / entry_cost - 1) * 100, True))

    eq = pd.Series(equity, index=df.index, name="equity")
    tr = pd.DataFrame(trades, columns=["entry_date", "entry_price", "exit_date", "exit_price", "sessions",
                                       "return_pct", "open"])
    closed = tr[~tr["open"]]
    daily = eq.pct_change().dropna()
    years = max((df.index[-1] - df.index[0]).days / 365.25, 1e-9)
    stats = {
        "total_return": (eq.iloc[-1] / capital - 1) * 100,
        "cagr": ((eq.iloc[-1] / capital) ** (1 / years) - 1) * 100 if eq.iloc[-1] > 0 else -100.0,
        "max_drawdown": _max_drawdown(eq),
        "sharpe": float(daily.mean() / daily.std() * math.sqrt(252)) if daily.std() > 0 else math.nan,
        "trades": float(len(closed)),
        "win_rate": float((closed["return_pct"] > 0).mean() * 100) if len(closed) else math.nan,
        "avg_trade": float(closed["return_pct"].mean()) if len(closed) else math.nan,
        "exposure": float(held[1:].mean() * 100),
        "fees": fees_paid,
    }
    return BacktestResult(eq, pd.Series(held, index=df.index, name="position"), tr, stats)
