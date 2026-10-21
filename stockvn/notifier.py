"""Gửi thông báo qua Email (SMTP), thông báo Windows (toast), thông báo trình duyệt (Web Push) và ntfy."""
from __future__ import annotations

import html
import logging
import re
import smtplib
import ssl
import sys
from email.message import EmailMessage

import requests

from . import webpush
from .config import Settings
from .db import Repository
from .models import Channel

log = logging.getLogger(__name__)

NTFY_TOPIC_RE = re.compile(r"[-_A-Za-z0-9]{1,64}")
NTFY_MAX_BYTES = 4000  # ntfy giới hạn nội dung 4.096 byte
NTFY_PRIORITY = 4  # high


class Notifier:
    def __init__(self, settings: Settings, repo: Repository | None = None):
        self.s = settings
        self.webpush = webpush.WebPush(settings, repo) if repo is not None else None

    def available(self) -> dict[Channel, bool]:
        return {ch: self.unavailable_reason(ch) is None for ch in Channel}

    def unavailable_reason(self, ch: Channel) -> str | None:
        """None nếu kênh dùng được, ngược lại là lý do để hiện cho người dùng."""
        if ch is Channel.EMAIL:
            return None if self.s.email_enabled else "chưa cấu hình SMTP_HOST / SMTP_TO"
        if ch is Channel.NTFY:
            if not self.s.ntfy_topic:
                return "chưa cấu hình NTFY_TOPIC"
            if not NTFY_TOPIC_RE.fullmatch(self.s.ntfy_topic):
                return "NTFY_TOPIC chỉ gồm chữ, số, '-' và '_' (tối đa 64 ký tự)"
            return None
        if ch is Channel.WEBPUSH:
            if not webpush.supported():
                return "thiếu gói pywebpush"
            if self.webpush is None:
                return "chưa gắn CSDL lưu đăng ký"
            if not self.webpush.repo.push_subscriptions():
                return "chưa có trình duyệt nào bật — bấm «Bật thông báo trình duyệt» ở trang Cài đặt"
            return None
        if not self.s.toast_enabled:
            return "đã tắt bằng TOAST_ENABLED=false"
        if sys.platform != "win32":
            return ("máy chủ không chạy Windows (vd. Streamlit Cloud) — popup chỉ hiện trên máy đang chạy app, "
                    "hãy dùng Email")
        try:
            import winotify  # noqa: F401
        except ImportError:
            return "thiếu gói winotify"
        return None

    def send(self, channels: tuple[Channel, ...], title: str, body: str) -> dict[Channel, str]:
        """Trả về trạng thái từng kênh: 'ok' hoặc thông báo lỗi. Không ném ngoại lệ."""
        result: dict[Channel, str] = {}
        for ch in channels:
            try:
                if ch is Channel.EMAIL:
                    self.send_email(title, body)
                elif ch is Channel.TOAST:
                    self.send_toast(title, body)
                elif ch is Channel.WEBPUSH:
                    self.send_webpush(title, body)
                elif ch is Channel.NTFY:
                    self.send_ntfy(title, body)
                result[ch] = "ok"
            except Exception as exc:  # noqa: BLE001
                log.error("Gửi %s thất bại: %s", ch, exc)
                result[ch] = f"lỗi: {exc}"
        return result

    # ---- Email
    def send_email(self, subject: str, body: str) -> None:
        if not self.s.email_enabled:
            raise RuntimeError("Chưa cấu hình SMTP_HOST / SMTP_TO trong .env")
        msg = EmailMessage()
        msg["Subject"] = subject
        msg["From"] = self.s.smtp_from
        msg["To"] = ", ".join(self.s.smtp_to)
        msg.set_content(body)
        msg.add_alternative(
            "<div style='font-family:Segoe UI,Arial,sans-serif;font-size:14px'>"
            f"<h3 style='margin:0 0 8px'>{html.escape(subject)}</h3>"
            f"<pre style='font-family:inherit;white-space:pre-wrap'>{html.escape(body)}</pre>"
            "<p style='color:#888;font-size:12px'>Thông báo tự động từ ứng dụng Chứng khoán VN. "
            "Không phải khuyến nghị đầu tư.</p></div>",
            subtype="html",
        )
        ctx = ssl.create_default_context()
        if self.s.smtp_use_ssl:
            with smtplib.SMTP_SSL(self.s.smtp_host, self.s.smtp_port, context=ctx, timeout=30) as smtp:
                self._login_send(smtp, msg)
        else:
            with smtplib.SMTP(self.s.smtp_host, self.s.smtp_port, timeout=30) as smtp:
                smtp.starttls(context=ctx)
                self._login_send(smtp, msg)

    def _login_send(self, smtp: smtplib.SMTP, msg: EmailMessage) -> None:
        if self.s.smtp_user:
            smtp.login(self.s.smtp_user, self.s.smtp_password)
        smtp.send_message(msg)

    # ---- ntfy
    def send_ntfy(self, title: str, body: str) -> None:
        if reason := self.unavailable_reason(Channel.NTFY):
            raise RuntimeError(f"ntfy không khả dụng: {reason}")
        # Gửi dạng JSON tới URL gốc: tiêu đề tiếng Việt không phải đi qua HTTP header.
        payload = {
            "topic": self.s.ntfy_topic,
            "title": title,
            "message": body.encode("utf-8")[:NTFY_MAX_BYTES].decode("utf-8", errors="ignore"),
            "priority": NTFY_PRIORITY,
            "tags": ["chart_with_upwards_trend"],
        }
        headers = {"Authorization": f"Bearer {self.s.ntfy_token}"} if self.s.ntfy_token else {}
        resp = requests.post(f"{self.s.ntfy_server.rstrip('/')}/", json=payload, headers=headers, timeout=15)
        if not resp.ok:
            raise RuntimeError(f"ntfy trả về HTTP {resp.status_code}: {resp.text[:200]}")

    # ---- Web Push
    def send_webpush(self, title: str, body: str) -> int:
        if reason := self.unavailable_reason(Channel.WEBPUSH):
            raise RuntimeError(f"Thông báo trình duyệt không khả dụng: {reason}")
        return self.webpush.send(title, body)

    # ---- Windows toast
    def send_toast(self, title: str, body: str) -> None:
        if reason := self.unavailable_reason(Channel.TOAST):
            raise RuntimeError(f"Thông báo Windows không khả dụng: {reason}")
        from winotify import Notification, audio

        toast = Notification(app_id=self.s.toast_app_id, title=title, msg=body[:250], duration="long")
        toast.set_audio(audio.Default, loop=False)
        toast.show()
