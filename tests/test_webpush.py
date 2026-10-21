import base64
import dataclasses
import json
from types import SimpleNamespace

import pytest

from stockvn import webpush as wp
from stockvn.config import get_settings
from stockvn.models import Channel
from stockvn.notifier import Notifier

pytestmark = pytest.mark.skipif(not wp.supported(), reason="cần pywebpush")

FCM = "https://fcm.googleapis.com/fcm/send/abc"
MOZ = "https://updates.push.services.mozilla.com/wpush/v2/xyz"


def sub(endpoint: str) -> dict:
    return {"endpoint": endpoint, "keys": {"p256dh": "p" * 87, "auth": "a" * 22}}


@pytest.fixture
def push(repo) -> wp.WebPush:
    settings = dataclasses.replace(get_settings(), webpush_private_key="", webpush_subject="", smtp_from="")
    return wp.WebPush(settings, repo)


def _unb64(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def test_key_generated_once_and_public_key_is_uncompressed_point(push, repo):
    key = push.private_key()
    assert push.private_key() == key  # lưu lại, không sinh khóa mới
    assert repo.get_meta(wp.VAPID_META_KEY) == key
    raw = _unb64(push.public_key())
    assert len(raw) == 65 and raw[0] == 4


def test_key_from_settings_wins(repo):
    key = wp.generate_private_key()
    push = wp.WebPush(dataclasses.replace(get_settings(), webpush_private_key=key), repo)
    assert push.private_key() == key
    assert repo.get_meta(wp.VAPID_META_KEY) is None


@pytest.mark.parametrize("endpoint, ok", [
    (FCM, True),
    (MOZ, True),
    ("https://wns2-par02p.notify.windows.com/w/?token=x", True),
    ("https://web.push.apple.com/abc", True),
    ("http://fcm.googleapis.com/fcm/send/abc", False),
    ("https://169.254.169.254/latest/meta-data", False),
    ("https://evil.example.com/fcm.googleapis.com", False),
    ("https://fcm.googleapis.com.evil.example.com/x", False),
])
def test_allowed_endpoint(endpoint, ok):
    assert wp.allowed_endpoint(endpoint) is ok


def test_subscribe_validates_and_upserts(push, repo):
    with pytest.raises(ValueError):
        push.subscribe(sub("https://internal.local/hook"))
    with pytest.raises(ValueError):
        push.subscribe({"endpoint": FCM, "keys": {}})
    push.subscribe(sub(FCM), "UA 1")
    push.subscribe(sub(FCM), "UA 2")
    rows = repo.push_subscriptions()
    assert len(rows) == 1 and rows[0]["user_agent"] == "UA 2"
    push.unsubscribe(FCM)
    assert repo.push_subscriptions() == []


def test_subscribe_limit(push, monkeypatch):
    monkeypatch.setattr(wp, "MAX_SUBSCRIPTIONS", 1)
    push.subscribe(sub(FCM))
    push.subscribe(sub(FCM))  # cập nhật đăng ký cũ vẫn được
    with pytest.raises(ValueError):
        push.subscribe(sub(MOZ))


def test_send_uses_fresh_claims_and_drops_expired(push, repo, monkeypatch):
    push.subscribe(sub(FCM))
    push.subscribe(sub(MOZ))
    calls = []

    def fake_webpush(info, data, vapid_private_key, vapid_claims, **kw):
        calls.append((info["endpoint"], json.loads(data), dict(vapid_claims), kw))
        vapid_claims["aud"] = info["endpoint"]  # pywebpush sửa dict được truyền vào
        if info["endpoint"] == MOZ:
            raise wp.WebPushException("gone", response=SimpleNamespace(status_code=410))

    monkeypatch.setattr(wp, "webpush", fake_webpush)
    assert push.send("FPT vượt 120.000", "Giá hiện tại 121.000") == 1
    assert [c[0] for c in calls] == [FCM, MOZ]
    assert all("aud" not in c[2] for c in calls)
    assert calls[0][1]["title"] == "FPT vượt 120.000"
    assert calls[0][2]["sub"] == wp.DEFAULT_SUBJECT
    assert calls[0][3]["ttl"] == wp.PUSH_TTL
    assert [r["endpoint"] for r in repo.push_subscriptions()] == [FCM]  # MOZ (410) đã bị xóa


def test_send_raises_when_all_fail(push, monkeypatch):
    push.subscribe(sub(FCM))

    def fail(*_a, **_kw):
        raise wp.WebPushException("boom", response=SimpleNamespace(status_code=500))

    monkeypatch.setattr(wp, "webpush", fail)
    with pytest.raises(RuntimeError, match="HTTP 500"):
        push.send("t", "b")


def test_notifier_webpush_channel(repo, monkeypatch):
    n = Notifier(get_settings(), repo)
    assert "trình duyệt" in n.unavailable_reason(Channel.WEBPUSH)
    n.webpush.subscribe(sub(FCM))
    assert n.available()[Channel.WEBPUSH]
    monkeypatch.setattr(wp, "webpush", lambda *a, **kw: None)
    assert n.send((Channel.WEBPUSH,), "t", "b") == {Channel.WEBPUSH: "ok"}
    assert Notifier(get_settings()).unavailable_reason(Channel.WEBPUSH)  # không có CSDL
