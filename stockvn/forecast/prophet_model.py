"""Prophet (Meta): xu hướng tuyến tính từng đoạn + mùa vụ năm, trên log(giá)."""
from __future__ import annotations

import logging

import numpy as np
import pandas as pd

from .base import Arr, BaseForecaster, future_business_days


class ProphetForecaster(BaseForecaster):
    key = "prophet"
    label = "Prophet"
    requires = ("prophet",)
    min_history = 120

    def _predict(self, df: pd.DataFrame, horizon: int) -> tuple[Arr, Arr, Arr]:
        from prophet import Prophet

        # cmdstanpy đặt lại mức log khi import -> phải hạ sau khi import.
        for name in ("prophet", "cmdstanpy"):
            logging.getLogger(name).setLevel(logging.WARNING)

        hist = pd.DataFrame({"ds": df.index, "y": np.log(df["close"].to_numpy(dtype=float))})
        m = Prophet(
            interval_width=1 - self.alpha,
            daily_seasonality=False,
            weekly_seasonality=False,  # không có giao dịch cuối tuần
            yearly_seasonality=len(df) >= 500,
            changepoint_prior_scale=0.05,
        )
        m.fit(hist)
        fc = m.predict(pd.DataFrame({"ds": future_business_days(df.index[-1], horizon)}))
        return np.exp(fc["yhat"]), np.exp(fc["yhat_lower"]), np.exp(fc["yhat_upper"])
