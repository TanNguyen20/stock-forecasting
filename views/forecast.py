from datetime import time, timedelta

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from stockvn.forecast import ALL_MODELS, DEFAULT_MODELS, available_models, run_forecast
from stockvn.forecast.ledger import mape_by_step, record, score_outcomes
from stockvn.market import fmt_volume, is_index, now_vn
from views.common import DISCLAIMER, date_breaks, fmt_pct, fmt_price, get_app, load_history, symbol_picker

repo = get_app().repo
st.title("🔮 Dự báo giá")
st.warning(DISCLAIMER)

avail = available_models()

with st.container(border=True):
    c1, c2, c3 = st.columns([2, 2, 1])
    with c1:
        symbol = symbol_picker(key="fc_symbol")
    with c2:
        horizon = st.slider("Tầm dự báo (số phiên giao dịch)", 5, 60, 20, step=5)
    with c3:
        conf = st.selectbox("Khoảng dự báo", [80, 90, 95], format_func=lambda v: f"{v}%")

    keys = st.multiselect(
        "Mô hình", list(ALL_MODELS), default=[k for k in DEFAULT_MODELS if k in avail],
        format_func=lambda k: ALL_MODELS[k].label + ("" if k in avail else " (chưa cài)"),
    )
    missing = [k for k in keys if k not in avail]
    if missing:
        st.error("Chưa cài thư viện cho: " + ", ".join(ALL_MODELS[k].label for k in missing)
                 + ". Chạy `pip install -r requirements-ml.txt`.")
    if {"lstm", "prophet"} & set(keys):
        st.caption("⏱️ LSTM và Prophet chạy lâu hơn (vài giây đến vài chục giây mỗi lần huấn luyện).")

    c4, c5 = st.columns([1, 2], vertical_alignment="bottom")
    do_bt = c4.toggle("Kiểm định walk-forward", value=True,
                      help="Dự báo lại các giai đoạn đã qua để đo sai số thực tế; dùng để gán trọng số ensemble")
    folds = c5.slider("Số lần kiểm định", 2, 6, 3, disabled=not do_bt)
    run = st.button("🚀 Chạy dự báo", type="primary", disabled=not keys or bool(missing))

df = load_history(symbol, min_rows=300)
if df.empty:
    st.stop()
idx = is_index(symbol)

params = (symbol, str(df.index[-1].date()), len(df), horizon, tuple(sorted(keys)), folds if do_bt else 0, conf)
cache: dict = st.session_state.setdefault("forecast_cache", {})

if run:
    bar = st.progress(0.0, text="Đang chuẩn bị…")
    report = run_forecast(
        df, horizon, [ALL_MODELS[k] for k in keys], backtest_folds=folds if do_bt else 0,
        alpha=1 - conf / 100, progress=lambda text, frac: bar.progress(min(frac, 1.0), text=text),
    )
    bar.empty()
    cache[params] = report
    if report.results:
        n = record(repo, symbol, df, report, conf)
        st.toast(f"Đã ghi {n} điểm dự báo vào sổ để chấm điểm khi đến ngày", icon="📒")


def model_label(key: str) -> str:
    return ALL_MODELS[key].label if key in ALL_MODELS else "Tổng hợp (Ensemble)" if key == "ensemble" else key


