"""Tiện ích dùng chung cho các trang Streamlit."""
from __future__ import annotations

from datetime import datetime

import pandas as pd
import streamlit as st

from stockvn.market import fmt_pct, fmt_price, is_index, now_vn
from stockvn.service import App, build_app
from stockvn.worker import background_state

DISCLAIMER = (
    "⚠️ Dữ liệu lấy từ nguồn công khai (VCI, DNSE, KBS) và có thể chậm hoặc sai lệch. "
    "Dự báo chỉ mang tính tham khảo, **không phải khuyến nghị đầu tư**."
)
DEFAULT_SYMBOLS = ["VNINDEX", "VN30", "FPT", "VNM", "HPG", "VCB", "MWG", "SSI"]

# Quy ước màu bảng giá Việt Nam: tăng xanh lá, giảm đỏ, đứng giá vàng.
UP, DOWN, FLAT = "#16a34a", "#dc2626", "#ca8a04"


@st.cache_resource
def get_app() -> App:
    return build_app()


@st.cache_data(ttl=24 * 3600, show_spinner=False)
def symbol_table() -> pd.DataFrame:
    app = get_app()
    df = app.repo.get_symbols()
    if df.empty:
        try:
            app.collector.refresh_symbols()
            df = app.repo.get_symbols()
        except Exception:  # noqa: BLE001 - không có mạng vẫn dùng được với mã nhập tay
            pass
    return df


def symbol_options() -> list[str]:
    df = symbol_table()
    stocks: list[str] = []
    if not df.empty:
        listed = df["type"].eq("STOCK") & df["exchange"].isin(["HOSE", "HNX", "UPCOM"])
        stocks = sorted(df.loc[listed, "symbol"])
    tracked = get_app().repo.watchlist()
    return list(dict.fromkeys([*DEFAULT_SYMBOLS, *tracked, *stocks]))


def symbol_label(symbol: str) -> str:
    df = symbol_table()
    if is_index(symbol):
        return f"{symbol} · Chỉ số"
    if df.empty:
        return symbol
    row = df.loc[df["symbol"].eq(symbol)]
    if row.empty:
        return symbol
    r = row.iloc[0]
    return f"{symbol} · {r['exchange']} · {r['name']}"


def symbol_picker(label: str = "Mã chứng khoán", key: str = "symbol", default: str | None = None) -> str:
    options = symbol_options()
    default = (default or st.session_state.get("current_symbol") or "FPT").upper()
    if default not in options:
        options.insert(0, default)
    sym = st.selectbox(label, options, index=options.index(default), key=key, format_func=symbol_label,
                       accept_new_options=True)
    sym = (sym or default).strip().upper()
    st.session_state["current_symbol"] = sym
    return sym


def load_history(symbol: str, min_rows: int = 250) -> pd.DataFrame:
    """Lấy lịch sử từ DB; tự tải về nếu chưa có hoặc quá ít."""
    app = get_app()
    _, last, count = app.repo.price_range(symbol)
    synced: dict = st.session_state.setdefault("synced_at", {})
    recently = symbol in synced and (now_vn() - synced[symbol]).total_seconds() < 600
    stale = last is None or last < now_vn().date()
    if not recently and (count < min_rows or stale):
        synced[symbol] = now_vn()
        with st.spinner(f"Đang tải dữ liệu {symbol}…"):
            try:
                app.collector.sync_history(symbol, full=count < min_rows)
            except Exception as exc:  # noqa: BLE001
                if count == 0:
                    st.error(f"Không tải được dữ liệu {symbol}: {exc}")
                else:
                    st.warning(f"Không cập nhật được dữ liệu mới, dùng dữ liệu đã lưu. ({exc})")
    return app.repo.get_prices(symbol)


WORKER_STATES = {
    "running": "✅ Đang chạy kèm giao diện",
    "starting": "⏳ Đang khởi động",
    "standby": "⏸️ Đang chờ — một worker khác (vd. `run_worker.bat`) đang chạy trên cùng CSDL",
    "stopped": "❌ Đã dừng do lỗi — xem `logs/app.log`",
    "off": "⛔ Không chạy kèm (`STOCK_EMBED_WORKER=false`)",
}


def worker_state_label() -> str:
    return WORKER_STATES.get(background_state(), background_state())


def color_change(v: float) -> str:
    if pd.isna(v) or abs(v) < 1e-9:
        return f"color: {FLAT}"
    return f"color: {UP if v > 0 else DOWN}"


def fmt_time(dt: datetime | None) -> str:
    return dt.strftime("%H:%M:%S %d/%m") if dt else "—"


__all__ = [
    "DISCLAIMER", "UP", "DOWN", "FLAT", "get_app", "symbol_table", "symbol_options", "symbol_label",
    "symbol_picker", "load_history", "color_change", "fmt_time", "fmt_price", "fmt_pct", "worker_state_label",
]
