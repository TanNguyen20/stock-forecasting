"""Giao diện chung cho các mô hình dự báo giá đóng cửa."""
from __future__ import annotations

import importlib.util
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import ClassVar

import numpy as np
import pandas as pd

from ..market import holidays

Arr = np.ndarray


@dataclass
class ForecastResult:
    key: str
    label: str
    dates: pd.DatetimeIndex
    mean: Arr
    lower: Arr | None = None
    upper: Arr | None = None

    def to_frame(self) -> pd.DataFrame:
        df = pd.DataFrame({"mean": self.mean}, index=self.dates)
        if self.lower is not None and self.upper is not None:
            df["lower"], df["upper"] = self.lower, self.upper
        return df


def future_business_days(last: pd.Timestamp, horizon: int) -> pd.DatetimeIndex:
    """Các ngày giao dịch tiếp theo (bỏ thứ 7, CN và ngày nghỉ lễ trong holidays.txt)."""
    return pd.bdate_range(last + pd.Timedelta(days=1), periods=horizon, freq="C",
                          holidays=sorted(holidays()), name="date")


def quantiles_to_prices(last_price: float, cum_logret: Arr, lo: Arr | None, hi: Arr | None):
    to_p = lambda x: None if x is None else last_price * np.exp(np.asarray(x, dtype=float))  # noqa: E731
    return to_p(cum_logret), to_p(lo), to_p(hi)


class BaseForecaster(ABC):
    key: ClassVar[str]
    label: ClassVar[str]
    requires: ClassVar[tuple[str, ...]] = ()
    min_history: ClassVar[int] = 60
    default_lookback: ClassVar[int] = 750  # số phiên gần nhất dùng để huấn luyện

    def __init__(self, alpha: float = 0.2, lookback: int | None = None):
        self.alpha = alpha  # 0.2 -> khoảng dự báo 80%
        self.lookback = lookback or self.default_lookback

    @classmethod
    def available(cls) -> bool:
        return all(importlib.util.find_spec(m) is not None for m in cls.requires)

    def forecast(self, df: pd.DataFrame, horizon: int) -> ForecastResult:
        """df: index ngày, cột open/high/low/close/volume (tăng dần theo thời gian)."""
        if horizon < 1:
            raise ValueError("horizon phải >= 1")
        data = df.dropna(subset=["close"])
        data = data[data["close"] > 0].iloc[-self.lookback:]
        if len(data) < self.min_history:
            raise ValueError(f"{self.label}: cần tối thiểu {self.min_history} phiên dữ liệu, hiện có {len(data)}")
        mean, lower, upper = self._predict(data, horizon)
        return ForecastResult(
            key=self.key, label=self.label, dates=future_business_days(data.index[-1], horizon),
            mean=np.asarray(mean, dtype=float),
            lower=None if lower is None else np.asarray(lower, dtype=float),
            upper=None if upper is None else np.asarray(upper, dtype=float),
        )

    @abstractmethod
    def _predict(self, df: pd.DataFrame, horizon: int) -> tuple[Arr, Arr | None, Arr | None]:
        """Trả về (giá dự báo, cận dưới, cận trên) cho horizon phiên tiếp theo."""
