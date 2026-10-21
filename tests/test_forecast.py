import numpy as np
import pytest

from stockvn.forecast import ALL_MODELS, backtest, ensemble, future_business_days, run_forecast
from stockvn.forecast.ml import make_features

from .conftest import make_prices

HORIZON = 10


@pytest.mark.parametrize("key", list(ALL_MODELS))
def test_model_output_shape_and_sanity(key, prices):
    cls = ALL_MODELS[key]
    if not cls.available():
        pytest.skip(f"chưa cài {cls.requires}")
    res = cls().forecast(prices, HORIZON)
    assert len(res.mean) == len(res.dates) == HORIZON
    assert np.isfinite(res.mean).all() and (res.mean > 0).all()
    assert res.dates[0] > prices.index[-1] and all(d.weekday() < 5 for d in res.dates)
    last = prices["close"].iloc[-1]
    assert np.all(np.abs(res.mean / last - 1) < 0.5), "dự báo 10 phiên không được lệch quá 50%"
    if res.lower is not None:
        assert (res.lower <= res.mean + 1e-6).all() and (res.mean <= res.upper + 1e-6).all()


def test_too_little_history_raises(prices):
    with pytest.raises(ValueError):
        ALL_MODELS["gbm"]().forecast(prices.head(50), HORIZON)


def test_features_do_not_look_ahead(prices):
    """Thay đổi dữ liệu tương lai không được làm đổi đặc trưng ở quá khứ."""
    f1 = make_features(prices)
    changed = prices.copy()
    changed.iloc[-20:, changed.columns.get_loc("close")] *= 1.5
    f2 = make_features(changed)
    np.testing.assert_allclose(f1.iloc[:-20].to_numpy(), f2.iloc[:-20].to_numpy(), equal_nan=True)


def test_backtest_naive_on_random_walk(prices):
    s = backtest(ALL_MODELS["naive"](), prices, HORIZON, folds=3)
    assert s.folds == 3 and s.mape > 0 and s.direction_acc is None
    assert 0 <= s.coverage <= 100


def test_ensemble_weights():
    prices = make_prices(400)
    naive = ALL_MODELS["naive"]().forecast(prices, 5)
    ets = ALL_MODELS["ets"]().forecast(prices, 5)
    e = ensemble([naive, ets])
    np.testing.assert_allclose(e.mean, ets.mean)  # naive bị loại khỏi ensemble
    assert ensemble([naive]) is None


def test_run_forecast_collects_errors(prices):
    class Broken(ALL_MODELS["naive"]):
        key, label = "broken", "Lỗi"

        def _predict(self, df, horizon):
            raise RuntimeError("boom")

    rep = run_forecast(prices, 5, [ALL_MODELS["ets"], Broken], backtest_folds=2)
    assert "ets" in rep.results and "broken" in rep.errors and "ets" in rep.scores
    assert rep.ensemble is not None


def test_future_business_days_skip_weekend():
    import pandas as pd

    days = future_business_days(pd.Timestamp("2026-10-09"), 3)  # thứ 6
    assert [d.strftime("%a") for d in days] == ["Mon", "Tue", "Wed"]
