import sqlite3
from dataclasses import replace
from datetime import date, datetime

import numpy as np
import pandas as pd
import pytest

from stockvn.alerts import AlertEngine, format_message, is_met, metric_value
from stockvn.db import Repository
from stockvn.market import VN_TZ, now_vn
from stockvn.models import Alert, AlertKind, Channel, Quote


def quote(price: float, ref: float = 50_000, volume: float = 1_000_000, avg: float = 1_000_000) -> Quote:
    return Quote("FPT", date(2026, 10, 7), price, ref, volume, avg, now_vn(), "TEST")


class FakeNotifier:
    def __init__(self):
        self.sent = []

    def send(self, channels, title, body):
        self.sent.append((channels, title, body))
        return {c: "ok" for c in channels}


@pytest.mark.parametrize("kind,q,threshold,expected", [
    (AlertKind.PRICE_ABOVE, quote(55_000), 55_000, True),
    (AlertKind.PRICE_ABOVE, quote(54_900), 55_000, False),
    (AlertKind.PRICE_BELOW, quote(45_000), 45_000, True),
    (AlertKind.CHANGE_UP, quote(51_500), 3, True),       # +3%
    (AlertKind.CHANGE_DOWN, quote(48_500), 3, True),     # -3%
    (AlertKind.CHANGE_DOWN, quote(51_500), 3, False),
    (AlertKind.VOLUME_SPIKE, quote(50_000, volume=2_500_000), 2, True),
])
def test_conditions(kind, q, threshold, expected):
    assert is_met(kind, metric_value(kind, q), threshold) is expected


def test_volume_spike_without_average_is_skipped():
    assert metric_value(AlertKind.VOLUME_SPIKE, quote(50_000, avg=0)) is None


def test_edge_trigger_fires_once_then_rearms(repo):
    notifier = FakeNotifier()
    engine = AlertEngine(repo, notifier)
    alert_id = repo.add_alert(Alert("FPT", AlertKind.PRICE_ABOVE, 55_000, (Channel.TOAST,)))

    assert engine.check({"FPT": quote(54_000)}) == []
    assert len(engine.check({"FPT": quote(55_500)})) == 1
    assert engine.check({"FPT": quote(56_000)}) == []  # vẫn trên ngưỡng: không gửi lặp
    assert repo.list_alerts()[0].armed is False

    engine.check({"FPT": quote(54_000)})  # rơi xuống dưới -> nạp lại
    assert repo.list_alerts()[0].armed is True
    assert len(engine.check({"FPT": quote(55_100)})) == 1

    assert len(notifier.sent) == 2
    log = repo.alert_log()
    assert len(log) == 2 and (log["symbol"] == "FPT").all()
    assert repo.list_alerts()[0].id == alert_id


def test_inactive_alert_is_ignored(repo):
    engine = AlertEngine(repo, FakeNotifier())
    alert_id = repo.add_alert(Alert("FPT", AlertKind.PRICE_ABOVE, 1))
    repo.set_alert_active(alert_id, False)
    assert engine.check({"FPT": quote(55_000)}) == []


def test_rsi_alert_uses_stored_history(repo, prices):
    repo.upsert_prices("FPT", prices, "TEST")
    last = prices.index[-1].date()
    q = replace(quote(prices["close"].iloc[-1]), date=last)
    repo.add_alert(Alert("FPT", AlertKind.RSI_ABOVE, 0))  # luôn đúng
    assert len(AlertEngine(repo, FakeNotifier()).check({"FPT": q})) == 1


def test_message_is_vietnamese():
    a = Alert("FPT", AlertKind.PRICE_ABOVE, 55_000, note="chốt lời")
    title, body = format_message(a, replace(quote(55_500), symbol="FPT"), 55_500)
    assert "FPT" in title and "55.500" in body and "chốt lời" in body


def test_repository_roundtrip(repo, prices):
    assert repo.upsert_prices("FPT", prices, "T") == len(prices)
    assert repo.upsert_prices("FPT", prices.tail(5), "T") == 5  # ghi đè, không nhân bản
    first, last, count = repo.price_range("FPT")
    assert count == len(prices) and first == prices.index[0].date() and last == prices.index[-1].date()
    got = repo.get_prices("FPT", start=prices.index[-10].date())
    assert len(got) == 10 and got["close"].iloc[-1] == pytest.approx(prices["close"].iloc[-1])

    repo.add_watch("fpt")
    repo.add_watch("FPT")
    assert repo.watchlist() == ["FPT"]
    repo.remove_watch("FPT")
    assert repo.watchlist() == []

    repo.set_meta("k", "v")
    assert repo.get_meta("k") == "v" and repo.get_meta("missing") is None

    alert_id = repo.add_alert(Alert("vnm", AlertKind.RSI_BELOW, 30, (Channel.EMAIL, Channel.TOAST), note="x"))
    a = repo.list_alerts()[0]
    assert (a.id, a.symbol, a.kind, a.channels) == (alert_id, "VNM", AlertKind.RSI_BELOW, (Channel.EMAIL, Channel.TOAST))
    repo.delete_alert(alert_id)
    assert repo.list_alerts() == []


def history(closes, end: date = date(2026, 10, 6)) -> pd.DataFrame:
    """Lịch sử giá kết thúc trước ngày của `quote()` (07/10/2026)."""
    c = np.asarray(closes, dtype=float)
    idx = pd.bdate_range(end=pd.Timestamp(end), periods=len(c), name="date")
    return pd.DataFrame({"open": c, "high": c * 1.01, "low": c * 0.99, "close": c, "volume": 1e6}, index=idx)


