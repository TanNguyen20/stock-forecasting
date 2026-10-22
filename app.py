"""Ứng dụng Chứng khoán VN.

    python app.py            # mở giao diện + worker cảnh báo khởi động ngay
    streamlit run app.py     # worker khởi động khi có phiên trình duyệt đầu tiên

Worker chạy kèm trong luồng nền; tắt bằng STOCK_EMBED_WORKER=false trong .env.
"""
import sys

from streamlit import runtime

from stockvn.config import get_settings
from stockvn.worker import start_background

if __name__ == "__main__" and not runtime.exists():
    # `python app.py`: khởi động worker trước rồi chạy máy chủ Streamlit trong cùng tiến trình.
    from streamlit.web import cli as stcli

    if get_settings().embed_worker:
        start_background()
    sys.argv = ["streamlit", "run", __file__, *sys.argv[1:]]
    sys.exit(stcli.main())

import streamlit as st  # noqa: E402

from stockvn.market import now_vn, session_label  # noqa: E402

if get_settings().embed_worker:
    start_background()  # chỉ khởi động một lần mỗi tiến trình

st.set_page_config(page_title="Chứng khoán VN", page_icon="📈", layout="wide")

pages = [
    st.Page("views/dashboard.py", title="Tổng quan", icon="📊", default=True),
    st.Page("views/portfolio.py", title="Danh mục đầu tư", icon="💼"),
    st.Page("views/chart.py", title="Biểu đồ & chỉ báo", icon="📈"),
    st.Page("views/screener.py", title="Bộ lọc cổ phiếu", icon="🔎"),
    st.Page("views/alerts.py", title="Cảnh báo giá", icon="🔔"),
    st.Page("views/forecast.py", title="Dự báo", icon="🔮"),
    st.Page("views/backtest.py", title="Backtest chiến lược", icon="🧪"),
    st.Page("views/settings.py", title="Cài đặt", icon="⚙️"),
]
nav = st.navigation(pages)

with st.sidebar:
    now = now_vn()
    st.caption(f"🕒 {now.strftime('%H:%M %d/%m/%Y')} (giờ VN)")
    st.caption(f"Trạng thái thị trường: **{session_label(now)}**")

nav.run()
