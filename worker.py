"""Tiến trình nền: cập nhật giá trong giờ giao dịch và gửi cảnh báo.

    python worker.py            # chạy liên tục
    python worker.py --once     # chạy một vòng rồi thoát (dùng với Task Scheduler)
    python worker.py --force    # chạy cả ngoài giờ giao dịch (để thử)
"""
from __future__ import annotations

import argparse
import logging
import signal
import sys
import time
from logging.handlers import RotatingFileHandler

from stockvn.config import get_settings
from stockvn.market import is_trading_day, is_trading_time, now_vn, session_label
from stockvn.service import build_app

log = logging.getLogger("worker")
EOD_KEY = "worker_eod_date"
IDLE_SLEEP = 60


def setup_logging() -> None:
    s = get_settings()
    s.log_dir.mkdir(parents=True, exist_ok=True)
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    file_h = RotatingFileHandler(s.log_dir / "worker.log", maxBytes=2_000_000, backupCount=3, encoding="utf-8")
    file_h.setFormatter(fmt)
    console = logging.StreamHandler(sys.stdout)
    console.setFormatter(fmt)
    logging.basicConfig(level=logging.INFO, handlers=[file_h, console])


def cycle(app) -> None:
    res = app.run_cycle()
    log.info("Đã cập nhật %d mã, lỗi %d, cảnh báo bắn %d", len(res.quotes), len(res.errors), len(res.fired))


def main() -> int:
    parser = argparse.ArgumentParser(description="Worker cập nhật giá & cảnh báo chứng khoán VN")
    parser.add_argument("--once", action="store_true", help="chạy một vòng rồi thoát")
    parser.add_argument("--force", action="store_true", help="chạy cả ngoài giờ giao dịch")
    parser.add_argument("--interval", type=int, default=None, help="giây giữa hai vòng (mặc định STOCK_POLL_SECONDS)")
    args = parser.parse_args()

    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    setup_logging()
    app = build_app()
    interval = max(15, args.interval or get_settings().poll_seconds)

    if args.once:
        cycle(app)
        return 0

    stop = False

    def _stop(*_):
        nonlocal stop
        stop = True
        log.info("Nhận tín hiệu dừng…")

    signal.signal(signal.SIGINT, _stop)
    signal.signal(signal.SIGTERM, _stop)
    log.info("Worker khởi động, chu kỳ %ss", interval)

    last_label = None
    while not stop:
        now = now_vn()
        wait = interval
        try:
            if args.force or is_trading_time(now):
                cycle(app)
            elif is_trading_day(now.date()) and now.hour >= 15 and app.repo.get_meta(EOD_KEY) != now.date().isoformat():
                log.info("Chốt giá cuối ngày")
                cycle(app)
                app.repo.set_meta(EOD_KEY, now.date().isoformat())
            else:
                wait = IDLE_SLEEP
                if (label := session_label(now)) != last_label:
                    log.info("Ngoài giờ giao dịch (%s), tạm nghỉ", label)
                    last_label = label
        except Exception:  # noqa: BLE001 - worker không được chết vì một vòng lỗi
            log.exception("Vòng cập nhật lỗi")
        for _ in range(wait):
            if stop:
                break
            time.sleep(1)
    return 0


if __name__ == "__main__":
    sys.exit(main())
