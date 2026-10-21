"""Mô hình thống kê: Naive (bước ngẫu nhiên), Holt-Winters/ETS, ARIMA. Tất cả mô hình hóa log(giá)."""
from __future__ import annotations

import warnings
from contextlib import contextmanager

import numpy as np
import pandas as pd
from scipy.stats import norm

from .base import Arr, BaseForecaster


@contextmanager
def _quiet():
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        yield


def _log_close(df: pd.DataFrame) -> pd.Series:
    # RangeIndex để statsmodels không cảnh báo về tần suất ngày (có ngày nghỉ).
    return pd.Series(np.log(df["close"].to_numpy(dtype=float)))


class NaiveForecaster(BaseForecaster):
    """Giá ngày mai = giá hôm nay. Mốc chuẩn: mô hình tốt phải thắng được mốc này."""

    key = "naive"
    label = "Naive (giữ giá cuối)"
    min_history = 30
    default_lookback = 250

    def _predict(self, df: pd.DataFrame, horizon: int) -> tuple[Arr, Arr, Arr]:
        y = _log_close(df)
        sigma = y.diff().std()
        steps = np.arange(1, horizon + 1)
        z = norm.ppf(1 - self.alpha / 2)
        last = df["close"].iloc[-1]
        band = z * sigma * np.sqrt(steps)
        return np.full(horizon, last), last * np.exp(-band), last * np.exp(band)


class ETSForecaster(BaseForecaster):
    """Holt-Winters (ETS) với xu hướng cộng tính tắt dần (damped trend)."""

    key = "ets"
    label = "Holt-Winters (ETS)"

    def _predict(self, df: pd.DataFrame, horizon: int) -> tuple[Arr, Arr, Arr]:
        from statsmodels.tsa.exponential_smoothing.ets import ETSModel

        y = _log_close(df)
        with _quiet():
            fit = ETSModel(y, error="add", trend="add", damped_trend=True).fit(disp=False)
            frame = fit.get_prediction(start=len(y), end=len(y) + horizon - 1).summary_frame(alpha=self.alpha)
        return np.exp(frame["mean"]), np.exp(frame["pi_lower"]), np.exp(frame["pi_upper"])


class ARIMAForecaster(BaseForecaster):
    """ARIMA(p,1,q) chọn bậc theo AIC trên lưới nhỏ."""

    key = "arima"
    label = "ARIMA (tự chọn bậc)"
    ORDERS = ((0, 1, 0), (1, 1, 0), (0, 1, 1), (1, 1, 1), (2, 1, 2))
    TRENDS = ("n", "t")  # không trôi / có trôi (drift)

    def _predict(self, df: pd.DataFrame, horizon: int) -> tuple[Arr, Arr, Arr]:
        from statsmodels.tsa.arima.model import ARIMA

        y = _log_close(df)
        best = None
        with _quiet():
            for order in self.ORDERS:
                for trend in self.TRENDS:
                    try:
                        res = ARIMA(y, order=order, trend=trend).fit()
                    except Exception:  # noqa: BLE001 - bậc không hội tụ thì bỏ qua
                        continue
                    if best is None or res.aic < best.aic:
                        best = res
            if best is None:
                raise RuntimeError("ARIMA không hội tụ với mọi cấu hình")
            frame = best.get_forecast(horizon).summary_frame(alpha=self.alpha)
        self.selected = (best.model.order, best.model.trend)
        return np.exp(frame["mean"]), np.exp(frame["mean_ci_lower"]), np.exp(frame["mean_ci_upper"])
