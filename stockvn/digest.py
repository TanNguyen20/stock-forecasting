"""Báo cáo cuối phiên: biến động danh mục theo dõi, lãi/lỗ danh mục đầu tư và các cảnh báo trong ngày."""
from __future__ import annotations

import logging
from datetime import date

from .db import Repository
from .market import fmt_change, fmt_pct, fmt_price, is_index
from .models import Channel
from .notifier import Notifier
from .portfolio import holdings, positions, summarize

log = logging.getLogger(__name__)

MAX_ALERT_LINES = 10
DIGEST_SYMBOL = "*"  # mã ghi trong nhật ký cảnh báo cho báo cáo cuối phiên


def _money(v: float) -> str:
    return f"{fmt_change(v)} VND"


def daily_digest(repo: Repository, day: date, sell_cost_pct: float = 0.0) -> tuple[str, str] | None:
    """(tiêu đề, nội dung) hoặc None nếu ngày `day` không có phiên nào (nghỉ lễ, chưa có giá)."""
    symbols = sorted(set(repo.watchlist()) | set(repo.held_symbols()))
    quotes = {s: q for s, q in repo.get_quotes(symbols).items() if q.date == day}
    if not quotes:
        return None

    lines: list[str] = []
    indices = [q for q in quotes.values() if is_index(q.symbol)]
    stocks = sorted((q for q in quotes.values() if not is_index(q.symbol)), key=lambda q: q.change_pct, reverse=True)
    for q in indices:
        lines.append(f"{q.symbol}: {fmt_price(q.price, True)} ({fmt_change(q.change, True)} · {fmt_pct(q.change_pct)})")
    if stocks:
        up = sum(q.change > 0 for q in stocks)
        down = sum(q.change < 0 for q in stocks)
        lines += ["", f"Danh mục theo dõi: {up} tăng · {down} giảm · {len(stocks) - up - down} đứng giá"]
        for q in stocks:
            arrow = "▲" if q.change > 0 else "▼" if q.change < 0 else "■"
            ratio = f" · KL {q.volume / q.avg_volume20:.1f}x TB20".replace(".", ",") if q.avg_volume20 else ""
            lines.append(f"{arrow} {q.symbol}: {fmt_price(q.price)} ({fmt_pct(q.change_pct)}){ratio}")

    trades = repo.trades()
    if not trades.empty:
        try:
            pos = positions(trades)
        except ValueError as exc:
            lines += ["", f"Danh mục đầu tư: lỗi sổ giao dịch ({exc})"]
        else:
            table = holdings(pos, repo.get_quotes([p for p in pos if pos[p].qty > 0]), sell_cost_pct)
            if not table.empty:
                s = summarize(pos, table)
                lines += [
                    "", "Danh mục đầu tư:",
                    f"Giá trị {fmt_price(s.value)} VND · vốn {fmt_price(s.cost)} VND",
                    f"Lãi/lỗ chưa chốt {_money(s.pnl)} ({fmt_pct(s.pnl_pct)}) · trong phiên {_money(s.day_pnl)}",
                ]

    log_df = repo.alert_log(limit=500)
    fired = log_df[log_df["triggered_at"].str.startswith(day.isoformat()) & log_df["symbol"].ne(DIGEST_SYMBOL)]
    lines += ["", f"Cảnh báo đã gửi hôm nay: {len(fired)}"]
    lines += [f"• {m.split(' | ', 1)[-1]} ({s})" for s, m in
              zip(fired["symbol"].head(MAX_ALERT_LINES), fired["message"].head(MAX_ALERT_LINES))]
    if len(fired) > MAX_ALERT_LINES:
        lines.append(f"… và {len(fired) - MAX_ALERT_LINES} cảnh báo khác")
    return f"📋 Tổng kết phiên {day.strftime('%d/%m/%Y')}", "\n".join(lines).strip()



def parse_channels(names: tuple[str, ...]) -> tuple[Channel, ...]:
    valid = {c.value for c in Channel}
    for n in names:
        if n not in valid:
            log.warning("DIGEST_CHANNELS: bỏ qua kênh không hợp lệ %r (hợp lệ: %s)", n, ", ".join(sorted(valid)))
    return tuple(Channel(n) for n in dict.fromkeys(names) if n in valid)


def send_digest(repo: Repository, notifier: Notifier, day: date, channels: tuple[Channel, ...],
                sell_cost_pct: float = 0.0) -> dict[Channel, str] | None:
    """Gửi báo cáo và ghi vào nhật ký cảnh báo. None nếu không có gì để gửi."""
    if not channels or (msg := daily_digest(repo, day, sell_cost_pct)) is None:
        return None
    title, body = msg
    status = notifier.send(channels, title, body)
    status_text = ", ".join(f"{ch.value}:{st}" for ch, st in status.items())
    repo.log_alert(None, DIGEST_SYMBOL, title, None, ",".join(c.value for c in channels), status_text)
    log.info("Đã gửi báo cáo cuối phiên: %s", status_text)
    return status
