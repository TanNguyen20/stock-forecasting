import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from stockvn.config import get_settings
from stockvn.market import fmt_change, fmt_volume, today_vn
from stockvn.portfolio import holdings, positions, summarize, trade_fee
from views.common import DISCLAIMER, color_change, fmt_pct, fmt_price, get_app, symbol_label, symbol_picker

app = get_app()
repo = app.repo
s = get_settings()
SIDES = {"buy": "Mua", "sell": "Bán"}

st.title("💼 Danh mục đầu tư")

trades = repo.trades()

# ---- Nhập giao dịch
with st.expander("➕ Ghi giao dịch", expanded=trades.empty):
    c1, c2, c3 = st.columns([2, 1, 1])
    with c1:
        symbol = symbol_picker(key="pf_symbol")
    side = c2.segmented_control("Loại lệnh", list(SIDES), format_func=SIDES.get, default="buy", required=True)
    traded_on = c3.date_input("Ngày khớp", value=today_vn(), max_value=today_vn(), format="DD/MM/YYYY")

    quote = repo.get_quotes([symbol]).get(symbol)
    c4, c5, c6 = st.columns(3)
    qty = c4.number_input("Số lượng (cổ phiếu)", min_value=0.0, value=100.0, step=100.0, format="%.0f")
    price = c5.number_input("Giá khớp (VND)", min_value=0.0, value=float(quote.price) if quote else 10_000.0,
                            step=50.0, format="%.0f",
                            help="Nhập 0 cho cổ phiếu thưởng / cổ tức bằng cổ phiếu.")
    auto_fee = trade_fee(side, qty, price, s.trade_fee_pct, s.sell_tax_pct)
    fee = c6.number_input("Phí + thuế (VND)", min_value=0.0, value=float(round(auto_fee)), step=1000.0, format="%.0f",
                          help=f"Mặc định: phí {s.trade_fee_pct:g}% mỗi chiều + thuế bán {s.sell_tax_pct:g}% "
                               "(đổi bằng TRADE_FEE_PCT / SELL_TAX_PCT trong .env).")
    note = st.text_input("Ghi chú", key="pf_note")
    if st.button("💾 Lưu giao dịch", type="primary", disabled=qty <= 0):
        new = pd.DataFrame([{"symbol": symbol, "side": side, "qty": qty, "price": price, "fee": fee,
                             "traded_on": pd.Timestamp(traded_on)}])
        try:
            positions(pd.concat([trades, new], ignore_index=True))
        except ValueError as exc:
            st.error(f"Không lưu được: {exc}")
        else:
            repo.add_trade(symbol, side, qty, price, fee, traded_on, note)
            if quote is None:
                try:
                    app.collector.refresh_quote(symbol)
                except Exception as exc:  # noqa: BLE001
                    st.warning(f"Đã lưu nhưng chưa lấy được giá {symbol}: {exc}")
            st.toast(f"Đã ghi lệnh {SIDES[side].lower()} {fmt_volume(qty)} {symbol}", icon="✅")
            st.rerun()

if trades.empty:
    st.info("Chưa có giao dịch nào. Ghi lệnh mua đầu tiên ở trên.")
    st.stop()

try:
    pos = positions(trades)
except ValueError as exc:
    st.error(f"Sổ giao dịch không hợp lệ: {exc}. Xóa hoặc sửa giao dịch sai ở bảng bên dưới.")
    pos = None

