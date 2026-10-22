import numpy as np
import pandas as pd
import pytest

from stockvn.forecast import ALL_MODELS, run_forecast
from stockvn.forecast.ledger import auto_forecast, mape_by_step, record, score_outcomes
from stockvn.screener import RULES, screen
from stockvn.strategy import STRATEGIES, buy_and_hold, run_backtest, sma_cross

from .conftest import make_prices


# ---------------------------------------------------------------- Sổ dự báo
def outcomes(**overrides) -> pd.DataFrame:
    base = {"symbol": "FPT", "model": "ets", "made_on": pd.Timestamp("2026-10-01"),
            "target_date": pd.Timestamp("2026-10-02"), "step": 1, "base": 100.0, "mean": 110.0,
            "lower": 95.0, "upper": 120.0, "conf": 80, "actual": 100.0}
    rows = overrides.pop("rows", [{}])
    return pd.DataFrame([{**base, **overrides, **r} for r in rows])


def test_score_outcomes_metrics():
    df = outcomes(rows=[
        {"actual": 100.0},                                 # sai 10%, sai hướng (đứng giá), trong khoảng
        {"actual": 125.0, "step": 2, "target_date": pd.Timestamp("2026-10-05")},  # đúng hướng, ngoài khoảng
        {"model": "naive", "mean": 100.0, "lower": None, "upper": None, "actual": 105.0},
    ])
    sc = score_outcomes(df).set_index("model")
    assert sc.loc["ets", "mape"] == pytest.approx((10 + 12) / 2)
    assert sc.loc["ets", "direction_acc"] == pytest.approx(50)
    assert sc.loc["ets", "coverage"] == pytest.approx(50) and sc.loc["ets", "conf"] == 80
    assert sc.loc["ets", "forecasts"] == 1 and sc.loc["ets", "points"] == 2
    assert np.isnan(sc.loc["naive", "direction_acc"]) and np.isnan(sc.loc["naive", "coverage"])
    assert list(score_outcomes(df, max_step=1).set_index("model").loc[:, "points"]) == [1, 1]
    assert score_outcomes(df.iloc[:0]).empty
    assert mape_by_step(df).loc[2, "ets"] == pytest.approx(12)


def test_ledger_roundtrip_scores_only_matured_forecasts(repo):
    df = make_prices(400)
    hist, future = df.iloc[:-10], df.iloc[-10:]
    repo.upsert_prices("FPT", hist, "T")
    report = run_forecast(hist, 10, [ALL_MODELS["naive"], ALL_MODELS["ets"]])
    assert record(repo, "FPT", hist, report, conf=80) == 30  # 2 mô hình + ensemble
    assert repo.forecast_log_count("FPT") == 30
    assert repo.forecast_outcomes("FPT").empty  # chưa đến ngày đích

    record(repo, "FPT", hist, report, conf=80)  # chạy lại cùng ngày dữ liệu: ghi đè
    assert repo.forecast_log_count() == 30

    repo.upsert_prices("FPT", future.iloc[:4], "T")
    got = repo.forecast_outcomes("FPT")
    assert len(got) == 12 and set(got["model"]) == {"naive", "ets", "ensemble"}
    assert len(repo.forecast_outcomes("FPT", until=future.index[1].date())) == 6
    naive = got[got["model"] == "naive"]
    assert (naive["mean"] == hist["close"].iloc[-1]).all()
    assert np.allclose(naive["actual"], future["close"].iloc[:4])


def test_auto_forecast_skips_short_history(repo):
    repo.upsert_prices("FPT", make_prices(400), "T")
    repo.upsert_prices("NEW", make_prices(50), "T")
    errors = auto_forecast(repo, ["FPT", "NEW"], ["naive", "khong-co"], horizon=5)
    assert errors == {}
    assert repo.forecast_log_count("FPT") == 5 and repo.forecast_log_count("NEW") == 0


# ---------------------------------------------------------------- Bộ lọc
def test_screen_rules_and_liquidity():
    up = make_prices(300, seed=1, drift=0.004)  # xu hướng tăng mạnh
    down = make_prices(300, seed=2, drift=-0.004)
    data = {"UP": up, "DOWN": down}
    assert screen(data, ["above_sma200"])["symbol"].tolist() == ["UP"]
    assert screen(data, ["below_sma200"])["symbol"].tolist() == ["DOWN"]
    assert screen(data, ["above_sma200", "below_sma200"]).empty
    assert set(screen(data, [])["symbol"]) == {"UP", "DOWN"}
    assert screen(data, [], min_value_bn=1e9).empty
    snap = screen(data, [])
    assert {"price", "rsi", "value_bn", "dist_sma200"} <= set(snap.columns)


