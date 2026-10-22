import secrets
import sys
from datetime import datetime

import pandas as pd
import streamlit as st

from stockvn.config import ROOT, get_settings
from stockvn.digest import daily_digest, parse_channels, send_digest
from stockvn.forecast import ALL_MODELS
from stockvn.market import HOLIDAYS_FILE, fmt_volume, holidays, last_session, parse_holidays, suggest_holidays
from stockvn.models import CHANNEL_LABELS, Channel
from stockvn.service import HEARTBEAT_KEY
from views.common import fmt_time, get_app, symbol_table, worker_state_label
from views.webpush_ui import webpush_toggle

app = get_app()
repo = app.repo
s = get_settings()
on_windows = sys.platform == "win32"

st.title("⚙️ Cài đặt & dữ liệu")

# ---- Thông báo
st.subheader("Kênh thông báo")
avail = app.notifier.available()
c1, c2 = st.columns(2)
with c1, st.container(border=True):
    st.markdown(f"**{CHANNEL_LABELS[Channel.EMAIL]}** — {'✅ Đã cấu hình' if avail[Channel.EMAIL] else '❌ Chưa cấu hình'}")
    if avail[Channel.EMAIL]:
        st.caption(f"Máy chủ: `{s.smtp_host}:{s.smtp_port}` · Gửi tới: {', '.join(s.smtp_to)}")
    st.caption("Khai báo trong file `.env` (xem `.env.example`), hoặc ở **Settings → Secrets** khi chạy trên "
               "Streamlit Cloud. Với Gmail, dùng **Mật khẩu ứng dụng** (App Password), không dùng mật khẩu đăng nhập.")
with c2, st.container(border=True):
    st.markdown(f"**{CHANNEL_LABELS[Channel.TOAST]}** — {'✅ Sẵn sàng' if avail[Channel.TOAST] else '❌ Không khả dụng'}")
    if reason := app.notifier.unavailable_reason(Channel.TOAST):
        st.caption(f"Lý do: {reason}.")
    st.caption("Hiện popup ở góc màn hình của chính máy đang chạy app (cần Windows). Chỉ hoạt động khi máy bật "
               "và worker đang chạy. Tắt bằng `TOAST_ENABLED=false` trong `.env`.")

c3, c4 = st.columns(2)
with c3, st.container(border=True):
    header = st.empty()  # điền sau webpush_toggle để phản ánh đăng ký vừa thêm/xóa
    st.caption("Popup của hệ điều hành (Windows, macOS, Android) do máy chủ đẩy qua dịch vụ push của trình duyệt — "
               "dùng được cả khi máy chủ chạy Linux (vd. Streamlit Cloud). Không cần mở tab app nhưng trình duyệt "
               "phải đang chạy. Bật riêng trên từng trình duyệt/thiết bị muốn nhận.")
    webpush_toggle(app)
    subs = repo.push_subscriptions()
    status = f"✅ {len(subs)} trình duyệt đã bật" if subs else "❌ Chưa có trình duyệt nào bật"
    header.markdown(f"**{CHANNEL_LABELS[Channel.WEBPUSH]}** (Web Push) — {status}")
with c4, st.container(border=True):
    st.markdown(f"**{CHANNEL_LABELS[Channel.NTFY]}** ([ntfy.sh](https://ntfy.sh)) — "
                f"{'✅ Đã cấu hình' if avail[Channel.NTFY] else '❌ Chưa cấu hình'}")
    if avail[Channel.NTFY]:
        # Topic có tác dụng như mật khẩu (ai biết đều đọc/gửi được) -> không hiện đầy đủ.
        st.caption(f"Máy chủ: `{s.ntfy_server}` · Topic: `{s.ntfy_topic[:4]}…` "
                   f"({len(s.ntfy_topic)} ký tự){' · có token' if s.ntfy_token else ''}")
    elif reason := app.notifier.unavailable_reason(Channel.NTFY):
        st.caption(f"Lý do: {reason}.")
    st.caption("Nhận trên điện thoại (app ntfy Android/iOS) hoặc máy tính (web app ntfy trên Chrome/Edge, bật "
               "thông báo) bằng cách đăng ký đúng topic trong `.env`/Secrets. Không cần tài khoản.")
    if not avail[Channel.NTFY]:
        topic = st.session_state.setdefault("ntfy_topic_hint", f"stockvn-{secrets.token_hex(12)}")
        st.code(f"NTFY_TOPIC={topic}", language="ini")
        st.caption("Topic ngẫu nhiên gợi ý — ai biết topic đều đọc và gửi được tin, đừng chia sẻ.")