@pytest.mark.parametrize("kind,price,threshold,expected", [
    (AlertKind.ABOVE_SMA, 51_000, 0, True),    # SMA20 = 50.000 -> trên 2%
    (AlertKind.ABOVE_SMA, 51_000, 3, False),
    (AlertKind.ABOVE_SMA, 49_000, 0, False),
    (AlertKind.BELOW_SMA, 49_000, 0, True),    # dưới 2%
    (AlertKind.BELOW_SMA, 49_000, 2.5, False),
])
def test_sma_conditions(kind, price, threshold, expected):
    value = metric_value(kind, quote(price), history([50_000] * 40), param=20)
    assert is_met(kind, value, threshold) is expected


def test_sma_needs_enough_history():
    assert metric_value(AlertKind.ABOVE_SMA, quote(51_000), history([50_000] * 10), param=50) is None


def test_52_week_high_and_low_ignore_today():
    h = history([50_000] * 300)  # đỉnh 50.500, đáy 49.500
    assert metric_value(AlertKind.HIGH_52W, quote(51_005), h) == pytest.approx(1.0)
    assert metric_value(AlertKind.HIGH_52W, quote(50_000), h) < 0
    assert metric_value(AlertKind.LOW_52W, quote(49_005), h) == pytest.approx(1.0)
    # Phiên hôm nay đã lưu (đỉnh trong phiên = giá hiện tại) không được tính vào đỉnh "trước đó".
    today = history([60_000], end=date(2026, 10, 7))
    assert metric_value(AlertKind.HIGH_52W, quote(51_005), pd.concat([h, today])) == pytest.approx(1.0)


def test_trailing_stop_uses_peak_since_creation():
    h = history([50_000] * 20 + [60_000] * 5 + [56_000] * 5)
    peak_day = h.index[20].date()
    assert metric_value(AlertKind.TRAILING_STOP, quote(54_000), h, since=peak_day) == pytest.approx(10.0)
    after_peak = h.index[-5].date()
    assert metric_value(AlertKind.TRAILING_STOP, quote(54_000), h, since=after_peak) == pytest.approx(
        (1 - 54_000 / 56_000) * 100)


def test_ceiling_and_floor():
    assert is_met(AlertKind.CEILING, metric_value(AlertKind.CEILING, quote(53_450), band=7), 0.3)  # +6,9%
    assert not is_met(AlertKind.CEILING, metric_value(AlertKind.CEILING, quote(52_000), band=7), 0.3)
    assert is_met(AlertKind.FLOOR, metric_value(AlertKind.FLOOR, quote(46_525), band=7), 0.3)  # -6,95%
    assert metric_value(AlertKind.CEILING, quote(53_450)) is None  # không rõ sàn
    assert metric_value(AlertKind.CEILING, replace(quote(53_450), symbol="VNINDEX"), band=7) is None


def test_engine_ceiling_reads_exchange_from_symbols(repo):
    repo.upsert_symbols(pd.DataFrame([{"symbol": "FPT", "exchange": "HOSE", "type": "STOCK", "name": "FPT"}]))
    repo.add_alert(Alert("FPT", AlertKind.CEILING, 0.3))
    assert len(AlertEngine(repo, FakeNotifier()).check({"FPT": quote(53_500)})) == 1


def test_engine_sma_alert_and_message(repo):
    repo.upsert_prices("FPT", history([50_000] * 60), "TEST")
    repo.add_alert(Alert("FPT", AlertKind.ABOVE_SMA, 0, param=50))
    fired = AlertEngine(repo, FakeNotifier()).check({"FPT": quote(51_000)})
    assert len(fired) == 1
    assert "SMA50" in fired[0].title and "trên SMA50" in fired[0].body


def test_engine_trailing_stop_from_created_at(repo):
    repo.upsert_prices("FPT", history([50_000] * 20 + [60_000] * 5), "TEST")
    created = datetime(2026, 9, 1, tzinfo=VN_TZ)
    repo.add_alert(Alert("FPT", AlertKind.TRAILING_STOP, 8, created_at=created))
    engine = AlertEngine(repo, FakeNotifier())
    assert engine.check({"FPT": quote(57_000)}) == []  # -5%
    assert len(engine.check({"FPT": quote(55_000)})) == 1  # -8,3%


def test_alert_param_roundtrip_and_label(repo):
    repo.add_alert(Alert("FPT", AlertKind.BELOW_SMA, 1.5, param=200))
    a = repo.list_alerts()[0]
    assert a.param == 200 and a.label == "Giá cắt xuống SMA200 (dưới ≥ x%)"


def test_old_database_gets_param_column(tmp_path):
    path = tmp_path / "old.db"
    with sqlite3.connect(path) as conn:
        conn.execute("CREATE TABLE alerts (id INTEGER PRIMARY KEY AUTOINCREMENT, symbol TEXT NOT NULL,"
                     " kind TEXT NOT NULL, threshold REAL NOT NULL, channels TEXT NOT NULL, note TEXT DEFAULT '',"
                     " active INTEGER NOT NULL DEFAULT 1, armed INTEGER NOT NULL DEFAULT 1,"
                     " created_at TEXT NOT NULL, last_triggered_at TEXT)")
        conn.execute("INSERT INTO alerts (symbol, kind, threshold, channels, created_at)"
                     " VALUES ('FPT', 'price_above', 1, 'toast', '2026-01-01T00:00:00+07:00')")
    repo = Repository(path)
    assert repo.list_alerts()[0].param is None
