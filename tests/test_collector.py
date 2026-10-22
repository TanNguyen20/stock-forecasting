from datetime import date

import pandas as pd
import pytest

from stockvn.collector import (
    Collector, SourceError, build_quote, parse_dnse, parse_kbs, parse_vci, parse_vci_symbols,
)
from stockvn.market import today_vn

from .conftest import make_prices

VCI_PAYLOAD = [{
    "symbol": "VNM", "o": [59400, 59000], "h": [59600, 59000], "l": [58500, 57300], "c": [58600, 57300],
    "v": [3690200, 3579300], "t": ["1790812800", "1790899200"],  # 01/10 và 02/10/2026 00:00 UTC
}]
DNSE_PAYLOAD = {"t": [1790820000, 1790906400], "o": [59.4, 59.0], "h": [59.6, 59.0], "l": [58.5, 57.3],
                "c": [58.6, 57.3], "v": [3690200, 3579300]}
KBS_PAYLOAD = {"symbol": "VNM", "data_day": [
    {"t": "2026-10-02 07:00", "o": 59000, "h": 59000, "l": 57300, "c": 57300, "v": 3579300},
    {"t": "2026-10-01 07:00", "o": 59400, "h": 59600, "l": 58500, "c": 58600, "v": 3690200},
]}


def _check(df: pd.DataFrame):
    df = df.sort_index()
    assert list(df.index.date) == [date(2026, 10, 1), date(2026, 10, 2)]
    assert df["close"].tolist() == [58600, 57300]


def test_parsers_normalize_to_vnd_and_vn_dates():
    _check(parse_vci(VCI_PAYLOAD))
    _check(parse_dnse(DNSE_PAYLOAD, scale=1000))
    _check(parse_kbs(KBS_PAYLOAD))


def test_parsers_handle_empty():
    assert parse_vci([]).empty
    assert parse_dnse({}, scale=1000).empty
    assert parse_kbs({"data_day": []}).empty


def test_parse_symbols():
    df = parse_vci_symbols([
        {"symbol": "FPT", "board": "HSX", "type": "STOCK", "organName": "Công ty Cổ phần FPT", "organShortName": "FPT Corp"},
        {"symbol": "ABC", "board": "UPCOM", "type": "STOCK", "organName": "Công ty ABC", "organShortName": None},
    ])
    assert df.loc[0, "exchange"] == "HOSE"
    assert df.loc[0, "name"] == "FPT Corp"
    assert df.loc[1, "name"] == "Công ty ABC"


def test_build_quote(prices):
    q = build_quote("FPT", prices, "TEST")
    assert q.price == prices["close"].iloc[-1]
    assert q.ref_price == prices["close"].iloc[-2]
    assert q.avg_volume20 == pytest.approx(prices["volume"].iloc[-21:-1].mean())
    assert q.change_pct == pytest.approx((q.price / q.ref_price - 1) * 100)


class _Src:
    def __init__(self, name, result):
        self.name, self.result, self.calls = name, result, 0

    def history(self, symbol, start, end):
        self.calls += 1
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


def test_collector_falls_back_to_next_source(repo, prices):
    c = Collector(repo)
    bad, empty, good = _Src("A", RuntimeError("down")), _Src("B", prices.iloc[:0]), _Src("C", prices)
    c.sources = [bad, empty, good]
    df, src = c.fetch_history("fpt", date(2022, 1, 1), date(2026, 1, 1))
    assert src == "C" and len(df) == len(prices)
    assert bad.calls == empty.calls == good.calls == 1


def test_collector_raises_when_all_fail(repo):
    c = Collector(repo)
    c.sources = [_Src("A", RuntimeError("x"))]
    with pytest.raises(SourceError):
        c.fetch_history("FPT", date(2026, 1, 1), date(2026, 2, 1))


def test_refresh_quote_persists(repo, prices):
    c = Collector(repo)
    c.sources = [_Src("T", prices.tail(30))]
    q = c.refresh_quote("FPT")
    assert repo.get_quotes(["FPT"])["FPT"].price == q.price
    assert repo.price_range("FPT")[2] == 30


def _past_prices(n: int = 30, scale: float = 1.0) -> pd.DataFrame:
    """n phiên kết thúc trước hôm nay (không có phiên hôm nay)."""
    df = make_prices(n)
    df.index = pd.bdate_range(end=pd.Timestamp(today_vn()) - pd.Timedelta(days=1), periods=n, name="date")
    df[["open", "high", "low", "close"]] *= scale
    return df


def _collector_with(repo, fresh: pd.DataFrame, name: str = "VCI"):
    c = Collector(repo)
    c.sources = [_Src(name, fresh)]
    full_syncs: list[str] = []
    c.sync_history = lambda symbol, years=10, full=False: full_syncs.append(symbol) or 0  # type: ignore[method-assign]
    return c, full_syncs


def test_adjusted_history_triggers_full_reload(repo):
    stored = _past_prices()
    repo.upsert_prices("FPT", stored, "VCI")
    c, full_syncs = _collector_with(repo, _past_prices(scale=0.95))  # nguồn hạ giá quá khứ 5% (chia cổ tức)
    c.refresh_quote("FPT")
    assert full_syncs == ["FPT"]


def test_same_history_is_upserted_without_reload(repo):
    repo.upsert_prices("FPT", _past_prices(), "VCI")
    c, full_syncs = _collector_with(repo, _past_prices())
    c.refresh_quote("FPT")
    assert full_syncs == []


def test_single_stale_session_is_not_an_adjustment(repo):
    stored = _past_prices()
    stored.iloc[-1, stored.columns.get_loc("close")] *= 1.03  # phiên cuối lưu giữa giờ, giá đóng cửa khác
    repo.upsert_prices("FPT", stored, "VCI")
    c, full_syncs = _collector_with(repo, _past_prices())
    c.refresh_quote("FPT")
    assert full_syncs == []
    assert repo.get_prices("FPT")["close"].iloc[-1] == pytest.approx(_past_prices()["close"].iloc[-1])


def test_different_source_is_not_compared(repo):
    repo.upsert_prices("FPT", _past_prices(), "DNSE")
    c, full_syncs = _collector_with(repo, _past_prices(scale=0.9), name="VCI")
    c.refresh_quote("FPT")
    assert full_syncs == []
