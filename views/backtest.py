from datetime import timedelta

import pandas as pd
import plotly.graph_objects as go
import streamlit as st
from plotly.subplots import make_subplots

from stockvn.config import get_settings
from stockvn.market import is_index
from stockvn.strategy import PARAM_LABELS, STRATEGIES, buy_and_hold, run_backtest
from views.common import DISCLAIMER, DOWN, UP, date_breaks, fmt_pct, fmt_price, load_history, symbol_picker

s = get_settings()
st.title("🧪 Backtest chiến lược")
st.caption("Tín hiệu tính trên giá đóng cửa, lệnh khớp ở giá mở cửa phiên kế tiếp (không nhìn trước dữ liệu). "
           "Chỉ mua/bán, không bán khống; mỗi lần mua dùng toàn bộ vốn.")

with st.container(border=True):
    c1, c2 = st.columns([2, 1])
    with c1:
        symbol = symbol_picker(key="bt_symbol")
    key = c2.selectbox("Chiến lược", list(STRATEGIES), format_func=lambda k: STRATEGIES[k].label)
    strat = STRATEGIES[key]
    st.caption(strat.help)

    params = {}
    if strat.params:
        for col, (name, (default, lo, hi)) in zip(st.columns(len(strat.params)), strat.params.items()):
            params[name] = int(col.number_input(PARAM_LABELS.get(name, name), lo, hi, default, step=1,
                                                key=f"bt_{key}_{name}"))

    c3, c4, c5, c6, c7 = st.columns(5)
    years = c3.selectbox("Giai đoạn", [1, 2, 3, 5, 10], index=2, format_func=lambda y: f"{y} năm gần nhất")
    capital = c4.number_input("Vốn ban đầu (VND)", min_value=1_000_000.0, value=100_000_000.0, step=10_000_000.0,
                              format="%.0f")
    fee_pct = c5.number_input("Phí mỗi chiều (%)", 0.0, 1.0, s.trade_fee_pct, step=0.01, format="%.2f")
    tax_pct = c6.number_input("Thuế bán (%)", 0.0, 1.0, s.sell_tax_pct, step=0.01, format="%.2f")
    min_hold = c7.number_input("Giữ tối thiểu (phiên)", 0, 10, 2, help="T+2: cổ phiếu mua phiên T bán được từ T+2")

if key == "sma_cross" and params["fast"] >= params["slow"]:
    st.error("SMA nhanh phải nhỏ hơn SMA chậm.")
    st.stop()

raw = load_history(symbol, min_rows=300)
if raw.empty:
    st.stop()
idx = is_index(symbol)

# Tính tín hiệu trên toàn bộ lịch sử (để chỉ báo "ấm" sẵn), chỉ chạy backtest trong giai đoạn chọn.
signal = strat.signal(raw, **params)
start = raw.index[-1] - timedelta(days=int(365.25 * years))
df = raw[raw.index >= start]
if len(df) < 20:
    st.error("Giai đoạn quá ngắn.")
    st.stop()
res = run_backtest(df, signal, capital, fee_pct, tax_pct, int(min_hold))
bench = run_backtest(df, buy_and_hold(df), capital, fee_pct, tax_pct, int(min_hold))

# ---- Chỉ số
st.subheader(f"{strat.label} · {symbol} · {df.index[0]:%d/%m/%Y} – {df.index[-1]:%d/%m/%Y}")
a, b = res.stats, bench.stats


def vs_bench(k: str) -> str:
    return f"{a[k] - b[k]:+.1f}".replace(".", ",") + " điểm %"


m = st.columns(4) + st.columns(4)
m[0].metric("Lợi nhuận", fmt_pct(a["total_return"]), vs_bench("total_return"))
m[1].metric("CAGR", fmt_pct(a["cagr"]), vs_bench("cagr"))
m[2].metric("Sụt giảm tối đa", fmt_pct(a["max_drawdown"]), vs_bench("max_drawdown"))
m[3].metric("Sharpe", "—" if pd.isna(a["sharpe"]) else f"{a['sharpe']:.2f}".replace(".", ","),
            help="Lợi suất ngày trung bình / độ lệch chuẩn × √252, lãi suất phi rủi ro = 0")
