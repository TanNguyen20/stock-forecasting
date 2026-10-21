import sys
from datetime import datetime

import pandas as pd
import streamlit as st

from stockvn.config import ROOT, get_settings
from stockvn.forecast import ALL_MODELS
from stockvn.models import CHANNEL_LABELS, Channel
from stockvn.service import HEARTBEAT_KEY
from views.common import fmt_time, get_app, symbol_table, worker_state_label

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

# ---- Mô hình
st.subheader("Mô hình dự báo")
st.dataframe(
    pd.DataFrame([{"Mô hình": cls.label, "Trạng thái": "✅ Sẵn sàng" if cls.available() else "❌ Chưa cài",
                   "Thư viện": ", ".join(cls.requires) or "statsmodels / scikit-learn"}
                  for cls in ALL_MODELS.values()]),
    hide_index=True, width="stretch",
)
