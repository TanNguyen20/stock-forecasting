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
