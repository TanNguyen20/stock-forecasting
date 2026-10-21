"""Tiến trình nền: cập nhật giá trong giờ giao dịch và gửi cảnh báo.

    python worker.py            # chạy liên tục
    python worker.py --once     # chạy một vòng rồi thoát (dùng với Task Scheduler)
    python worker.py --force    # chạy cả ngoài giờ giao dịch (để thử)

Không bắt buộc khi đã chạy `python app.py`: giao diện tự chạy kèm worker (tắt bằng STOCK_EMBED_WORKER=false).
Nếu worker khác (kể cả worker trong giao diện) đang chạy trên cùng CSDL, tiến trình này chờ và tự tiếp quản khi nó dừng.
"""
from __future__ import annotations

import argparse
import logging
import signal
import sys
import threading
from logging.handlers import RotatingFileHandler

from stockvn.config import get_settings
from stockvn.service import create_app
from stockvn.worker import LOG_FORMAT, InstanceLock, lock_path, poll_interval, run_cycle, serve, utf8_stdio

log = logging.getLogger("worker")


def setup_logging() -> None:
    s = get_settings()
    s.log_dir.mkdir(parents=True, exist_ok=True)
    fmt = logging.Formatter(LOG_FORMAT)
    file_h = RotatingFileHandler(s.log_dir / "worker.log", maxBytes=2_000_000, backupCount=3, encoding="utf-8")
    file_h.setFormatter(fmt)
    console = logging.StreamHandler(sys.stdout)
    console.setFormatter(fmt)
    logging.basicConfig(level=logging.INFO, handlers=[file_h, console])


def main() -> int:
    parser = argparse.ArgumentParser(description="Worker cập nhật giá & cảnh báo chứng khoán VN")
    parser.add_argument("--once", action="store_true", help="chạy một vòng rồi thoát")
    parser.add_argument("--force", action="store_true", help="chạy cả ngoài giờ giao dịch")
    parser.add_argument("--interval", type=int, default=None, help="giây giữa hai vòng (mặc định STOCK_POLL_SECONDS)")
    args = parser.parse_args()

    utf8_stdio()
    setup_logging()

    if args.once:
        lock = InstanceLock(lock_path())
        if not lock.acquire():
            log.info("Worker khác đang chạy trên CSDL này, bỏ qua vòng --once")
            return 0
        try:
            run_cycle(create_app())
        finally:
            lock.release()
        return 0

    stop = threading.Event()

    def _stop(*_):
        stop.set()
        log.info("Nhận tín hiệu dừng…")

    signal.signal(signal.SIGINT, _stop)
    signal.signal(signal.SIGTERM, _stop)
    serve(stop, poll_interval(args.interval), force=args.force)
    return 0


if __name__ == "__main__":
    sys.exit(main())
