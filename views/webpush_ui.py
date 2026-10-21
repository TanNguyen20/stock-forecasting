"""Nút bật/tắt thông báo trình duyệt (Web Push) — component tự viết trong webpush_frontend/, không cần build."""
from __future__ import annotations

from pathlib import Path

import streamlit as st
import streamlit.components.v1 as components

from stockvn import webpush
from stockvn.service import App

_component = components.declare_component("webpush", path=str(Path(__file__).parent / "webpush_frontend"))


def webpush_toggle(app: App) -> None:
    if not webpush.supported() or app.notifier.webpush is None:
        st.caption("Cần cài gói `pywebpush` (có trong `requirements.txt`).")
        return
    push = app.notifier.webpush
    event = _component(public_key=push.public_key(), key="webpush_toggle", default=None)
    # Giá trị component giữ nguyên qua các lần chạy lại: chỉ xử lý mỗi sự kiện một lần.
    if not isinstance(event, dict) or event.get("nonce") == st.session_state.get("_webpush_nonce"):
        return
    st.session_state["_webpush_nonce"] = event.get("nonce")
    action = event.get("action")
    try:
        if action in ("subscribe", "sync"):
            push.subscribe(event.get("subscription") or {}, str(event.get("ua", "")))
            if action == "subscribe":
                st.toast("Đã bật thông báo trình duyệt", icon="🔔")
        elif action == "unsubscribe":
            push.unsubscribe(str(event.get("endpoint", "")))
            st.toast("Đã tắt thông báo trên trình duyệt này", icon="🔕")
    except ValueError as exc:
        st.error(str(exc))