m[4].metric("Số lệnh đã đóng", f"{a['trades']:.0f}")
m[5].metric("Tỷ lệ thắng", "—" if pd.isna(a["win_rate"]) else fmt_pct(a["win_rate"], signed=False))
m[6].metric("Thời gian nắm giữ", fmt_pct(a["exposure"], signed=False))
m[7].metric("Phí + thuế đã trả", f"{fmt_price(a['fees'])} VND")
st.caption(f"Mũi tên: chênh lệch so với mua & giữ cùng giai đoạn (lợi nhuận {fmt_pct(b['total_return'])}, "
           f"sụt giảm tối đa {fmt_pct(b['max_drawdown'])}).")

# ---- Biểu đồ
fig = make_subplots(rows=3, cols=1, shared_xaxes=True, vertical_spacing=0.04, row_heights=[0.45, 0.35, 0.2])
fig.add_trace(go.Scatter(x=df.index, y=df["close"], name=symbol, line=dict(color="#0f172a", width=1.3)), row=1, col=1)
closed = res.trades
fig.add_trace(go.Scatter(x=closed["entry_date"], y=closed["entry_price"], mode="markers", name="Mua",
                         marker=dict(symbol="triangle-up", size=10, color=UP)), row=1, col=1)
sold = closed[~closed["open"]]
fig.add_trace(go.Scatter(x=sold["exit_date"], y=sold["exit_price"], mode="markers", name="Bán",
                         marker=dict(symbol="triangle-down", size=10, color=DOWN)), row=1, col=1)
fig.add_trace(go.Scatter(x=res.equity.index, y=res.equity, name="Chiến lược", line=dict(color="#2563eb", width=2)),
              row=2, col=1)
fig.add_trace(go.Scatter(x=bench.equity.index, y=bench.equity, name="Mua & giữ",
                         line=dict(color="#94a3b8", width=1.5, dash="dash")), row=2, col=1)
dd = (res.equity / res.equity.cummax() - 1) * 100
fig.add_trace(go.Scatter(x=dd.index, y=dd, name="Sụt giảm (%)", fill="tozeroy", line=dict(color=DOWN, width=1),
                         fillcolor="rgba(220,38,38,0.15)"), row=3, col=1)
fig.update_yaxes(title_text="Giá", row=1, col=1)
fig.update_yaxes(title_text="Tài khoản", row=2, col=1)
fig.update_yaxes(title_text="Sụt giảm %", row=3, col=1)
fig.update_layout(height=720, margin=dict(l=10, r=10, t=30, b=10), hovermode="x unified",
                  legend=dict(orientation="h", y=1.04, x=0), separators=",.")
fig.update_xaxes(rangebreaks=date_breaks(df.index))
st.plotly_chart(fig, width="stretch")

# ---- Danh sách lệnh
with st.expander(f"📄 Danh sách {len(res.trades)} lệnh"):
    if res.trades.empty:
        st.caption("Chiến lược không phát sinh lệnh nào trong giai đoạn này.")
    else:
        t = res.trades.iloc[::-1]
        st.dataframe(pd.DataFrame({
            "Ngày mua": t["entry_date"].dt.strftime("%d/%m/%Y"), "Giá mua": t["entry_price"].map(lambda v: fmt_price(v, idx)),
            "Ngày bán": [("(đang giữ) " if o else "") + d.strftime("%d/%m/%Y") for d, o in zip(t["exit_date"], t["open"])],
            "Giá bán": t["exit_price"].map(lambda v: fmt_price(v, idx)), "Số phiên": t["sessions"],
            "Lãi/lỗ (sau phí)": t["return_pct"].map(fmt_pct),
        }), hide_index=True, width="stretch")

st.caption("Dùng giá lịch sử đã điều chỉnh của nguồn dữ liệu; cho phép mua lẻ cổ phiếu, chưa tính trượt giá và "
           "giới hạn thanh khoản. Kết quả quá khứ không bảo đảm kết quả tương lai. " + DISCLAIMER)
