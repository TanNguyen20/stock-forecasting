from datetime import datetime

import pandas as pd
import streamlit as st

from stockvn.market import is_index, now_vn
from stockvn.models import ALERT_LABELS, ALERT_UNITS, CHANNEL_LABELS, Alert, AlertKind, Channel
from stockvn.service import HEARTBEAT_KEY
from stockvn.worker import background_state
from views.common import DISCLAIMER, fmt_price, fmt_time, get_app, symbol_picker, worker_state_label

app = get_app()
repo = app.repo
available = app.notifier.available()

st.title("🔔 Cảnh báo giá")

# ---- Trạng thái worker
beat = repo.get_meta(HEARTBEAT_KEY)
beat_dt = datetime.fromisoformat(beat) if beat else None
if background_state() == "running":
    st.success(f"Worker chạy kèm giao diện · lần kiểm tra gần nhất {fmt_time(beat_dt)}", icon="✅")
elif beat_dt and (now_vn() - beat_dt).total_seconds() < 600:
    st.success(f"Worker đang chạy · lần kiểm tra gần nhất {fmt_time(beat_dt)}", icon="✅")
else:
    st.warning(
        f"Worker nền: {worker_state_label()}. Lần kiểm tra gần nhất {fmt_time(beat_dt)} "
        "(ngoài giờ giao dịch worker tạm nghỉ). Xem trang **Cài đặt**.", icon="⏸️",
    )

# ---- Tạo cảnh báo
with st.container(border=True):
    st.subheader("Tạo cảnh báo mới")
    c1, c2 = st.columns(2)
    with c1:
        symbol = symbol_picker(key="alert_symbol")
    with c2:
        kind = st.selectbox("Điều kiện", list(AlertKind), format_func=lambda k: ALERT_LABELS[k])

    quote = repo.get_quotes([symbol]).get(symbol)
    if quote is None:
        try:
            quote = app.collector.refresh_quote(symbol)
        except Exception as exc:  # noqa: BLE001
            st.warning(f"Chưa lấy được giá {symbol}: {exc}")

    idx = is_index(symbol)
    if kind in (AlertKind.PRICE_ABOVE, AlertKind.PRICE_BELOW):
        base = quote.price if quote else 10_000.0
        default = base * (1.05 if kind is AlertKind.PRICE_ABOVE else 0.95)
        default = round(default, 2) if idx else float(round(default / 100) * 100)
        step = 1.0 if idx else 100.0
    else:
        default, step = {
            AlertKind.CHANGE_UP: (3.0, 0.5), AlertKind.CHANGE_DOWN: (3.0, 0.5),
            AlertKind.VOLUME_SPIKE: (2.0, 0.5), AlertKind.RSI_ABOVE: (70.0, 1.0), AlertKind.RSI_BELOW: (30.0, 1.0),
        }[kind]

    with st.form("new_alert", clear_on_submit=False, border=False):
        c3, c4 = st.columns(2)
        unit = "điểm" if idx and ALERT_UNITS[kind] == "VND" else ALERT_UNITS[kind]
        threshold = c3.number_input(f"Ngưỡng ({unit})" if unit else "Ngưỡng", min_value=0.0, value=float(default),
                                    step=step, format="%.2f" if idx or step < 1 else "%.0f")
        if quote:
            c3.caption(f"Giá hiện tại: **{fmt_price(quote.price, idx)}** ({quote.date.strftime('%d/%m')})")
        channels = c4.multiselect(
            "Kênh thông báo", list(Channel), format_func=lambda c: CHANNEL_LABELS[c],
            default=[c for c in Channel if available[c]],
        )
        if not any(available.values()):
            c4.caption("⚠️ Chưa có kênh nào dùng được — cấu hình Email ở trang Cài đặt.")
        for ch in channels:
            if reason := app.notifier.unavailable_reason(ch):
                c4.caption(f"⚠️ {CHANNEL_LABELS[ch]}: {reason}.")
        note = st.text_input("Ghi chú (tùy chọn)", placeholder="Ví dụ: chốt lời một phần")
        if st.form_submit_button("➕ Tạo cảnh báo", type="primary"):
            if not channels:
                st.error("Chọn ít nhất một kênh thông báo.")
            else:
                alert_id = repo.add_alert(Alert(symbol=symbol, kind=kind, threshold=threshold,
                                                channels=tuple(channels), note=note))
                st.toast(f"Đã tạo cảnh báo #{alert_id} cho {symbol}", icon="✅")
                st.rerun()