st.code(
    "SMTP_HOST=smtp.gmail.com\nSMTP_PORT=465\nSMTP_USE_SSL=true\n"
    "SMTP_USER=ban@gmail.com\nSMTP_PASSWORD=mat-khau-ung-dung-16-ky-tu\nSMTP_TO=ban@gmail.com",
    language="ini",
)
st.caption("Sau khi sửa `.env` hoặc Secrets, khởi động lại ứng dụng và worker để áp dụng.")

# ---- Worker
st.subheader("Worker nền (gửi cảnh báo tự động)")
beat = repo.get_meta(HEARTBEAT_KEY)
st.markdown(f"Trạng thái: {worker_state_label()}")
st.write("Lần chạy gần nhất:", fmt_time(datetime.fromisoformat(beat)) if beat else "chưa chạy")
st.markdown(
    f"""
Worker cập nhật giá mỗi **{s.poll_seconds} giây** trong giờ giao dịch (9:00–11:30, 13:00–15:00, thứ 2–6)
cho các mã trong danh mục và các mã có cảnh báo, rồi gửi thông báo khi điều kiện thỏa.
Mặc định worker **chạy kèm giao diện** (tắt bằng `STOCK_EMBED_WORKER=false`) và dừng khi app dừng.
Mỗi CSDL chỉ có một worker hoạt động; worker đến sau tự chờ và tiếp quản khi worker trước dừng.
"""
)
if on_windows:
    st.markdown(
        f"""
- `run_app.bat` (hoặc `.venv\\Scripts\\python app.py`) khởi động giao diện và worker cùng lúc.
- Chạy riêng không cần giao diện: mở terminal tại `{ROOT}` và chạy `run_worker.bat`.
- Tự khởi động cùng Windows: dùng **Task Scheduler** → *Create Task* → Trigger *At log on* → Action chạy `run_worker.bat`.
"""
    )
else:
    st.markdown(
        """
- Trên **Streamlit Community Cloud**, app ngủ sau 12 giờ không có truy cập — worker dừng theo, cảnh báo không
  được gửi cho tới khi có người mở lại app.
- Trên máy chủ tự quản: chạy `python worker.py` như một dịch vụ nền (vd. systemd) để cảnh báo không phụ thuộc giao diện.
"""
    )
if st.button("▶️ Chạy một vòng ngay"):
    with st.spinner("Đang cập nhật giá & kiểm tra cảnh báo…"):
        res = app.run_cycle()
    st.success(f"Đã cập nhật {len(res.quotes)} mã, {len(res.fired)} cảnh báo được gửi.")
    for sym, err in res.errors.items():
        st.warning(f"{sym}: {err}")

# ---- Dữ liệu
st.subheader("Dữ liệu")
st.caption(f"CSDL: `{s.db_path}` · Nguồn theo thứ tự ưu tiên: {' → '.join(s.sources)}")
c1, c2 = st.columns(2)
if c1.button("🔄 Cập nhật danh sách mã", width="stretch"):
    with st.spinner("Đang tải danh sách mã…"):
        n = app.collector.refresh_symbols()
    symbol_table.clear()
    st.success(f"Đã lưu {n} mã.")
if c2.button("📥 Đồng bộ lịch sử toàn bộ danh mục", width="stretch"):
    watch = repo.watchlist()
    bar = st.progress(0.0)
    for i, sym in enumerate(watch):
        bar.progress((i + 1) / len(watch), text=f"{sym}…")
        try:
            app.collector.sync_history(sym)
        except Exception as exc:  # noqa: BLE001
            st.warning(f"{sym}: {exc}")
    bar.empty()
    st.success("Đã đồng bộ.")

stored = []
for sym in repo.stored_symbols():
    first, last, count = repo.price_range(sym)
    stored.append({"Mã": sym, "Từ ngày": first, "Đến ngày": last, "Số phiên": count})
if stored:
    st.dataframe(pd.DataFrame(stored), hide_index=True, width="stretch")

# ---- Báo cáo cuối phiên
st.subheader("Báo cáo cuối phiên")
digest_channels = parse_channels(s.digest_channels)
if digest_channels:
    st.markdown(f"✅ Worker gửi sau khi chốt giá (sau 15:00) qua: "
                f"**{', '.join(CHANNEL_LABELS[c] for c in digest_channels)}**")
else:
    st.markdown("⛔ Đang tắt. Bật bằng `DIGEST_CHANNELS=email,ntfy` (các kênh: "
                f"{', '.join(f'`{c.value}`' for c in Channel)}) trong `.env`.")
