"""Thông tin thị trường Việt Nam: múi giờ, giờ giao dịch, định dạng số."""
from __future__ import annotations

import math
from datetime import date, datetime, time, timedelta, timezone

# Việt Nam không dùng giờ mùa hè -> dùng offset cố định, không phụ thuộc tzdata trên Windows.
VN_TZ = timezone(timedelta(hours=7), name="ICT")

INDEX_SYMBOLS = frozenset({"VNINDEX", "VN30", "HNXINDEX", "HNX30", "UPCOMINDEX", "VN100"})

# HOSE: 9:00-11:30, 13:00-14:45 (ATC); HNX/UPCoM khớp lệnh đến 14:45, thỏa thuận đến 15:00.
SESSIONS = ((time(9, 0), time(11, 30)), (time(13, 0), time(15, 0)))


def now_vn() -> datetime:
    return datetime.now(VN_TZ)


def today_vn() -> date:
    return now_vn().date()


def is_index(symbol: str) -> bool:
    return symbol.upper() in INDEX_SYMBOLS


def is_trading_day(d: date) -> bool:
    """Thứ 2 - thứ 6. Chưa xử lý ngày nghỉ lễ."""
    return d.weekday() < 5


def is_trading_time(at: datetime | None = None) -> bool:
    at = (at or now_vn()).astimezone(VN_TZ)
    if not is_trading_day(at.date()):
        return False
    t = at.time()
    return any(start <= t <= end for start, end in SESSIONS)


def session_label(at: datetime | None = None) -> str:
    at = (at or now_vn()).astimezone(VN_TZ)
    if not is_trading_day(at.date()):
        return "Nghỉ cuối tuần"
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
