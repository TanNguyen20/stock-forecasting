"""Web Push (VAPID): máy chủ gửi thông báo tới trình duyệt đã đăng ký qua dịch vụ push của trình duyệt.

Trình duyệt hiện popup của hệ điều hành (Windows, macOS, Android) kể cả khi máy chủ chạy Linux
và không mở tab app — chỉ cần trình duyệt đang chạy.
"""
from __future__ import annotations

import base64
import json
import logging
from urllib.parse import urlparse

from .config import Settings
from .db import Repository

try:
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from py_vapid import Vapid01
    from pywebpush import WebPushException, webpush
except ImportError:  # pragma: no cover - gói tùy chọn (pywebpush kéo theo cryptography, py_vapid)
    webpush = None  # type: ignore[assignment]

log = logging.getLogger(__name__)

VAPID_META_KEY = "webpush_vapid_private_key"
DEFAULT_SUBJECT = "mailto:stockvn@example.com"
PUSH_TTL = 4 * 3600  # dịch vụ push giữ tin tối đa 4 giờ nếu trình duyệt đang tắt
MAX_SUBSCRIPTIONS = 20
MAX_BODY = 1000
GONE = {404, 410}  # đăng ký đã hết hạn / bị hủy -> xóa

# Chỉ gửi tới dịch vụ push của trình duyệt lớn: endpoint do trình duyệt (người xem) gửi lên,
# nếu không giới hạn thì máy chủ có thể bị lợi dụng để gọi URL tùy ý (SSRF).
PUSH_HOSTS = (
    "fcm.googleapis.com",          # Chrome, Edge (bản mới), Opera, Samsung Internet
    "android.googleapis.com",
    "push.services.mozilla.com",   # Firefox
    "notify.windows.com",          # Edge (WNS)
    "push.apple.com",              # Safari
)


def supported() -> bool:
    return webpush is not None


def allowed_endpoint(endpoint: str) -> bool:
    url = urlparse(endpoint)
    host = (url.hostname or "").lower()
    return url.scheme == "https" and any(host == h or host.endswith("." + h) for h in PUSH_HOSTS)


def _b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def generate_private_key() -> str:
    """Khóa riêng VAPID dạng raw 32 byte, base64url — giá trị cho WEBPUSH_VAPID_PRIVATE_KEY."""
    key = ec.generate_private_key(ec.SECP256R1())
    return _b64url(key.private_numbers().private_value.to_bytes(32, "big"))


class WebPush:
    def __init__(self, settings: Settings, repo: Repository):
        self.s = settings
        self.repo = repo

    @property
    def subject(self) -> str:
        if self.s.webpush_subject:
            return self.s.webpush_subject
        return f"mailto:{self.s.smtp_from}" if self.s.smtp_from else DEFAULT_SUBJECT

    def private_key(self) -> str:
        # Ưu tiên khóa trong .env/Secrets; nếu không có thì tự sinh một lần và lưu vào CSDL.
        return self.s.webpush_private_key or self.repo.setdefault_meta(VAPID_META_KEY, generate_private_key())

    def _vapid(self):
        return Vapid01.from_string(self.private_key())

    def public_key(self) -> str:
        """applicationServerKey cho `pushManager.subscribe` (điểm EC không nén, base64url)."""
        raw = self._vapid().public_key.public_bytes(
            serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint
        )
        return _b64url(raw)

    def subscribe(self, subscription: dict, user_agent: str = "") -> None:
        endpoint = str(subscription.get("endpoint", ""))
        keys = subscription.get("keys") or {}
        p256dh, auth = str(keys.get("p256dh", "")), str(keys.get("auth", ""))
        if not allowed_endpoint(endpoint):
            raise ValueError("Dịch vụ push của trình duyệt này không được hỗ trợ")
        if not (p256dh and auth) or len(p256dh) > 200 or len(auth) > 100:
            raise ValueError("Đăng ký push không hợp lệ")
        known = {s["endpoint"] for s in self.repo.push_subscriptions()}
        if endpoint not in known and len(known) >= MAX_SUBSCRIPTIONS:
            raise ValueError(f"Đã đạt tối đa {MAX_SUBSCRIPTIONS} trình duyệt đăng ký")
        self.repo.add_push_subscription(endpoint, p256dh, auth, user_agent[:200])

    def unsubscribe(self, endpoint: str) -> None:
        self.repo.remove_push_subscription(endpoint)

    def send(self, title: str, body: str, tag: str | None = None) -> int:
        """Gửi tới mọi trình duyệt đã đăng ký; trả về số trình duyệt nhận. Ném lỗi nếu không gửi được cái nào."""
        if not supported():
            raise RuntimeError("Thiếu gói pywebpush")
        subs = self.repo.push_subscriptions()
        if not subs:
            raise RuntimeError("Chưa có trình duyệt nào bật thông báo")
        vapid = self._vapid()
        payload = json.dumps({"title": title, "body": body[:MAX_BODY], "tag": tag}, ensure_ascii=False)
        sent, errors = 0, []
        for sub in subs:
            info = {"endpoint": sub["endpoint"], "keys": {"p256dh": sub["p256dh"], "auth": sub["auth"]}}
            try:
                webpush(
                    info, data=payload, vapid_private_key=vapid,
                    vapid_claims={"sub": self.subject},  # dict mới mỗi lần: pywebpush ghi "aud" vào đây
                    ttl=PUSH_TTL, timeout=15, headers={"Urgency": "high"},
                )
                sent += 1
            except WebPushException as exc:
                status = getattr(exc.response, "status_code", None)
                if status in GONE:
                    log.info("Đăng ký push đã hết hạn, xóa: %s", urlparse(sub["endpoint"]).hostname)
                    self.repo.remove_push_subscription(sub["endpoint"])
                else:
                    errors.append(f"{urlparse(sub['endpoint']).hostname}: HTTP {status}")
            except Exception as exc:  # noqa: BLE001 - lỗi mạng một trình duyệt không chặn các trình duyệt khác
                errors.append(f"{urlparse(sub['endpoint']).hostname}: {exc}")
        if errors:
            log.warning("Gửi Web Push lỗi: %s", "; ".join(errors))
        if sent == 0:
            raise RuntimeError("; ".join(errors) or "Mọi đăng ký đã hết hạn — bật lại thông báo trên trình duyệt")
        return sent
