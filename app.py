"""Ứng dụng Chứng khoán VN — chạy: streamlit run app.py"""
import streamlit as st

from stockvn.market import now_vn, session_label

st.set_page_config(page_title="Chứng khoán VN", page_icon="📈", layout="wide")

pages = [
    st.Page("views/dashboard.py", title="Tổng quan", icon="📊", default=True),
    st.Page("views/chart.py", title="Biểu đồ & chỉ báo", icon="📈"),
    st.Page("views/alerts.py", title="Cảnh báo giá", icon="🔔"),
    st.Page("views/forecast.py", title="Dự báo", icon="🔮"),
    st.Page("views/settings.py", title="Cài đặt", icon="⚙️"),
]
nav = st.navigation(pages)

with st.sidebar:
    now = now_vn()
    st.caption(f"🕒 {now.strftime('%H:%M %d/%m/%Y')} (giờ VN)")
    st.caption(f"Trạng thái thị trường: **{session_label(now)}**")

nav.run()