st.caption("Gồm biến động các mã theo dõi, lãi/lỗ danh mục đầu tư và các cảnh báo đã gửi trong ngày.")
day = last_session()
c1, c2 = st.columns(2)
if c1.button(f"👁️ Xem trước báo cáo phiên {day.strftime('%d/%m')}", width="stretch"):
    msg = daily_digest(repo, day, s.trade_fee_pct + s.sell_tax_pct)
    if msg is None:
        st.info("Chưa có giá của phiên này cho các mã theo dõi.")
    else:
        st.markdown(f"**{msg[0]}**")
        st.code(msg[1], language=None)
if c2.button("📨 Gửi thử ngay", width="stretch", disabled=not digest_channels):
    status = send_digest(repo, app.notifier, day, digest_channels, s.trade_fee_pct + s.sell_tax_pct)
    if status is None:
        st.info("Chưa có giá của phiên này cho các mã theo dõi.")
    for ch, st_text in (status or {}).items():
        (st.success if st_text == "ok" else st.error)(f"{CHANNEL_LABELS[ch]}: {st_text}")

# ---- Lịch nghỉ lễ
st.subheader("Lịch nghỉ lễ")
st.caption(f"File `{HOLIDAYS_FILE.name}` ở thư mục gốc. Ngày nghỉ được bỏ qua khi worker cập nhật giá, khi tính ngày "
           "dự báo và trên trục thời gian của biểu đồ. Mỗi dòng `YYYY-MM-DD` hoặc `YYYY-MM-DD..YYYY-MM-DD`, "
           "chú thích sau `#`.")
current = HOLIDAYS_FILE.read_text(encoding="utf-8") if HOLIDAYS_FILE.exists() else ""
text = st.text_area("Ngày nghỉ", current, height=260, label_visibility="collapsed")
known = holidays()
suggested = [d for d in suggest_holidays(repo.trading_dates()) if d not in known]
c1, c2 = st.columns(2)
if c1.button("💾 Lưu lịch nghỉ", disabled=text == current, width="stretch"):
    HOLIDAYS_FILE.write_text(text.rstrip() + "\n", encoding="utf-8")
    st.toast(f"Đã lưu {len(parse_holidays(text))} ngày nghỉ", icon="✅")
    st.rerun()
if suggested:
    st.info("Gợi ý từ dữ liệu đã lưu — các ngày thứ 2–6 không mã nào có phiên: "
            + ", ".join(d.strftime("%d/%m/%Y") for d in suggested[-20:])
            + (f" (và {len(suggested) - 20} ngày khác)" if len(suggested) > 20 else ""))
    if c2.button(f"➕ Thêm {len(suggested)} ngày gợi ý", width="stretch"):
        lines = "\n".join(f"{d.isoformat()}  # gợi ý từ dữ liệu" for d in suggested)
        HOLIDAYS_FILE.write_text(current.rstrip() + "\n\n" + lines + "\n", encoding="utf-8")
        st.rerun()
st.caption("Dữ liệu chỉ cho biết những ngày đã qua. Ngày nghỉ sắp tới cần điền theo thông báo của HOSE/HNX.")

# ---- Mô hình
st.subheader("Mô hình dự báo")
st.dataframe(
    pd.DataFrame([{"Mô hình": cls.label, "Trạng thái": "✅ Sẵn sàng" if cls.available() else "❌ Chưa cài",
                   "Thư viện": ", ".join(cls.requires) or "statsmodels / scikit-learn"}
                  for cls in ALL_MODELS.values()]),
    hide_index=True, width="stretch",
)
if s.forecast_log_models:
    st.caption(f"📒 Sổ dự báo: worker tự dự báo {s.forecast_log_horizon} phiên mỗi tối cho các mã theo dõi bằng "
               f"`{','.join(s.forecast_log_models)}` (`FORECAST_LOG_MODELS`, để trống để tắt). "
               f"Sổ hiện có {fmt_volume(repo.forecast_log_count())} điểm dự báo.")
else:
    st.caption("📒 Worker không tự dự báo mỗi tối (`FORECAST_LOG_MODELS` trống). Dự báo chạy tay vẫn được ghi sổ.")
st.caption(f"Phí giao dịch mặc định: {s.trade_fee_pct:g}% mỗi chiều, thuế bán {s.sell_tax_pct:g}% "
           "(`TRADE_FEE_PCT`, `SELL_TAX_PCT`) — dùng cho Danh mục đầu tư và Backtest.")
