"""Các kiểu dữ liệu dùng chung."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from enum import StrEnum


@dataclass(frozen=True)
class Quote:
    symbol: str
    date: date
    price: float
    ref_price: float  # giá đóng cửa phiên trước (giá tham chiếu)
    volume: float
    avg_volume20: float
    updated_at: datetime
    source: str

    @property
    def change(self) -> float:
        return self.price - self.ref_price

    @property
    def change_pct(self) -> float:
        return (self.price / self.ref_price - 1) * 100 if self.ref_price else 0.0


class AlertKind(StrEnum):
    PRICE_ABOVE = "price_above"
    PRICE_BELOW = "price_below"
    CHANGE_UP = "change_up"
    CHANGE_DOWN = "change_down"
    VOLUME_SPIKE = "volume_spike"
    RSI_ABOVE = "rsi_above"
    RSI_BELOW = "rsi_below"


ALERT_LABELS: dict[AlertKind, str] = {
    AlertKind.PRICE_ABOVE: "Giá vượt lên ≥ ngưỡng",
    AlertKind.PRICE_BELOW: "Giá giảm xuống ≤ ngưỡng",
    AlertKind.CHANGE_UP: "Tăng trong phiên ≥ x%",
    AlertKind.CHANGE_DOWN: "Giảm trong phiên ≥ x%",
    AlertKind.VOLUME_SPIKE: "Khối lượng ≥ x lần TB 20 phiên",
    AlertKind.RSI_ABOVE: "RSI(14) ≥ ngưỡng (quá mua)",
    AlertKind.RSI_BELOW: "RSI(14) ≤ ngưỡng (quá bán)",
}

ALERT_UNITS: dict[AlertKind, str] = {
    AlertKind.PRICE_ABOVE: "VND",
    AlertKind.PRICE_BELOW: "VND",
    AlertKind.CHANGE_UP: "%",
    AlertKind.CHANGE_DOWN: "%",
    AlertKind.VOLUME_SPIKE: "lần",
    AlertKind.RSI_ABOVE: "",
    AlertKind.RSI_BELOW: "",
}


class Channel(StrEnum):
    EMAIL = "email"
    TOAST = "toast"
    WEBPUSH = "webpush"


CHANNEL_LABELS: dict[Channel, str] = {
    Channel.EMAIL: "Email",
    Channel.TOAST: "Thông báo Windows",
    Channel.WEBPUSH: "Thông báo trình duyệt",
}


@dataclass
class Alert:
    symbol: str
    kind: AlertKind
    threshold: float
    channels: tuple[Channel, ...] = (Channel.TOAST,)
    note: str = ""
    active: bool = True
    # armed = True: sẵn sàng bắn. Sau khi bắn -> False; bật lại khi điều kiện hết đúng.
    armed: bool = True
    id: int | None = None
    created_at: datetime | None = None
    last_triggered_at: datetime | None = None
