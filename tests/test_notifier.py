import dataclasses

import pytest

from stockvn import notifier as notifier_mod
from stockvn.config import get_settings
from stockvn.models import Channel
from stockvn.notifier import Notifier


def make(**overrides) -> Notifier:
    return Notifier(dataclasses.replace(get_settings(), **overrides))


def test_email_reason_until_configured():
    assert make(smtp_host="", smtp_to=()).unavailable_reason(Channel.EMAIL)
    n = make(smtp_host="smtp.example.com", smtp_to=("a@example.com",))
    assert n.unavailable_reason(Channel.EMAIL) is None
    assert n.available()[Channel.EMAIL]


def test_toast_disabled_by_setting():
    assert "TOAST_ENABLED" in make(toast_enabled=False).unavailable_reason(Channel.TOAST)


def test_toast_unavailable_on_non_windows_server(monkeypatch):
    monkeypatch.setattr(notifier_mod.sys, "platform", "linux")
    n = make(toast_enabled=True)
    assert "Windows" in n.unavailable_reason(Channel.TOAST)
    assert not n.available()[Channel.TOAST]
    with pytest.raises(RuntimeError, match="không chạy Windows"):
        n.send_toast("t", "b")


class FakeResponse:
    def __init__(self, status: int = 200, text: str = ""):
        self.status_code, self.text, self.ok = status, text, status < 400


def test_ntfy_reasons():
    assert "NTFY_TOPIC" in make(ntfy_topic="").unavailable_reason(Channel.NTFY)
    assert "chữ, số" in make(ntfy_topic="có dấu cách").unavailable_reason(Channel.NTFY)
    assert make(ntfy_topic="stockvn-abc_123").unavailable_reason(Channel.NTFY) is None


def test_ntfy_posts_json_to_root_with_token(monkeypatch):
    calls = []

    def fake_post(url, json, headers, timeout):
        calls.append((url, json, headers))
        return FakeResponse()

    monkeypatch.setattr(notifier_mod.requests, "post", fake_post)
    n = make(ntfy_server="https://ntfy.example.com/", ntfy_topic="stockvn-x", ntfy_token="tk_abc")
    assert n.send((Channel.NTFY,), "FPT vượt 120.000", "x" * 5000) == {Channel.NTFY: "ok"}
    url, payload, headers = calls[0]
    assert url == "https://ntfy.example.com/"  # topic nằm trong JSON, không lộ trên URL
    assert payload["topic"] == "stockvn-x" and payload["title"] == "FPT vượt 120.000"
    assert len(payload["message"].encode()) <= notifier_mod.NTFY_MAX_BYTES
    assert headers == {"Authorization": "Bearer tk_abc"}


def test_ntfy_truncates_without_breaking_utf8(monkeypatch):
    sent = {}
    monkeypatch.setattr(notifier_mod.requests, "post",
                        lambda url, json, headers, timeout: sent.update(json) or FakeResponse())
    make(ntfy_topic="t").send_ntfy("t", "ệ" * 3000)  # 3 byte/ký tự -> cắt giữa ký tự
    assert set(sent["message"]) == {"ệ"}
    assert sent.get("priority") == notifier_mod.NTFY_PRIORITY


def test_ntfy_http_error_reported(monkeypatch):
    monkeypatch.setattr(notifier_mod.requests, "post",
                        lambda *a, **kw: FakeResponse(429, '{"error":"limit reached"}'))
    res = make(ntfy_topic="t", ntfy_token="").send((Channel.NTFY,), "t", "b")
    assert "HTTP 429" in res[Channel.NTFY] and "limit reached" in res[Channel.NTFY]
