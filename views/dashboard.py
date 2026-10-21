import pandas as pd
import streamlit as st

from stockvn.market import fmt_change, fmt_volume, is_index, is_trading_time, now_vn
from views.common import (
    DISCLAIMER, color_change, fmt_pct, fmt_price, fmt_time, get_app, symbol_label, symbol_picker,
)

app = get_app()
repo = app.repo

st.title("📊 Tổng quan danh mục")

watch = repo.watchlist()

c1, c2 = st.columns([3, 1], vertical_alignment="bottom")
with c1:
    new_sym = symbol_picker("Thêm mã vào danh mục theo dõi", key="dash_add")
with c2:
    if st.button("➕ Thêm", width="stretch"):
        if new_sym in watch:
            st.toast(f"{new_sym} đã có trong danh mục")
        else:
            repo.add_watch(new_sym)
            try:
                app.collector.refresh_quote(new_sym)
            except Exception as exc:  # noqa: BLE001
                st.warning(f"Đã thêm {new_sym} nhưng chưa lấy được giá: {exc}")
            st.rerun()

if not watch:
    st.info("Danh mục đang trống. Thêm mã ở trên (ví dụ VNINDEX, FPT, VNM, HPG).")
    st.stop()

with st.container(horizontal=True, vertical_alignment="center"):
    refresh = st.button("🔄 Cập nhật giá", type="primary")
    auto = st.toggle("Tự động (60s)", value=False, help="Chỉ tự cập nhật trong giờ giao dịch")
if refresh:
    with st.spinner("Đang lấy giá…"):
        _, errors = app.collector.refresh_quotes(watch)
    for sym, err in errors.items():
        st.warning(f"{sym}: {err}")


@st.fragment(run_every=60 if auto else None)
def board():
    if auto and is_trading_time():
        app.collector.refresh_quotes(watch)
    quotes = repo.get_quotes(watch)
    tried: set = st.session_state.setdefault("quote_tried", set())
    missing = [s for s in watch if s not in quotes and s not in tried]
    if missing:
        tried.update(missing)
        app.collector.refresh_quotes(missing)
        quotes = repo.get_quotes(watch)

    indices = [s for s in watch if is_index(s) and s in quotes]
    if indices:
        cols = st.columns(min(len(indices), 4))
        for col, sym in zip(cols, indices):
            q = quotes[sym]
            col.metric(sym, fmt_price(q.price, index=True), f"{fmt_change(q.change, True)} ({fmt_pct(q.change_pct)})")

    rows = []
    for sym in watch:
        q = quotes.get(sym)
        if q is None or is_index(sym):
            continue
        spark = repo.get_prices(sym).tail(30)["close"].tolist()
        rows.append({
            "Mã": sym, "Tên": symbol_label(sym).split(" · ", 1)[-1],
            "Giá": q.price, "+/-": q.change, "%": q.change_pct,
            "KL": q.volume, "KL/TB20": q.volume / q.avg_volume20 if q.avg_volume20 else None,
            "30 phiên": spark, "Ngày": q.date.strftime("%d/%m"), "Cập nhật": fmt_time(q.updated_at),
        })
    if rows:
        df = pd.DataFrame(rows)
        styled = (
            df.style.map(color_change, subset=["+/-", "%"])
            .apply(lambda s: [color_change(v) for v in df["%"]], subset=["Giá"])
            .format({"Giá": fmt_price, "+/-": fmt_change, "%": fmt_pct,
                     "KL": fmt_volume, "KL/TB20": lambda v: "—" if pd.isna(v) else f"{v:.2f}x"})
        )
        st.dataframe(
            styled, hide_index=True, width="stretch",
            column_config={"30 phiên": st.column_config.LineChartColumn("30 phiên", width="small")},
        )
    if auto:
        st.caption(f"Tự động cập nhật mỗi 60 giây trong giờ giao dịch · lần cuối {fmt_time(now_vn())}")


board()

with st.expander("🗑️ Xóa mã khỏi danh mục"):
    to_remove = st.multiselect("Chọn mã", watch)
    if st.button("Xóa", disabled=not to_remove):
        for s in to_remove:
            repo.remove_watch(s)
        st.rerun()

st.caption(DISCLAIMER)
