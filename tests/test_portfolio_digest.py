from datetime import date, datetime, timedelta

import pandas as pd
import pytest

from stockvn.digest import DIGEST_SYMBOL, daily_digest, parse_channels, send_digest
from stockvn.market import VN_TZ, today_vn
from stockvn.models import Channel, Quote
from stockvn.portfolio import holdings, positions, summarize, trade_fee

DAY = today_vn()  # nhật ký cảnh báo ghi theo giờ thực


def q(symbol: str, price: float, ref: float, day: date = DAY) -> Quote:
    return Quote(symbol, day, price, ref, 2_000_000, 1_000_000, datetime(2026, 10, 7, 15, 5, tzinfo=VN_TZ), "TEST")


def trades(*rows) -> pd.DataFrame:
    return pd.DataFrame(rows, columns=["symbol", "side", "qty", "price", "fee", "traded_on"]).assign(
        traded_on=lambda d: pd.to_datetime(d["traded_on"]))


def test_trade_fee_adds_tax_on_sell():
    assert trade_fee("buy", 1000, 50_000, 0.15, 0.1) == pytest.approx(75_000)
    assert trade_fee("sell", 1000, 50_000, 0.15, 0.1) == pytest.approx(125_000)


def test_average_cost_and_realized_pnl():
    pos = positions(trades(
        ("FPT", "buy", 1000, 100_000, 150_000, "2026-01-05"),
        ("FPT", "buy", 1000, 120_000, 180_000, "2026-02-05"),
        ("FPT", "sell", 500, 130_000, 162_500, "2026-03-05"),
    ))["FPT"]
    avg = (100_000_000 + 150_000 + 120_000_000 + 180_000) / 2000
    assert pos.avg_cost == pytest.approx(avg)
    assert pos.qty == 1500
    assert pos.realized == pytest.approx(500 * (130_000 - avg) - 162_500)
    assert pos.fees == pytest.approx(150_000 + 180_000 + 162_500)



def test_selling_everything_resets_cost():
    pos = positions(trades(("HPG", "buy", 100, 25_000, 0, "2026-01-05"), ("HPG", "sell", 100, 30_000, 0, "2026-01-08"),
                           ("HPG", "buy", 100, 20_000, 0, "2026-02-05")))["HPG"]
    assert pos.qty == 100 and pos.avg_cost == 20_000 and pos.realized == pytest.approx(500_000)


def test_oversell_raises():
    with pytest.raises(ValueError, match="vượt"):
        positions(trades(("FPT", "buy", 100, 1, 0, "2026-01-05"), ("FPT", "sell", 200, 1, 0, "2026-01-06")))


def test_holdings_table_and_summary():
    pos = positions(trades(("FPT", "buy", 1000, 100_000, 0, "2026-01-05"), ("VNM", "buy", 1000, 60_000, 0, "2026-01-05")))
    table = holdings(pos, {"FPT": q("FPT", 110_000, 105_000), "VNM": q("VNM", 57_000, 60_000)}, sell_cost_pct=0.25)
    fpt = table.set_index("symbol").loc["FPT"]
    assert fpt["pnl"] == pytest.approx(10_000_000) and fpt["pnl_pct"] == pytest.approx(10)
    assert fpt["day_pnl"] == pytest.approx(5_000_000)
    assert fpt["net_pnl"] == pytest.approx(110_000_000 * 0.9975 - 100_000_000)
    assert table["weight"].sum() == pytest.approx(100)
    s = summarize(pos, table)
    assert s.pnl == pytest.approx(7_000_000) and s.pnl_pct == pytest.approx(7_000_000 / 160_000_000 * 100)


def test_held_symbols(repo):
    repo.add_trade("fpt", "buy", 100, 1, 0, date(2026, 1, 5))
    repo.add_trade("HPG", "buy", 100, 1, 0, date(2026, 1, 5))
    repo.add_trade("HPG", "sell", 100, 1, 0, date(2026, 1, 8))
    assert repo.held_symbols() == ["FPT"]
    assert list(repo.trades()["symbol"]) == ["FPT", "HPG", "HPG"]


class FakeNotifier:
    def __init__(self):
        self.sent = []

    def send(self, channels, title, body):
        self.sent.append((channels, title, body))
        return {c: "ok" for c in channels}


def test_daily_digest_content(repo):
    for s in ("VNINDEX", "FPT", "VNM"):
        repo.add_watch(s)
    repo.upsert_quote(q("VNINDEX", 1800, 1790))
    repo.upsert_quote(q("FPT", 110_000, 105_000))
    repo.upsert_quote(q("VNM", 57_000, 60_000))
    repo.add_trade("FPT", "buy", 1000, 100_000, 0, date(2026, 1, 5))
    repo.log_alert(1, "FPT", "🔔 FPT: Giá vượt lên ≥ ngưỡng | Giá 110.000 VND", 110_000, "toast", "ok")
    title, body = daily_digest(repo, DAY)
    assert DAY.strftime("%d/%m/%Y") in title
    assert body.index("FPT") < body.index("VNM")  # sắp theo % thay đổi
    assert "VNINDEX: 1.800,00" in body
    assert "2 tăng" not in body and "1 tăng · 1 giảm" in body
    assert "Lãi/lỗ chưa chốt +10.000.000 VND" in body
    assert "Cảnh báo đã gửi hôm nay: 1" in body


def test_digest_skips_days_without_quotes(repo):
    repo.add_watch("FPT")
    repo.upsert_quote(q("FPT", 110_000, 105_000, day=DAY - timedelta(days=1)))
    assert daily_digest(repo, DAY) is None


def test_send_digest_logs_and_ignores_itself(repo):
    repo.add_watch("FPT")
    repo.upsert_quote(q("FPT", 110_000, 105_000))
    notifier = FakeNotifier()
    assert send_digest(repo, notifier, DAY, (Channel.EMAIL,)) == {Channel.EMAIL: "ok"}
    assert repo.alert_log()["symbol"].tolist() == [DIGEST_SYMBOL]
    _, body = daily_digest(repo, DAY)
    assert "Cảnh báo đã gửi hôm nay: 0" in body
    assert send_digest(repo, notifier, DAY, ()) is None


def test_parse_channels_drops_unknown():
    assert parse_channels(("email", "telegram", "ntfy", "email")) == (Channel.EMAIL, Channel.NTFY)
