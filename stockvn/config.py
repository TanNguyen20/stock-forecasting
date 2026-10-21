"""Cấu hình ứng dụng, đọc từ biến môi trường hoặc file .env ở thư mục gốc."""
from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")


def _bool(value: str | None, default: bool = False) -> bool:
    if value is None or value.strip() == "":
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _list(value: str | None) -> tuple[str, ...]:
    if not value:
        return ()
    return tuple(x.strip() for x in value.split(",") if x.strip())


@dataclass(frozen=True)
class Settings:
    db_path: Path
    log_dir: Path
    sources: tuple[str, ...]
    poll_seconds: int
    history_years: int
    embed_worker: bool

    smtp_host: str
    smtp_port: int
    smtp_user: str
    smtp_password: str
    smtp_from: str
    smtp_to: tuple[str, ...]
    smtp_use_ssl: bool

    toast_enabled: bool
    toast_app_id: str

    webpush_private_key: str
    webpush_subject: str

    @property
    def email_enabled(self) -> bool:
        return bool(self.smtp_host and self.smtp_to)


def _path(value: str | None, default: str) -> Path:
    p = Path(value or default)
    return p if p.is_absolute() else ROOT / p


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    env = os.environ.get
    return Settings(
        db_path=_path(env("STOCK_DB_PATH"), "data/stock.db"),
        log_dir=_path(env("STOCK_LOG_DIR"), "logs"),
        sources=_list(env("STOCK_SOURCES")) or ("VCI", "DNSE", "KBS"),
        poll_seconds=int(env("STOCK_POLL_SECONDS", "60")),
        history_years=int(env("STOCK_HISTORY_YEARS", "10")),
        embed_worker=_bool(env("STOCK_EMBED_WORKER"), default=True),
        smtp_host=env("SMTP_HOST", ""),
        smtp_port=int(env("SMTP_PORT", "465")),
        smtp_user=env("SMTP_USER", ""),
        smtp_password=env("SMTP_PASSWORD", ""),
        smtp_from=env("SMTP_FROM", "") or env("SMTP_USER", ""),
        smtp_to=_list(env("SMTP_TO")),
        smtp_use_ssl=_bool(env("SMTP_USE_SSL"), default=True),
        toast_enabled=_bool(env("TOAST_ENABLED"), default=True),
        toast_app_id=env("TOAST_APP_ID", "Chứng khoán VN"),
        webpush_private_key=env("WEBPUSH_VAPID_PRIVATE_KEY", ""),
        webpush_subject=env("WEBPUSH_SUBJECT", ""),
    )
