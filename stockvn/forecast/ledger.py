"""Sổ dự báo: ghi lại mọi lần dự báo rồi chấm điểm khi đến ngày đích (kiểm định ngoài mẫu thực sự).

Walk-forward chỉ dự báo lại vài giai đoạn đã qua; sổ dự báo tích lũy dần theo thời gian thật,
nên số điểm đánh giá tăng mỗi ngày và không thể vô tình "nhìn trước" dữ liệu.
"""
from __future__ import annotations

import logging
from collections.abc import Iterable

import numpy as np
import pandas as pd

from ..db import Repository
from . import ALL_MODELS
from .evaluate import ForecastReport, run_forecast

log = logging.getLogger(__name__)

SCORE_COLUMNS = ["model", "mape", "direction_acc", "coverage", "conf", "forecasts", "points"]


def record(repo: Repository, symbol: str, df: pd.DataFrame, report: ForecastReport, conf: int) -> int:
    """Ghi các kết quả (kể cả ensemble) của một lần dự báo trên `df`."""
    made_on, base = df.index[-1].date(), float(df["close"].iloc[-1])
    results = [*report.results.values(), *([report.ensemble] if report.ensemble else [])]
    return sum(
        repo.log_forecast(symbol, r.key, made_on, base, r.dates, r.mean, r.lower, r.upper, conf)
        for r in results
    )


def score_outcomes(df: pd.DataFrame, max_step: int | None = None) -> pd.DataFrame:
    """Chấm điểm theo mô hình. df: kết quả Repository.forecast_outcomes()."""
    if max_step:
        df = df[df["step"] <= max_step]
    if df.empty:
        return pd.DataFrame(columns=SCORE_COLUMNS)
    pred_dir = np.sign(df["mean"] - df["base"])
    has_band = df["lower"].notna() & df["upper"].notna()
    d = df.assign(
        ape=(df["mean"] - df["actual"]).abs() / df["actual"] * 100,
        # Dự báo đi ngang (Naive) không có hướng -> không tính.
        hit=(pred_dir == np.sign(df["actual"] - df["base"])).where(pred_dir != 0),
        covered=((df["actual"] >= df["lower"]) & (df["actual"] <= df["upper"])).where(has_band),
        band_conf=df["conf"].where(has_band),
    )
    g = d.astype({"hit": float, "covered": float}).groupby("model")
    out = pd.DataFrame({
        "mape": g["ape"].mean(),
        "direction_acc": g["hit"].mean() * 100,
        "coverage": g["covered"].mean() * 100,
        "conf": g["band_conf"].mean(),
        "forecasts": g["made_on"].nunique(),
        "points": g.size(),
    }).reset_index()
    return out[SCORE_COLUMNS].sort_values("mape", ignore_index=True)


def mape_by_step(df: pd.DataFrame) -> pd.DataFrame:
    """MAPE (%) theo số phiên tính từ ngày dự báo: index = step, cột = mô hình."""
    if df.empty:
        return pd.DataFrame()
    ape = (df["mean"] - df["actual"]).abs() / df["actual"] * 100
    return df.assign(ape=ape).pivot_table(index="step", columns="model", values="ape", aggfunc="mean")


def auto_forecast(repo: Repository, symbols: Iterable[str], model_keys: Iterable[str], horizon: int,
                  conf: int = 80, min_rows: int = 300) -> dict[str, str]:
    """Dự báo và ghi sổ cho từng mã (worker chạy sau giờ đóng cửa). Trả về lỗi theo mã."""
    models = [ALL_MODELS[k] for k in model_keys if k in ALL_MODELS and ALL_MODELS[k].available()]
    errors: dict[str, str] = {}
    if not models:
        return errors
    for sym in symbols:
        df = repo.get_prices(sym)
        if len(df) < min_rows:
            continue
        report = run_forecast(df, horizon, models, alpha=1 - conf / 100)
        n = record(repo, sym, df, report, conf)
        if report.errors:
            errors[sym] = "; ".join(f"{k}: {v}" for k, v in report.errors.items())
        log.info("Đã ghi sổ dự báo %s: %d điểm, %d mô hình lỗi", sym, n, len(report.errors))
    return errors