def ledger_section() -> None:
    st.subheader("📒 Sổ dự báo — độ chính xác thực tế")
    now = now_vn()
    until = now.date() if now.time() >= time(15) else now.date() - timedelta(days=1)  # phiên hôm nay chưa chốt
    outcomes = repo.forecast_outcomes(symbol, until=until)
    logged = repo.forecast_log_count(symbol)
    if outcomes.empty:
        st.caption(f"Sổ có {fmt_volume(logged)} điểm dự báo cho {symbol} nhưng chưa điểm nào đến ngày đích. Mỗi lần "
                   "chạy dự báo (và worker mỗi tối sau giờ đóng cửa) đều được ghi lại; điểm số xuất hiện sau vài phiên.")
        return
    max_step = int(outcomes["step"].max())
    step = max_step
    if max_step > 1:
        step = st.slider("Chỉ tính các điểm cách ngày dự báo tối đa (phiên)", 1, max_step, max_step, key="ledger_step")
    sc = score_outcomes(outcomes, max_step=step)
    naive = sc.loc[sc["model"].eq("naive"), "mape"]
    sc["Thắng Naive?"] = sc["mape"].lt(naive.iloc[0]).map({True: "✅", False: "❌"}) if len(naive) else "—"
    sc.loc[sc["model"].eq("naive"), "Thắng Naive?"] = "(mốc chuẩn)"
    # Định dạng sẵn thành chuỗi: ô trống (Naive không có hướng / khoảng) hiện "—".
    sc["coverage"] = [f"{c:.0f}% / {k:.0f}%" if pd.notna(c) else "—" for c, k in zip(sc["coverage"], sc["conf"])]
    sc["mape"] = sc["mape"].map(lambda v: f"{v:.2f}".replace(".", ","))
    sc["direction_acc"] = sc["direction_acc"].map(lambda v: "—" if pd.isna(v) else f"{v:.0f}")
    st.dataframe(
        sc.drop(columns=["conf"]).assign(model=sc["model"].map(model_label)).rename(columns={
            "model": "Mô hình", "mape": "MAPE (%)", "direction_acc": "Đúng hướng (%)",
            "coverage": "Độ phủ thực tế / danh nghĩa", "forecasts": "Số lần dự báo", "points": "Số điểm",
        }),
        hide_index=True, width="stretch",
    )
    by_step = mape_by_step(outcomes[outcomes["step"] <= step])
    if len(by_step) > 1:
        fig = go.Figure([go.Scatter(x=by_step.index, y=by_step[m], name=model_label(m), mode="lines+markers")
                         for m in by_step.columns])
        fig.update_layout(height=320, margin=dict(l=10, r=10, t=30, b=10), xaxis_title="Số phiên sau ngày dự báo",
                          yaxis_title="MAPE (%)", legend=dict(orientation="h", y=1.1, x=0), separators=",.")
        st.plotly_chart(fig, width="stretch")
    st.caption("Khác với walk-forward (dự báo lại quá khứ), sổ dự báo chấm điểm các dự báo đã đưa ra trước khi có "
               "giá thực tế — kiểm định ngoài mẫu thật sự. Ít điểm thì kết quả còn nhiễu.")


report = cache.get(params)
if report is None:
    st.info("Chọn mô hình rồi bấm **Chạy dự báo**.")
    ledger_section()
    st.stop()

for key, err in report.errors.items():
    st.error(f"{ALL_MODELS[key].label}: {err}")
if not report.results:
    st.stop()

last_close = float(df["close"].iloc[-1])
last_date = df.index[-1]
series = dict(report.results)
if report.ensemble:
    series["ensemble"] = report.ensemble

# ---- Biểu đồ
band_key = st.segmented_control(
    "Hiển thị khoảng dự báo của", list(series), default="ensemble" if "ensemble" in series else list(series)[0],
    format_func=lambda k: series[k].label, required=True,
)
hist = df["close"].tail(max(120, horizon * 4))
fig = go.Figure()
fig.add_trace(go.Scatter(x=hist.index, y=hist, name="Giá thực tế", line=dict(color="#0f172a", width=2)))
palette = ["#64748b", "#3b82f6", "#f59e0b", "#10b981", "#a855f7", "#ef4444"]
for i, (key, res) in enumerate(series.items()):
    x = [last_date, *res.dates]
    if key == band_key and res.lower is not None:
        fig.add_trace(go.Scatter(x=x, y=[last_close, *res.upper], line=dict(width=0), showlegend=False, hoverinfo="skip"))
        fig.add_trace(go.Scatter(x=x, y=[last_close, *res.lower], line=dict(width=0), fill="tonexty",
                                 fillcolor="rgba(37,99,235,0.15)", name=f"Khoảng {conf}% – {res.label}"))
    is_ens = key == "ensemble"
    fig.add_trace(go.Scatter(
        x=x, y=[last_close, *res.mean], name=res.label,
        line=dict(width=3 if is_ens else 1.5, dash="solid" if is_ens else "dash",
                  color="#2563eb" if is_ens else palette[i % len(palette)]),
    ))
