import time

import pandas as pd
import streamlit as st

from stockvn.market import is_index, last_session
from stockvn.screener import LOOKBACK, RULES, screen
from views.common import DISCLAIMER, color_change, fmt_pct, fmt_price, get_app, symbol_table

app = get_app()
repo = app.repo

st.title("🔎 Bộ lọc cổ phiếu")
st.caption("Lọc trên dữ liệu giá đã lưu trong CSDL. Mã chưa có dữ liệu cần tải về trước (nút bên dưới).")

UNIVERSES = {"watch": "Danh mục theo dõi", "stored": "Mã đã có dữ liệu", "exchange": "Theo sàn niêm yết"}
EXCHANGES = ["HOSE", "HNX", "UPCOM"]
MIN_ROWS = 250  # ít hơn -> tải lại 2 năm lịch sử (đủ cho SMA200 và 52 tuần)
SECONDS_PER_SYMBOL = 1.0  # ước lượng thời gian tải một mã (gồm thời gian nghỉ giữa các yêu cầu)

with st.container(border=True):
    c1, c2 = st.columns([2, 1])
    universe = c1.segmented_control("Phạm vi", list(UNIVERSES), format_func=UNIVERSES.get, default="stored",
                                    required=True)
    exchanges = c2.multiselect("Sàn", EXCHANGES, default=["HOSE"], disabled=universe != "exchange")
    rules = st.pills("Điều kiện (thỏa tất cả)", list(RULES), format_func=lambda k: RULES[k].label,
                     selection_mode="multi", default=["above_sma200"])
    min_value = st.number_input("Giá trị giao dịch TB 20 phiên tối thiểu (tỷ VND)", min_value=0.0, value=1.0,
                                step=0.5, help="Loại mã thanh khoản thấp (khó mua/bán ở giá mong muốn)")

if universe == "watch":
    symbols = repo.watchlist()
elif universe == "stored":
    symbols = repo.stored_symbols()
else:
    table = symbol_table()
    if table.empty:
        st.error("Chưa có danh sách mã. Vào **Cài đặt → Cập nhật danh sách mã**.")
        st.stop()
    symbols = sorted(table.loc[table["type"].eq("STOCK") & table["exchange"].isin(exchanges), "symbol"])

symbols = [sym for sym in symbols if not is_index(sym)]
if not symbols:
    st.info("Phạm vi đang chọn chưa có mã cổ phiếu nào.")
    st.stop()

# ---- Dữ liệu còn thiếu / cũ
session = last_session()
need = []
for sym in symbols:
    _, last, count = repo.price_range(sym)
    if last is None or count < MIN_ROWS or last < session:
        need.append((sym, count < MIN_ROWS))
missing = sum(full for _, full in need)
if need:
    eta = len(need) * SECONDS_PER_SYMBOL
    eta_text = f"{eta:.0f} giây" if eta < 60 else f"{eta / 60:.0f} phút"
    st.warning(f"{len(need)}/{len(symbols)} mã chưa có đủ dữ liệu đến phiên {session.strftime('%d/%m/%Y')} "
               f"({missing} mã cần tải mới 2 năm lịch sử). Ước tính ~{eta_text}.", icon="📥")
    if st.button(f"📥 Tải / cập nhật {len(need)} mã"):
        bar = st.progress(0.0)
        errors = {}
        for i, (sym, full) in enumerate(need):
            bar.progress((i + 1) / len(need), text=f"{sym} ({i + 1}/{len(need)})")
            try:
                app.collector.sync_history(sym, years=2, full=full)
            except Exception as exc:  # noqa: BLE001 - một mã lỗi không dừng cả lượt tải
                errors[sym] = str(exc)
            time.sleep(0.3)  # tránh gửi dồn dập tới máy chủ nguồn
        bar.empty()
        if errors:
            with st.expander(f"⚠️ {len(errors)} mã lỗi"):
                st.write(errors)
        st.rerun()

# ---- Lọc
if st.button("🔍 Lọc", type="primary"):
    with st.spinner(f"Đang tính chỉ báo cho {len(symbols)} mã…"):
        data = repo.latest_prices(symbols, LOOKBACK)
        st.session_state["screen_result"] = (tuple(symbols), tuple(rules), min_value, screen(data, rules, min_value))

saved = st.session_state.get("screen_result")
if saved is None or saved[:3] != (tuple(symbols), tuple(rules), min_value):
    st.info("Chọn điều kiện rồi bấm **Lọc**.")
    st.stop()

result: pd.DataFrame = saved[3]
st.subheader(f"{len(result)} mã thỏa điều kiện")
if result.empty:
    st.stop()

names = symbol_table().set_index("symbol")
view = pd.DataFrame({
    "Mã": result["symbol"],
    "Sàn": result["symbol"].map(names["exchange"]).fillna("—"),
    "Tên": result["symbol"].map(names["name"]).fillna(""),
    "Giá": result["price"], "% phiên": result["change_pct"], "% 1 tháng": result["ret_1m"],
    "RSI(14)": result["rsi"], "KL/TB20": result["vol_ratio"], "GTGD TB20 (tỷ)": result["value_bn"],
    "So với SMA200": result["dist_sma200"], "Phiên cuối": result["date"].dt.strftime("%d/%m"),
}).sort_values("GTGD TB20 (tỷ)", ascending=False, ignore_index=True)
event = st.dataframe(
    view.style.map(color_change, subset=["% phiên", "% 1 tháng", "So với SMA200"]).format({
        "Giá": fmt_price, "% phiên": fmt_pct, "% 1 tháng": fmt_pct, "RSI(14)": "{:.1f}", "KL/TB20": "{:.2f}x",
        "GTGD TB20 (tỷ)": "{:,.1f}", "So với SMA200": fmt_pct,
    }, na_rep="—"),
    hide_index=True, width="stretch", on_select="rerun", selection_mode="multi-row", key="screen_table",
)
picked = view.loc[event.selection.rows, "Mã"].tolist()
watch = set(repo.watchlist())
new = [p for p in picked if p not in watch]
if st.button(f"➕ Thêm {len(new)} mã đã chọn vào danh mục theo dõi", disabled=not new):
    for sym in new:
        repo.add_watch(sym)
    st.toast(f"Đã thêm {', '.join(new)}", icon="✅")
st.download_button("⬇️ Tải CSV", view.to_csv(index=False).encode("utf-8-sig"), file_name="bo_loc.csv", mime="text/csv")

st.caption(DISCLAIMER)
