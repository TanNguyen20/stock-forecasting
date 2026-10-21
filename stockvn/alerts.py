"""Đánh giá điều kiện cảnh báo và gửi thông báo.

Cảnh báo kích hoạt theo cạnh (edge-triggered): bắn một lần khi điều kiện chuyển sang đúng,
sau đó tự nạp lại khi điều kiện hết đúng -> không bị gửi lặp mỗi lần kiểm tra.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import timedelta

import pandas as pd

from .db import Repository
from .indicators import rsi
from .market import fmt_pct, fmt_price, fmt_volume, is_index, now_vn
from .models import ALERT_LABELS, Alert, AlertKind, Channel, Quote
from .notifier import Notifier

log = logging.getLogger(__name__)

_BELOW_KINDS = {AlertKind.PRICE_BELOW, AlertKind.RSI_BELOW}
_RSI_KINDS = {AlertKind.RSI_ABOVE, AlertKind.RSI_BELOW}


@dataclass
class Triggered:
    alert: Alert
    value: float
    title: str
    body: str
    status: dict[Channel, str] = field(default_factory=dict)


def metric_value(kind: AlertKind, quote: Quote, closes: pd.Series | None = None) -> float | None:
    match kind:
        case AlertKind.PRICE_ABOVE | AlertKind.PRICE_BELOW:
            return quote.price
        case AlertKind.CHANGE_UP:
            return quote.change_pct
        case AlertKind.CHANGE_DOWN:
            return -quote.change_pct  # ngưỡng dương: giảm >= x%
        case AlertKind.VOLUME_SPIKE:
            return quote.volume / quote.avg_volume20 if quote.avg_volume20 > 0 else None
        case AlertKind.RSI_ABOVE | AlertKind.RSI_BELOW:
            if closes is None or len(closes) < 15:
                return None
            value = rsi(closes).iloc[-1]
            return None if pd.isna(value) else float(value)
    return None


def is_met(kind: AlertKind, value: float, threshold: float) -> bool:
    return value <= threshold if kind in _BELOW_KINDS else value >= threshold


def format_message(alert: Alert, quote: Quote, value: float) -> tuple[str, str]:
    idx = is_index(quote.symbol)
    unit = "điểm" if idx else "VND"
    match alert.kind:
        case AlertKind.PRICE_ABOVE | AlertKind.PRICE_BELOW:
            detail = f"Giá {fmt_price(value, idx)} {unit} (ngưỡng {fmt_price(alert.threshold, idx)})"
        case AlertKind.CHANGE_UP | AlertKind.CHANGE_DOWN:
            detail = f"Biến động {fmt_pct(quote.change_pct)} (ngưỡng {alert.threshold:g}%)"
        case AlertKind.VOLUME_SPIKE:
            detail = (f"Khối lượng {fmt_volume(quote.volume)} = {value:.1f} lần TB20 "
                      f"(ngưỡng {alert.threshold:g} lần)")
        case _:
            detail = f"RSI(14) = {value:.1f} (ngưỡng {alert.threshold:g})"
    arrow = "▲" if quote.change >= 0 else "▼"
    title = f"🔔 {quote.symbol}: {ALERT_LABELS[alert.kind]}"
    body = "\n".join(filter(None, [
        detail,
        f"Giá hiện tại: {fmt_price(quote.price, idx)} {arrow} {fmt_pct(quote.change_pct)}",
        f"Thời điểm: {quote.updated_at.strftime('%H:%M %d/%m/%Y')}",
        f"Ghi chú: {alert.note}" if alert.note else "",
    ]))
    return title, body


class AlertEngine:
    def __init__(self, repo: Repository, notifier: Notifier):
        self.repo = repo
        self.notifier = notifier

    def _closes(self, quote: Quote) -> pd.Series:
        df = self.repo.get_prices(quote.symbol, start=quote.date - timedelta(days=400))
        return df["close"]

    def check(self, quotes: dict[str, Quote]) -> list[Triggered]:
        fired: list[Triggered] = []
        for alert in self.repo.list_alerts(active_only=True):
            quote = quotes.get(alert.symbol)
            if quote is None:
                continue
            closes = self._closes(quote) if alert.kind in _RSI_KINDS else None
            value = metric_value(alert.kind, quote, closes)
            if value is None:
                continue
            met = is_met(alert.kind, value, alert.threshold)
            if met and alert.armed:
                fired.append(self._fire(alert, quote, value))
            elif not met and not alert.armed:
                self.repo.set_alert_armed(alert.id, True)
        return fired

    def _fire(self, alert: Alert, quote: Quote, value: float) -> Triggered:
        title, body = format_message(alert, quote, value)
        status = self.notifier.send(alert.channels, title, body)
        self.repo.set_alert_armed(alert.id, False, triggered_at=now_vn())
        status_text = ", ".join(f"{ch.value}:{st}" for ch, st in status.items()) or "không có kênh"
        self.repo.log_alert(alert.id, alert.symbol, f"{title} | {body.splitlines()[0]}", value,
                            ",".join(c.value for c in alert.channels), status_text)
        log.info("Cảnh báo #%s %s bắn: %s", alert.id, alert.symbol, status_text)
        return Triggered(alert, value, title, body, status)
