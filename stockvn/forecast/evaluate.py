"""Kiểm định walk-forward, gộp mô hình (ensemble) và hàm chạy tổng hợp."""
from __future__ import annotations

import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from .base import BaseForecaster, ForecastResult


@dataclass
class Score:
    key: str
    label: str
    mape: float  # sai số phần trăm tuyệt đối trung bình (%)
    rmse: float  # VND
    direction_acc: float | None  # tỷ lệ đoán đúng hướng tăng/giảm cuối tầm (%); None nếu mô hình không dự báo hướng
    coverage: float | None  # tỷ lệ giá thực tế nằm trong khoảng dự báo (%)
    folds: int


def backtest(model: BaseForecaster, df: pd.DataFrame, horizon: int, folds: int = 3) -> Score:
    """Cắt chuỗi tại `folds` mốc liên tiếp cuối dữ liệu, mỗi mốc dự báo `horizon` phiên rồi so với thực tế."""
    ape, sq, direction, cover = [], [], [], []
    used = 0
    for i in range(folds):
        cutoff = len(df) - horizon * (folds - i)
        train, test = df.iloc[:cutoff], df.iloc[cutoff:cutoff + horizon]
        if len(train) < model.min_history or test.empty:
            continue
        res = model.forecast(train, horizon)
        actual = test["close"].to_numpy(dtype=float)
        pred = res.mean[: len(actual)]
        base = float(train["close"].iloc[-1])
        ape.extend(np.abs(pred - actual) / actual)
        sq.extend((pred - actual) ** 2)
        if (pred_dir := np.sign(pred[-1] - base)) != 0:  # dự báo đi ngang (Naive) không có hướng
            direction.append(pred_dir == np.sign(actual[-1] - base))
        if res.lower is not None and res.upper is not None:
            lo, hi = res.lower[: len(actual)], res.upper[: len(actual)]
            cover.extend((actual >= lo) & (actual <= hi))
        used += 1
    if not used:
        raise ValueError("Không đủ dữ liệu để kiểm định")
    return Score(
        key=model.key, label=model.label,
        mape=float(np.mean(ape) * 100), rmse=float(np.sqrt(np.mean(sq))),
        direction_acc=float(np.mean(direction) * 100) if direction else None,
        coverage=float(np.mean(cover) * 100) if cover else None,
        folds=used,
    )


def ensemble(results: Iterable[ForecastResult], weights: dict[str, float] | None = None) -> ForecastResult | None:
    """Trung bình có trọng số trong không gian log. Trọng số mặc định bằng nhau."""
    results = [r for r in results if r.key != "naive"]
    if weights and any(weights.get(r.key, 0) > 0 for r in results):
        results = [r for r in results if weights.get(r.key, 0) > 0]
    else:
        weights = None
    if not results:
        return None
    w = np.array([weights[r.key] if weights else 1.0 for r in results], dtype=float)
    w = w / w.sum()

    def combine(attr: str):
        arrs = [getattr(r, attr) for r in results]
        if any(a is None for a in arrs):
            return None
        return np.exp(np.tensordot(w, np.log(np.vstack(arrs)), axes=1))

    return ForecastResult(
        key="ensemble", label="Tổng hợp (Ensemble)", dates=results[0].dates,
        mean=combine("mean"), lower=combine("lower"), upper=combine("upper"),
    )


@dataclass
class ForecastReport:
    results: dict[str, ForecastResult] = field(default_factory=dict)
    scores: dict[str, Score] = field(default_factory=dict)
    errors: dict[str, str] = field(default_factory=dict)
    seconds: dict[str, float] = field(default_factory=dict)
    ensemble: ForecastResult | None = None

    def scores_frame(self) -> pd.DataFrame:
        return pd.DataFrame([vars(s) for s in self.scores.values()])


def run_forecast(
    df: pd.DataFrame,
    horizon: int,
    models: Iterable[type[BaseForecaster]],
    backtest_folds: int = 0,
    alpha: float = 0.2,
    progress: Callable[[str, float], None] | None = None,
) -> ForecastReport:
    models = list(models)
    report = ForecastReport()
    for i, cls in enumerate(models):
        if progress:
            progress(f"Đang chạy {cls.label}…", i / len(models))
        t0 = time.perf_counter()
        try:
            model = cls(alpha=alpha)
            report.results[cls.key] = model.forecast(df, horizon)
            if backtest_folds:
                report.scores[cls.key] = backtest(model, df, horizon, backtest_folds)
        except Exception as exc:  # noqa: BLE001 - một mô hình lỗi không làm hỏng cả báo cáo
            report.errors[cls.key] = f"{type(exc).__name__}: {exc}"
        report.seconds[cls.key] = time.perf_counter() - t0

    # Có kiểm định -> trọng số tỷ lệ nghịch với MAPE; không có -> trọng số bằng nhau.
    weights = {k: 1 / max(s.mape, 1e-6) for k, s in report.scores.items()} or None
    report.ensemble = ensemble(report.results.values(), weights)
    if progress:
        progress("Hoàn tất", 1.0)
    return report
