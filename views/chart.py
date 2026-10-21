import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st
from plotly.subplots import make_subplots

from stockvn.indicators import add_indicators
from stockvn.market import fmt_volume, is_index
from views.common import DISCLAIMER, DOWN, UP, fmt_pct, fmt_price, get_app, load_history, symbol_picker

app = get_app()
st.title("📈 Biểu đồ & chỉ báo kỹ thuật")

PERIODS = {"3 tháng": 63, "6 tháng": 126, "1 năm": 252, "3 năm": 756, "5 năm": 1260, "Tất cả": None}

c1, c2 = st.columns([4, 1], vertical_alignment="bottom")
with c1:
    symbol = symbol_picker(key="chart_symbol")
with c2:
    if st.button("⟳ Tải lại", width="stretch",
                 help="Tải lại toàn bộ lịch sử để cập nhật giá điều chỉnh (chia cổ tức, tách cổ phiếu)"):
        with st.spinner("Đang tải lại…"):
            n = app.collector.sync_history(symbol, full=True)
        st.toast(f"Đã lưu {n} phiên của {symbol}")

with st.container(horizontal=True, wrap=True):
    period = st.segmented_control("Khoảng thời gian", list(PERIODS), default="1 năm", required=True)
    overlays = st.pills("Đường trên giá", ["SMA20", "SMA50", "SMA200", "Bollinger"],
                        selection_mode="multi", default=["SMA20", "SMA50"])
    panels = st.pills("Khung phụ", ["Khối lượng", "RSI", "MACD"], selection_mode="multi",
                      default=["Khối lượng", "RSI"])

raw = load_history(symbol)
if raw.empty:
    st.stop()

df = add_indicators(raw)
view = df.tail(PERIODS[period]) if PERIODS[period] else df
idx = is_index(symbol)

# ---- Thống kê nhanh
close = df["close"]
last = close.iloc[-1]


def ret(n: int) -> float:
    return (last / close.iloc[-n - 1] - 1) * 100 if len(close) > n else np.nan


ytd_base = close[close.index.year < close.index[-1].year]
ytd = (last / ytd_base.iloc[-1] - 1) * 100 if not ytd_base.empty else np.nan
year = df.tail(252)
vol_ann = np.log(close).diff().tail(252).std() * np.sqrt(252) * 100

m = [*st.columns(3), *st.columns(3)]
m[0].metric("Giá đóng cửa", fmt_price(last, idx), fmt_pct(ret(1)))
m[1].metric("1 tuần", fmt_pct(ret(5)))
m[2].metric("1 tháng", fmt_pct(ret(21)))
m[3].metric("Từ đầu năm", fmt_pct(ytd))
m[4].metric("Cao / thấp 52 tuần", f"{fmt_price(year['high'].max(), idx)} / {fmt_price(year['low'].min(), idx)}")
m[5].metric("Biến động năm", fmt_pct(vol_ann, signed=False), help="Độ lệch chuẩn lợi suất ngày × √252")

rows = 1 + len(panels)
heights = [0.55] + [0.45 / max(len(panels), 1)] * len(panels) if panels else [1.0]
fig = make_subplots(rows=rows, cols=1, shared_xaxes=True, vertical_spacing=0.03, row_heights=heights)
fig.add_trace(go.Candlestick(
    x=view.index, open=view["open"], high=view["high"], low=view["low"], close=view["close"], name=symbol,
    increasing_line_color=UP, decreasing_line_color=DOWN,
), row=1, col=1)
for name, col, color in (("SMA20", "sma20", "#f59e0b"), ("SMA50", "sma50", "#3b82f6"), ("SMA200", "sma200", "#a855f7")):
    if name in overlays:
        fig.add_trace(go.Scatter(x=view.index, y=view[col], name=name, line=dict(width=1.3, color=color)), row=1, col=1)
if "Bollinger" in overlays:
    fig.add_trace(go.Scatter(x=view.index, y=view["bb_upper"], name="BB trên", line=dict(width=0.8, color="#94a3b8")), row=1, col=1)
    fig.add_trace(go.Scatter(x=view.index, y=view["bb_lower"], name="BB dưới", line=dict(width=0.8, color="#94a3b8"),
                             fill="tonexty", fillcolor="rgba(148,163,184,0.12)"), row=1, col=1)

r = 2
if "Khối lượng" in panels:
    colors = np.where(view["close"] >= view["open"], UP, DOWN)
    fig.add_trace(go.Bar(x=view.index, y=view["volume"], name="Khối lượng", marker_color=colors, opacity=0.7), row=r, col=1)
    fig.add_trace(go.Scatter(x=view.index, y=view["vol_sma20"], name="KL TB20", line=dict(width=1, color="#64748b")), row=r, col=1)
    fig.update_yaxes(title_text="KL", row=r, col=1)
    r += 1
if "RSI" in panels:
    fig.add_trace(go.Scatter(x=view.index, y=view["rsi14"], name="RSI(14)", line=dict(width=1.2, color="#8b5cf6")), row=r, col=1)
    for level in (30, 70):
        fig.add_hline(y=level, line_dash="dot", line_color="#94a3b8", row=r, col=1)
    fig.update_yaxes(title_text="RSI", range=[0, 100], row=r, col=1)
    r += 1
if "MACD" in panels:
    hist_colors = np.where(view["macd_hist"] >= 0, UP, DOWN)
    fig.add_trace(go.Bar(x=view.index, y=view["macd_hist"], name="Histogram", marker_color=hist_colors, opacity=0.6), row=r, col=1)
    fig.add_trace(go.Scatter(x=view.index, y=view["macd"], name="MACD", line=dict(width=1.2, color="#0ea5e9")), row=r, col=1)
    fig.add_trace(go.Scatter(x=view.index, y=view["macd_signal"], name="Signal", line=dict(width=1.2, color="#f97316")), row=r, col=1)
    fig.update_yaxes(title_text="MACD", row=r, col=1)

fig.update_layout(
    height=420 + 160 * len(panels), margin=dict(l=10, r=10, t=30, b=10), xaxis_rangeslider_visible=False,
    legend=dict(orientation="h", y=1.02, x=0), hovermode="x unified", separators=",.",
)
# Ẩn cuối tuần để biểu đồ liền mạch
fig.update_xaxes(rangebreaks=[dict(bounds=["sat", "mon"])])
st.plotly_chart(fig, width="stretch")

with st.expander("📄 Dữ liệu"):
    table = view[["open", "high", "low", "close", "volume", "rsi14"]].iloc[::-1].copy()
    table.index = table.index.strftime("%d/%m/%Y")
    st.dataframe(
        table.rename(columns={"open": "Mở", "high": "Cao", "low": "Thấp", "close": "Đóng", "volume": "KL", "rsi14": "RSI"})
        .style.format({"Mở": fmt_price, "Cao": fmt_price, "Thấp": fmt_price, "Đóng": fmt_price, "KL": fmt_volume,
                       "RSI": "{:.1f}"}),
        width="stretch",
    )
    st.download_button("⬇️ Tải CSV", raw.to_csv().encode("utf-8-sig"), file_name=f"{symbol}.csv", mime="text/csv")

st.caption(DISCLAIMER)
