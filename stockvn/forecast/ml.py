"""Gradient Boosting trên đặc trưng kỹ thuật, dự báo trực tiếp lợi suất lũy kế cho từng tầm dự báo."""
from __future__ import annotations

import numpy as np
import pandas as pd

from ..indicators import bollinger, macd, rsi, sma
from .base import Arr, BaseForecaster, quantiles_to_prices

# Huấn luyện mô hình riêng cho các tầm này, nội suy cho các tầm ở giữa.
HORIZON_GRID = (1, 2, 3, 5, 7, 10, 15, 20, 30, 45, 60)


def make_features(df: pd.DataFrame) -> pd.DataFrame:
    """Đặc trưng tại phiên t chỉ dùng thông tin đến hết phiên t (không nhìn trước)."""
    c = df["close"].astype(float)
    v = df["volume"].astype(float).replace(0, np.nan)
    logc = np.log(c)
    r = logc.diff()
    f = pd.DataFrame(index=df.index)
    for k in range(1, 6):
        f[f"r_lag{k}"] = r.shift(k - 1)
    for k in (5, 10, 20, 60):
        f[f"ret_{k}"] = logc - logc.shift(k)
    f["vol_5"] = r.rolling(5).std()
    f["vol_20"] = r.rolling(20).std()
    f["rsi14"] = rsi(c) / 100
    f["macd_hist"] = macd(c)["macd_hist"] / c
    f["dist_sma20"] = c / sma(c, 20) - 1
    f["dist_sma50"] = c / sma(c, 50) - 1
    bb = bollinger(c)
    f["bb_pos"] = (c - bb["bb_lower"]) / (bb["bb_upper"] - bb["bb_lower"])
    f["vol_ratio"] = np.log(v / v.rolling(20).mean())
    f["range"] = (df["high"] - df["low"]) / c
    return f.replace([np.inf, -np.inf], np.nan)


def _interp(horizon: int, keys: list[int], values: list[float]) -> Arr:
    return np.interp(np.arange(1, horizon + 1), [0, *keys], [0.0, *values])


class GBMForecaster(BaseForecaster):
    key = "gbm"
    label = "Gradient Boosting (ML)"
    min_history = 200
    default_lookback = 2500

    def _model(self):
        from sklearn.ensemble import HistGradientBoostingRegressor

        return HistGradientBoostingRegressor(
            max_iter=150, learning_rate=0.05, max_leaf_nodes=15, min_samples_leaf=30,
            l2_regularization=1.0, random_state=0,
        )

    def _predict(self, df: pd.DataFrame, horizon: int) -> tuple[Arr, Arr, Arr]:
        X = make_features(df)
        logc = np.log(df["close"].astype(float))
        x_last = X.iloc[[-1]]
        if x_last.isna().any(axis=None):
            x_last = X.ffill().iloc[[-1]]

        keys = sorted({k for k in HORIZON_GRID if k < horizon} | {horizon})
        mids, los, his = [], [], []
        for k in keys:
            y = logc.shift(-k) - logc
            mask = X.notna().all(axis=1) & y.notna()
            Xk, yk = X[mask], y[mask]
            if len(Xk) < 100:
                raise ValueError(f"Không đủ dữ liệu để huấn luyện tầm {k} phiên")
            # Tách tập hiệu chỉnh cuối chuỗi; bỏ k phiên đệm để nhãn không chồng lấn (purge).
            n_cal = max(40, len(Xk) // 5)
            train_end = len(Xk) - n_cal - k
            m = self._model().fit(Xk.iloc[:train_end], yk.iloc[:train_end])
            resid = yk.iloc[-n_cal:].to_numpy() - m.predict(Xk.iloc[-n_cal:])
            q_lo, q_hi = np.quantile(resid, [self.alpha / 2, 1 - self.alpha / 2])
            pred = float(self._model().fit(Xk, yk).predict(x_last)[0])
            mids.append(pred)
            los.append(pred + q_lo)
            his.append(pred + q_hi)

        last = float(df["close"].iloc[-1])
        return quantiles_to_prices(
            last, _interp(horizon, keys, mids), _interp(horizon, keys, los), _interp(horizon, keys, his)
        )