def test_screen_skips_stale_and_short_series():
    fresh = make_prices(300)
    stale = make_prices(300)
    stale.index = stale.index - pd.Timedelta(days=60)
    data = {"FRESH": fresh, "STALE": stale, "SHORT": make_prices(30)}
    assert screen(data, [])["symbol"].tolist() == ["FRESH"]


def test_every_rule_runs_on_real_like_data():
    from stockvn.indicators import add_indicators

    d = add_indicators(make_prices(300))
    for key, rule in RULES.items():
        assert isinstance(bool(rule.test(d)), bool), key


def test_latest_prices_bulk_read(repo):
    repo.upsert_prices("A", make_prices(50), "T")
    repo.upsert_prices("B", make_prices(20), "T")
    got = repo.latest_prices(["A", "B", "C"], 30)
    assert set(got) == {"A", "B"} and len(got["A"]) == 30 and len(got["B"]) == 20
    assert got["A"].index.is_monotonic_increasing


# ---------------------------------------------------------------- Backtest
def flat(prices, opens=None) -> pd.DataFrame:
    c = np.asarray(prices, dtype=float)
    o = c if opens is None else np.asarray(opens, dtype=float)
    idx = pd.bdate_range("2026-01-05", periods=len(c), name="date")
    return pd.DataFrame({"open": o, "high": np.maximum(o, c), "low": np.minimum(o, c), "close": c, "volume": 1e6},
                        index=idx)


def test_backtest_executes_next_open_with_fees():
    df = flat([100, 100, 110, 120, 120], opens=[100, 100, 110, 120, 130])
    signal = pd.Series([1, 1, 1, 0, 0], index=df.index, dtype=float)
    res = run_backtest(df, signal, capital=1_000, fee_pct=0.1, tax_pct=0.1, min_hold=2)
    # Mua ở giá mở cửa phiên 2 (100), bán ở giá mở cửa phiên 5 (130) sau tín hiệu bán phiên 4.
    t = res.trades.iloc[0]
    assert (t["entry_price"], t["exit_price"], t["sessions"], bool(t["open"])) == (100, 130, 3, False)
    expected = 1_000 * (1 - 0.001) / 100 * 130 * (1 - 0.002)
    assert res.equity.iloc[-1] == pytest.approx(expected)
    assert res.stats["trades"] == 1 and res.stats["win_rate"] == 100
    assert res.stats["fees"] == pytest.approx(1_000 * 0.001 + 1_000 * 0.999 / 100 * 130 * 0.002)


def test_backtest_respects_t_plus_2():
    df = flat([100] * 6)
    signal = pd.Series([1, 0, 0, 0, 0, 0], index=df.index, dtype=float)  # muốn bán ngay phiên sau khi mua
    res = run_backtest(df, signal, fee_pct=0, tax_pct=0, min_hold=2)
    t = res.trades.iloc[0]
    assert t["entry_date"] == df.index[1] and t["exit_date"] == df.index[3]


def test_backtest_open_position_and_buy_and_hold():
    df = make_prices(300, drift=0.002)
    res = run_backtest(df, buy_and_hold(df), fee_pct=0, tax_pct=0)
    assert res.stats["trades"] == 0 and bool(res.trades.iloc[-1]["open"])
    assert res.stats["total_return"] == pytest.approx((df["close"].iloc[-1] / df["open"].iloc[1] - 1) * 100)
    assert res.stats["max_drawdown"] <= 0 and res.stats["exposure"] == pytest.approx(100)


def test_signals_use_only_past_data():
    df = make_prices(300)
    for key, strat in STRATEGIES.items():
        full = strat.signal(df)
        cut = strat.signal(df.iloc[:200])
        assert full.iloc[:200].equals(cut), key  # thêm dữ liệu tương lai không đổi tín hiệu quá khứ
        assert set(full.unique()) <= {0.0, 1.0}


def test_sma_cross_signal():
    df = flat([10] * 60 + [20] * 10)
    sig = sma_cross(df, fast=5, slow=20)
    assert sig.iloc[59] == 0 and sig.iloc[-1] == 1


def test_backtest_needs_data():
    with pytest.raises(ValueError):
        run_backtest(flat([1]), pd.Series([1.0]))
