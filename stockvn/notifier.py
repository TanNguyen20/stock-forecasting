"""Gửi thông báo qua Email (SMTP) và thông báo Windows (toast)."""
from __future__ import annotations

import html
import logging
import smtplib
import ssl
import sys
from email.message import EmailMessage

from .config import Settings
from .models import Channel

log = logging.getLogger(__name__)


class Notifier:
    def __init__(self, settings: Settings):
        self.s = settings

    def available(self) -> dict[Channel, bool]:
        return {Channel.EMAIL: self.s.email_enabled, Channel.TOAST: self.toast_supported()}

    def send(self, channels: tuple[Channel, ...], title: str, body: str) -> dict[Channel, str]:
        """Trả về trạng thái từng kênh: 'ok' hoặc thông báo lỗi. Không ném ngoại lệ."""
        result: dict[Channel, str] = {}
        for ch in channels:
            try:
                if ch is Channel.EMAIL:
                    self.send_email(title, body)
                elif ch is Channel.TOAST:
                    self.send_toast(title, body)
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

    # ---- Windows toast
    def toast_supported(self) -> bool:
        if not (self.s.toast_enabled and sys.platform == "win32"):
            return False
        try:
            import winotify  # noqa: F401
        except ImportError:
            return False
        return True

    def send_toast(self, title: str, body: str) -> None:
        if not self.toast_supported():
            raise RuntimeError("Thông báo Windows không khả dụng (cần Windows + gói winotify)")
        from winotify import Notification, audio

        toast = Notification(app_id=self.s.toast_app_id, title=title, msg=body[:250], duration="long")
        toast.set_audio(audio.Default, loop=False)
        toast.show()
