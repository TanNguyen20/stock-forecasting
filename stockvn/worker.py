"""Vòng lặp worker: cập nhật giá trong giờ giao dịch và gửi cảnh báo.

Dùng chung cho tiến trình riêng (`worker.py`) và luồng nền chạy kèm giao diện (`app.py`).
Khóa file cạnh CSDL đảm bảo mỗi CSDL chỉ có một worker hoạt động, tránh gửi cảnh báo trùng;
worker đến sau sẽ chờ và tự tiếp quản khi worker trước dừng.
"""
from __future__ import annotations

import logging
import sys
import threading
from collections.abc import Callable
from datetime import date
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import IO

from .config import get_settings
from .digest import parse_channels, send_digest
from .market import is_trading_day, is_trading_time, now_vn, session_label
from .service import App, create_app

log = logging.getLogger(__name__)

EOD_KEY = "worker_eod_date"
IDLE_SLEEP = 60
LOCK_RETRY = 30
MIN_INTERVAL = 15
THREAD_NAME = "stockvn-worker"
LOG_FORMAT = "%(asctime)s %(levelname)s %(name)s: %(message)s"


# ---------- Khóa một-worker-mỗi-CSDL ----------
class InstanceLock:
    """Khóa file độc quyền, không chặn. Hệ điều hành tự nhả khi tiến trình chết."""

    def __init__(self, path: Path):
        self.path = path
        self._fh: IO[str] | None = None

    def acquire(self) -> bool:
        if self._fh is not None:
            return True
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fh = open(self.path, "a+")  # noqa: SIM115 - giữ mở suốt thời gian giữ khóa
        try:
            if sys.platform == "win32":
                import msvcrt

                fh.seek(0)
                msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            fh.close()
            return False
        self._fh = fh
        return True

    def release(self) -> None:
        if self._fh is None:
            return
        try:
            if sys.platform == "win32":
                import msvcrt

                self._fh.seek(0)
                msvcrt.locking(self._fh.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(self._fh.fileno(), fcntl.LOCK_UN)
        except OSError:
            pass
        finally:
            self._fh.close()
            self._fh = None


def lock_path() -> Path:
    db = get_settings().db_path
    return db.with_name(db.name + ".worker.lock")


# ---------- Vòng lặp ----------
def poll_interval(override: int | None = None) -> int:
    return max(MIN_INTERVAL, override or get_settings().poll_seconds)


def run_cycle(app: App) -> None:
    res = app.run_cycle()
    log.info("Đã cập nhật %d mã, lỗi %d, cảnh báo bắn %d", len(res.quotes), len(res.errors), len(res.fired))


def end_of_day(app: App, day: date) -> None:
    """Sau khi chốt giá: gửi báo cáo cuối phiên, rồi dự báo và ghi sổ cho các mã đang theo dõi."""
    s = get_settings()
    try:
        send_digest(app.repo, app.notifier, day, parse_channels(s.digest_channels),
                    sell_cost_pct=s.trade_fee_pct + s.sell_tax_pct)
    except Exception:  # noqa: BLE001 - lỗi báo cáo không được chặn phần dự báo
        log.exception("Gửi báo cáo cuối phiên lỗi")
    if s.forecast_log_models:
        from .forecast.ledger import auto_forecast  # nạp scipy/statsmodels chỉ khi cần

        log.info("Dự báo cuối ngày (%s, %d phiên)", ",".join(s.forecast_log_models), s.forecast_log_horizon)
        for sym, err in auto_forecast(app.repo, app.tracked_symbols(), s.forecast_log_models,
                                      s.forecast_log_horizon).items():
            log.warning("Dự báo %s: %s", sym, err)


def _sleep(stop: threading.Event, seconds: int) -> None:
    # Chờ từng giây để Ctrl+C trên Windows được xử lý kịp ở luồng chính.
    for _ in range(seconds):
        if stop.wait(1):
            return


def run_loop(app: App, stop: threading.Event, interval: int, force: bool = False) -> None:
    last_label = None
    while not stop.is_set():
        now = now_vn()
        wait = interval
        try:
            if force or is_trading_time(now):
                run_cycle(app)
            elif is_trading_day(now.date()) and now.hour >= 15 and app.repo.get_meta(EOD_KEY) != now.date().isoformat():
                log.info("Chốt giá cuối ngày")
                run_cycle(app)
                app.repo.set_meta(EOD_KEY, now.date().isoformat())
                end_of_day(app, now.date())
            else:
                wait = IDLE_SLEEP
                if (label := session_label(now)) != last_label:
                    log.info("Ngoài giờ giao dịch (%s), tạm nghỉ", label)
                    last_label = label
        except Exception:  # noqa: BLE001 - worker không được chết vì một vòng lỗi
            log.exception("Vòng cập nhật lỗi")
        _sleep(stop, wait)


def serve(stop: threading.Event, interval: int, force: bool = False,
          on_state: Callable[[str], None] | None = None) -> None:
    """Giành khóa (chờ nếu worker khác đang giữ) rồi chạy vòng lặp đến khi `stop` được đặt."""
    notify = on_state or (lambda _state: None)
    lock = InstanceLock(lock_path())
    waiting = False
    while not lock.acquire():
        if not waiting:
            log.info("Đã có worker khác chạy trên CSDL này, chờ để tiếp quản…")
            notify("standby")
            waiting = True
        _sleep(stop, LOCK_RETRY)
        if stop.is_set():
            return
    try:
        notify("running")
        log.info("Worker khởi động, chu kỳ %ss", interval)
        run_loop(create_app(), stop, interval, force)
    finally:
        lock.release()
        log.info("Worker đã dừng")


# ---------- Chạy kèm giao diện (luồng nền trong tiến trình Streamlit) ----------
_guard = threading.Lock()
_thread: threading.Thread | None = None
_stop = threading.Event()
_state = "off"


def _set_state(state: str) -> None:
    global _state
    _state = state


def utf8_stdio() -> None:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")


def _setup_embedded_logging() -> None:
    """Ghi log `stockvn.*` của tiến trình giao diện ra logs/app.log và console."""
    pkg = logging.getLogger("stockvn")
    if any(getattr(h, "_stockvn", False) for h in pkg.handlers):
        return
    s = get_settings()
    s.log_dir.mkdir(parents=True, exist_ok=True)
    utf8_stdio()
    fmt = logging.Formatter(LOG_FORMAT)
    handlers: list[logging.Handler] = [
        RotatingFileHandler(s.log_dir / "app.log", maxBytes=2_000_000, backupCount=3, encoding="utf-8"),
        logging.StreamHandler(sys.stdout),
    ]
    for h in handlers:
        h.setFormatter(fmt)
        h._stockvn = True  # type: ignore[attr-defined]
        pkg.addHandler(h)
    pkg.setLevel(logging.INFO)


def _run_embedded() -> None:
    try:
        serve(_stop, poll_interval(), on_state=_set_state)
    except Exception:  # noqa: BLE001
        log.exception("Worker nền dừng do lỗi")
    finally:
        _set_state("stopped")


def start_background() -> bool:
    """Khởi động worker trong luồng nền của tiến trình hiện tại, chỉ một lần. True nếu vừa khởi động."""
    global _thread
    with _guard:
        if _thread is not None:
            return False
        _setup_embedded_logging()
        _set_state("starting")
        _thread = threading.Thread(target=_run_embedded, name=THREAD_NAME, daemon=True)
        _thread.start()
        return True


def background_state() -> str:
    """off · starting · running · standby (worker khác đang giữ khóa) · stopped."""
    return _state