fig.update_layout(height=520, margin=dict(l=10, r=10, t=30, b=10), hovermode="x unified",
                  legend=dict(orientation="h", y=1.05, x=0), separators=",.")
fig.update_xaxes(rangebreaks=date_breaks(hist.index.append(series[band_key].dates)))
st.plotly_chart(fig, width="stretch")

# ---- Tóm tắt
st.subheader(f"Tóm tắt sau {horizon} phiên (đến {series[band_key].dates[-1].strftime('%d/%m/%Y')})")
rows = []
for key, res in series.items():
    end = res.mean[-1]
    rows.append({
        "Mô hình": res.label,
        "Giá dự báo": fmt_price(end, idx),
        "Thay đổi": fmt_pct((end / last_close - 1) * 100),
        f"Khoảng {conf}%": (f"{fmt_price(res.lower[-1], idx)} – {fmt_price(res.upper[-1], idx)}"
                            if res.lower is not None else "—"),
        "Thời gian chạy": f"{report.seconds.get(key, 0):.1f}s" if key in report.seconds else "",
    })
st.caption(f"Giá đóng cửa gần nhất: **{fmt_price(last_close, idx)}** ({last_date.strftime('%d/%m/%Y')})")
st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch")

# ---- Kiểm định
if report.scores:
    st.subheader("Kết quả kiểm định walk-forward")
    sc = report.scores_frame()
    naive_mape = sc.loc[sc["key"].eq("naive"), "mape"]
    sc["Thắng Naive?"] = sc["mape"].lt(naive_mape.iloc[0]).map({True: "✅", False: "❌"}) if len(naive_mape) else "—"
    sc.loc[sc["key"].eq("naive"), "Thắng Naive?"] = "(mốc chuẩn)"
    st.dataframe(
        sc.drop(columns=["key"]).rename(columns={
            "label": "Mô hình", "mape": "MAPE (%)", "rmse": "RMSE", "direction_acc": "Đúng hướng (%)",
            "coverage": f"Độ phủ khoảng {conf}% (%)", "folds": "Số lần",
        }).style.format({"MAPE (%)": "{:.2f}", "RMSE": "{:,.0f}", "Đúng hướng (%)": "{:.0f}",
                         f"Độ phủ khoảng {conf}% (%)": "{:.0f}"}, na_rep="—"),
        hide_index=True, width="stretch",
    )
    st.caption(
        f"MAPE: sai số % trung bình trên {folds} giai đoạn × {horizon} phiên gần nhất. "
        "Mô hình không thắng được Naive (giữ nguyên giá) thì không có giá trị dự báo hơn đoán ngẫu nhiên. "
        "Số lần kiểm định ít nên kết quả có độ nhiễu lớn. Ensemble dùng trọng số tỷ lệ nghịch với MAPE."
    )

# ---- Chi tiết theo ngày
with st.expander("📄 Chi tiết theo từng phiên"):
    detail = pd.DataFrame({res.label: res.mean for res in series.values()}, index=series[band_key].dates)
    detail.index = detail.index.strftime("%d/%m/%Y")
    st.dataframe(detail.style.format(lambda v: fmt_price(v, idx)), width="stretch")
    st.download_button("⬇️ Tải CSV", detail.to_csv().encode("utf-8-sig"),
                       file_name=f"du_bao_{symbol}_{horizon}phien.csv", mime="text/csv")

ledger_section()
