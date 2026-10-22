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
    ABOVE_SMA = "above_sma"  # param = số phiên SMA
    BELOW_SMA = "below_sma"
    HIGH_52W = "high_52w"
    LOW_52W = "low_52w"
    TRAILING_STOP = "trailing_stop"
    CEILING = "ceiling"
    FLOOR = "floor"


ALERT_LABELS: dict[AlertKind, str] = {
    AlertKind.PRICE_ABOVE: "Giá vượt lên ≥ ngưỡng",
    AlertKind.PRICE_BELOW: "Giá giảm xuống ≤ ngưỡng",
    AlertKind.CHANGE_UP: "Tăng trong phiên ≥ x%",
    AlertKind.CHANGE_DOWN: "Giảm trong phiên ≥ x%",
    AlertKind.VOLUME_SPIKE: "Khối lượng ≥ x lần TB 20 phiên",
    AlertKind.RSI_ABOVE: "RSI(14) ≥ ngưỡng (quá mua)",
    AlertKind.RSI_BELOW: "RSI(14) ≤ ngưỡng (quá bán)",
    AlertKind.ABOVE_SMA: "Giá cắt lên SMA (trên ≥ x%)",
    AlertKind.BELOW_SMA: "Giá cắt xuống SMA (dưới ≥ x%)",
    AlertKind.HIGH_52W: "Phá đỉnh 52 tuần (vượt ≥ x%)",
    AlertKind.LOW_52W: "Thủng đáy 52 tuần (thấp hơn ≥ x%)",
    AlertKind.TRAILING_STOP: "Giảm ≥ x% từ đỉnh kể từ khi tạo (trailing stop)",
    AlertKind.CEILING: "Chạm giá trần (cách trần ≤ x%)",
    AlertKind.FLOOR: "Chạm giá sàn (cách sàn ≤ x%)",
}

ALERT_UNITS: dict[AlertKind, str] = {
    AlertKind.PRICE_ABOVE: "VND",
    AlertKind.PRICE_BELOW: "VND",
    AlertKind.CHANGE_UP: "%",
    AlertKind.CHANGE_DOWN: "%",
    AlertKind.VOLUME_SPIKE: "lần",
    AlertKind.RSI_ABOVE: "",
    AlertKind.RSI_BELOW: "",
    AlertKind.ABOVE_SMA: "%",
    AlertKind.BELOW_SMA: "%",
    AlertKind.HIGH_52W: "%",
    AlertKind.LOW_52W: "%",
    AlertKind.TRAILING_STOP: "%",
    AlertKind.CEILING: "%",
    AlertKind.FLOOR: "%",
}

SMA_KINDS = frozenset({AlertKind.ABOVE_SMA, AlertKind.BELOW_SMA})
DEFAULT_SMA = 20


class Channel(StrEnum):
    EMAIL = "email"
    TOAST = "toast"
    WEBPUSH = "webpush"
    NTFY = "ntfy"


CHANNEL_LABELS: dict[Channel, str] = {
    Channel.EMAIL: "Email",
    Channel.TOAST: "Thông báo Windows",
    Channel.WEBPUSH: "Thông báo trình duyệt",
    Channel.NTFY: "ntfy",
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
    param: float | None = None  # tham số phụ của điều kiện, vd. số phiên SMA

    @property
    def label(self) -> str:
        text = ALERT_LABELS[self.kind]
        if self.kind in SMA_KINDS:
            text = text.replace("SMA", f"SMA{int(self.param or DEFAULT_SMA)}")
        return text
