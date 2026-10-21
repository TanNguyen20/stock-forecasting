"""Các mô hình dự báo giá. Prophet và LSTM là tùy chọn (chỉ bật khi đã cài prophet / torch)."""
from __future__ import annotations

from .base import BaseForecaster, ForecastResult, future_business_days
from .evaluate import ForecastReport, Score, backtest, ensemble, run_forecast
from .lstm import LSTMForecaster
from .ml import GBMForecaster
from .prophet_model import ProphetForecaster
from .statistical import ARIMAForecaster, ETSForecaster, NaiveForecaster

ALL_MODELS: dict[str, type[BaseForecaster]] = {
    cls.key: cls
    for cls in (NaiveForecaster, ETSForecaster, ARIMAForecaster, GBMForecaster, ProphetForecaster, LSTMForecaster)
}
DEFAULT_MODELS = ("naive", "ets", "arima", "gbm")


def available_models() -> dict[str, type[BaseForecaster]]:
    return {k: cls for k, cls in ALL_MODELS.items() if cls.available()}


__all__ = [
    "ALL_MODELS", "DEFAULT_MODELS", "BaseForecaster", "ForecastReport", "ForecastResult", "Score",
    "available_models", "backtest", "ensemble", "future_business_days", "run_forecast",
]
