from dataclasses import replace
from datetime import date

import pytest

from stockvn.alerts import AlertEngine, format_message, is_met, metric_value
from stockvn.market import now_vn
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