# ---- Danh sách cảnh báo
st.subheader("Danh sách cảnh báo")
alerts = repo.list_alerts()
if not alerts:
    st.info("Chưa có cảnh báo nào.")
else:
    quotes = repo.get_quotes({a.symbol for a in alerts})
    table = pd.DataFrame([{
        "id": a.id,
        "Bật": a.active,
        "Mã": a.symbol,
        "Điều kiện": ALERT_LABELS[a.kind],
        "Ngưỡng": fmt_price(a.threshold, is_index(a.symbol)) if ALERT_UNITS[a.kind] == "VND" else f"{a.threshold:g} {ALERT_UNITS[a.kind]}",
        "Giá hiện tại": fmt_price(quotes[a.symbol].price, is_index(a.symbol)) if a.symbol in quotes else "—",
        "Kênh": ", ".join(CHANNEL_LABELS[c] for c in a.channels),
        "Trạng thái": "Chờ kích hoạt" if a.armed else "Đã bắn (chờ nạp lại)",
        "Lần bắn gần nhất": fmt_time(a.last_triggered_at),
        "Ghi chú": a.note,
        "Xóa": False,
    } for a in alerts])
    edited = st.data_editor(
        table, hide_index=True, width="stretch", key="alerts_editor",
        disabled=[c for c in table.columns if c not in ("Bật", "Xóa")],
        column_config={"id": st.column_config.NumberColumn("#", width="small"),
                       "Bật": st.column_config.CheckboxColumn(width="small"),
                       "Xóa": st.column_config.CheckboxColumn(width="small")},
    )
    with st.container(horizontal=True):
        save = st.button("💾 Lưu thay đổi")
        check = st.button("▶️ Kiểm tra ngay", help="Cập nhật giá và kiểm tra tất cả cảnh báo đang bật")
    if save:
        for (_, old), (_, new) in zip(table.iterrows(), edited.iterrows()):
            if new["Xóa"]:
                repo.delete_alert(int(new["id"]))
            elif new["Bật"] != old["Bật"]:
                repo.set_alert_active(int(new["id"]), bool(new["Bật"]))
        st.rerun()
    if check:
        with st.spinner("Đang kiểm tra…"):
            res = app.run_cycle(heartbeat=False)
        for sym, err in res.errors.items():
            st.warning(f"{sym}: {err}")
        if res.fired:
            for t in res.fired:
                st.success(f"**{t.title}**\n\n{t.body}")
        else:
            st.info("Không có cảnh báo nào thỏa điều kiện (hoặc đã bắn trước đó).")

# ---- Gửi thử
with st.expander("🧪 Gửi thông báo thử"):
    c1, c2 = st.columns(2)
    for col, ch in zip((c1, c2), Channel):
        if col.button(f"Gửi thử {CHANNEL_LABELS[ch]}", disabled=not available[ch], width="stretch"):
            res = app.notifier.send((ch,), "Thử nghiệm cảnh báo", "Nếu bạn nhận được thông báo này, kênh đã hoạt động.")
            (col.success if res[ch] == "ok" else col.error)(f"{CHANNEL_LABELS[ch]}: {res[ch]}")
        if reason := app.notifier.unavailable_reason(ch):
            col.caption(reason[:1].upper() + reason[1:])

# ---- Nhật ký
st.subheader("Nhật ký cảnh báo")
log_df = repo.alert_log()
if log_df.empty:
    st.caption("Chưa có cảnh báo nào được gửi.")
else:
    log_df["triggered_at"] = pd.to_datetime(log_df["triggered_at"]).dt.strftime("%H:%M:%S %d/%m/%Y")
    st.dataframe(
        log_df.rename(columns={"triggered_at": "Thời điểm", "symbol": "Mã", "message": "Nội dung",
                               "channels": "Kênh", "status": "Kết quả"}),
        hide_index=True, width="stretch",
    )

st.caption(DISCLAIMER)
