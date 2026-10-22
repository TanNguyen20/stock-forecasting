"""Danh mục đầu tư: vị thế, giá vốn bình quân và lãi/lỗ tính từ sổ giao dịch."""
from __future__ import annotations

import math
from dataclasses import dataclass

import pandas as pd

from .market import fmt_volume
from .models import Quote

EPS = 1e-9


@dataclass
class Position:
    symbol: str
    qty: float = 0.0
    avg_cost: float = 0.0  # giá vốn bình quân / cổ phiếu, đã gồm phí mua
    realized: float = 0.0  # lãi/lỗ đã chốt, sau phí và thuế bán
    fees: float = 0.0  # tổng phí + thuế đã trả

    @property
    def cost(self) -> float:
        return self.qty * self.avg_cost


def trade_fee(side: str, qty: float, price: float, fee_pct: float, tax_pct: float) -> float:
    """Phí môi giới mỗi chiều + thuế bán, tính theo % giá trị lệnh (VND)."""
    return qty * price * (fee_pct + (tax_pct if side == "sell" else 0.0)) / 100


def positions(trades: pd.DataFrame) -> dict[str, Position]:
    """Giá vốn bình quân gia quyền. trades: cột symbol, side, qty, price, fee, traded_on.

    Ném ValueError nếu một lệnh bán vượt số lượng đang có tại thời điểm đó.
    """
    out: dict[str, Position] = {}
    for t in trades.sort_values("traded_on", kind="stable").itertuples():
        p = out.setdefault(t.symbol, Position(t.symbol))
        p.fees += t.fee
        if t.side == "buy":
            p.avg_cost = (p.cost + t.qty * t.price + t.fee) / (p.qty + t.qty)
            p.qty += t.qty
            continue
        if t.qty > p.qty + EPS:
            raise ValueError(f"{t.symbol}: lệnh bán {fmt_volume(t.qty)} cổ phiếu ngày "
                             f"{pd.Timestamp(t.traded_on):%d/%m/%Y} vượt số đang có ({fmt_volume(p.qty)})")
        p.realized += t.qty * (t.price - p.avg_cost) - t.fee
        p.qty -= t.qty
        if p.qty < EPS:
            p.qty, p.avg_cost = 0.0, 0.0
    return out


def holdings(pos: dict[str, Position], quotes: dict[str, Quote], sell_cost_pct: float = 0.0) -> pd.DataFrame:
    """Bảng vị thế đang mở theo giá hiện tại. sell_cost_pct: phí + thuế nếu bán ngay (%)."""
    rows = []
    for p in pos.values():
        if p.qty <= 0:
            continue
        q = quotes.get(p.symbol)
        price = q.price if q else math.nan
        value = p.qty * price
        rows.append({
            "symbol": p.symbol, "qty": p.qty, "avg_cost": p.avg_cost, "price": price,
            "change_pct": q.change_pct if q else math.nan,
            "day_pnl": p.qty * q.change if q else math.nan,
            "cost": p.cost, "value": value,
            "pnl": value - p.cost,
            "pnl_pct": (price / p.avg_cost - 1) * 100 if p.avg_cost else math.nan,
            "net_pnl": value * (1 - sell_cost_pct / 100) - p.cost,
            "realized": p.realized,
        })
    df = pd.DataFrame(rows, columns=["symbol", "qty", "avg_cost", "price", "change_pct", "day_pnl", "cost",
                                     "value", "pnl", "pnl_pct", "net_pnl", "realized"])
    total = df["value"].sum()
    df["weight"] = df["value"] / total * 100 if total else math.nan
    return df.sort_values("value", ascending=False, ignore_index=True)


@dataclass
class Summary:
    cost: float
    value: float
    pnl: float
    day_pnl: float
    realized: float
    fees: float

    @property
    def pnl_pct(self) -> float:
        return self.pnl / self.cost * 100 if self.cost else math.nan


def summarize(pos: dict[str, Position], table: pd.DataFrame) -> Summary:
    return Summary(
        cost=float(table["cost"].sum()), value=float(table["value"].sum()), pnl=float(table["pnl"].sum()),
        day_pnl=float(table["day_pnl"].sum()), realized=sum(p.realized for p in pos.values()),
        fees=sum(p.fees for p in pos.values()),
    )
