"""Đánh giá điều kiện cảnh báo và gửi thông báo.

Cảnh báo kích hoạt theo cạnh (edge-triggered): bắn một lần khi điều kiện chuyển sang đúng,
sau đó tự nạp lại khi điều kiện hết đúng -> không bị gửi lặp mỗi lần kiểm tra.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date, timedelta

import pandas as pd

from .db import Repository
from .indicators import rsi, sma
from .market import PRICE_BANDS, fmt_pct, fmt_price, fmt_volume, is_index, now_vn
from .models import DEFAULT_SMA, Alert, AlertKind, Channel, Quote
from .notifier import Notifier

log = logging.getLogger(__name__)

# Điều kiện "giá trị <= ngưỡng"; còn lại là "giá trị >= ngưỡng".
_BELOW_KINDS = {AlertKind.PRICE_BELOW, AlertKind.RSI_BELOW, AlertKind.CEILING, AlertKind.FLOOR}
_RSI_KINDS = {AlertKind.RSI_ABOVE, AlertKind.RSI_BELOW}
_HISTORY_KINDS = _RSI_KINDS | {
    AlertKind.ABOVE_SMA, AlertKind.BELOW_SMA, AlertKind.HIGH_52W, AlertKind.LOW_52W, AlertKind.TRAILING_STOP,
}
HISTORY_DAYS = 400  # ngày lịch: đủ cho SMA200 và 52 tuần
MIN_52W_SESSIONS = 20  # mã mới niêm yết: đỉnh/đáy tính trên số phiên đang có


@dataclass
class Triggered:
    alert: Alert
    value: float
    title: str
    body: str
    status: dict[Channel, str] = field(default_factory=dict)


def _closes(quote: Quote, history: pd.DataFrame) -> pd.Series:
    """Giá đóng cửa các phiên trước + giá hiện tại của phiên `quote.date`."""
    past = history.loc[history.index < pd.Timestamp(quote.date), "close"]
    return pd.concat([past, pd.Series([quote.price], index=[pd.Timestamp(quote.date)])])


def _prior(quote: Quote, history: pd.DataFrame, n: int = 252) -> pd.DataFrame:
    return history.loc[history.index < pd.Timestamp(quote.date)].tail(n)


def metric_value(kind: AlertKind, quote: Quote, history: pd.DataFrame | None = None, *,
                 param: float | None = None, since: date | None = None, band: float | None = None) -> float | None:
    """Giá trị so với ngưỡng. history: lịch sử giá (index ngày, cột close/high/low) cho các điều kiện cần."""
    match kind:
        case AlertKind.PRICE_ABOVE | AlertKind.PRICE_BELOW:
            return quote.price
        case AlertKind.CHANGE_UP:
            return quote.change_pct
        case AlertKind.CHANGE_DOWN:
            return -quote.change_pct  # ngưỡng dương: giảm >= x%
        case AlertKind.VOLUME_SPIKE:
            return quote.volume / quote.avg_volume20 if quote.avg_volume20 > 0 else None
        case AlertKind.CEILING | AlertKind.FLOOR:
            if band is None or is_index(quote.symbol):
                return None
            # Khoảng cách (điểm %) tới trần/sàn; làm tròn bước giá khiến trần thực tế thấp hơn biên độ một chút.
            return band - quote.change_pct if kind is AlertKind.CEILING else band + quote.change_pct
    if history is None or history.empty:
        return None
    match kind:
        case AlertKind.RSI_ABOVE | AlertKind.RSI_BELOW:
            closes = _closes(quote, history)
            if len(closes) < 15:
                return None
            value = rsi(closes).iloc[-1]
            return None if pd.isna(value) else float(value)
        case AlertKind.ABOVE_SMA | AlertKind.BELOW_SMA:
            n = int(param or DEFAULT_SMA)
            closes = _closes(quote, history)
            if len(closes) < n:
                return None
            diff = (quote.price / sma(closes, n).iloc[-1] - 1) * 100
            return float(diff if kind is AlertKind.ABOVE_SMA else -diff)
        case AlertKind.HIGH_52W:
            prior = _prior(quote, history)
            if len(prior) < MIN_52W_SESSIONS:
                return None
            return float((quote.price / prior["high"].max() - 1) * 100)
        case AlertKind.LOW_52W:
            prior = _prior(quote, history)
            if len(prior) < MIN_52W_SESSIONS:
                return None
            return float((1 - quote.price / prior["low"].min()) * 100)
        case AlertKind.TRAILING_STOP:
            closes = _closes(quote, history)
            if since is not None:
                closes = closes[closes.index >= pd.Timestamp(since)]
            return float((1 - quote.price / closes.max()) * 100)
    return None


def is_met(kind: AlertKind, value: float, threshold: float) -> bool:
    return value <= threshold if kind in _BELOW_KINDS else value >= threshold


def format_message(alert: Alert, quote: Quote, value: float) -> tuple[str, str]:
    idx = is_index(quote.symbol)
    unit = "điểm" if idx else "VND"
    price = f"{fmt_price(quote.price, idx)} {unit}"
    match alert.kind:
        case AlertKind.PRICE_ABOVE | AlertKind.PRICE_BELOW:
            detail = f"Giá {fmt_price(value, idx)} {unit} (ngưỡng {fmt_price(alert.threshold, idx)})"
        case AlertKind.CHANGE_UP | AlertKind.CHANGE_DOWN:
            detail = f"Biến động {fmt_pct(quote.change_pct)} (ngưỡng {alert.threshold:g}%)"
        case AlertKind.VOLUME_SPIKE:
            detail = (f"Khối lượng {fmt_volume(quote.volume)} = {value:.1f} lần TB20 "
                      f"(ngưỡng {alert.threshold:g} lần)")
        case AlertKind.ABOVE_SMA | AlertKind.BELOW_SMA:
            side = "trên" if alert.kind is AlertKind.ABOVE_SMA else "dưới"
            detail = (f"Giá {price} {side} SMA{int(alert.param or DEFAULT_SMA)} {fmt_pct(value, signed=False)} "
                      f"(ngưỡng {alert.threshold:g}%)")
        case AlertKind.HIGH_52W:
            detail = f"Giá {price} cao hơn đỉnh 52 tuần trước đó {fmt_pct(value, signed=False)}"
        case AlertKind.LOW_52W:
            detail = f"Giá {price} thấp hơn đáy 52 tuần trước đó {fmt_pct(value, signed=False)}"
        case AlertKind.TRAILING_STOP:
            detail = (f"Giá {price} thấp hơn đỉnh kể từ khi tạo cảnh báo {fmt_pct(value, signed=False)} "
                      f"(ngưỡng {alert.threshold:g}%)")
        case AlertKind.CEILING | AlertKind.FLOOR:
            side = "trần" if alert.kind is AlertKind.CEILING else "sàn"
            detail = f"Giá {price} ({fmt_pct(quote.change_pct)}) cách giá {side} {fmt_pct(max(value, 0), signed=False)}"
        case _:
            detail = f"RSI(14) = {value:.1f} (ngưỡng {alert.threshold:g})"
    arrow = "▲" if quote.change >= 0 else "▼"
    title = f"🔔 {quote.symbol}: {alert.label}"
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

    def _history(self, quote: Quote, since: date | None = None) -> pd.DataFrame:
        start = quote.date - timedelta(days=HISTORY_DAYS)
        return self.repo.get_prices(quote.symbol, start=min(start, since) if since else start)

    def _bands(self) -> dict[str, float]:
        df = self.repo.get_symbols()
        if df.empty:
            return {}
        return {s: PRICE_BANDS[e] for s, e in zip(df["symbol"], df["exchange"]) if e in PRICE_BANDS}

    def check(self, quotes: dict[str, Quote]) -> list[Triggered]:
        fired: list[Triggered] = []
        histories: dict[str, pd.DataFrame] = {}
        bands: dict[str, float] | None = None
        for alert in self.repo.list_alerts(active_only=True):
            quote = quotes.get(alert.symbol)
            if quote is None:
                continue
            since = alert.created_at.date() if alert.created_at else None
            history = None
            if alert.kind is AlertKind.TRAILING_STOP:
                history = self._history(quote, since)
            elif alert.kind in _HISTORY_KINDS:
                if alert.symbol not in histories:
                    histories[alert.symbol] = self._history(quote)
                history = histories[alert.symbol]
            band = None
            if alert.kind in (AlertKind.CEILING, AlertKind.FLOOR):
                bands = self._bands() if bands is None else bands
                band = bands.get(alert.symbol)
            value = metric_value(alert.kind, quote, history, param=alert.param, since=since, band=band)
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