# ---- Vị thế
if pos is not None:
    held = [p for p in pos if pos[p].qty > 0]
    if st.button("🔄 Cập nhật giá", disabled=not held):
        with st.spinner("Đang lấy giá…"):
            _, errors = app.collector.refresh_quotes(held)
        for sym, err in errors.items():
            st.warning(f"{sym}: {err}")
    table = holdings(pos, repo.get_quotes(held), sell_cost_pct=s.trade_fee_pct + s.sell_tax_pct)
    summ = summarize(pos, table)

    m = st.columns(3) + st.columns(3)
    m[0].metric("Giá trị thị trường", fmt_price(summ.value))
    m[1].metric("Vốn đang đầu tư", fmt_price(summ.cost))
    m[2].metric("Lãi/lỗ chưa chốt", fmt_change(summ.pnl), fmt_pct(summ.pnl_pct))
    m[3].metric("Lãi/lỗ trong phiên", fmt_change(summ.day_pnl))
    m[4].metric("Lãi/lỗ đã chốt", fmt_change(summ.realized))
    m[5].metric("Tổng phí & thuế đã trả", fmt_price(summ.fees))

    if table.empty:
        st.info("Không còn vị thế mở.")
    else:
        c1, c2 = st.columns([3, 1])
        view = pd.DataFrame({
            "Mã": table["symbol"], "Tên": [symbol_label(x).split(" · ", 1)[-1] for x in table["symbol"]],
            "SL": table["qty"], "Giá vốn": table["avg_cost"], "Giá": table["price"], "% phiên": table["change_pct"],
            "Giá trị": table["value"], "Lãi/lỗ": table["pnl"], "%": table["pnl_pct"],
            "Sau phí bán": table["net_pnl"], "Tỷ trọng": table["weight"],
        })
        c1.dataframe(
            view.style.map(color_change, subset=["% phiên", "Lãi/lỗ", "%", "Sau phí bán"]).format({
                "SL": fmt_volume, "Giá vốn": fmt_price, "Giá": fmt_price, "% phiên": fmt_pct, "Giá trị": fmt_price,
                "Lãi/lỗ": fmt_change, "%": fmt_pct, "Sau phí bán": fmt_change,
                "Tỷ trọng": lambda v: fmt_pct(v, signed=False),
            }),
            hide_index=True, width="stretch",
            column_config={"Sau phí bán": st.column_config.Column(
                help="Lãi/lỗ nếu bán toàn bộ ngay bây giờ, đã trừ phí bán và thuế")},
        )
        fig = go.Figure(go.Pie(labels=table["symbol"], values=table["value"], hole=0.55, textinfo="label+percent",
                               sort=False))
        fig.update_layout(height=300, margin=dict(l=0, r=0, t=10, b=0), showlegend=False, separators=",.")
        c2.plotly_chart(fig, width="stretch")
    st.caption("Giá vốn bình quân gia quyền, đã gồm phí mua. Cổ tức tiền mặt chưa được tính vào lãi/lỗ; "
               "cổ phiếu thưởng ghi là lệnh mua giá 0.")

# ---- Sổ giao dịch
st.subheader("Sổ giao dịch")
log_view = trades.assign(
    side=trades["side"].map(SIDES), traded_on=trades["traded_on"].dt.strftime("%d/%m/%Y"),
    qty=trades["qty"].map(fmt_volume), price=trades["price"].map(fmt_price),
    value=(trades["qty"] * trades["price"]).map(fmt_price), fee=trades["fee"].map(fmt_price), delete=False,
)[["id", "traded_on", "symbol", "side", "qty", "price", "value", "fee", "note", "delete"]].iloc[::-1]
edited = st.data_editor(
    log_view, hide_index=True, width="stretch", key="trades_editor",
    disabled=[c for c in log_view.columns if c != "delete"],
    column_config={
        "id": st.column_config.NumberColumn("#", width="small"),
        "traded_on": "Ngày", "symbol": "Mã", "side": "Lệnh",
        "qty": "SL", "price": "Giá", "value": "Giá trị", "fee": "Phí + thuế",
        "note": "Ghi chú", "delete": st.column_config.CheckboxColumn("Xóa", width="small"),
    },
)
to_delete = edited.loc[edited["delete"], "id"].astype(int).tolist()
if st.button(f"🗑️ Xóa {len(to_delete)} giao dịch đã chọn", disabled=not to_delete):
    for trade_id in to_delete:
        repo.delete_trade(trade_id)
    st.rerun()
st.download_button("⬇️ Tải CSV", trades.to_csv(index=False).encode("utf-8-sig"), file_name="so_giao_dich.csv",
                   mime="text/csv")

st.caption(DISCLAIMER)
