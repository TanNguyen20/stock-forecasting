"""Thông tin thị trường Việt Nam: múi giờ, giờ giao dịch, ngày nghỉ lễ, định dạng số."""
from __future__ import annotations

import logging
import math
from collections.abc import Iterable
from datetime import date, datetime, time, timedelta, timezone

from .config import ROOT

log = logging.getLogger(__name__)

# Việt Nam không dùng giờ mùa hè -> dùng offset cố định, không phụ thuộc tzdata trên Windows.
VN_TZ = timezone(timedelta(hours=7), name="ICT")

INDEX_SYMBOLS = frozenset({"VNINDEX", "VN30", "HNXINDEX", "HNX30", "UPCOMINDEX", "VN100"})

# HOSE: 9:00-11:30, 13:00-14:45 (ATC); HNX/UPCoM khớp lệnh đến 14:45, thỏa thuận đến 15:00.
SESSIONS = ((time(9, 0), time(11, 30)), (time(13, 0), time(15, 0)))

# Biên độ dao động giá cổ phiếu (%) so với giá tham chiếu. Ngày đầu niêm yết / GDKHQ có thể khác.
PRICE_BANDS = {"HOSE": 7.0, "HNX": 10.0, "UPCOM": 15.0}

# Mỗi dòng một ngày `YYYY-MM-DD` hoặc một khoảng `YYYY-MM-DD..YYYY-MM-DD`; phần sau '#' là chú thích.
HOLIDAYS_FILE = ROOT / "holidays.txt"
_holiday_cache: tuple[float, frozenset[date]] | None = None


def parse_holidays(text: str) -> frozenset[date]:
    days: set[date] = set()
    for n, raw in enumerate(text.splitlines(), 1):
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        try:
            first, _, last = line.partition("..")
            start = date.fromisoformat(first.strip())
            end = date.fromisoformat(last.strip()) if last else start
        except ValueError:
            log.warning("holidays.txt dòng %d không hợp lệ: %r", n, raw)
            continue
        days.update(start + timedelta(days=i) for i in range((end - start).days + 1))
    return frozenset(days)


def holidays() -> frozenset[date]:
    """Ngày nghỉ lễ (sàn không giao dịch) từ holidays.txt; tự nạp lại khi file thay đổi."""
    global _holiday_cache
    try:
        mtime = HOLIDAYS_FILE.stat().st_mtime
    except OSError:
        return frozenset()
    if _holiday_cache is None or _holiday_cache[0] != mtime:
        _holiday_cache = (mtime, parse_holidays(HOLIDAYS_FILE.read_text(encoding="utf-8")))
    return _holiday_cache[1]


def suggest_holidays(trading_dates: Iterable[date]) -> list[date]:
    """Các ngày thứ 2-6 nằm giữa dữ liệu đã lưu mà không mã nào có phiên -> nhiều khả năng là ngày nghỉ lễ."""
    traded = set(trading_dates)
    if not traded:
        return []
    start, end = min(traded), max(traded)
    days = (start + timedelta(days=i) for i in range((end - start).days + 1))
    return [d for d in days if d.weekday() < 5 and d not in traded]


def now_vn() -> datetime:
    return datetime.now(VN_TZ)


def today_vn() -> date:
    return now_vn().date()


def is_index(symbol: str) -> bool:
    return symbol.upper() in INDEX_SYMBOLS


def is_holiday(d: date) -> bool:
    return d in holidays()


def is_trading_day(d: date) -> bool:
    """Thứ 2 - thứ 6, trừ ngày nghỉ lễ trong holidays.txt."""
    return d.weekday() < 5 and not is_holiday(d)


def last_session(at: datetime | None = None) -> date:
    """Ngày của phiên gần nhất đã mở cửa (hôm nay nếu là ngày giao dịch và đã qua 9:00)."""
    at = (at or now_vn()).astimezone(VN_TZ)
    d = at.date()
    if is_trading_day(d) and at.time() >= SESSIONS[0][0]:
        return d
    d -= timedelta(days=1)
    while not is_trading_day(d):
        d -= timedelta(days=1)
    return d


def is_trading_time(at: datetime | None = None) -> bool:
    at = (at or now_vn()).astimezone(VN_TZ)
    if not is_trading_day(at.date()):
        return False
    t = at.time()
    return any(start <= t <= end for start, end in SESSIONS)


def session_label(at: datetime | None = None) -> str:
    at = (at or now_vn()).astimezone(VN_TZ)
    if at.weekday() >= 5:
        return "Nghỉ cuối tuần"
    if is_holiday(at.date()):
        return "Nghỉ lễ"
    t = at.time()
    if t < SESSIONS[0][0]:
        return "Trước phiên"
    if t <= SESSIONS[0][1]:
        return "Phiên sáng"
    if t < SESSIONS[1][0]:
        return "Nghỉ trưa"
    if t <= SESSIONS[1][1]:
        return "Phiên chiều"
    return "Đã đóng cửa"


def _group(n: float, decimals: int) -> str:
    s = f"{n:,.{decimals}f}"  # 1,234,567.89
    return s.replace(",", "_").replace(".", ",").replace("_", ".")  # 1.234.567,89


def fmt_price(v: float | None, index: bool = False) -> str:
    if v is None or (isinstance(v, float) and math.isnan(v)):
        return "—"
    return _group(v, 2 if index else 0)


def fmt_change(v: float | None, index: bool = False) -> str:
    """Thay đổi giá có dấu: +1.200 / -13,44."""
    if v is None or (isinstance(v, float) and math.isnan(v)):
        return "—"
    return ("+" if v > 0 else "-" if v < 0 else "") + fmt_price(abs(v), index)


def fmt_pct(v: float | None, signed: bool = True) -> str:
    if v is None or (isinstance(v, float) and math.isnan(v)):
        return "—"
    return (f"{v:+.2f}" if signed else f"{v:.2f}").replace(".", ",") + "%"


def fmt_volume(v: float | None) -> str:
    if v is None or (isinstance(v, float) and math.isnan(v)):
        return "—"
    return _group(v, 0)
